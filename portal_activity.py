#!/usr/bin/env python3
"""Generate a Portal-ready dashboard showing the most active beehive clips.

Creates a self-contained HTML with embedded video thumbnails, fullscreen
playback, and activity metrics.

Usage:
    python portal_activity.py results/ --preview    # open in browser
    python portal_activity.py results/              # push to Portal via ADB
"""

from __future__ import annotations

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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("portal_activity")


def _frame_to_b64(frame: np.ndarray, max_width: int = 640) -> str:
    h, w = frame.shape[:2]
    if w > max_width:
        scale = max_width / w
        frame = cv2.resize(frame, (max_width, int(h * scale)))
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return base64.b64encode(buf.tobytes()).decode("ascii")


def _extract_thumbnail(clip_path: Path) -> str | None:
    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        return None
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.set(cv2.CAP_PROP_POS_FRAMES, total // 2)
    ret, frame = cap.read()
    cap.release()
    if not ret:
        return None
    return _frame_to_b64(frame)


def _video_to_b64(clip_path: Path) -> str | None:
    data = clip_path.read_bytes()
    return base64.b64encode(data).decode("ascii")


def generate_dashboard(activity_file: Path, clips_dir: Path) -> str:
    data = json.loads(activity_file.read_text())
    top_clips = data["top_clips"]
    total = data["total_analyzed"]

    cards = []
    for clip_info in top_clips:
        clip_path = clips_dir / clip_info["clip"]
        thumb_b64 = _extract_thumbnail(clip_path) if clip_path.exists() else None
        video_b64 = _video_to_b64(clip_path) if clip_path.exists() else None

        # Extract timestamp from filename
        name = clip_info["clip"]
        ts = name.replace("florida-bees-", "").replace("-00-00.mp4", "").replace("t", " ").replace("-", ":")
        # Fix: first 3 colons are date separators
        parts = ts.split(" ")
        if len(parts) >= 2:
            date_part = parts[0].replace(":", "-", 2)
            ts = f"{date_part} {parts[1]}"

        cards.append({
            "clip": name,
            "timestamp": ts,
            "tracks": clip_info["tracks"],
            "max_blobs": clip_info["blob_count_max"],
            "thumb": thumb_b64 or "",
            "video": video_b64 or "",
        })

    cards_json = json.dumps(cards)

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
<title>Beehive Activity</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: #111; color: #f0e6d3;
    font-family: system-ui, sans-serif;
    overflow-x: hidden;
  }}
  .header {{
    background: linear-gradient(135deg, #2d1f00, #4a3520);
    padding: 14px 20px;
    display: flex; align-items: center; justify-content: space-between;
    position: sticky; top: 0; z-index: 10;
  }}
  .header h1 {{ font-size: 1.3em; color: #ffd700; }}
  .header .stats {{ font-size: 0.85em; opacity: 0.6; }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 12px; padding: 12px;
  }}
  .card {{
    background: #1e1e1e; border-radius: 10px;
    overflow: hidden; cursor: pointer;
    transition: transform 0.15s, box-shadow 0.15s;
    border: 1px solid #333;
  }}
  .card:hover {{ transform: scale(1.02); box-shadow: 0 4px 20px rgba(255,215,0,0.15); }}
  .card img {{
    width: 100%; aspect-ratio: 16/9; object-fit: cover;
    display: block;
  }}
  .card .info {{ padding: 10px 12px; }}
  .card .time {{ font-size: 0.85em; opacity: 0.5; }}
  .card .metrics {{ display: flex; gap: 10px; margin-top: 6px; }}
  .metric {{
    background: #2a2a2a; padding: 3px 8px; border-radius: 6px;
    font-size: 0.8em;
  }}
  .metric.hot {{ background: #d32f2f; color: #fff; }}
  .metric.warm {{ background: #ff6d00; color: #fff; }}

  /* Fullscreen video overlay */
  .overlay {{
    display: none; position: fixed; top: 0; left: 0;
    width: 100vw; height: 100vh; background: #000;
    z-index: 100; align-items: center; justify-content: center;
    flex-direction: column;
  }}
  .overlay.active {{ display: flex; }}
  .overlay video {{
    max-width: 100%; max-height: 85vh; border-radius: 4px;
  }}
  .overlay .close {{
    position: absolute; top: 16px; right: 20px;
    font-size: 2em; color: #fff; cursor: pointer;
    background: rgba(0,0,0,0.5); border-radius: 50%;
    width: 44px; height: 44px; display: flex;
    align-items: center; justify-content: center;
  }}
  .overlay .caption {{
    color: #aaa; font-size: 0.9em; margin-top: 10px;
    text-align: center; padding: 0 20px;
  }}
</style></head><body>

<div class="header">
  <h1>&#x1f41d; Most Active Clips</h1>
  <div class="stats">Top 5% of {total} clips</div>
</div>

<div class="grid" id="grid"></div>

<div class="overlay" id="overlay">
  <div class="close" onclick="closeVideo()">&#x2715;</div>
  <video id="player" controls autoplay loop></video>
  <div class="caption" id="caption"></div>
</div>

<script>
const cards = {cards_json};

const grid = document.getElementById('grid');
cards.forEach((c, i) => {{
  const activityLevel = c.tracks > 150 ? 'hot' : c.tracks > 80 ? 'warm' : '';
  grid.innerHTML += `
    <div class="card" onclick="playVideo(${{i}})">
      <img src="data:image/jpeg;base64,${{c.thumb}}" alt="${{c.clip}}">
      <div class="info">
        <div class="time">${{c.timestamp}}</div>
        <div class="metrics">
          <span class="metric ${{activityLevel}}">${{c.tracks}} tracks</span>
          <span class="metric">${{c.max_blobs}} peak blobs</span>
        </div>
      </div>
    </div>`;
}});

function playVideo(i) {{
  const c = cards[i];
  const player = document.getElementById('player');
  player.src = 'data:video/mp4;base64,' + c.video;
  document.getElementById('caption').textContent =
    c.timestamp + ' — ' + c.tracks + ' tracked objects';
  document.getElementById('overlay').classList.add('active');
  player.play();
  // Request fullscreen on the video element
  if (player.requestFullscreen) player.requestFullscreen();
  else if (player.webkitRequestFullscreen) player.webkitRequestFullscreen();
}}

function closeVideo() {{
  const player = document.getElementById('player');
  player.pause();
  player.src = '';
  document.getElementById('overlay').classList.remove('active');
  if (document.exitFullscreen) document.exitFullscreen();
  else if (document.webkitExitFullscreen) document.webkitExitFullscreen();
}}

document.addEventListener('keydown', e => {{
  if (e.key === 'Escape') closeVideo();
}});
</script>
</body></html>"""


def main():
    parser = argparse.ArgumentParser(description="Beehive activity dashboard for Portal")
    parser.add_argument("results_dir", type=Path)
    parser.add_argument("--preview", action="store_true", help="Open in browser")
    args = parser.parse_args()

    activity_file = args.results_dir / "top_activity.json"
    if not activity_file.exists():
        log.error("No top_activity.json — run activity analysis first")
        sys.exit(1)

    clips_dir = args.results_dir.parent / "clips"
    html = generate_dashboard(activity_file, clips_dir)

    out = args.results_dir / "activity_dashboard.html"
    out.write_text(html)
    log.info("Wrote %s", out)

    if args.preview:
        import webbrowser
        webbrowser.open(str(out))
    else:
        if shutil.which("adb"):
            subprocess.run(["adb", "shell", "mkdir", "-p", "/sdcard/beehive"])
            subprocess.run(["adb", "push", str(out), "/sdcard/beehive/activity.html"])
            subprocess.run(["adb", "shell", "am", "start", "-a", "android.intent.action.VIEW",
                          "-d", "file:///sdcard/beehive/activity.html"])
            log.info("Pushed to Portal!")
        else:
            log.info("No adb found — open %s in a browser", out)


if __name__ == "__main__":
    import argparse
    main()
