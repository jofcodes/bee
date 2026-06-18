#!/usr/bin/env python3
"""Deploy an interactive beehive threat dashboard to Meta Portal.

Shows flagged events with annotated frames (red rectangles highlighting
the detected objects) and vision model descriptions.

Prerequisites:
    brew install --cask android-platform-tools
    # Connect Portal via USB-C, enable ADB in Settings → Debug

Usage:
    python portal_deploy.py results/              # push to Portal
    python portal_deploy.py results/ --top 10     # only top 10 events
    python portal_deploy.py results/ --preview    # open in browser instead
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import shutil
import subprocess
import sys
from html import escape
from pathlib import Path

import cv2
import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("portal")

PORTAL_PATH = "/sdcard/beehive"


def _check_adb() -> bool:
    if not shutil.which("adb"):
        log.error(
            "adb not found. Install it:\n"
            "  brew install --cask android-platform-tools"
        )
        return False
    result = subprocess.run(["adb", "devices"], capture_output=True, text=True)
    lines = [l for l in result.stdout.strip().split("\n")[1:] if l.strip()]
    if not lines:
        log.error("No device connected via ADB.")
        return False
    log.info("Found device: %s", lines[0].split("\t")[0])
    return True


def _adb(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["adb", *args], capture_output=True, text=True)


def _extract_middle_frame(clip_path: Path) -> np.ndarray | None:
    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        return None
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.set(cv2.CAP_PROP_POS_FRAMES, total // 2)
    ret, frame = cap.read()
    cap.release()
    return frame if ret else None


def _annotate_frame(frame: np.ndarray, min_area: int = 100) -> np.ndarray:
    """Find moving/distinct objects and draw red rectangles around them."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (11, 11), 0)

    # Use adaptive thresholding to find dark objects on lighter background
    thresh = cv2.adaptiveThreshold(
        blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, 31, 10
    )

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    annotated = frame.copy()
    boxes = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        boxes.append((x, y, w, h, area))

    # Draw red rectangles on the largest objects (likely the detected animals)
    boxes.sort(key=lambda b: b[4], reverse=True)
    for x, y, w, h, area in boxes[:10]:
        cv2.rectangle(annotated, (x, y), (x + w, y + h), (0, 0, 255), 2)

    return annotated


def _frame_to_b64(frame: np.ndarray, max_width: int = 800) -> str:
    h, w = frame.shape[:2]
    if w > max_width:
        scale = max_width / w
        frame = cv2.resize(frame, (max_width, int(h * scale)))
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(buf.tobytes()).decode("ascii")


def generate_portal_html(
    digest: dict,
    clips_dir: Path,
    top_n: int | None = None,
) -> str:
    events = digest.get("events", [])
    if top_n:
        events = events[:top_n]

    total_analyzed = digest.get("total_clips_analyzed", 0)
    total_flagged = len(events)

    slides: list[dict] = []
    for event in events:
        clip_name = event.get("clip", "")
        clip_path = clips_dir / clip_name
        description = event.get("description", "")
        animals = event.get("animals_seen", [])
        threat = event.get("threat_level", "none")
        confidence = event.get("confidence", "unknown")

        img_b64 = ""
        if clip_path.exists():
            frame = _extract_middle_frame(clip_path)
            if frame is not None:
                annotated = _annotate_frame(frame)
                img_b64 = _frame_to_b64(annotated)

        slides.append({
            "clip": clip_name,
            "img": img_b64,
            "description": description,
            "animals": animals,
            "threat": threat,
            "confidence": confidence,
        })

    slides_json = json.dumps(slides)

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Beehive Monitor</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: #1a1a1a; color: #f0e6d3;
    font-family: system-ui, sans-serif;
    height: 100vh; width: 100vw; overflow: hidden;
    touch-action: pan-x;
  }}
  .header {{
    background: linear-gradient(135deg, #2d1f00, #4a3520);
    padding: 12px 20px;
    display: flex; align-items: center; justify-content: space-between;
  }}
  .header h1 {{ font-size: 1.4em; color: #ffd700; }}
  .header .stats {{ font-size: 0.9em; opacity: 0.7; }}
  .slide {{
    display: flex; flex-direction: column;
    height: calc(100vh - 56px); padding: 12px;
  }}
  .frame-container {{
    flex: 1; display: flex; align-items: center; justify-content: center;
    min-height: 0;
  }}
  .frame-container img {{
    max-height: 100%; max-width: 100%;
    border-radius: 8px; border: 2px solid #4a3520;
    object-fit: contain;
  }}
  .info-bar {{
    padding: 10px 0; flex-shrink: 0;
  }}
  .clip-name {{ font-size: 0.85em; opacity: 0.5; margin-bottom: 4px; }}
  .description {{ font-size: 1.1em; line-height: 1.3; margin-bottom: 6px; }}
  .tags {{ display: flex; gap: 6px; flex-wrap: wrap; }}
  .tag {{
    padding: 3px 10px; border-radius: 12px;
    font-size: 0.8em; font-weight: 600;
  }}
  .tag.animal {{ background: #d32f2f; color: #fff; }}
  .tag.threat {{ background: #ff6d00; color: #fff; }}
  .tag.confidence {{ background: #4a3520; color: #ffd700; }}
  .nav {{
    position: fixed; top: 50%; transform: translateY(-50%);
    font-size: 2em; color: rgba(255,215,0,0.4); cursor: pointer;
    padding: 20px; user-select: none;
  }}
  .nav:hover {{ color: rgba(255,215,0,0.8); }}
  .nav.left {{ left: 0; }}
  .nav.right {{ right: 0; }}
  .progress {{
    position: fixed; bottom: 0; left: 0; height: 3px;
    background: #ffd700; transition: width 0.3s;
  }}
  .empty {{
    display: flex; align-items: center; justify-content: center;
    height: 80vh; font-size: 1.3em; opacity: 0.5; text-align: center;
    padding: 20px;
  }}
</style></head><body>

<div class="header">
  <h1>&#x1f41d; Beehive Threats</h1>
  <div class="stats">
    <span id="counter"></span> &middot;
    {total_flagged} flagged / {total_analyzed} analyzed
  </div>
</div>
<div id="content"></div>
<div class="nav left" onclick="show(idx-1)">&#x276E;</div>
<div class="nav right" onclick="show(idx+1)">&#x276F;</div>
<div class="progress" id="progress"></div>

<script>
const slides = {slides_json};
let idx = 0;
let autoTimer = null;

function show(i) {{
  clearInterval(autoTimer);
  if (!slides.length) {{
    document.getElementById('content').innerHTML =
      '<div class="empty">No threats detected<br>&#x2705; All clips show normal honeybee activity</div>';
    document.getElementById('counter').textContent = '0 / 0';
    return;
  }}
  idx = ((i % slides.length) + slides.length) % slides.length;
  const s = slides[idx];
  const animals = (s.animals || []).map(a =>
    '<span class="tag animal">' + a + '</span>').join('');
  const threat = s.threat && s.threat !== 'none' ?
    '<span class="tag threat">' + s.threat + ' threat</span>' : '';
  const conf = s.confidence ?
    '<span class="tag confidence">' + s.confidence + '</span>' : '';

  document.getElementById('content').innerHTML =
    '<div class="slide">' +
    '<div class="frame-container">' +
    (s.img ? '<img src="data:image/jpeg;base64,' + s.img + '">' : '') +
    '</div>' +
    '<div class="info-bar">' +
    '<div class="clip-name">' + s.clip + '</div>' +
    '<div class="description">' + (s.description || '') + '</div>' +
    '<div class="tags">' + animals + threat + conf + '</div>' +
    '</div></div>';

  document.getElementById('counter').textContent = (idx+1) + ' / ' + slides.length;
  document.getElementById('progress').style.width =
    ((idx+1) / slides.length * 100) + '%';

  autoTimer = setInterval(() => show(idx+1), 10000);
}}

// Touch/swipe support
let touchX = 0;
document.addEventListener('touchstart', e => {{ touchX = e.touches[0].clientX; }});
document.addEventListener('touchend', e => {{
  const dx = e.changedTouches[0].clientX - touchX;
  if (Math.abs(dx) > 50) show(dx > 0 ? idx-1 : idx+1);
}});

// Keyboard support
document.addEventListener('keydown', e => {{
  if (e.key === 'ArrowRight' || e.key === ' ') show(idx+1);
  if (e.key === 'ArrowLeft') show(idx-1);
}});

show(0);
</script>
</body></html>"""


def deploy_to_portal(results_dir: Path, top_n: int | None = None) -> None:
    digest_path = results_dir / "digest.json"
    if not digest_path.exists():
        log.error("No digest.json in %s — run the analysis first.", results_dir)
        sys.exit(1)

    digest = json.loads(digest_path.read_text())
    clips_dir = results_dir.parent / "clips"

    log.info("Generating Portal dashboard (%d events)…", len(digest.get("events", [])))
    html = generate_portal_html(digest, clips_dir, top_n)

    dashboard_path = results_dir / "portal_dashboard.html"
    dashboard_path.write_text(html)
    log.info("Wrote %s", dashboard_path)

    log.info("Pushing dashboard to Portal…")
    _adb("shell", "mkdir", "-p", PORTAL_PATH)
    result = _adb("push", str(dashboard_path), f"{PORTAL_PATH}/dashboard.html")
    if result.returncode != 0:
        log.error("adb push failed: %s", result.stderr)
        sys.exit(1)

    _adb(
        "shell", "am", "start",
        "-a", "android.intent.action.VIEW",
        "-d", f"file://{PORTAL_PATH}/dashboard.html",
    )
    log.info("Dashboard deployed to Portal!")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy beehive threat dashboard to Portal via ADB.",
    )
    parser.add_argument(
        "results_dir", type=Path,
        help="Results directory (containing digest.json).",
    )
    parser.add_argument(
        "--top", type=int, default=None,
        help="Show only the top N events.",
    )
    parser.add_argument(
        "--preview", action="store_true",
        help="Open in browser instead of pushing to Portal.",
    )
    parser.add_argument(
        "--check-only", action="store_true",
        help="Only check ADB connection.",
    )
    args = parser.parse_args()

    if args.check_only:
        sys.exit(0 if _check_adb() else 1)

    if args.preview:
        digest_path = args.results_dir / "digest.json"
        digest = json.loads(digest_path.read_text())
        clips_dir = args.results_dir.parent / "clips"
        html = generate_portal_html(digest, clips_dir, args.top)
        dashboard_path = args.results_dir / "portal_dashboard.html"
        dashboard_path.write_text(html)
        log.info("Wrote %s", dashboard_path)
        import webbrowser
        webbrowser.open(str(dashboard_path))
        return

    if not _check_adb():
        sys.exit(1)
    deploy_to_portal(args.results_dir, args.top)


if __name__ == "__main__":
    main()
