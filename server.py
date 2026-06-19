#!/usr/bin/env python3
"""Beehive Monitor Server — serves the dashboard and handles refresh requests.

Runs on your laptop. Portal connects to it over the local network.
Provides:
  - GET /           → the activity dashboard
  - GET /threats    → the threat dashboard
  - POST /refresh   → download new clips, run analysis, regenerate dashboards
  - GET /status     → check if a refresh is running

Usage:
    python server.py                    # start on port 8888
    python server.py --port 9000        # custom port
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, send_file, request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("beehive_server")

PROJECT_DIR = Path(__file__).parent.resolve()
CLIPS_DIR = PROJECT_DIR / "clips"
RESULTS_DIR = PROJECT_DIR / "results"
VENV_PYTHON = PROJECT_DIR / ".venv" / "bin" / "python"

app = Flask(__name__)

# Refresh state
_refresh_lock = threading.Lock()
_refresh_status = {
    "running": False,
    "last_refresh": None,
    "last_result": None,
    "error": None,
}


def _run_refresh():
    """Download new clips, run vision analysis, regenerate dashboards."""
    global _refresh_status

    with _refresh_lock:
        if _refresh_status["running"]:
            return
        _refresh_status["running"] = True
        _refresh_status["error"] = None

    try:
        python = str(VENV_PYTHON) if VENV_PYTHON.exists() else "python3"

        # Step 1: Download new clips from Blink (uses saved token)
        log.info("Step 1/3: Downloading new clips from Blink...")
        token_file = PROJECT_DIR / ".blink_token.json"
        if token_file.exists():
            try:
                result = subprocess.run(
                    [python, str(PROJECT_DIR / "download_blink.py"),
                     "-o", str(CLIPS_DIR), "--days", "1"],
                    cwd=str(PROJECT_DIR),
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode != 0:
                    log.warning("Blink download failed (may need 2FA) — analyzing existing clips only")
            except subprocess.TimeoutExpired:
                log.warning("Blink download timed out (likely needs 2FA) — analyzing existing clips only")
        else:
            log.info("No Blink token found — analyzing existing clips only.")

        # Step 2: Run vision analysis on new clips
        log.info("Step 2/3: Running vision analysis on new clips...")
        result = subprocess.run(
            [python, str(PROJECT_DIR / "run.py"),
             str(CLIPS_DIR), "-o", str(RESULTS_DIR)],
            cwd=str(PROJECT_DIR),
            capture_output=True, text=True, timeout=3600,
        )
        log.info("Vision analysis output: %s", result.stdout[-200:] if result.stdout else "(empty)")

        # Step 3: Regenerate activity dashboard
        # Skip the full activity re-ranking (takes ~15 min for 1746 clips).
        # Just regenerate the dashboard HTML from existing top_activity.json.
        log.info("Step 3/3: Regenerating dashboard...")
        result = subprocess.run(
            [python, str(PROJECT_DIR / "portal_activity.py"), str(RESULTS_DIR)],
            cwd=str(PROJECT_DIR),
            capture_output=True, text=True, timeout=120,
        )

        # Push to Portal if ADB is connected
        adb = "/usr/local/platform-tools/adb"
        if Path(adb).exists():
            devices = subprocess.run([adb, "devices"], capture_output=True, text=True)
            if len(devices.stdout.strip().split("\n")) > 1:
                subprocess.run([adb, "push",
                              str(RESULTS_DIR / "activity_dashboard.html"),
                              "/sdcard/beehive/activity.html"])
                log.info("Pushed updated dashboard to Portal")

        _refresh_status["last_refresh"] = datetime.now().isoformat()
        _refresh_status["last_result"] = "success"
        log.info("Refresh complete!")

    except Exception as exc:
        log.error("Refresh failed: %s", exc)
        _refresh_status["error"] = str(exc)
        _refresh_status["last_result"] = "error"

    finally:
        _refresh_status["running"] = False


@app.route("/")
def dashboard():
    """Serve the activity dashboard."""
    dashboard_path = RESULTS_DIR / "activity_dashboard.html"
    if dashboard_path.exists():
        return send_file(dashboard_path)
    return "<h1>No dashboard yet — trigger a refresh first</h1>", 404


@app.route("/threats")
def threats():
    """Serve the threat dashboard."""
    dashboard_path = RESULTS_DIR / "portal_dashboard.html"
    if dashboard_path.exists():
        return send_file(dashboard_path)
    return "<h1>No threat dashboard yet</h1>", 404


@app.route("/refresh", methods=["POST", "GET"])
def refresh():
    """Trigger a refresh — download new clips, analyze, regenerate dashboard."""
    if _refresh_status["running"]:
        return jsonify({"status": "already_running", **_refresh_status})

    thread = threading.Thread(target=_run_refresh, daemon=True)
    thread.start()
    return jsonify({"status": "started", "message": "Refresh started in background"})


@app.route("/status")
def status():
    """Check refresh status."""
    # Add clip counts
    clip_count = len(list(CLIPS_DIR.glob("*.mp4"))) if CLIPS_DIR.exists() else 0
    progress_file = RESULTS_DIR / "vision_progress.jsonl"
    analyzed = 0
    flagged = 0
    if progress_file.exists():
        for line in progress_file.read_text().strip().splitlines():
            try:
                r = json.loads(line)
                if not r.get("error"):
                    analyzed += 1
                if r.get("has_non_bee_content"):
                    flagged += 1
            except (json.JSONDecodeError, KeyError):
                pass

    return jsonify({
        **_refresh_status,
        "clips_on_disk": clip_count,
        "clips_analyzed": analyzed,
        "events_flagged": flagged,
    })


@app.route("/clips/<path:filename>")
def serve_clip(filename):
    """Serve a video clip."""
    clip_path = CLIPS_DIR / filename
    if clip_path.exists():
        return send_file(clip_path, mimetype="video/mp4")
    return "Not found", 404


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument("--host", type=str, default="0.0.0.0")
    args = parser.parse_args()

    # Get local IP for display
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
    except Exception:
        local_ip = "localhost"
    finally:
        s.close()

    print(f"\n{'=' * 50}")
    print(f"  Beehive Monitor Server")
    print(f"  Dashboard: http://{local_ip}:{args.port}/")
    print(f"  Threats:   http://{local_ip}:{args.port}/threats")
    print(f"  Refresh:   http://{local_ip}:{args.port}/refresh")
    print(f"  Status:    http://{local_ip}:{args.port}/status")
    print(f"{'=' * 50}\n")

    app.run(host=args.host, port=args.port, debug=False)
