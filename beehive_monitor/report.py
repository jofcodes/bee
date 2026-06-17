"""Report generation — JSON digest + standalone HTML with thumbnails."""

from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime
from html import escape
from pathlib import Path

import cv2
import numpy as np

from .config import ReportConfig
from .models import FlaggedEvent

log = logging.getLogger(__name__)


def _thumb_b64(crop: np.ndarray, max_width: int) -> str:
    h, w = crop.shape[:2]
    if w > max_width:
        scale = max_width / w
        crop = cv2.resize(crop, (max_width, int(h * scale)))
    _, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return base64.b64encode(buf.tobytes()).decode("ascii")


def save_crops(
    events: list[FlaggedEvent],
    crops_map: dict[int, list[np.ndarray]],
    output_dir: Path,
) -> None:
    """Save crop images to disk and update event.crop_paths."""
    crops_dir = output_dir / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)
    for i, event in enumerate(events):
        crops = crops_map.get(i, [])
        for j, crop in enumerate(crops):
            fname = f"event_{i:04d}_frame_{j:02d}.jpg"
            path = crops_dir / fname
            cv2.imwrite(str(path), crop)
            event.crop_paths.append(path)


def write_json_digest(
    events: list[FlaggedEvent],
    total_clips: int,
    output_dir: Path,
) -> Path:
    """Write a machine-readable JSON digest."""
    records = []
    for event in events:
        records.append({
            "clip": str(event.clip_path.name),
            "timestamp": event.timestamp.isoformat(),
            "anomaly_score": round(event.anomaly_score, 2),
            "reasons": event.reasons,
            "frame_count": len(event.frame_indices),
            "crops": [str(p.name) for p in event.crop_paths],
            "level2_confirmed": event.level2_confirmed,
            "level2_response": event.level2_response or None,
        })
    digest = {
        "generated": datetime.now().isoformat(),
        "total_clips_analyzed": total_clips,
        "events_flagged": len(events),
        "events": records,
    }
    path = output_dir / "digest.json"
    with open(path, "w") as f:
        json.dump(digest, f, indent=2)
    log.info("Wrote %s", path)
    return path


def write_html_report(
    events: list[FlaggedEvent],
    crops_map: dict[int, list[np.ndarray]],
    context_frames: dict[int, np.ndarray | None],
    total_clips: int,
    output_dir: Path,
    report_cfg: ReportConfig,
    clips_dir: Path | None = None,
) -> Path:
    """Write a standalone HTML report with embedded thumbnails."""
    parts: list[str] = []
    parts.append(f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Beehive Monitor Report</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 2em auto; background: #fafaf8; color: #222; }}
  h1 {{ color: #b8860b; }}
  .summary {{ background: #fff8e1; padding: 1em; border-radius: 8px; margin-bottom: 2em; }}
  .event {{ border: 1px solid #ddd; border-radius: 8px; padding: 1em; margin-bottom: 1.5em; background: #fff; }}
  .event.confirmed {{ border-color: #d32f2f; background: #fff5f5; }}
  .event.dismissed {{ border-color: #aaa; opacity: 0.6; }}
  .score {{ float: right; font-size: 1.3em; font-weight: bold; color: #d32f2f; }}
  .reason {{ background: #fff3cd; padding: 0.3em 0.6em; border-radius: 4px; margin: 0.2em 0; display: inline-block; font-size: 0.9em; }}
  .l2 {{ margin-top: 0.5em; padding: 0.5em; background: #e8f5e9; border-radius: 4px; font-size: 0.9em; }}
  .l2.alert {{ background: #ffebee; }}
  .thumbs {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 0.5em; }}
  .thumbs img {{ border-radius: 4px; border: 1px solid #ccc; }}
  .context {{ margin-top: 0.5em; margin-bottom: 0.5em; }}
  .context img {{ max-width: 100%; border-radius: 8px; border: 2px solid #b8860b; }}
  .clip-video {{ margin-top: 0.5em; }}
  .clip-video video {{ max-width: 100%; border-radius: 8px; border: 1px solid #ccc; }}
  small {{ color: #666; }}
</style></head><body>
<h1>&#x1f41d; Beehive Monitor — Anomaly Report</h1>
<div class="summary">
  <strong>Generated:</strong> {escape(datetime.now().strftime("%Y-%m-%d %H:%M"))}<br>
  <strong>Clips analyzed:</strong> {total_clips}<br>
  <strong>Events flagged:</strong> {len(events)}
</div>
""")

    shown = events[: report_cfg.max_events]
    for i, event in enumerate(shown):
        css_class = "event"
        if event.level2_confirmed is True:
            css_class += " confirmed"
        elif event.level2_confirmed is False:
            css_class += " dismissed"

        parts.append(f'<div class="{css_class}">')
        parts.append(f'<span class="score">{event.anomaly_score:.1f}</span>')
        parts.append(f"<strong>{escape(event.clip_path.name)}</strong><br>")
        parts.append(f"<small>{escape(event.timestamp.strftime('%Y-%m-%d %H:%M:%S'))}</small><br>")

        for reason in event.reasons:
            parts.append(f'<span class="reason">{escape(reason)}</span> ')

        # Level 2 result
        if event.level2_response:
            alert = "alert" if event.level2_confirmed else ""
            label = {True: "CONFIRMED", False: "Normal", None: "Uncertain"}.get(
                event.level2_confirmed, "?"
            )
            parts.append(
                f'<div class="l2 {alert}"><strong>Vision model ({label}):</strong> '
                f"{escape(event.level2_response)}</div>"
            )

        # Video player for the clip
        clip_path = event.clip_path
        if clips_dir and clip_path.exists():
            # Use relative path from report to clip
            try:
                rel = os.path.relpath(clip_path, output_dir)
            except ValueError:
                rel = str(clip_path)
            parts.append(
                f'<div class="clip-video">'
                f'<video controls loop muted preload="metadata" width="100%">'
                f'<source src="{escape(rel)}" type="video/mp4">'
                f'</video></div>'
            )

        # Context frame (full view with highlighted blob)
        ctx = context_frames.get(i)
        if ctx is not None:
            b64_ctx = _thumb_b64(ctx, 800)
            parts.append(
                f'<div class="context"><img src="data:image/jpeg;base64,{b64_ctx}" '
                f'alt="full frame with highlighted anomaly"></div>'
            )

        # Thumbnails
        crops = crops_map.get(i, [])
        if crops:
            parts.append('<div class="thumbs">')
            for crop in crops[:5]:
                b64 = _thumb_b64(crop, report_cfg.thumbnail_width)
                parts.append(f'<img src="data:image/jpeg;base64,{b64}" alt="crop">')
            parts.append("</div>")

        parts.append("</div>")

    if len(events) > report_cfg.max_events:
        parts.append(f"<p><em>… and {len(events) - report_cfg.max_events} more events (see digest.json)</em></p>")

    parts.append("</body></html>")

    path = output_dir / "report.html"
    path.write_text("\n".join(parts))
    log.info("Wrote %s", path)
    return path
