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
brew install ollama
ollama pull llava
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

### 6. Deploy to Portal (optional)

Show the most interesting clips on a Meta Portal as a fullscreen slideshow:
```bash
brew install --cask android-platform-tools
# Connect Portal via USB-C, enable ADB in Settings → Debug
python portal_deploy.py ~/Documents/AI\ outputs/bee/results --top 10
```

## Configuration

All thresholds are in `config.yaml`. Key settings:

| Setting | Default | What it controls |
|---------|---------|-----------------|
| `vision.model` | llava | Ollama vision model |
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
