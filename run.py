#!/usr/bin/env python3
"""Beehive Monitor — batch anomaly detection for beehive camera footage.

Usage:
    python run.py /path/to/clips                           # defaults
    python run.py /path/to/clips -o results/ -c config.yaml
    python run.py /path/to/clips --level 1                 # skip vision model
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from beehive_monitor.config import load_config
from beehive_monitor.level1 import analyze_clip, detect_anomalies, extract_crops
from beehive_monitor.level2 import confirm_event
from beehive_monitor.report import save_crops, write_html_report, write_json_digest

VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".m4v"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("beehive")


def find_clips(folder: Path) -> list[Path]:
    clips = sorted(
        p for p in folder.rglob("*") if p.suffix.lower() in VIDEO_EXTS
    )
    return clips


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Beehive Monitor — detect anomalies in beehive camera footage.",
    )
    parser.add_argument(
        "clips_dir",
        type=Path,
        help="Folder containing video clips (.mp4, .avi, .mov, .mkv).",
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=Path("results"),
        help="Output directory for report and crops (default: ./results).",
    )
    parser.add_argument(
        "-c", "--config",
        type=Path,
        default=Path("config.yaml"),
        help="Path to config YAML (default: ./config.yaml).",
    )
    parser.add_argument(
        "--level",
        type=int,
        choices=[1, 2],
        default=2,
        help="Analysis level: 1 = blob analysis only, 2 = + vision model (default: 2).",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose logging.",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # ── Load config ────────────────────────────────────────────────────
    cfg = load_config(args.config)
    if args.level == 1:
        cfg.vision.enabled = False

    # ── Find clips ─────────────────────────────────────────────────────
    clips = find_clips(args.clips_dir)
    if not clips:
        log.error("No video files found in %s", args.clips_dir)
        sys.exit(1)
    log.info("Found %d clips in %s", len(clips), args.clips_dir)

    # ── Level 1: analyze each clip ─────────────────────────────────────
    analyses = []
    for i, clip_path in enumerate(clips, 1):
        log.info("[%d/%d] %s", i, len(clips), clip_path.name)
        analysis = analyze_clip(clip_path, cfg)
        analyses.append(analysis)
        tracks_info = f"{len(analysis.tracks)} tracks" if analysis.tracks else "no motion"
        log.info("  → %d frames, %s", analysis.frame_count, tracks_info)

    # ── Detect outliers across the batch ───────────────────────────────
    log.info("Running outlier detection across %d clips…", len(analyses))
    events = detect_anomalies(analyses, cfg)
    log.info("Flagged %d events from %d clips", len(events), len(analyses))

    # ── Extract crops for flagged events ───────────────────────────────
    crops_map: dict[int, list] = {}
    for i, event in enumerate(events):
        crops = extract_crops(event)
        crops_map[i] = crops

    # ── Level 2: vision-model confirmation ─────────────────────────────
    if cfg.vision.enabled and events:
        log.info("Running Level 2 vision confirmation on %d events…", len(events))
        for i, event in enumerate(events):
            crops = crops_map.get(i, [])
            if crops:
                confirm_event(event, crops[: cfg.vision.max_crops_per_clip], cfg.vision)

        confirmed = sum(1 for e in events if e.level2_confirmed is True)
        dismissed = sum(1 for e in events if e.level2_confirmed is False)
        log.info("Vision model: %d confirmed, %d dismissed, %d uncertain",
                 confirmed, dismissed, len(events) - confirmed - dismissed)

    # ── Generate reports ───────────────────────────────────────────────
    args.output.mkdir(parents=True, exist_ok=True)
    save_crops(events, crops_map, args.output)
    write_json_digest(events, len(analyses), args.output)
    write_html_report(events, crops_map, len(analyses), args.output, cfg.report)

    # ── Summary ────────────────────────────────────────────────────────
    print(f"\n{'=' * 50}")
    print(f"  Clips analyzed:  {len(analyses)}")
    print(f"  Events flagged:  {len(events)}")
    if cfg.vision.enabled:
        confirmed = sum(1 for e in events if e.level2_confirmed is True)
        print(f"  Vision confirmed: {confirmed}")
    print(f"  Report:  {args.output / 'report.html'}")
    print(f"  Digest:  {args.output / 'digest.json'}")
    print(f"  Crops:   {args.output / 'crops/'}")
    print(f"{'=' * 50}\n")


if __name__ == "__main__":
    main()
