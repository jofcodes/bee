# Beehive Monitor

Anomaly detection for beehive camera footage. Points at a folder of video clips
(from a Blink camera, RTSP capture, or any source), flags unusual activity
(wasps, hornets, predators, robbing), and ignores normal honeybee traffic.

**Design principle:** anomaly detector first, not a trained classifier.
No labeled data needed — it models "normal" bee traffic and flags deviations.

## Architecture

| Level | What it does | Requires |
|-------|-------------|----------|
| **1 — Blob analysis** | Background subtraction → blob detection → track size/speed/dwell/trajectory → statistical outlier detection | OpenCV only |
| **2 — Vision confirmation** | Sends flagged crops to a local vision model (LLaVA via Ollama) to describe what it sees | Ollama running locally |
| **3 — Trained classifier** *(future)* | Fine-tune YOLO on crops surfaced by 1–2 | Labeled data from levels 1–2 |

## Quick Start

### 1. Install dependencies

```bash
cd beehive-monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Python ≥ 3.10** required.

### 2. (Optional) Set up Ollama for Level 2

If you want vision-model confirmation (recommended), install Ollama and pull a
vision model:

```bash
# Install Ollama: https://ollama.com/download
ollama pull llava:13b
```

To skip Level 2 and run blob analysis only, use `--level 1`.

### 3. Get your clips

**Option A — USB pull (recommended):**
Pull the USB drive from your Blink Sync Module 2. Copy the `.mp4` files to a
folder on your laptop:
```bash
cp -r /Volumes/BLINK_USB/clip/ ~/beehive-clips/
```

**Option B — Download with blinkpy (fragile but automated):**
```bash
pip install blinkpy==0.25.6
python download_blink.py -o ~/beehive-clips --days 7
```
This will prompt for your Blink email/password and handle 2FA. The script
includes workarounds for known auth bugs (#1217 cookie loss, #1233 HTTP 202),
but Blink changes their API frequently — if it breaks, fall back to USB pull.
Session tokens are cached in `.blink_token.json` so you only need 2FA once.

**Option C — Manual download:**
Download clips from the Blink app (one at a time, unfortunately).

### 4. Run the analysis

```bash
# Full analysis (Level 1 + Level 2)
python run.py ~/beehive-clips/

# Level 1 only (no vision model needed)
python run.py ~/beehive-clips/ --level 1

# Custom output dir and config
python run.py ~/beehive-clips/ -o ~/results -c config.yaml

# Verbose logging
python run.py ~/beehive-clips/ -v
```

### 5. Review the results

Open `results/report.html` in your browser — a standalone page with:
- Flagged events ranked by anomaly score
- Thumbnail crops of the suspicious blobs
- Level 2 vision model descriptions (if enabled)
- Color-coded: red border = vision-confirmed, dimmed = dismissed

Machine-readable digest: `results/digest.json`

Individual crops: `results/crops/`

### 6. (Optional) Deploy to Portal

Show the most interesting clips on a Meta Portal as a fullscreen slideshow.
Requires ADB access — follow the [Portal hacking guide](https://docs.google.com/document/d/1_ECxsB_qlhhxY4gGT8nAsUyqCF-Cs9sq1FeQJBnTc6o).

```bash
# One-time setup
brew install --cask android-platform-tools
# Connect Portal via USB-C, enable ADB in Settings → Debug

# Deploy dashboard (after running analysis)
python portal_deploy.py results/
python portal_deploy.py results/ --top 10    # only top 10 events

# Check connection without deploying
python portal_deploy.py results/ --check-only
```

The dashboard auto-cycles through flagged events every 8 seconds.
Tap right side to advance, left side to go back.

## Configuration

All thresholds are in `config.yaml` — see comments in that file.

Key tuning knobs:

| Setting | Default | What it controls |
|---------|---------|-----------------|
| `blob.min_area` | 100 px² | Minimum blob size (filters noise) |
| `outlier.z_threshold` | 2.5 | How many σ from normal to flag |
| `outlier.min_persistence_frames` | 5 | Blob must persist this many frames |
| `outlier.abs_max_blob_area` | 5000 px² | Absolute "this is too big" threshold |
| `outlier.large_blob_ratio` | 3.0× | Flag if largest blob > N× median |
| `outlier.time_bucket_hours` | 2 | Per-time-of-day normal modeling |
| `vision.model` | llava:13b | Ollama model for Level 2 |

## How It Works

### Level 1 — "This isn't normal"

1. **Background subtraction** (OpenCV MOG2) extracts moving objects from each clip
2. **Blob detection** finds contours, computes area, centroid, color histogram
3. **Tracking** links blobs across frames (greedy nearest-neighbor)
4. **Per-track features**: size, speed, dwell score, trajectory linearity
5. **Time-bucketed statistics**: groups clips by time of day, computes median/IQR
6. **Outlier flagging**: clips with features outside the norm get flagged
7. **Persistence filter**: anomaly must last ≥ N frames to reduce false alarms

Bees = steady stream of similar-sized blobs flowing in/out.
Intruders = bigger, hover/loiter, or different color profile.

### Level 2 — "What is it?"

For each flagged crop, a vision model (LLaVA via Ollama, running locally) is
asked: *"Is there anything other than honeybees here?"* This keeps the volume
low (only flagged events are sent) and provides a natural-language description
of what was detected.

## File Structure

```
beehive-monitor/
├── run.py                      # CLI entry point — batch analysis
├── download_blink.py           # Download clips from Blink (with auth workarounds)
├── portal_deploy.py            # Deploy dashboard to Meta Portal via ADB
├── config.yaml                 # All thresholds (edit this)
├── requirements.txt            # Pinned dependencies
├── README.md                   # You are here
└── beehive_monitor/
    ├── __init__.py
    ├── config.py               # Config loading
    ├── models.py               # Dataclasses (Blob, Track, ClipAnalysis, etc.)
    ├── level1.py               # Background subtraction + blob analysis + outlier detection
    ├── level2.py               # Vision model confirmation via Ollama/LLaVA
    └── report.py               # JSON + HTML report generation
```

## Blink Camera Access — Research Notes (June 2026)

| Method | Reliability | Batch? | Notes |
|--------|------------|--------|-------|
| **USB from Sync Module 2** | High | Yes | Best option. Standard MP4s, no auth needed |
| **blinkpy v0.25.6** | Fragile | Yes (when working) | 2FA auth broken for many users (issues #1217, #1233). API breaks every few months |
| **Home Assistant** | Broken | Limited | Uses blinkpy, inherits its bugs |
| **Blink app manual** | Works | No | One clip at a time, mobile only |

**If buying new cameras:** consider Reolink or Amcrest (~$30-60) with RTSP —
standard video stream, no cloud auth, works with ffmpeg/OpenCV directly.

### Sources
- blinkpy GitHub: `fronzbot/blinkpy` (v0.25.6, released June 14, 2026)
- blinkpy auth issues: #1217 (cookie loss), #1233 (HTTP 202 vs 412)
- Blink Sync Module 2 local storage: Blink support docs
