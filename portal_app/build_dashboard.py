#!/usr/bin/env python3
"""Build the Beehive Portal dashboard — one self-contained HTML page.

Layout (top to bottom), per the Portal app spec:
  1. THREATS      — clips the Llama vision model flagged as non-bee / a threat,
                    each with a red-boxed still + the model's description.
                    Shows a clean "all clear" state when nothing is flagged.
  2. ACTIVITY     — the top-N% most active clips (ranked by motion), as a grid
                    of thumbnails with red bounding boxes + activity metrics
                    + the vision description.
  3. PLAYBACK     — tap any clip to play it fullscreen; or start AMBIENT mode,
                    which auto-advances through the clips fullscreen on a muted
                    loop (a "background video" view). Touch + swipe friendly.

Red boxes come from the project's own Level-1 motion detector
(beehive_monitor) so they match the rest of the pipeline. Vision text is merged
from results/vision_progress.jsonl (preferred) with a fallback to digest.json.

Everything (stills + videos) is embedded as base64 so the page is a single file
that works offline inside the Portal WebView — no server, no file:// fetches.

Usage:
    python portal_app/build_dashboard.py \
        --results results --clips clips \
        --out results/portal_dashboard.html \
        --asset-out portal_app/app/src/main/assets/dashboard/index.html
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import sys
from datetime import datetime
from html import escape
from pathlib import Path

import cv2
import numpy as np

# Reuse the project's detector + config so metrics/boxes are consistent.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from beehive_monitor.config import load_config  # noqa: E402
from beehive_monitor.level1 import _extract_blobs  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("build_dashboard")

STILL_WIDTH = 720          # width of the boxed still / thumbnail (px)
RED = (0, 0, 255)          # BGR — matches level1.extract_context_frame
THREAT_RANK = {"high": 3, "medium": 2, "low": 1, "none": 0, "unknown": 0, "": 0}


# ── frame helpers ───────────────────────────────────────────────────────

def _b64_jpg(frame: np.ndarray, quality: int = 80) -> str:
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return base64.b64encode(buf.tobytes()).decode("ascii") if ok else ""


def _resize_w(frame: np.ndarray, width: int) -> tuple[np.ndarray, float]:
    h, w = frame.shape[:2]
    if w <= width:
        return frame, 1.0
    scale = width / w
    return cv2.resize(frame, (width, int(h * scale))), scale


def boxed_still(clip_path: Path, cfg) -> tuple[str, int]:
    """Find the peak-activity frame and draw red boxes on every detected blob.

    Returns (base64 jpg, detection_count). Single-pass; keeps only one frame in
    memory. Mirrors the Level-1 loop (MOG2 + morphology + warmup skip)."""
    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        return "", 0
    bg = cv2.createBackgroundSubtractorMOG2(
        history=cfg.background.history, varThreshold=cfg.background.var_threshold,
        detectShadows=False)
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (cfg.blob.morph_kernel, cfg.blob.morph_kernel))

    best_count, best_frame, best_bboxes = -1, None, []
    idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        fg = bg.apply(frame)
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, kernel)
        fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, kernel)
        _, fg = cv2.threshold(fg, 200, 255, cv2.THRESH_BINARY)
        if idx < cfg.background.warmup_frames:
            idx += 1
            continue
        blobs = _extract_blobs(frame, fg, idx, cfg.blob.min_area)
        if len(blobs) > best_count:
            best_count = len(blobs)
            best_frame = frame.copy()
            best_bboxes = [b.bbox for b in blobs]
        idx += 1
    cap.release()

    if best_frame is None:
        return "", 0

    frame, scale = _resize_w(best_frame, STILL_WIDTH)
    for (x, y, w, h) in best_bboxes:
        x, y, w, h = int(x * scale), int(y * scale), int(w * scale), int(h * scale)
        cv2.rectangle(frame, (x, y), (x + w, y + h), RED, 2)
    label = f"{best_count} detected"
    cv2.putText(frame, label, (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4)
    cv2.putText(frame, label, (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 1)
    return _b64_jpg(frame), best_count


def clean_thumb(clip_path: Path) -> str:
    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        return ""
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    cap.set(cv2.CAP_PROP_POS_FRAMES, total // 2)
    ret, frame = cap.read()
    cap.release()
    if not ret:
        return ""
    frame, _ = _resize_w(frame, STILL_WIDTH)
    return _b64_jpg(frame)


def video_b64(clip_path: Path) -> str:
    try:
        return base64.b64encode(clip_path.read_bytes()).decode("ascii")
    except OSError:
        return ""


def pretty_ts(name: str) -> str:
    """florida-bees-2026-06-16t20-47-48-00-00.mp4 -> 2026-06-16 20:47:48"""
    s = name.replace("florida-bees-", "").replace(".mp4", "")
    s = s.replace("-00-00", "")
    if "t" in s:
        date, _, tm = s.partition("t")
        return f"{date} {tm.replace('-', ':')}"
    return name


# ── vision data ─────────────────────────────────────────────────────────

def load_vision(results_dir: Path) -> dict[str, dict]:
    """clip name -> merged vision record. digest.json first, then the richer/
    newer vision_progress.jsonl overlaid on top."""
    data: dict[str, dict] = {}
    digest = results_dir / "digest.json"
    if digest.exists():
        for c in json.loads(digest.read_text()).get("all_clips", []):
            data[c["clip"]] = {
                "description": c.get("description", "") or "",
                "animals": [], "confidence": "", "threat_level": "none",
                "has_non_bee": bool(c.get("has_non_bee_content", False)),
                "error": c.get("error", "") or "",
            }
    vp = results_dir / "vision_progress.jsonl"
    if vp.exists():
        for line in vp.read_text().strip().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            clip = r.get("clip")
            if not clip:
                continue
            cur = data.get(clip, {})
            data[clip] = {
                "description": (r.get("description") or cur.get("description", "")),
                "animals": r.get("animals_seen") or cur.get("animals", []),
                "confidence": r.get("confidence") or cur.get("confidence", ""),
                "threat_level": (r.get("threat_level") or "none"),
                "has_non_bee": bool(r.get("has_non_bee_content",
                                          cur.get("has_non_bee", False))),
                "error": r.get("error", "") or "",
            }
    return data


def is_threat(v: dict) -> bool:
    return bool(v) and (v.get("has_non_bee") or
                        THREAT_RANK.get(str(v.get("threat_level", "none")).lower(), 0) > 0)


# ── card assembly ───────────────────────────────────────────────────────

def build_card(clip_name: str, clips_dir: Path, cfg, vision: dict,
               metrics: dict | None) -> dict | None:
    clip_path = clips_dir / clip_name
    if not clip_path.exists():
        log.warning("missing clip on disk: %s", clip_name)
        return None
    log.info("rendering %s", clip_name)
    boxed, detected = boxed_still(clip_path, cfg)
    v = vision.get(clip_name, {})
    desc = (v.get("description") or "").strip()
    return {
        "clip": clip_name,
        "timestamp": pretty_ts(clip_name),
        "tracks": (metrics or {}).get("tracks"),
        "max_blobs": (metrics or {}).get("blob_count_max"),
        "detected": detected,
        "thumb": clean_thumb(clip_path),
        "boxed": boxed,
        "video": video_b64(clip_path),
        "description": desc,
        "animals": v.get("animals") or [],
        "threat_level": str(v.get("threat_level", "none")).lower(),
        "confidence": v.get("confidence", ""),
        "is_threat": is_threat(v),
    }


def generate(results_dir: Path, clips_dir: Path, cfg, max_threats: int = 12) -> str:
    activity_file = results_dir / "top_activity.json"
    if not activity_file.exists():
        log.error("No %s — run rank_activity.py first.", activity_file)
        sys.exit(1)
    act = json.loads(activity_file.read_text())
    top_clips = act.get("top_clips", [])
    total = act.get("total_analyzed", 0)
    pct = act.get("percentile", 10)

    vision = load_vision(results_dir)

    # Threat clips: vision-flagged, present on disk, sorted by severity.
    threat_names = sorted(
        [c for c, v in vision.items()
         if is_threat(v) and not v.get("error") and (clips_dir / c).exists()],
        key=lambda c: (THREAT_RANK.get(vision[c]["threat_level"], 0), c),
        reverse=True,
    )[:max_threats]

    threats, activity = [], []
    for name in threat_names:
        card = build_card(name, clips_dir, cfg, vision, None)
        if card:
            threats.append(card)
    for c in top_clips:
        card = build_card(c["clip"], clips_dir, cfg, vision, c)
        if card:
            activity.append(card)

    payload = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total": total,
        "percentile": pct,
        "threats": threats,
        "activity": activity,
    }
    html = _TEMPLATE.replace("/*__DATA__*/null", json.dumps(payload))
    log.info("threats=%d  activity=%d  (top %g%% of %d)",
             len(threats), len(activity), pct, total)
    return html


# ── HTML/CSS/JS template (placeholder __DATA__ filled above) ─────────────
# Plain template string (not an f-string) so JS/CSS braces stay literal.
_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no, viewport-fit=cover">
<title>Beehive Monitor</title>
<style>
  :root { --bg:#0f0d0a; --panel:#1b1813; --panel2:#241f17; --gold:#ffce3a;
          --amber:#ff8f1f; --red:#ff4438; --text:#f3ead6; --muted:#a99e86; }
  * { margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  html,body { height:100%; }
  body { background:var(--bg); color:var(--text);
         font-family:system-ui,-apple-system,"Segoe UI",sans-serif; overflow-x:hidden; }
  .wrap { max-width:1600px; margin:0 auto; padding-bottom:40px; }

  header { position:sticky; top:0; z-index:20; display:flex; align-items:center;
           justify-content:space-between; gap:12px; padding:16px 22px;
           background:linear-gradient(135deg,#2a1d05,#140f06); border-bottom:1px solid #3a2f17; }
  header h1 { font-size:1.5rem; color:var(--gold); letter-spacing:.3px; }
  header .meta { font-size:.85rem; color:var(--muted); text-align:right; }
  .ambient-btn { background:linear-gradient(135deg,var(--amber),var(--gold)); color:#1a1206;
                 border:none; border-radius:999px; padding:12px 22px; font-size:1rem;
                 font-weight:700; cursor:pointer; white-space:nowrap; }
  .ambient-btn:active { transform:scale(.96); }
  .exit-btn { background:transparent; color:var(--muted); border:1px solid #3a2f17;
              border-radius:999px; padding:12px 18px; font-size:.95rem; font-weight:600;
              cursor:pointer; white-space:nowrap; }
  .exit-btn:active { transform:scale(.96); background:#1f1a12; }
  .refresh-btn { background:transparent; color:var(--gold); border:1px solid #4a3a1a;
                 border-radius:999px; padding:12px 18px; font-size:.95rem; font-weight:600;
                 cursor:pointer; white-space:nowrap; }
  .refresh-btn:active { transform:scale(.96); background:#1f1a12; }
  .refresh-btn.spinning { opacity:0.6; pointer-events:none; }

  section { padding:22px 22px 8px; }
  .sec-title { display:flex; align-items:center; gap:10px; font-size:1.15rem;
               margin-bottom:14px; color:var(--text); }
  .sec-title .pill { font-size:.72rem; font-weight:700; padding:3px 10px; border-radius:999px;
                     background:var(--panel2); color:var(--muted); }
  .sec-title.threats .dot { width:11px; height:11px; border-radius:50%; background:var(--red); }
  .sec-title.activity .dot { width:11px; height:11px; border-radius:50%; background:var(--gold); }

  .allclear { background:var(--panel); border:1px solid #2b3a26; border-radius:14px;
              padding:26px; display:flex; align-items:center; gap:16px; }
  .allclear .check { font-size:2rem; color:#5fd36a; }
  .allclear .sub { color:var(--muted); font-size:.9rem; margin-top:3px; }

  .grid { display:grid; gap:14px;
          grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); }
  .card { background:var(--panel); border:1px solid #322a1c; border-radius:14px;
          overflow:hidden; cursor:pointer; transition:transform .12s,box-shadow .12s; }
  .card:active { transform:scale(.98); }
  .card:hover { box-shadow:0 6px 24px rgba(255,206,58,.14); }
  .card.threat { border-color:#6e2b25; }
  .thumb-wrap { position:relative; aspect-ratio:16/9; background:#000; }
  .thumb-wrap img { width:100%; height:100%; object-fit:cover; display:block; }
  .play-badge { position:absolute; inset:0; display:flex; align-items:center; justify-content:center;
                font-size:2.6rem; color:#fff; text-shadow:0 2px 10px rgba(0,0,0,.7); opacity:.85; pointer-events:none; }
  .tag { position:absolute; top:8px; left:8px; font-size:.7rem; font-weight:700;
         padding:3px 8px; border-radius:6px; background:rgba(0,0,0,.6); color:#fff; }
  .tag.boxed { background:rgba(255,68,56,.85); }
  .badge-threat { position:absolute; top:8px; right:8px; font-size:.7rem; font-weight:800;
                  padding:3px 9px; border-radius:6px; text-transform:uppercase; }
  .lv-high{background:var(--red);color:#fff;} .lv-medium{background:var(--amber);color:#1a1206;}
  .lv-low{background:#caa64a;color:#1a1206;}
  .info { padding:11px 13px 13px; }
  .time { font-size:.82rem; color:var(--muted); }
  .desc { font-size:.9rem; line-height:1.35; margin-top:6px; color:var(--text);
          display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden; }
  .desc.empty { color:var(--muted); font-style:italic; }
  .metrics { display:flex; flex-wrap:wrap; gap:7px; margin-top:9px; }
  .metric { background:var(--panel2); color:var(--text); font-size:.76rem;
            padding:3px 9px; border-radius:7px; }
  .metric.hot { background:var(--red); color:#fff; } .metric.warm{ background:var(--amber); color:#1a1206; }
  .chip { font-size:.72rem; padding:2px 8px; border-radius:999px; background:#33291a; color:var(--gold); }

  /* Fullscreen / ambient overlay */
  .overlay { display:none; position:fixed; inset:0; background:#000; z-index:100;
             align-items:center; justify-content:center; }
  .overlay.active { display:flex; }
  .overlay video { width:100%; height:100%; object-fit:contain; background:#000; }
  .ov-caption { position:absolute; left:0; right:0; bottom:0; padding:18px 22px 26px;
                background:linear-gradient(transparent,rgba(0,0,0,.85)); transition:opacity .4s; }
  .ov-caption h3 { color:var(--gold); font-size:1.05rem; margin-bottom:4px; }
  .ov-caption p { color:#e8dcc4; font-size:.92rem; max-width:900px; }
  .ov-close { position:absolute; top:14px; right:18px; width:48px; height:48px; border-radius:50%;
              background:rgba(0,0,0,.55); color:#fff; font-size:1.6rem; border:none; cursor:pointer;
              display:flex; align-items:center; justify-content:center; }
  .ov-nav { position:absolute; top:50%; transform:translateY(-50%); width:56px; height:56px;
            border-radius:50%; background:rgba(0,0,0,.4); color:#fff; font-size:2rem; border:none;
            cursor:pointer; opacity:.7; } .ov-nav:active{opacity:1;}
  .ov-prev{left:14px;} .ov-next{right:14px;}
  .ov-dots { position:absolute; top:16px; left:50%; transform:translateX(-50%);
             font-size:.85rem; color:#cbbfa6; background:rgba(0,0,0,.4); padding:4px 12px; border-radius:999px; }
  .ambient .ov-close, .ambient .ov-nav { opacity:.25; }
  .hint { position:absolute; top:50%; left:50%; transform:translate(-50%,-50%); color:#fff;
          font-size:.95rem; opacity:.0; pointer-events:none; }
  .empty-note{color:var(--muted);font-size:.85rem;padding:6px 2px;}
</style></head>
<body>
<div class="wrap">
  <header>
    <div><h1>&#x1f41d; Beehive Monitor</h1></div>
    <div style="display:flex;align-items:center;gap:12px;">
      <div class="meta" id="meta"></div>
      <button class="refresh-btn" id="refreshBtn">&#x21bb; Refresh</button>
      <button class="exit-btn" id="exitBtn">&#x2715; Exit</button>
      <button class="ambient-btn" id="ambientBtn">&#x25b6; Ambient</button>
    </div>
  </header>

  <section id="threatsSec">
    <div class="sec-title threats"><span class="dot"></span> Threats
      <span class="pill" id="threatPill"></span></div>
    <div id="threats"></div>
  </section>

  <section id="activitySec">
    <div class="sec-title activity"><span class="dot"></span> Most active clips
      <span class="pill" id="activityPill"></span></div>
    <div class="grid" id="activity"></div>
  </section>
</div>

<div class="overlay" id="overlay">
  <video id="player" playsinline></video>
  <div class="ov-dots" id="ovDots"></div>
  <button class="ov-nav ov-prev" id="ovPrev">&#x2039;</button>
  <button class="ov-nav ov-next" id="ovNext">&#x203a;</button>
  <button class="ov-close" id="ovClose">&#x2715;</button>
  <div class="ov-caption" id="ovCaption"><h3></h3><p></p></div>
</div>

<script>
const DATA = /*__DATA__*/null;

const $ = s => document.querySelector(s);
function esc(t){ const d=document.createElement('div'); d.textContent=t==null?'':t; return d.innerHTML; }
function lvlClass(l){ return l==='high'?'lv-high':l==='medium'?'lv-medium':l==='low'?'lv-low':''; }

// ---- render ----
let lastRefresh = '';
try { if (window.Beehive && Beehive.lastRefreshTime) lastRefresh = Beehive.lastRefreshTime(); } catch(e){}
let metaHtml = 'Top ' + DATA.percentile + '% of ' + DATA.total + ' analyzed clips<br>Updated ' + esc(DATA.generated);
if (lastRefresh) { metaHtml += '<br>Auto refresh daily at 7 AM &bull; last check ' + esc(lastRefresh); }
else { metaHtml += '<br>Auto refresh daily at 7 AM'; }
$('#meta').innerHTML = metaHtml;
$('#threatPill').textContent = DATA.threats.length + ' flagged';
$('#activityPill').textContent = DATA.activity.length + ' clips';

function cardHTML(c, idx, kind){
  const img = c.boxed ? c.boxed : c.thumb;
  const lvl = c.threat_level && c.threat_level!=='none'
      ? '<span class="badge-threat '+lvlClass(c.threat_level)+'">'+esc(c.threat_level)+'</span>' : '';
  let metrics = '';
  if (c.tracks!=null){
    const cls = c.tracks>150?'hot':c.tracks>80?'warm':'';
    metrics += '<span class="metric '+cls+'">'+c.tracks+' tracks</span>';
  }
  if (c.max_blobs!=null) metrics += '<span class="metric">'+c.max_blobs+' peak</span>';
  if (c.detected) metrics += '<span class="metric">'+c.detected+' boxed</span>';
  (c.animals||[]).slice(0,3).forEach(a=> metrics += '<span class="chip">'+esc(a)+'</span>');
  const desc = c.description
      ? '<div class="desc">'+esc(c.description)+'</div>'
      : '<div class="desc empty">No vision description yet.</div>';
  return '<div class="card '+(kind==='threat'?'threat':'')+'" data-kind="'+kind+'" data-idx="'+idx+'">'
    + '<div class="thumb-wrap"><img loading="lazy" src="data:image/jpeg;base64,'+img+'">'
    + (c.boxed?'<span class="tag boxed">red boxes = detected</span>':'')
    + lvl + '<div class="play-badge">&#x25b6;</div></div>'
    + '<div class="info"><div class="time">'+esc(c.timestamp)+'</div>'
    + desc + '<div class="metrics">'+metrics+'</div></div></div>';
}

const threatsEl = $('#threats');
if (DATA.threats.length === 0){
  threatsEl.innerHTML = '<div class="allclear"><div class="check">&#x2713;</div>'
    + '<div><div><b>All clear.</b></div><div class="sub">No non-bee activity flagged across '
    + DATA.total + ' analyzed clips. Threats appear here when the vision model spots a wasp, hornet, or predator.</div></div></div>';
} else {
  threatsEl.className = 'grid';
  threatsEl.innerHTML = DATA.threats.map((c,i)=>cardHTML(c,i,'threat')).join('');
}
$('#activity').innerHTML = DATA.activity.length
  ? DATA.activity.map((c,i)=>cardHTML(c,i,'activity')).join('')
  : '<div class="empty-note">No ranked clips yet — run rank_activity.py.</div>';

// ---- playback / ambient ----
const overlay=$('#overlay'), player=$('#player'),
      capEl=$('#ovCaption'), dots=$('#ovDots');
let playlist=[], pos=0, ambient=false, hideTimer=null;

function buildPlaylist(kind, idx){
  // single clip -> that clip then continue through its section; ambient -> activity (fallback threats)
  const src = kind==='threat' ? DATA.threats : DATA.activity;
  playlist = src.slice();
  pos = Math.max(0, idx||0);
}
function show(i){
  pos = (i + playlist.length) % playlist.length;
  const c = playlist[pos];
  player.src = 'data:video/mp4;base64,' + c.video;
  player.muted = ambient;
  player.loop = !ambient;          // ambient advances on 'ended'; single loops
  player.controls = !ambient;
  capEl.querySelector('h3').textContent = c.timestamp + (c.tracks!=null? '  •  '+c.tracks+' tracks':'');
  capEl.querySelector('p').textContent = c.description || (c.animals||[]).join(', ') || '';
  dots.textContent = (pos+1) + ' / ' + playlist.length + (ambient?'  •  ambient':'');
  player.play().catch(()=>{});
}
function openOverlay(kind, idx, isAmbient){
  if (!DATA.activity.length && !DATA.threats.length) return;
  ambient = !!isAmbient;
  overlay.classList.toggle('ambient', ambient);
  if (ambient && !DATA.activity.length) kind='threat';
  buildPlaylist(kind, idx);
  overlay.classList.add('active');
  if (overlay.requestFullscreen) overlay.requestFullscreen().catch(()=>{});
  show(pos);
  scheduleHide();
  try { if(window.Beehive && Beehive.setKeepScreenOn) Beehive.setKeepScreenOn(ambient); } catch(e){}
}
function closeOverlay(){
  player.pause(); player.removeAttribute('src'); player.load();
  overlay.classList.remove('active'); ambient=false;
  if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen().catch(()=>{});
  try { if(window.Beehive && Beehive.setKeepScreenOn) Beehive.setKeepScreenOn(false); } catch(e){}
}
function scheduleHide(){
  capEl.style.opacity='1'; clearTimeout(hideTimer);
  hideTimer=setTimeout(()=>{ capEl.style.opacity='0'; }, 3500);
}

document.addEventListener('click', e=>{
  const card = e.target.closest('.card');
  if (card) openOverlay(card.dataset.kind, +card.dataset.idx, false);
});
$('#ambientBtn').addEventListener('click', ()=> openOverlay('activity', 0, true));
$('#exitBtn').addEventListener('click', ()=>{
  try { if (window.Beehive && Beehive.exit) Beehive.exit(); else history.back(); }
  catch(e){ window.close(); }
});
$('#refreshBtn').addEventListener('click', ()=>{
  const btn=$('#refreshBtn'); btn.classList.add('spinning'); btn.textContent='↻ Refreshing...';
  try {
    if (window.Beehive && Beehive.refresh) {
      Beehive.refresh();
      // reload page after short delay to pick up new dashboard pushed via adb
      setTimeout(()=>{ location.reload(); }, 1200);
    } else {
      location.reload();
    }
  } catch(e){ location.reload(); }
});
$('#ovClose').addEventListener('click', closeOverlay);
$('#ovPrev').addEventListener('click', ()=>{ show(pos-1); scheduleHide(); });
$('#ovNext').addEventListener('click', ()=>{ show(pos+1); scheduleHide(); });
player.addEventListener('ended', ()=>{ if(ambient) show(pos+1); });
overlay.addEventListener('mousemove', scheduleHide);

// touch: swipe L/R = prev/next, swipe down = close, tap = toggle caption
let tx=0, ty=0, tt=0;
overlay.addEventListener('touchstart', e=>{ const t=e.changedTouches[0]; tx=t.clientX; ty=t.clientY; tt=Date.now(); }, {passive:true});
overlay.addEventListener('touchend', e=>{
  const t=e.changedTouches[0], dx=t.clientX-tx, dy=t.clientY-ty, dt=Date.now()-tt;
  if (Math.abs(dx)>60 && Math.abs(dx)>Math.abs(dy)){ show(pos + (dx<0?1:-1)); scheduleHide(); }
  else if (dy>90 && Math.abs(dy)>Math.abs(dx)){ closeOverlay(); }
  else if (dt<250){ scheduleHide(); }
}, {passive:true});

document.addEventListener('keydown', e=>{
  if (!overlay.classList.contains('active')) return;
  if (e.key==='Escape') closeOverlay();
  else if (e.key==='ArrowRight') { show(pos+1); scheduleHide(); }
  else if (e.key==='ArrowLeft') { show(pos-1); scheduleHide(); }
});

// Launch straight into ambient mode if opened with #ambient (handy for the app).
if (location.hash === '#ambient') window.addEventListener('load', ()=> openOverlay('activity',0,true));
</script>
</body></html>"""


def main() -> None:
    p = argparse.ArgumentParser(description="Build the Beehive Portal dashboard HTML.")
    p.add_argument("--results", type=Path, default=Path("results"))
    p.add_argument("--clips", type=Path, default=Path("clips"))
    p.add_argument("--config", type=Path, default=Path("config.yaml"))
    p.add_argument("--out", type=Path, default=Path("results/portal_dashboard.html"))
    p.add_argument("--asset-out", type=Path, default=None,
                   help="Also write a copy here (e.g. the Android app's assets).")
    args = p.parse_args()

    cfg = load_config(args.config)
    html = generate(args.results, args.clips, cfg)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html)
    size_mb = len(html.encode()) / 1024 / 1024
    log.info("Wrote %s (%.1f MB)", args.out, size_mb)
    if args.asset_out:
        args.asset_out.parent.mkdir(parents=True, exist_ok=True)
        args.asset_out.write_text(html)
        log.info("Wrote %s", args.asset_out)


if __name__ == "__main__":
    main()
