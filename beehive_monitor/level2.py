"""Vision-based clip analysis using Llama Vision models.

Sends sampled frames from each clip to a vision model and asks it to
identify what animals/insects are present. Only clips with non-bee content
are flagged.

Supports two backends:
  - llama_api: Meta's Llama API (api.llama.com) — best quality, uses MetaGen
  - ollama: Local Ollama server — runs on your machine, no API key needed

Policy notes:
  - Uses Meta's own Llama models only (Meta-approved).
  - Only personal beehive footage is processed — no Meta internal data.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from .config import VisionConfig

log = logging.getLogger(__name__)

ANALYSIS_PROMPT = """\
You are analyzing a frame from a beehive entrance camera. This camera watches \
the entrance of an active honeybee hive.

CRITICAL: The vast majority of insects you see at a beehive entrance ARE honeybees. \
This is their home. Only flag something as non-bee if you are VERY confident.

How to tell honeybees from wasps/hornets:
- HONEYBEES: fuzzy/hairy bodies, golden-brown or amber color, rounded body shape, \
  carry pollen on their legs. These are EXPECTED here.
- WASPS/HORNETS: smooth/shiny bodies, narrow "waist" between thorax and abdomen, \
  bright yellow-black stripes, more elongated shape. These are THREATS.
- If unsure, assume it's a honeybee — they live here.

Only set has_non_bee_content to TRUE if you see:
- An animal that is clearly NOT an insect (bird, mouse, rat, lizard, snake)
- An insect that is clearly NOT a bee (large hornet, spider, beetle)
- Robbing behavior (chaotic mass of bees fighting, not orderly in/out traffic)
- A swarm (very dense ball/cluster of thousands of bees)

Respond with ONLY a JSON object:
{
  "has_non_bee_content": true/false,
  "animals_seen": ["list of animals/insects identified"],
  "description": "brief description of what you see",
  "confidence": "high/medium/low",
  "threat_level": "none/low/medium/high"
}

When in doubt, set has_non_bee_content to FALSE. Better to miss a wasp than \
to falsely flag normal bee activity.\
"""


@dataclass
class VisionResult:
    """Result of vision analysis for a single clip."""

    clip_path: Path
    timestamp: datetime
    has_non_bee_content: bool
    animals_seen: list[str]
    description: str
    confidence: str
    threat_level: str
    frame_path: Path | None = None
    raw_response: str = ""
    error: str = ""


def _encode_frame(frame: np.ndarray) -> str:
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(buf.tobytes()).decode("ascii")


def _extract_frames(clip_path: Path, n_frames: int = 3) -> list[np.ndarray]:
    """Extract n evenly-spaced frames from a clip."""
    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        return []

    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total <= 0:
        cap.release()
        return []

    start = max(0, int(total * 0.1))
    end = max(start + 1, int(total * 0.9))
    indices = np.linspace(start, end, n_frames, dtype=int)

    frames = []
    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
        ret, frame = cap.read()
        if ret:
            frames.append(frame)

    cap.release()
    return frames


def _parse_response(text: str) -> dict:
    """Extract JSON from model response."""
    for line in text.strip().splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                pass

    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

    lower = text.lower()
    has_threat = any(
        w in lower
        for w in ("wasp", "hornet", "yellow jacket", "predator", "rat",
                   "mouse", "bird", "spider", "ant ", "beetle", "robbing",
                   "unusual", "threat")
    )
    return {
        "has_non_bee_content": has_threat,
        "animals_seen": [],
        "description": text.strip()[:200],
        "confidence": "low",
        "threat_level": "unknown",
    }


def _call_llama_api(b64: str, vision_cfg: VisionConfig) -> str:
    """Call Meta's Llama API with an image, with retry on rate limit."""
    import time
    from openai import OpenAI

    api_key = vision_cfg.api_key or os.environ.get("LLAMA_API_KEY", "")
    client = OpenAI(base_url=vision_cfg.host, api_key=api_key)

    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=vision_cfg.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": ANALYSIS_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                            },
                        ],
                    }
                ],
                max_tokens=300,
            )
            return response.choices[0].message.content
        except Exception as exc:
            if "429" in str(exc) and attempt < 2:
                wait = 2 ** (attempt + 1)
                log.debug("Rate limited, waiting %ds...", wait)
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("Max retries exceeded")


def _call_ollama(b64: str, vision_cfg: VisionConfig) -> str:
    """Call local Ollama server with an image."""
    import ollama as ollama_lib

    ollama_client = ollama_lib.Client(host=vision_cfg.host)
    response = ollama_client.chat(
        model=vision_cfg.model,
        messages=[
            {
                "role": "user",
                "content": ANALYSIS_PROMPT,
                "images": [b64],
            }
        ],
    )
    return response["message"]["content"]


def analyze_clip_vision(
    clip_path: Path,
    vision_cfg: VisionConfig,
    client=None,
) -> VisionResult:
    """Analyze a clip by sending a sampled frame to a vision model."""
    timestamp = datetime.fromtimestamp(clip_path.stat().st_mtime)

    frames = _extract_frames(clip_path, n_frames=3)
    if not frames:
        return VisionResult(
            clip_path=clip_path,
            timestamp=timestamp,
            has_non_bee_content=False,
            animals_seen=[],
            description="Could not extract frames",
            confidence="none",
            threat_level="none",
            error="No frames extracted",
        )

    frame = frames[len(frames) // 2]
    b64 = _encode_frame(frame)

    try:
        if vision_cfg.backend == "ollama":
            text = _call_ollama(b64, vision_cfg)
        else:
            text = _call_llama_api(b64, vision_cfg)

        parsed = _parse_response(text)

        result = VisionResult(
            clip_path=clip_path,
            timestamp=timestamp,
            has_non_bee_content=parsed.get("has_non_bee_content", False),
            animals_seen=parsed.get("animals_seen", []),
            description=parsed.get("description", ""),
            confidence=parsed.get("confidence", "unknown"),
            threat_level=parsed.get("threat_level", "none"),
            raw_response=text,
        )
        log.info(
            "  %s → %s | %s",
            clip_path.name,
            "FLAGGED" if result.has_non_bee_content else "normal",
            result.description[:80],
        )
        return result

    except Exception as exc:
        log.error("Vision error for %s: %s", clip_path.name, exc)
        return VisionResult(
            clip_path=clip_path,
            timestamp=timestamp,
            has_non_bee_content=False,
            animals_seen=[],
            description="",
            confidence="none",
            threat_level="none",
            error=str(exc),
        )
