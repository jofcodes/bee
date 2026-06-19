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


def generate_dashboard(activity_file: Path, clips_dir: Path, digest_file: Path | None = None) -> str:
    data = json.loads(activity_file.read_text())
    top_clips = data["top_clips"]
    total = data["total_analyzed"]

    # Load threat data if available
    threats = []
    if digest_file and digest_file.exists():
        digest = json.loads(digest_file.read_text())
        threats = digest.get("events", [])

    cards = []
    for clip_info in top_clips:
        clip_path = clips_dir / clip_info["clip"]
        thumb_b64 = _extract_thumbnail(clip_path) if clip_path.exists() else None
        video_b64 = _video_to_b64(clip_path) if clip_path.exists() else None

        name = clip_info["clip"]
        ts = name.replace("florida-bees-", "").replace("-00-00.mp4", "").replace("t", " ").replace("-", ":")
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

    # Build threat cards
    threat_cards = []
    for t in threats:
        clip_path = clips_dir / t.get("clip", "")
        thumb_b64 = _extract_thumbnail(clip_path) if clip_path.exists() else None
        video_b64 = _video_to_b64(clip_path) if clip_path.exists() else None
        ts = t.get("timestamp", "")[:19]
        threat_cards.append({
            "clip": t.get("clip", ""),
            "timestamp": ts,
            "description": t.get("description", ""),
            "animals": t.get("animals_seen", []),
            "threat_level": t.get("threat_level", "none"),
            "confidence": t.get("confidence", "unknown"),
            "thumb": thumb_b64 or "",
            "video": video_b64 or "",
        })

    cards_json = json.dumps(cards)
    threats_json = json.dumps(threat_cards)
    num_threats = len(threat_cards)

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
  .header .right {{ display: flex; align-items: center; gap: 12px; }}
  .header .stats {{ font-size: 0.85em; opacity: 0.6; }}
  .refresh-btn {{
    background: #ffd700; color: #2d1f00; border: none;
    padding: 6px 14px; border-radius: 6px; font-weight: 700;
    font-size: 0.85em; cursor: pointer;
  }}
  .refresh-btn:active {{ opacity: 0.7; }}
  .refresh-btn.spinning {{ animation: spin 1s linear infinite; opacity: 0.6; pointer-events: none; }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
  .status-bar {{
    background: #222; padding: 8px 20px; font-size: 0.8em; color: #888;
    display: none; border-bottom: 1px solid #333;
  }}
  .status-bar.visible {{ display: block; }}
  .tabs {{
    display: flex; background: #1a1a1a; border-bottom: 1px solid #333;
  }}
  .tab {{
    flex: 1; padding: 10px; text-align: center; cursor: pointer;
    font-size: 0.95em; color: #888; border-bottom: 3px solid transparent;
    transition: color 0.2s, border-color 0.2s;
  }}
  .tab.active {{ color: #ffd700; border-bottom-color: #ffd700; }}
  .tab .count {{
    display: inline-block; background: #d32f2f; color: #fff;
    border-radius: 10px; padding: 1px 7px; font-size: 0.75em;
    margin-left: 4px; vertical-align: middle;
  }}
  .tab-content {{ display: none; }}
  .tab-content.active {{ display: block; }}
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
  <div class="right">
    <div class="stats">Top 5% of {total} clips</div>
    <button class="refresh-btn" id="refreshBtn" onclick="triggerRefresh()">&#x21bb; Refresh</button>
  </div>
</div>
<div class="status-bar" id="statusBar"></div>

<div class="tabs">
  <div class="tab active" id="tabActivity" onclick="switchTab('activity')">&#x1f41d; Activity</div>
  <div class="tab" id="tabThreats" onclick="switchTab('threats')">&#x26a0;&#xfe0f; Threats{f' <span class="count">{num_threats}</span>' if num_threats > 0 else ''}</div>
</div>

<div class="tab-content active" id="contentActivity">
  <div class="grid" id="grid"></div>
</div>

<div class="tab-content" id="contentThreats">
  <div class="grid" id="threatGrid"></div>
</div>

<div class="overlay" id="overlay">
  <div class="close" onclick="closeVideo()">&#x2715;</div>
  <video id="player" controls autoplay loop></video>
  <div class="caption" id="caption"></div>
</div>

<script>
const cards = {cards_json};
const threatCards = {threats_json};

function switchTab(tab) {{
  document.getElementById('tabActivity').classList.toggle('active', tab === 'activity');
  document.getElementById('tabThreats').classList.toggle('active', tab === 'threats');
  document.getElementById('contentActivity').classList.toggle('active', tab === 'activity');
  document.getElementById('contentThreats').classList.toggle('active', tab === 'threats');
}}

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

// Render threat cards
const threatGrid = document.getElementById('threatGrid');
if (threatCards.length === 0) {{
  threatGrid.innerHTML = '<div style="padding:40px;text-align:center;color:#666;font-size:1.2em;grid-column:1/-1">&#x2705; No threats detected — all clips show normal honeybee activity</div>';
}} else {{
  threatCards.forEach((c, i) => {{
    const animals = (c.animals || []).map(a => '<span class="metric hot">' + a + '</span>').join('');
    threatGrid.innerHTML += `
      <div class="card" onclick="playThreatVideo(${{i}})">
        <img src="data:image/jpeg;base64,${{c.thumb}}" alt="${{c.clip}}">
        <div class="info">
          <div class="time">${{c.timestamp}}</div>
          <div style="margin:4px 0;font-size:0.9em">${{c.description || ''}}</div>
          <div class="metrics">
            ${{animals}}
            ${{c.threat_level && c.threat_level !== 'none' ? '<span class="metric warm">' + c.threat_level + '</span>' : ''}}
          </div>
        </div>
      </div>`;
  }});
}}

function playThreatVideo(i) {{
  const c = threatCards[i];
  const player = document.getElementById('player');
  player.src = 'data:video/mp4;base64,' + c.video;
  document.getElementById('caption').textContent =
    c.timestamp + ' — ' + (c.description || 'Threat detected');
  document.getElementById('overlay').classList.add('active');
  player.play();
  if (player.requestFullscreen) player.requestFullscreen();
  else if (player.webkitRequestFullscreen) player.webkitRequestFullscreen();
}}

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

// Refresh functionality
const SERVER = window.location.origin;
function triggerRefresh() {{
  const btn = document.getElementById('refreshBtn');
  const bar = document.getElementById('statusBar');
  btn.classList.add('spinning');
  btn.textContent = '⟳ Refreshing...';
  bar.classList.add('visible');
  bar.textContent = 'Downloading new clips and running analysis...';

  fetch(SERVER + '/refresh', {{method: 'POST'}})
    .then(r => r.json())
    .then(d => {{
      bar.textContent = 'Refresh started — this may take a few minutes...';
      pollStatus();
    }})
    .catch(e => {{
      bar.textContent = 'Could not reach server. Make sure server.py is running on your laptop.';
      btn.classList.remove('spinning');
      btn.textContent = '↻ Refresh';
    }});
}}

function pollStatus() {{
  const btn = document.getElementById('refreshBtn');
  const bar = document.getElementById('statusBar');
  fetch(SERVER + '/status')
    .then(r => r.json())
    .then(d => {{
      if (d.running) {{
        bar.textContent = `Analyzing... ${{d.clips_analyzed}} clips processed, ${{d.events_flagged}} flagged`;
        setTimeout(pollStatus, 5000);
      }} else {{
        btn.classList.remove('spinning');
        btn.textContent = '↻ Refresh';
        if (d.last_result === 'success') {{
          bar.textContent = `Last refresh: ${{d.last_refresh}} — ${{d.clips_analyzed}} clips, ${{d.events_flagged}} flagged. Reloading...`;
          setTimeout(() => window.location.reload(), 2000);
        }} else if (d.error) {{
          bar.textContent = 'Refresh failed: ' + d.error;
        }}
      }}
    }})
    .catch(() => setTimeout(pollStatus, 5000));
}}

show(0);
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
    digest_file = args.results_dir / "digest.json"
    html = generate_dashboard(activity_file, clips_dir, digest_file)

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
