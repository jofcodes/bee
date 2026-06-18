#!/usr/bin/env bash
#
# Build the Beehive Monitor Portal app, install it on a USB-connected Portal,
# and launch it. Run this once (and again whenever you change the app itself).
#
# Prereqs (one-time): see README.md
#   - Portal connected via USB-C with "ADB Enabled" (Settings -> Debug)
#   - android-platform-tools (adb) installed
#   - Android SDK + Gradle (easiest: open this folder in Android Studio once,
#     or `gradle wrapper` to generate ./gradlew)
#
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

PKG="com.josephine.beehive"
ACT="$PKG/.MainActivity"

echo "==> [1/4] Generating dashboard from the latest results/ ..."
python3 build_dashboard.py \
  --results ../results --clips ../clips --config ../config.yaml \
  --out ../results/portal_dashboard.html \
  --asset-out app/src/main/assets/dashboard/index.html

echo "==> [2/4] Building debug APK ..."
if [ -x ./gradlew ]; then
  ./gradlew assembleDebug
else
  command -v gradle >/dev/null || { echo "ERROR: no ./gradlew and no 'gradle' on PATH. Open this folder in Android Studio once, or run 'gradle wrapper'."; exit 1; }
  gradle assembleDebug
fi
APK="app/build/outputs/apk/debug/app-debug.apk"

echo "==> [3/4] Checking for a connected Portal ..."
adb get-state >/dev/null 2>&1 || { echo "ERROR: no device via adb. Connect the Portal over USB-C and enable ADB (Settings -> Debug)."; exit 1; }

echo "==> [4/4] Installing + launching on Portal ..."
adb install -r "$APK"
# Drop any quick-refresh override so the freshly bundled dashboard is shown.
adb shell rm -f "/sdcard/Android/data/$PKG/files/dashboard/index.html" 2>/dev/null || true
adb shell am start -n "$ACT"

echo ""
echo "Done. 'Beehive Monitor' is installed and running on your Portal."
echo "Tip: launch straight into the ambient loop with:"
echo "  adb shell am start -n $ACT -d 'file:///android_asset/dashboard/index.html#ambient'"
