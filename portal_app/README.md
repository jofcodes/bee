# Beehive Monitor — Portal app

A real, installed Android app for **Meta Portal** that displays your beehive
analysis results. It is deployed the way the
[Vibe-coding new apps for Portal](https://docs.google.com/document/d/1_ECxsB_qlhhxY4gGT8nAsUyqCF-Cs9sq1FeQJBnTc6o)
guide prescribes — `adb install` → `am start -n` — so it shows up as its own app
on the device (launcher icon, full-screen, no browser chrome).

Under the hood it's a thin **WebView wrapper**: one full-screen Activity that
renders a single self-contained dashboard page. Stills and video are embedded
as base64, so it runs **offline on the Portal with no network and no server**
(the app requests no INTERNET permission). The app behaves like a normal Portal
app: tap the Beehive Monitor icon to open, tap Exit in the header or press Home
to leave, swipe from screen edge to reveal system navigation.

---

## TL;DR

```bash
# 0) one-time: Portal connected via USB-C, "ADB Enabled" in Settings → Debug,
#    and:  brew install --cask android-platform-tools

cd portal_app
./deploy.sh          # generate dashboard + build APK + install + launch on Portal

# after each new analysis scan:
./refresh.sh         # regenerate + push the new dashboard (no rebuild)
./refresh.sh --rank  # also recompute the top 10% first

# automatic daily refresh at 7 AM (pulls Blink cloud, analyzes, pushes to Portal if connected):
# one-time setup on your Mac:
launchctl load ~/Library/LaunchAgents/com.josephine.beehive.monitor.plist
# or run manually anytime:
../scripts/auto_refresh.sh
```

No command line? Open the `portal_app/` folder in **Android Studio** and press
**Run ▶** with the Portal selected. In the app header tap **Refresh** to reload
the latest dashboard pushed from your Mac, **Exit** to return to Portal Home,
or **Ambient** for fullscreen loop.

---

## What it shows (one scrolling page)

1. **Threats — first.** Clips the Llama vision model flagged as non-bee / a
   threat (wasp, hornet, predator). Each card shows a red-boxed still, the
   model's description, the animals it named, and a colored threat badge
   (low/medium/high). When nothing is flagged it shows a clean **"All clear"**
   panel.
2. **Most active clips.** The **top 10%** busiest clips ranked by motion, as a
   grid of thumbnails. Each thumbnail is the clip's **peak-activity frame with
   red bounding boxes** around every detected moving object, plus a
   "N detected" count, activity metrics (tracks / peak blobs), and the vision
   description.
3. **Fullscreen + ambient playback.**
   - **Tap any clip** → plays fullscreen with sound and a caption.
   - **Ambient button** (top-right) → auto-advances through the clips fullscreen
     on a **muted loop** — a "background video" view you can leave running.

### Controls & gestures (in fullscreen)

| Gesture / key | Action |
|---|---|
| Tap a card | Open that clip fullscreen |
| Swipe left / right · ← → | Next / previous clip |
| Swipe down · ✕ · Esc | Exit fullscreen back to grid |
| Refresh button top-right | Reload dashboard from latest pushed version; shows last auto-refresh time |
| Exit button top-right | Close app and return to Portal Home |
| Ambient button top-right | Start muted auto-advancing loop, keeps screen awake |
| Swipe from top or bottom edge | Reveal system navigation bar, then tap Home |

The app uses non-sticky immersive mode — swipe from screen edge reveals system bars and they stay visible until you tap back into content. Screen timeout follows normal Portal settings except in Ambient mode, where keep-screen-on is enabled so it works as always-on display. Tap Exit or press Home to leave at any time.

---

## Architecture

```
┌─────────────────────────── your Mac ───────────────────────────┐
│  clips/*.mp4                                                     │
│      │                                                          │
│      ├─ rank_activity.py ─► results/top_activity.json  (top 10%)│
│      │                      results/activity_rank.jsonl         │
│      │   (reuses beehive_monitor Level-1: MOG2 + blob tracking) │
│      │                                                          │
│      └─ run.py (vision) ─► results/vision_progress.jsonl        │
│                            results/digest.json                  │
│                ▼                                                │
│   portal_app/build_dashboard.py                                 │
│     • red boxes on each clip's peak frame (same config.yaml)    │
│     • merge vision text (vision_progress → digest fallback)     │
│     • embed stills + video as base64                            │
│                ▼                                                │
│   app/src/main/assets/dashboard/index.html  (one self-contained │
│                                               HTML page)        │
│                ▼   ./deploy.sh  (gradle assembleDebug)          │
│   app-debug.apk ── adb install ──► ┌─────────────────────────┐  │
│                    adb am start ──►│  Meta Portal            │  │
│                                    │  MainActivity = WebView │  │
└────────────────────────────────────┤  renders the dashboard │──┘
                                      └─────────────────────────┘
```

The WebView is configured for media display: JavaScript on, autoplay allowed
(`mediaPlaybackRequiresUserGesture=false`), HTML5 fullscreen bridged to a real
fullscreen surface, immersive non-sticky system bar hiding, and a JavaScript
bridge (`Beehive.exit()`, `Beehive.setKeepScreenOn()`) for Exit button and
ambient keep-awake control.

---

## Data inputs

The generator reads these from `results/` (paths are configurable via flags):

| File | Produced by | Used for | Key fields |
|---|---|---|---|
| `top_activity.json` | `rank_activity.py` | the activity grid | `top_clips[] {clip, tracks, blob_count_max, frame_count}`, `total_analyzed`, `percentile` |
| `activity_rank.jsonl` | `rank_activity.py` | full ranked list (resumable) | one JSON object per clip |
| `vision_progress.jsonl` | `run.py` (live scan) | **preferred** vision text + threats | `clip, has_non_bee_content, animals_seen, description, confidence, threat_level, error` |
| `digest.json` | `run.py` | vision fallback | `all_clips[] {clip, has_non_bee_content, description, error}` |
| `clips/*.mp4` | your Blink pull | stills + video | — |

**A clip is treated as a threat** when `has_non_bee_content` is true **or**
`threat_level` is `low`/`medium`/`high`. Red boxes always come from the project's
own Level-1 motion detector (so they match the rest of the pipeline).

---

## Requirements

- A **Portal** signed in with your employee-linked Facebook/Messenger account
  (required to see **Settings → Debug → ADB Enabled**).
- **android-platform-tools** (`adb`): `brew install --cask android-platform-tools`
- To build the APK: **Android Studio** (easiest — bundles the Android SDK +
  JDK 17 + Gradle) or a command-line **Android SDK + Gradle 8.2 + JDK 17**.
- **Python** with the repo's deps (the existing `.venv`) to (re)generate the
  dashboard — uses `opencv-python` + the `beehive_monitor` package.

---

## One-time setup (from the Portal guide)

1. Connect the Portal via **USB-C** (on Portal Go, pop the rubber cover to reach
   the port).
2. On the Portal: **Settings → Debug → ADB Enabled** (enter PIN if asked; tap
   **Allow** to trust this computer the first time).
3. Make the device visible to `adb`. On an RL laptop, Maui handles drivers:
   ```bash
   maui sf                 # select Portal
   maui install-rl-driver
   maui devices            # confirm the Portal shows up
   ```
4. Confirm `adb` sees it:
   ```bash
   adb devices             # your Portal should be listed
   ```

---

## Build & deploy

**Android Studio (easiest):** open `portal_app/`, let it sync, then **Run ▶**
with the Portal selected. Android Studio generates the Gradle wrapper and
provides the SDK/JDK.

**Command line:**
```bash
cd portal_app
# first time only, if you don't have ./gradlew:  gradle wrapper
./deploy.sh
```
`deploy.sh` regenerates the dashboard from the latest `results/`, builds the
debug APK, installs it, and launches it.

Launch straight into the ambient loop:
```bash
adb shell am start -n com.josephine.beehive/.MainActivity \
  -d 'file:///android_asset/dashboard/index.html#ambient'
```

---

## Refreshing when a new scan finishes

The app loads a dashboard file it can update **without reinstalling**:
```bash
cd portal_app
./refresh.sh            # regenerate dashboard + push to Portal (no rebuild)
./refresh.sh --rank     # also re-rank the top 10% first (reruns Level-1)
```
If your Portal blocks writes to the app's folder, run `./deploy.sh` instead
(rebuild + reinstall) — same result, a few seconds slower.

### Regenerate / re-rank manually

```bash
# re-rank to any cutoff (writes results/top_activity.json):
python rank_activity.py clips -o results --percentile 10   # or 5, 20, …

# rebuild just the dashboard HTML from current results:
python portal_app/build_dashboard.py \
  --results results --clips clips --config config.yaml \
  --out results/portal_dashboard.html \
  --asset-out portal_app/app/src/main/assets/dashboard/index.html
```
`results/portal_dashboard.html` is a normal web page — open it in any browser to
preview exactly what the Portal will show.

### Automatic daily refresh at 7 AM

On your Mac, a launchd job runs every morning at 7:00 AM to pull new Blink clips,
analyze them with Ollama vision model, rank activity, rebuild the dashboard,
and push to Portal if it's connected via USB or on same ADB network.

One-time setup:
```bash
# script already at scripts/auto_refresh.sh in repo
launchctl load ~/Library/LaunchAgents/com.josephine.beehive.monitor.plist
# check status:
launchctl list | grep beehive
# run manually anytime to test:
~/Documents/AI\ outputs/bee/scripts/auto_refresh.sh
# view logs:
tail -f ~/Documents/AI\ outputs/bee/logs/auto_refresh.out.log
```

The script does:
1. start Ollama if needed
2. compute hours since last successful refresh (from `results/last_refresh.txt`)
3. `python download_blink.py -o recent_clips --hours N` — uses saved `.blink_token.json` so no 2FA prompt after first interactive login; if token expired it logs warning and falls back to existing clips folder
4. `python run.py` on new clips (incremental resume via vision_progress.jsonl)
5. `python rank_activity.py --percentile 10`
6. `python portal_app/build_dashboard.py` regenerates HTML with updated timestamp
7. `adb push` to Portal dashboard folder if device connected, then relaunches app; if not connected, dashboard waits on disk for next manual refresh or next day when plugged in

In-app UI shows "Auto refresh daily at 7 AM" in header meta line, and last check timestamp if available via `Beehive.lastRefreshTime()`. Tap Refresh button top-right in app header to reload the latest pushed dashboard instantly without reinstall.

To disable automatic runs:
```bash
launchctl unload ~/Library/LaunchAgents/com.josephine.beehive.monitor.plist
```

---

## Requirement → where it lives

| Requirement | Implemented in |
|---|---|
| Top 10% most active clips as a grid w/ thumbnails | `rank_activity.py` + grid in `build_dashboard.py` |
| Tap a clip → fullscreen playback | overlay player + WebView fullscreen bridge (`MainActivity.java`) |
| Red bounding boxes on stills of detected objects | `boxed_still()` (reuses `beehive_monitor` Level-1) |
| Vision model's description | merged from `vision_progress.jsonl` / `digest.json` |
| Swipe / touch navigation | touch handlers (swipe = prev/next/exit) |
| Exit button → return to Portal Home | header button + `Beehive.exit()` JS bridge → `finish()` in `MainActivity.java` |
| Normal app behavior, not kiosk | non-sticky immersive, no keep-screen-on except ambient, launcher icon |
| Refreshable on new results | `refresh.sh` (push) / `deploy.sh` (reinstall) |
| Threats first → activity → ambient fullscreen | single-page layout + Ambient mode |

---

## Files

```
portal_app/
├── README.md             # this document
├── build_dashboard.py    # generates dashboard HTML (boxes + vision + playback + Exit/Refresh buttons)
├── deploy.sh             # generate → build APK → install → launch on Portal
├── refresh.sh            # regenerate + push new dashboard (no reinstall)
├── settings.gradle, build.gradle, gradle.properties
└── app/
    ├── build.gradle      # plain Java app, no AndroidX deps; minSdk 24, targetSdk 34
    └── src/main/
        ├── AndroidManifest.xml         # launcher activity; no INTERNET permission
        ├── java/com/josephine/beehive/MainActivity.java  # WebView + Beehive JS bridge exit/refresh/setKeepScreenOn
        ├── assets/dashboard/index.html # generated dashboard (gitignored)
        └── res/                        # icon, strings, fullscreen theme
../rank_activity.py        # activity ranking → results/top_activity.json
../scripts/auto_refresh.sh # daily 7am pull Blink → analyze → rank → build → adb push
~/Library/LaunchAgents/com.josephine.beehive.monitor.plist  # launchd schedule
```

---

## Troubleshooting

- **`adb` shows nothing** — re-tap *ADB Enabled* on the Portal; re-run
  `maui devices`; reseat the USB-C cable.
- **How to exit the app** — tap **Exit** button top-right in header, or press Back until you leave grid view, or swipe from top/bottom edge to reveal system navigation then tap Home. From adb: `adb shell input keyevent 3`.
- **Gradle/JDK error** — AGP 8.2 needs **JDK 17**. Build in Android Studio
  (bundles it) or point `JAVA_HOME` at a JDK 17.
- **No `./gradlew`** — open once in Android Studio, or run `gradle wrapper`.
- **Ambient video won't autoplay** — it's muted on purpose (autoplay policy);
  single playback (tap a card) has sound.
- **Dashboard looks stale after reinstall** — `deploy.sh` clears the pushed
  override so the freshly bundled page is used.

---

## Policy notes

- Personal beehive footage only — no Meta internal data.
- The app uses **no network** (no INTERNET permission); everything is local.
- Vision analysis uses Meta-approved **Llama** models only; keep Ollama bound to
  `127.0.0.1` if you use the local backend.
