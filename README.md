# Beehive Monitor

Anomaly and threat detection for beehive camera footage. Points at a folder of
video clips, identifies interesting events (wasps, hornets, predators, robbing)
and ignores normal honeybee traffic.

## How It Works

**Level 1 — Blob Analysis** (fast, no model needed):
Background subtraction → blob tracking → statistical outlier detection.
Flags clips with unusual size + behavior patterns. Good for pre-filtering.

**Level 2 — Vision Model** (primary detector, recommended):
Sends sampled frames to a local LLaVA vision model via Ollama. Asks "what
animals do you see?" and flags anything that isn't normal honeybees. This is
what actually identifies wasps, hornets, rats, etc.

## Quick Start

### 1. Install dependencies

```bash
cd beehive-monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Install Ollama + LLaVA (for vision detection)

```bash
# Download from https://ollama.com/download and install the macOS app
# Then pull Meta's Llama 3.2 Vision model:
ollama pull llama3.2-vision
```

Make sure Ollama is running (`ollama serve` in another terminal, or it auto-starts on macOS).

### 3. Get your clips

**Option A — USB pull from Sync Module 2 (most reliable):**
```bash
cp -r /Volumes/BLINK_USB/clip/ ~/Documents/AI\ outputs/bee/clips/
```

**Option B — Download with blinkpy:**
```bash
pip install blinkpy==0.25.6
python download_blink.py -o ~/Documents/AI\ outputs/bee/clips --days 7
```

### 4. Run the analysis

```bash
# Vision analysis (recommended — identifies animals)
python run.py ~/Documents/AI\ outputs/bee/clips -o ~/Documents/AI\ outputs/bee/results

# Limit to N clips for testing
python run.py ~/Documents/AI\ outputs/bee/clips -o ~/Documents/AI\ outputs/bee/results -n 10

# Blob analysis only (no Ollama needed, faster but less accurate)
python run.py ~/Documents/AI\ outputs/bee/clips -o ~/Documents/AI\ outputs/bee/results --level 1
```

### 5. Review the results

Open `results/report.html` in your browser. Each flagged event shows:
- Video player for the clip
- Full-frame image with the anomaly highlighted
- What the vision model identified
- Anomaly score and reasons

### 6. Deploy to Portal

The Portal app shows threats first, then most active grid, with Exit button top-right to return to Portal Home, Ambient button for muted loop, swipe gestures for navigation, and normal screen timeout except in Ambient mode.

```bash
# one-time: install adb
brew install --cask android-platform-tools
# or without brew:
# curl -O https://dl.google.com/android/repository/platform-tools-latest-darwin.zip
# unzip -q platform-tools-latest-darwin.zip ; sudo mv platform-tools /usr/local/
# echo 'export PATH=/usr/local/platform-tools:$PATH' >> ~/.zshrc

# Connect Portal via USB-C, enable ADB in Settings → Debug, tap Allow
adb devices   # should list your Portal

# rank and deploy
python rank_activity.py clips -o results --percentile 10
cd portal_app && ./deploy.sh    # first time builds APK, installs, launches
# or open portal_app in Android Studio and press Run

# after new scans, no rebuild needed:
cd portal_app && ./refresh.sh
```

For a quick browser preview open `results/portal_dashboard.html` — same HTML the Portal app renders offline.

### 7. Automatic daily refresh at 7 AM

On your Mac, enable the launchd job once:
```bash
launchctl load ~/Library/LaunchAgents/com.josephine.beehive.monitor.plist
```
Every morning at 7 AM it runs `scripts/auto_refresh.sh` which pulls Blink clips since last run (`--hours` auto-computed), runs vision analysis incrementally, ranks top 10%, rebuilds dashboard, and pushes to Portal via adb if connected. Logs go to `logs/auto_refresh.out.log`. In the Portal app header tap Refresh to reload latest pushed dashboard, Exit to return Home, Ambient for fullscreen loop.

Disable with `launchctl unload ~/Library/LaunchAgents/com.josephine.beehive.monitor.plist`.

## Configuration

All thresholds are in `config.yaml`. Key settings:

| Setting | Default | What it controls |
|---------|---------|-----------------|
| `vision.model` | llama3.2-vision | Ollama vision model (Meta-approved) |
| `vision.max_clips` | 0 (all) | Limit clips per run |
| `outlier.z_threshold` | 5.0 | Statistical outlier sensitivity |
| `outlier.abs_max_blob_area` | 80000 px² | Absolute "too big" threshold |

## File Structure

```
beehive-monitor/
├── run.py                      # Main CLI — batch analysis
├── download_blink.py           # Download clips from Blink cameras
├── portal_deploy.py            # Deploy dashboard to Meta Portal via ADB
├── config.yaml                 # All thresholds (edit this)
├── requirements.txt            # Pinned dependencies
└── beehive_monitor/
    ├── config.py               # Config loading
    ├── models.py               # Data classes
    ├── level1.py               # Blob analysis + outlier detection
    ├── level2.py               # Vision model (Ollama/LLaVA)
    └── report.py               # HTML report + JSON digest
```

## Blink Camera Access (June 2026)

| Method | Reliability | Notes |
|--------|------------|-------|
| **USB from Sync Module 2** | High | Best option — standard MP4s |
| **blinkpy v0.25.6** | Fragile | 2FA auth breaks periodically |
| **Blink app manual** | Works | One clip at a time |

Sources: blinkpy GitHub (fronzbot/blinkpy v0.25.6), issues #1217, #1233.
