#!/usr/bin/env python3
"""Deploy the most interesting beehive clips to a Meta Portal device.

Uses ADB to push a fullscreen slideshow dashboard to Portal.
Portal must have ADB enabled (Settings → Debug → ADB Enabled) and be
connected via USB-C. See the Portal hacking guide:
https://docs.google.com/document/d/1_ECxsB_qlhhxY4gGT8nAsUyqCF-Cs9sq1FeQJBnTc6o

Prerequisites:
    brew install --cask android-platform-tools   # installs adb
    # Connect Portal via USB-C, enable ADB, allow connection on Portal

Usage:
    # After running the beehive analysis:
    python portal_deploy.py results/              # push to Portal
    python portal_deploy.py results/ --top 10     # only top 10 events
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import shutil
import subprocess
import sys
from html import escape
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("portal")

PORTAL_PATH = "/sdcard/beehive"


def _check_adb() -> bool:
    """Verify adb is installed and a device is connected."""
    if not shutil.which("adb"):
        log.error(
            "adb not found. Install it:\n"
            "  brew install --cask android-platform-tools"
        )
        return False
    result = subprocess.run(
        ["adb", "devices"], capture_output=True, text=True
    )
    lines = [l for l in result.stdout.strip().split("\n")[1:] if l.strip()]
    if not lines:
        log.error(
            "No device connected. Connect your Portal via USB-C and "
            "enable ADB in Settings → Debug → ADB Enabled."
        )
        return False
    log.info("Found device: %s", lines[0].split("\t")[0])
    return True


def _adb(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["adb", *args], capture_output=True, text=True)


def _read_crop_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def generate_portal_html(
    digest: dict,
    crops_dir: Path,
    top_n: int | None = None,
) -> str:
    """Generate a fullscreen slideshow HTML page for Portal."""
    events = digest.get("events", [])
    if top_n:
        events = events[:top_n]

    # Build slides with embedded images
    slides_js: list[str] = []
    for i, event in enumerate(events):
        crop_files = event.get("crops", [])
        img_tag = ""
        if crop_files:
            crop_path = crops_dir / crop_files[0]
            if crop_path.exists():
                b64 = _read_crop_b64(crop_path)
                img_tag = f'<img src="data:image/jpeg;base64,{b64}" class="crop">'

        reasons_html = "".join(
            f'<span class="tag">{escape(r)}</span>' for r in event.get("reasons", [])
        )
        l2 = event.get("level2_response") or ""
        l2_html = f'<div class="l2">{escape(l2)}</div>' if l2 else ""
        confirmed = event.get("level2_confirmed")
        badge = ""
        if confirmed is True:
            badge = '<span class="badge alert">CONFIRMED</span>'
        elif confirmed is False:
            badge = '<span class="badge ok">Normal</span>'

        slide = {
            "clip": event.get("clip", ""),
            "time": event.get("timestamp", ""),
            "score": event.get("anomaly_score", 0),
            "html": (
                f'{img_tag}'
                f'<div class="info">'
                f'<div class="title">{escape(event.get("clip", ""))}{badge}</div>'
                f'<div class="time">{escape(event.get("timestamp", "")[:19])}</div>'
                f'<div class="score">Score: {event.get("anomaly_score", 0)}</div>'
                f'<div class="reasons">{reasons_html}</div>'
                f'{l2_html}'
                f'</div>'
            ),
        }
        slides_js.append(json.dumps(slide["html"]))

    slides_array = ",\n    ".join(slides_js) if slides_js else '""'

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
  }}
  .header {{
    background: linear-gradient(135deg, #2d1f00, #4a3520);
    padding: 16px 24px;
    display: flex; align-items: center; justify-content: space-between;
  }}
  .header h1 {{ font-size: 1.6em; color: #ffd700; }}
  .header .counter {{ font-size: 1.1em; opacity: 0.7; }}
  .slide {{
    display: flex; height: calc(100vh - 70px);
    padding: 20px; gap: 20px; align-items: center;
  }}
  .crop {{
    max-height: 100%; max-width: 55%;
    border-radius: 12px; border: 2px solid #4a3520;
    object-fit: contain;
  }}
  .info {{ flex: 1; padding: 20px; }}
  .title {{ font-size: 1.4em; font-weight: bold; margin-bottom: 8px; }}
  .time {{ font-size: 1.1em; opacity: 0.6; margin-bottom: 12px; }}
  .score {{ font-size: 1.3em; color: #ff6b35; margin-bottom: 16px; }}
  .tag {{
    display: inline-block; background: #4a3520; color: #ffd700;
    padding: 4px 12px; border-radius: 20px; margin: 3px 4px 3px 0;
    font-size: 0.85em;
  }}
  .l2 {{
    margin-top: 16px; padding: 12px; background: #2d2d1a;
    border-radius: 8px; font-size: 0.95em; line-height: 1.4;
    border-left: 3px solid #ffd700;
  }}
  .badge {{
    display: inline-block; padding: 2px 10px; border-radius: 12px;
    font-size: 0.75em; margin-left: 10px; vertical-align: middle;
  }}
  .badge.alert {{ background: #d32f2f; color: #fff; }}
  .badge.ok {{ background: #388e3c; color: #fff; }}
  .empty {{
    display: flex; align-items: center; justify-content: center;
    height: 80vh; font-size: 1.5em; opacity: 0.5;
  }}
  .progress {{
    position: fixed; bottom: 0; left: 0; height: 4px;
    background: #ffd700; transition: width 0.3s;
  }}
</style></head><body>

<div class="header">
  <h1>&#x1f41d; Beehive Monitor</h1>
  <div class="counter" id="counter"></div>
</div>
<div id="content"></div>
<div class="progress" id="progress"></div>

<script>
const slides = [
    {slides_array}
];
const INTERVAL = 8000;
let idx = 0;

function show(i) {{
  if (!slides.length) {{
    document.getElementById('content').innerHTML =
      '<div class="empty">No anomalies detected — all clear!</div>';
    return;
  }}
  idx = ((i % slides.length) + slides.length) % slides.length;
  document.getElementById('content').innerHTML =
    '<div class="slide">' + slides[idx] + '</div>';
  document.getElementById('counter').textContent =
    (idx + 1) + ' / ' + slides.length;
  document.getElementById('progress').style.width =
    ((idx + 1) / slides.length * 100) + '%';
}}

show(0);
setInterval(() => show(idx + 1), INTERVAL);

document.addEventListener('click', (e) => {{
  if (e.clientX > window.innerWidth / 2) show(idx + 1);
  else show(idx - 1);
}});
</script>
</body></html>"""


def deploy_to_portal(results_dir: Path, top_n: int | None = None) -> None:
    """Generate dashboard and push to Portal via ADB."""
    digest_path = results_dir / "digest.json"
    if not digest_path.exists():
        log.error("No digest.json in %s — run the analysis first.", results_dir)
        sys.exit(1)

    digest = json.loads(digest_path.read_text())
    crops_dir = results_dir / "crops"

    log.info(
        "Generating Portal dashboard (%d events)…",
        len(digest.get("events", [])),
    )
    html = generate_portal_html(digest, crops_dir, top_n)

    # Write dashboard locally
    dashboard_path = results_dir / "portal_dashboard.html"
    dashboard_path.write_text(html)
    log.info("Wrote %s", dashboard_path)

    # Push to Portal
    log.info("Pushing dashboard to Portal…")
    _adb("shell", "mkdir", "-p", PORTAL_PATH)
    result = _adb("push", str(dashboard_path), f"{PORTAL_PATH}/dashboard.html")
    if result.returncode != 0:
        log.error("adb push failed: %s", result.stderr)
        sys.exit(1)

    # Launch in browser on Portal
    log.info("Launching dashboard on Portal…")
    _adb(
        "shell", "am", "start",
        "-a", "android.intent.action.VIEW",
        "-d", f"file://{PORTAL_PATH}/dashboard.html",
    )
    log.info("Dashboard deployed! It should now be visible on your Portal.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy beehive anomaly dashboard to Portal via ADB.",
    )
    parser.add_argument(
        "results_dir",
        type=Path,
        help="Results directory from beehive analysis (containing digest.json).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        help="Show only the top N events (default: all).",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only check ADB connection, don't deploy.",
    )
    args = parser.parse_args()

    if not _check_adb():
        sys.exit(1)
    if args.check_only:
        print("ADB connection OK!")
        return

    deploy_to_portal(args.results_dir, args.top)


if __name__ == "__main__":
    main()
