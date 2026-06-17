"""Level 2 — vision-model confirmation via Ollama (LLaVA).

For each flagged event from Level 1, send the crop(s) to a local
vision model and ask whether it sees anything other than honeybees.
"""

from __future__ import annotations

import base64
import json
import logging
from io import BytesIO

import cv2
import numpy as np

from .config import VisionConfig
from .models import FlaggedEvent

log = logging.getLogger(__name__)


def _encode_crop(crop: np.ndarray) -> str:
    """Encode a BGR numpy array to a base64 JPEG string."""
    _, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(buf.tobytes()).decode("ascii")


def _parse_response(text: str) -> tuple[bool | None, str]:
    """Try to extract structured answer from model response."""
    # Try JSON parse first
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                obj = json.loads(line)
                anomaly = obj.get("anomaly")
                desc = obj.get("description", "")
                return anomaly, desc
            except json.JSONDecodeError:
                pass

    # Fallback: keyword search
    lower = text.lower()
    if any(w in lower for w in ("wasp", "hornet", "yellow jacket", "predator", "robbing", "unusual")):
        return True, text.strip()
    if "no" in lower and "anomal" in lower:
        return False, text.strip()
    if "normal" in lower and "honeybee" in lower:
        return False, text.strip()

    # Ambiguous — return the text but don't confirm
    return None, text.strip()


def confirm_event(
    event: FlaggedEvent,
    crops: list[np.ndarray],
    vision_cfg: VisionConfig,
) -> None:
    """Send crops to Ollama/LLaVA and update the event with the response.

    Modifies event in place: sets level2_response and level2_confirmed.
    """
    try:
        import ollama
    except ImportError:
        log.warning(
            "ollama package not installed — skipping Level 2. "
            "Install with: pip install ollama"
        )
        event.level2_response = "SKIPPED: ollama not installed"
        return

    if not crops:
        event.level2_response = "No crops available"
        return

    # Use the best (largest) crop
    best_crop = max(crops, key=lambda c: c.shape[0] * c.shape[1])
    b64 = _encode_crop(best_crop)

    try:
        client = ollama.Client(host=vision_cfg.host)
        response = client.chat(
            model=vision_cfg.model,
            messages=[
                {
                    "role": "user",
                    "content": vision_cfg.prompt,
                    "images": [b64],
                }
            ],
        )
        text = response["message"]["content"]
        confirmed, description = _parse_response(text)
        event.level2_confirmed = confirmed
        event.level2_response = description
        log.info(
            "Level 2 for %s: confirmed=%s — %s",
            event.clip_path.name,
            confirmed,
            description[:100],
        )
    except Exception as exc:
        log.error("Ollama call failed: %s", exc)
        event.level2_response = f"ERROR: {exc}"
