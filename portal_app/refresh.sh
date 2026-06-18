#!/usr/bin/env bash
#
# Quick refresh: regenerate the dashboard from the newest results/ and push it
# to the Portal WITHOUT rebuilding/reinstalling the APK. Run ./deploy.sh once
# first (to install the app); after that, use this whenever a new scan finishes.
#
# Optional: pass --rank to re-rank activity first (slower; needs the clips).
#
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

PKG="com.josephine.beehive"
DEST_DIR="/sdcard/Android/data/$PKG/files/dashboard"
DEST="$DEST_DIR/index.html"

if [ "${1:-}" = "--rank" ]; then
  echo "==> Re-ranking activity (top 10%) ..."
  python3 ../rank_activity.py ../clips -o ../results --population digest --percentile 10
fi

echo "==> Regenerating dashboard ..."
python3 build_dashboard.py \
  --results ../results --clips ../clips --config ../config.yaml \
  --out ../results/portal_dashboard.html \
  --asset-out app/src/main/assets/dashboard/index.html

echo "==> Checking for a connected Portal ..."
adb get-state >/dev/null 2>&1 || { echo "ERROR: no device via adb. Connect the Portal and enable ADB."; exit 1; }

echo "==> Pushing dashboard to Portal ..."
adb shell mkdir -p "$DEST_DIR" 2>/dev/null || true
if adb push ../results/portal_dashboard.html "$DEST"; then
  adb shell am start -n "$PKG/.MainActivity"
  echo "Refreshed — the Portal is now showing the latest results."
else
  echo ""
  echo "Push to the app folder was blocked by the device. Fall back to a full"
  echo "rebuild/reinstall instead:  ./deploy.sh"
  exit 1
fi
