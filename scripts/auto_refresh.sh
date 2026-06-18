#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

LOG="$HERE/results/auto_refresh.log"
mkdir -p results logs
exec > >(tee -a "$LOG") 2>&1

echo "=== Beehive auto refresh $(date) ==="

# activate venv if present
if [ -d .venv ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

# ensure ollama running
if ! curl -s --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null; then
  echo "Starting ollama..."
  ollama serve > /tmp/ollama.log 2>&1 &
  sleep 5
fi

# determine hours since last successful refresh
LAST_FILE="results/last_refresh.txt"
HOURS=24
if [ -f "$LAST_FILE" ]; then
  LAST_EPOCH=$(cat "$LAST_FILE")
  NOW_EPOCH=$(date +%s)
  DIFF=$(( (NOW_EPOCH - LAST_EPOCH) / 3600 ))
  if [ "$DIFF" -gt 1 ]; then
    HOURS=$((DIFF + 1))
  else
    HOURS=2
  fi
  echo "Last refresh $(date -r "$LAST_EPOCH" 2>/dev/null || date -d @$LAST_EPOCH), pulling last ${HOURS}h"
else
  echo "No last refresh record, defaulting to last 24h"
fi
# cap at 48h to avoid huge downloads
if [ "$HOURS" -gt 48 ]; then HOURS=48; fi

# download from Blink cloud - requires prior saved token or will prompt and fail non-interactively
# We rely on .blink_token.json saved from previous interactive login.
# If token expired, script will exit non-zero and log will show 2FA required.
echo "==> Pulling Blink clips --hours $HOURS ..."
if python download_blink.py -o recent_clips --hours "$HOURS"; then
  echo "Blink download succeeded"
else
  echo "WARN: Blink download failed or needs 2FA - check $LOG . Proceeding with existing clips folder as fallback."
fi

# copy recent to working clips folder for analysis, or use existing clips
# Prefer recent_clips if it has new files, else fall back to clips
SRC="recent_clips"
if [ ! -d "$SRC" ] || [ -z "$(ls -A "$SRC" 2>/dev/null)" ]; then
  SRC="clips"
fi
echo "Using source folder: $SRC"

# run vision analysis incremental - run.py handles resume via vision_progress.jsonl
echo "==> Running vision analysis ..."
python run.py "$SRC" -o results || echo "run.py exited non-zero, continuing with existing results"

echo "==> Ranking activity top 10% ..."
python rank_activity.py "$SRC" -o results --percentile 10 || true

echo "==> Building dashboard ..."
python portal_app/build_dashboard.py --results results --clips "$SRC" --config config.yaml --out results/portal_dashboard.html --asset-out portal_app/app/src/main/assets/dashboard/index.html

# record timestamp for next incremental run
date +%s > "$LAST_FILE"
date > results/last_refresh.txt
echo "Updated last refresh timestamp"

# push to Portal if connected via adb
if command -v adb >/dev/null && adb get-state >/dev/null 2>&1; then
  echo "==> Portal connected, pushing dashboard via refresh.sh ..."
  cd portal_app
  ./refresh.sh || echo "refresh.sh failed, try ./deploy.sh manually next time Portal is connected"
  cd ..
else
  echo "No Portal connected via adb right now — dashboard built locally at results/portal_dashboard.html . Will push next time device is plugged in or on next scheduled run when connected."
fi

echo "=== Done $(date) ==="
