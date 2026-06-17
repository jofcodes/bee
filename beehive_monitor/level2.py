"""Vision-based clip analysis using Ollama with Llama 3.2 Vision.

Sends sampled frames from each clip to Meta's Llama 3.2 Vision model
running locally via Ollama, and asks it to identify what animals/insects
are present. Only clips with non-bee content are flagged.

Policy notes:
  - Uses Meta's own Llama 3.2 Vision model only (Meta-approved).
  - Ollama is bound to localhost (127.0.0.1) — not exposed on the network.
  - Only personal beehive footage is processed — no Meta internal data.

Requires Ollama running locally:
    # Install from https://ollama.com/download (macOS app)
    ollama pull llama3.2-vision
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from .config import VisionConfig

log = logging.getLogger(__name__)

ANALYSIS_PROMPT = """\
You are analyzing a frame from a beehive entrance camera. Your job is to identify \
what is visible in the image.

Look carefully for:
1. Honeybees (normal — the expected residents)
2. Wasps or hornets (yellow jackets, paper wasps, Asian giant hornets, etc.)
3. Other predators (birds, mice, rats, lizards, spiders, ants, beetles)
4. Robbing behavior (many bees fighting/wrestling at the entrance)
5. Swarm activity (large dense cluster of bees)
6. Any other unusual animals, insects, or objects

Respond with ONLY a JSON object (no other text):
{
  "has_non_bee_content": true/false,
  "animals_seen": ["list of animals/insects identified"],
  "description": "brief description of what you see",
  "confidence": "high/medium/low",
  "threat_level": "none/low/medium/high"
}

If you only see normal honeybees coming and going, set has_non_bee_content to false.\
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

    # Skip first and last 10% (often blank/transitional)
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
    # Try direct JSON parse
    for line in text.strip().splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                pass

    # Try finding JSON block in the text
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

    # Fallback: keyword detection
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


def analyze_clip_vision(
    clip_path: Path,
    vision_cfg: VisionConfig,
    client=None,
) -> VisionResult:
    """Analyze a clip by sending a sampled frame to Ollama/Llama 3.2 Vision."""
    import ollama as ollama_lib

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

    # Use the middle frame for analysis (best chance of showing action)
    frame = frames[len(frames) // 2]
    b64 = _encode_frame(frame)

    try:
        # Connect to Ollama on localhost only (not exposed to network)
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
        text = response["message"]["content"]
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
