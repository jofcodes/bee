#!/usr/bin/env python3
"""Generate a Portal dashboard for the beehive monitor.

Main view: grid of threat clips and most-active clips, with refresh button.
Ambient mode: fullscreen autoplay of the most interesting clips back-to-back.

Usage:
    python portal_activity.py results/ --preview    # open in browser
    python portal_activity.py results/              # push to Portal via ADB
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import shutil
import subprocess
import sys
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
    return _frame_to_b64(frame) if ret else None


def generate_dashboard(activity_file: Path, clips_dir: Path, digest_file: Path | None = None) -> str:
    data = json.loads(activity_file.read_text())
    top_clips = data["top_clips"]
    total = data["total_analyzed"]

    threats = []
    if digest_file and digest_file.exists():
        digest = json.loads(digest_file.read_text())
        threats = digest.get("events", [])

    cards = []
    for clip_info in top_clips:
        clip_path = clips_dir / clip_info["clip"]
        thumb_b64 = _extract_thumbnail(clip_path) if clip_path.exists() else None
        name = clip_info["clip"]
        ts = name.replace("florida-bees-", "").replace("-00-00.mp4", "").replace("t", " ").replace("-", ":")
        parts = ts.split(" ")
        if len(parts) >= 2:
            ts = f"{parts[0].replace(':', '-', 2)} {parts[1]}"
        cards.append({
            "clip": name, "timestamp": ts,
            "tracks": clip_info["tracks"], "max_blobs": clip_info["blob_count_max"],
            "thumb": thumb_b64 or "",
        })

    threat_cards = []
    for t in threats:
        clip_path = clips_dir / t.get("clip", "")
        thumb_b64 = _extract_thumbnail(clip_path) if clip_path.exists() else None
        threat_cards.append({
            "clip": t.get("clip", ""), "timestamp": t.get("timestamp", "")[:19],
            "description": t.get("description", ""),
            "animals": t.get("animals_seen", []), "thumb": thumb_b64 or "",
        })

    ambient_clips = [t.get("clip", "") for t in threats] + [c["clip"] for c in cards]
    num_threats = len(threat_cards)

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
<title>Beehive Monitor</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#111; color:#f0e6d3; font-family:system-ui,sans-serif; overflow-x:hidden; }}
.header {{ background:linear-gradient(135deg,#2d1f00,#4a3520); padding:14px 20px; display:flex; align-items:center; justify-content:space-between; position:sticky; top:0; z-index:10; }}
.header h1 {{ font-size:1.3em; color:#ffd700; }}
.header .right {{ display:flex; gap:10px; align-items:center; }}
.btn {{ border:none; padding:6px 14px; border-radius:6px; font-weight:700; font-size:0.85em; cursor:pointer; }}
.btn-gold {{ background:#ffd700; color:#2d1f00; }}
.btn-ambient {{ background:#1a5276; color:#aed6f1; }}
.btn:active {{ opacity:0.7; }}
.section-title {{ padding:14px 20px 6px; font-size:1em; color:#ffd700; font-weight:700; }}
.section-title .count {{ background:#d32f2f; color:#fff; border-radius:10px; padding:1px 8px; font-size:0.75em; margin-left:6px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(260px,1fr)); gap:12px; padding:8px 12px 20px; }}
.card {{ background:#1e1e1e; border-radius:10px; overflow:hidden; cursor:pointer; transition:transform .15s; border:1px solid #333; }}
.card:hover {{ transform:scale(1.02); box-shadow:0 4px 20px rgba(255,215,0,.15); }}
.card.threat {{ border-color:#d32f2f; }}
.card img {{ width:100%; aspect-ratio:16/9; object-fit:cover; display:block; }}
.card .info {{ padding:10px 12px; }}
.card .time {{ font-size:.8em; opacity:.5; }}
.card .desc {{ font-size:.85em; margin-top:3px; }}
.metrics {{ display:flex; gap:8px; margin-top:5px; }}
.metric {{ background:#2a2a2a; padding:2px 8px; border-radius:6px; font-size:.75em; }}
.metric.hot {{ background:#d32f2f; color:#fff; }}
.metric.warm {{ background:#ff6d00; color:#fff; }}
.empty {{ padding:30px; text-align:center; color:#555; }}
.video-overlay {{ display:none; position:fixed; top:0; left:0; width:100vw; height:100vh; background:#000; z-index:100; align-items:center; justify-content:center; flex-direction:column; }}
.video-overlay.active {{ display:flex; }}
.video-overlay video {{ max-width:100%; max-height:90vh; }}
.video-overlay .close {{ position:absolute; top:12px; right:16px; font-size:1.8em; color:#fff; cursor:pointer; background:rgba(0,0,0,.5); border-radius:50%; width:40px; height:40px; display:flex; align-items:center; justify-content:center; }}
.ambient {{ display:none; position:fixed; top:0; left:0; width:100vw; height:100vh; background:#000; z-index:200; cursor:none; }}
.ambient.active {{ display:block; }}
.ambient video {{ width:100vw; height:100vh; object-fit:contain; }}
.ambient .info {{ position:fixed; bottom:0; left:0; right:0; background:linear-gradient(transparent,rgba(0,0,0,.85)); padding:40px 24px 16px; opacity:0; transition:opacity .3s; }}
.ambient:hover .info {{ opacity:1; }}
.ambient .info .title {{ font-size:1.2em; font-weight:700; }}
.ambient .info .meta {{ font-size:.85em; opacity:.6; margin-top:2px; }}
.ambient .exit {{ position:fixed; top:12px; right:16px; font-size:1.5em; color:rgba(255,255,255,.3); cursor:pointer; z-index:210; opacity:0; transition:opacity .3s; }}
.ambient:hover .exit {{ opacity:1; }}
.ambient .bar {{ position:fixed; bottom:0; left:0; height:3px; background:#ffd700; z-index:210; transition:width .5s; }}
.toast {{ position:fixed; top:60px; left:50%; transform:translateX(-50%); background:rgba(0,0,0,.85); color:#ffd700; padding:8px 20px; border-radius:20px; font-size:.85em; display:none; z-index:300; }}
.toast.visible {{ display:block; }}
</style></head><body>

<div class="header">
  <h1>&#x1f41d; Beehive Monitor</h1>
  <div class="right">
    <button class="btn btn-ambient" onclick="startAmbient()">&#x25b6; Ambient</button>
    <button class="btn btn-gold" id="refreshBtn" onclick="triggerRefresh()">&#x21bb; Refresh</button>
  </div>
</div>

{'<div class="section-title">&#x26a0;&#xfe0f; Threats <span class="count">' + str(num_threats) + '</span></div><div class="grid" id="threatGrid"></div>' if num_threats > 0 else ''}
<div class="section-title">&#x1f41d; Most Active (top 5% of {total} clips)</div>
<div class="grid" id="activityGrid"></div>

<div class="video-overlay" id="videoOverlay">
  <div class="close" onclick="closeVideo()">&#x2715;</div>
  <video id="singlePlayer" controls autoplay></video>
</div>

<div class="ambient" id="ambient">
  <video id="ambientPlayer" autoplay muted playsinline></video>
  <div class="info"><div class="title" id="ambTitle"></div><div class="meta" id="ambMeta"></div></div>
  <div class="exit" onclick="stopAmbient()">&#x2715; Exit</div>
  <div class="bar" id="ambBar"></div>
</div>

<div class="toast" id="toast"></div>

<script>
const S = window.location.origin;
const cards = {json.dumps(cards)};
const threats = {json.dumps(threat_cards)};
const ambList = {json.dumps(ambient_clips)};

// Render activity grid
const ag = document.getElementById('activityGrid');
cards.forEach(c => {{
  const lv = c.tracks > 150 ? 'hot' : c.tracks > 80 ? 'warm' : '';
  ag.innerHTML += '<div class="card" onclick="playSingle(\\'' + c.clip + '\\')"><img src="data:image/jpeg;base64,' + c.thumb + '"><div class="info"><div class="time">' + c.timestamp + '</div><div class="metrics"><span class="metric ' + lv + '">' + c.tracks + ' tracks</span><span class="metric">' + c.max_blobs + ' peak</span></div></div></div>';
}});

// Render threat grid
const tg = document.getElementById('threatGrid');
if (tg) threats.forEach(c => {{
  const an = (c.animals||[]).map(a => '<span class="metric hot">' + a + '</span>').join('');
  tg.innerHTML += '<div class="card threat" onclick="playSingle(\\'' + c.clip + '\\')"><img src="data:image/jpeg;base64,' + c.thumb + '"><div class="info"><div class="time">' + c.timestamp + '</div><div class="desc">' + (c.description||'') + '</div><div class="metrics">' + an + '</div></div></div>';
}});

// Single clip playback
function playSingle(name) {{
  const p = document.getElementById('singlePlayer');
  p.src = S + '/clips/' + name;
  document.getElementById('videoOverlay').classList.add('active');
  p.play();
}}
function closeVideo() {{
  const p = document.getElementById('singlePlayer');
  p.pause(); p.src = '';
  document.getElementById('videoOverlay').classList.remove('active');
}}

// Ambient mode
let ai = 0;
function startAmbient() {{
  ai = 0;
  document.getElementById('ambient').classList.add('active');
  // Request fullscreen on the ambient overlay
  const el = document.getElementById('ambient');
  if (el.requestFullscreen) el.requestFullscreen();
  else if (el.webkitRequestFullscreen) el.webkitRequestFullscreen();
  playAmb(0);
}}
function stopAmbient() {{
  document.getElementById('ambient').classList.remove('active');
  const p = document.getElementById('ambientPlayer');
  p.pause(); p.src = '';
  if (document.exitFullscreen) document.exitFullscreen();
  else if (document.webkitExitFullscreen) document.webkitExitFullscreen();
}}
function playAmb(i) {{
  if (!ambList.length) return;
  ai = ((i % ambList.length) + ambList.length) % ambList.length;
  const name = ambList[ai];
  const p = document.getElementById('ambientPlayer');
  p.src = S + '/clips/' + name;
  p.play().catch(() => {{}});
  document.getElementById('ambTitle').textContent = name.replace('florida-bees-','').replace('.mp4','').replace(/-/g,':').replace('t',' ');
  document.getElementById('ambMeta').textContent = (ai+1) + ' / ' + ambList.length;
  document.getElementById('ambBar').style.width = ((ai+1)/ambList.length*100) + '%';
}}
document.getElementById('ambientPlayer').addEventListener('ended', () => playAmb(ai+1));
let ambTX = 0;
document.getElementById('ambient').addEventListener('touchstart', e => {{ ambTX = e.touches[0].clientX; }});
document.getElementById('ambient').addEventListener('touchend', e => {{
  const dx = e.changedTouches[0].clientX - ambTX;
  if (Math.abs(dx) > 50) playAmb(dx > 0 ? ai-1 : ai+1);
}});

// Refresh
function triggerRefresh() {{
  const t = document.getElementById('toast');
  t.textContent = 'Refreshing...'; t.classList.add('visible');
  fetch(S+'/refresh',{{method:'POST'}}).then(r=>r.json()).then(()=>pollStatus()).catch(()=>{{
    t.textContent='Server unreachable'; setTimeout(()=>t.classList.remove('visible'),3000);
  }});
}}
function pollStatus() {{
  const t = document.getElementById('toast');
  fetch(S+'/status').then(r=>r.json()).then(d=>{{
    if(d.running){{ t.textContent='Analyzing '+d.clips_analyzed+' clips...'; setTimeout(pollStatus,5000); }}
    else{{ t.textContent=d.last_result==='success'?'Done — reloading...':'Refresh complete';
      setTimeout(()=>{{ t.classList.remove('visible'); if(d.last_result==='success') location.reload(); }},2000);
    }}
  }}).catch(()=>setTimeout(pollStatus,5000));
}}

document.addEventListener('keydown', e => {{ if(e.key==='Escape'){{ closeVideo(); stopAmbient(); }} }});
</script></body></html>"""


def main():
    parser = argparse.ArgumentParser(description="Beehive activity dashboard for Portal")
    parser.add_argument("results_dir", type=Path)
    parser.add_argument("--preview", action="store_true", help="Open in browser instead of pushing to Portal")
    args = parser.parse_args()

    activity_file = args.results_dir / "top_activity.json"
    if not activity_file.exists():
        log.error("No top_activity.json in %s — run activity analysis first", args.results_dir)
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
        adb = "/usr/local/platform-tools/adb"
        if shutil.which("adb") or Path(adb).exists():
            adb_cmd = adb if Path(adb).exists() else "adb"
            subprocess.run([adb_cmd, "push", str(out), "/sdcard/beehive/activity.html"])
            subprocess.run([adb_cmd, "shell", "am", "start", "-a", "android.intent.action.VIEW",
                          "-d", "file:///sdcard/beehive/activity.html"])
            log.info("Pushed to Portal!")
        else:
            log.info("No adb found — open %s in a browser", out)


if __name__ == "__main__":
    main()
