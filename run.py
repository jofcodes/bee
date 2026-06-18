#!/usr/bin/env python3
"""Beehive Monitor — detect interesting events in beehive camera footage.

Usage:
    python run.py /path/to/clips                           # vision-first (default)
    python run.py /path/to/clips --level 1                 # blob analysis only
    python run.py /path/to/clips -o results/ -c config.yaml
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from beehive_monitor.config import load_config
from beehive_monitor.level1 import analyze_clip, detect_anomalies, extract_crops, extract_context_frame
from beehive_monitor.level2 import analyze_clip_vision, VisionResult
from beehive_monitor.report import save_crops, write_html_report, write_json_digest

VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".m4v"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("beehive")


def find_clips(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.suffix.lower() in VIDEO_EXTS)


def run_vision_pipeline(clips: list[Path], cfg, args) -> None:
    """Vision-first pipeline: send frames to vision model, flag non-bee content."""

    if cfg.vision.backend == "ollama":
        try:
            import ollama as ollama_lib
            ollama_client = ollama_lib.Client(host=cfg.vision.host)
            ollama_client.list()
        except Exception as exc:
            log.error("Cannot connect to Ollama at %s: %s", cfg.vision.host, exc)
            sys.exit(1)
    else:
        # Llama API — need API key
        api_key = cfg.vision.api_key or os.environ.get("LLAMA_API_KEY", "")
        if not api_key:
            key_file = Path(".llama_key")
            if key_file.exists():
                api_key = key_file.read_text().strip()
        if not api_key:
            print("No Llama API key found.")
            print("Save key to .llama_key file or set LLAMA_API_KEY env var")
            api_key = input("  Or paste your Llama API key now: ").strip()
        if not api_key:
            log.error("No API key provided.")
            sys.exit(1)
        cfg.vision.api_key = api_key

    max_clips = cfg.vision.max_clips or len(clips)
    clips = clips[:max_clips]

    log.info("Analyzing %d clips with Ollama/%s…", len(clips), cfg.vision.model)

    # Load any previously saved incremental results to resume
    incremental_file = args.output / "vision_progress.jsonl"
    already_done: set[str] = set()
    if incremental_file.exists():
        for line in incremental_file.read_text().strip().splitlines():
            try:
                r = json.loads(line)
                already_done.add(r["clip"])
            except (json.JSONDecodeError, KeyError):
                pass
        log.info("Resuming — %d clips already processed", len(already_done))

    results: list[VisionResult] = []
    flagged: list[VisionResult] = []

    with open(incremental_file, "a") as progress_f:
        for i, clip_path in enumerate(clips, 1):
            if clip_path.name in already_done:
                continue
            log.info("[%d/%d] %s", i, len(clips), clip_path.name)
            result = analyze_clip_vision(clip_path, cfg.vision)
            results.append(result)
            if result.has_non_bee_content:
                flagged.append(result)
            # Save incrementally
            progress_f.write(json.dumps({
                "clip": clip_path.name,
                "has_non_bee_content": result.has_non_bee_content,
                "animals_seen": result.animals_seen,
                "description": result.description,
                "confidence": result.confidence,
                "threat_level": result.threat_level,
                "error": result.error,
                "timestamp": result.timestamp.isoformat(),
            }) + "\n")
            progress_f.flush()

    # Merge with previously completed results
    all_results_data = []
    if incremental_file.exists():
        for line in incremental_file.read_text().strip().splitlines():
            try:
                all_results_data.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    total_analyzed = len(all_results_data)
    all_flagged = [r for r in all_results_data if r.get("has_non_bee_content")]
    log.info("Vision analysis complete: %d/%d clips flagged", len(all_flagged), total_analyzed)

    # Build report from flagged results
    from beehive_monitor.models import FlaggedEvent
    events = []
    crops_map = {}
    context_frames = {}

    for i, r_data in enumerate(all_flagged):
        clip_path = Path(args.clips_dir) / r_data["clip"]
        from beehive_monitor.level2 import _extract_frames
        frames = _extract_frames(clip_path, n_frames=3) if clip_path.exists() else []
        ctx_frame = frames[len(frames) // 2] if frames else None

        event = FlaggedEvent(
            clip_path=clip_path,
            timestamp=datetime.fromisoformat(r_data["timestamp"]),
            frame_indices=[],
            track=None,
            anomaly_score={"high": 3.0, "medium": 2.0, "low": 1.0}.get(r_data.get("confidence", ""), 1.0),
            reasons=[
                f"Animals seen: {', '.join(r_data.get('animals_seen', []))}" if r_data.get("animals_seen") else "Non-bee content detected",
                f"Threat level: {r_data.get('threat_level', 'unknown')}",
            ],
            level2_response=r_data.get("description", ""),
            level2_confirmed=True,
        )
        events.append(event)
        crops_map[i] = []
        context_frames[i] = ctx_frame

    args.output.mkdir(parents=True, exist_ok=True)

    digest = {
        "generated": datetime.now().isoformat(),
        "total_clips_analyzed": total_analyzed,
        "events_flagged": len(all_flagged),
        "clips_with_errors": sum(1 for r in all_results_data if r.get("error")),
        "events": all_flagged,
        "all_clips": [
            {
                "clip": r.get("clip", ""),
                "has_non_bee_content": r.get("has_non_bee_content", False),
                "description": r.get("description", "")[:100],
                "error": r.get("error", ""),
            }
            for r in all_results_data
        ],
    }
    digest_path = args.output / "digest.json"
    with open(digest_path, "w") as f:
        json.dump(digest, f, indent=2)
    log.info("Wrote %s", digest_path)

    save_crops(events, crops_map, args.output)
    write_html_report(
        events, crops_map, context_frames,
        total_analyzed, args.output, cfg.report,
        clips_dir=args.clips_dir,
    )

    # Summary
    print(f"\n{'=' * 50}")
    print(f"  Clips analyzed:  {total_analyzed}")
    print(f"  Events flagged:  {len(all_flagged)}")
    if all_flagged:
        print(f"  Animals found:   {', '.join(set(a for r in all_flagged for a in r.get('animals_seen', [])))}")
    print(f"  Report:  {args.output / 'report.html'}")
    print(f"  Digest:  {args.output / 'digest.json'}")
    print(f"{'=' * 50}\n")


def run_blob_pipeline(clips: list[Path], cfg, args) -> None:
    """Original Level 1 blob-analysis pipeline."""
    analyses = []
    for i, clip_path in enumerate(clips, 1):
        log.info("[%d/%d] %s", i, len(clips), clip_path.name)
        analysis = analyze_clip(clip_path, cfg)
        analyses.append(analysis)
        tracks_info = f"{len(analysis.tracks)} tracks" if analysis.tracks else "no motion"
        log.info("  → %d frames, %s", analysis.frame_count, tracks_info)

    log.info("Running outlier detection across %d clips…", len(analyses))
    events = detect_anomalies(analyses, cfg)
    log.info("Flagged %d events from %d clips", len(events), len(analyses))

    crops_map: dict[int, list] = {}
    context_frames: dict[int, any] = {}
    for i, event in enumerate(events):
        crops_map[i] = extract_crops(event)
        context_frames[i] = extract_context_frame(event)

    args.output.mkdir(parents=True, exist_ok=True)
    save_crops(events, crops_map, args.output)
    write_json_digest(events, len(analyses), args.output)
    write_html_report(events, crops_map, context_frames, len(analyses), args.output, cfg.report, clips_dir=args.clips_dir)

    print(f"\n{'=' * 50}")
    print(f"  Clips analyzed:  {len(analyses)}")
    print(f"  Events flagged:  {len(events)}")
    print(f"  Report:  {args.output / 'report.html'}")
    print(f"{'=' * 50}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Beehive Monitor — detect interesting events in beehive camera footage.",
    )
    parser.add_argument(
        "clips_dir", type=Path,
        help="Folder containing video clips (.mp4, .avi, .mov, .mkv).",
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=Path("results"),
        help="Output directory for report and crops (default: ./results).",
    )
    parser.add_argument(
        "-c", "--config", type=Path, default=Path("config.yaml"),
        help="Path to config YAML (default: ./config.yaml).",
    )
    parser.add_argument(
        "--level", type=int, choices=[1, 2], default=2,
        help="1 = blob analysis only, 2 = Llama Vision (default: 2).",
    )
    parser.add_argument(
        "-n", "--max-clips", type=int, default=0,
        help="Max clips to analyze (0 = all).",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    cfg = load_config(args.config)
    if args.max_clips:
        cfg.vision.max_clips = args.max_clips

    clips = find_clips(args.clips_dir)
    if not clips:
        log.error("No video files found in %s", args.clips_dir)
        sys.exit(1)
    log.info("Found %d clips in %s", len(clips), args.clips_dir)

    if args.level == 2 and cfg.vision.enabled:
        run_vision_pipeline(clips, cfg, args)
    else:
        run_blob_pipeline(clips, cfg, args)


if __name__ == "__main__":
    main()
