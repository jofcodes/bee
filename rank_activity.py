#!/usr/bin/env python3
"""Rank beehive clips by Level-1 motion activity.

Reuses the project's own Level-1 detector (beehive_monitor.level1.analyze_clip)
so the metrics match the rest of the pipeline exactly:

  - tracks          = number of tracked moving objects across the clip
  - blob_count_max  = peak number of simultaneous moving blobs in any frame

Writes:
  results/activity_rank.jsonl   full per-clip results (incremental / resumable)
  results/top_activity.json     the ranked top-N% used by the Portal dashboard

Usage:
    python rank_activity.py clips -o results                 # default: top 10%
    python rank_activity.py clips -o results --percentile 5
    python rank_activity.py clips -o results --population all # rank every clip on disk
    python rank_activity.py clips -o results --population digest  # only clips in digest.json (default)
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
from pathlib import Path

from beehive_monitor.config import load_config
from beehive_monitor.level1 import analyze_clip

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("rank_activity")


def _clip_population(clips_dir: Path, output: Path, population: str) -> list[Path]:
    """Decide which clips to rank."""
    if population == "all":
        return sorted(clips_dir.glob("*.mp4"))

    # "digest": rank exactly the clips the pipeline already analyzed.
    digest = output / "digest.json"
    if digest.exists():
        names = [c["clip"] for c in json.loads(digest.read_text()).get("all_clips", [])]
        paths = [clips_dir / n for n in names if (clips_dir / n).exists()]
        if paths:
            return paths
    log.warning("digest.json not found/empty — falling back to all clips on disk")
    return sorted(clips_dir.glob("*.mp4"))


def main() -> None:
    p = argparse.ArgumentParser(description="Rank beehive clips by motion activity.")
    p.add_argument("clips_dir", type=Path)
    p.add_argument("-o", "--output", type=Path, default=Path("results"))
    p.add_argument("-c", "--config", type=Path, default=Path("config.yaml"))
    p.add_argument("--percentile", type=float, default=10.0,
                   help="Top N%% most active clips to surface (default: 10).")
    p.add_argument("--population", choices=["digest", "all"], default="digest",
                   help="Which clips to rank: those in digest.json (default) or all on disk.")
    args = p.parse_args()

    cfg = load_config(args.config)
    args.output.mkdir(parents=True, exist_ok=True)
    rank_file = args.output / "activity_rank.jsonl"

    clips = _clip_population(args.clips_dir, args.output, args.population)
    if not clips:
        log.error("No clips to rank in %s", args.clips_dir)
        sys.exit(1)

    # Resume: skip clips already scored.
    done: dict[str, dict] = {}
    if rank_file.exists():
        for line in rank_file.read_text().strip().splitlines():
            try:
                r = json.loads(line)
                done[r["clip"]] = r
            except (json.JSONDecodeError, KeyError):
                pass
        log.info("Resuming — %d clips already scored", len(done))

    log.info("Ranking %d clips by Level-1 activity…", len(clips))
    t0 = time.time()
    with open(rank_file, "a") as f:
        for i, clip in enumerate(clips, 1):
            if clip.name in done:
                continue
            try:
                a = analyze_clip(clip, cfg)
                rec = {
                    "clip": clip.name,
                    "tracks": len(a.tracks),
                    "blob_count_max": a.features.blob_count_max if a.features else 0,
                    "frame_count": a.frame_count,
                }
            except Exception as exc:  # keep going on a bad clip
                rec = {"clip": clip.name, "tracks": 0, "blob_count_max": 0,
                       "frame_count": 0, "error": str(exc)}
            done[clip.name] = rec
            f.write(json.dumps(rec) + "\n")
            f.flush()
            if i % 10 == 0 or i == len(clips):
                rate = (time.time() - t0) / max(1, i - (len(clips) - len([c for c in clips if c.name not in done])))
                log.info("[%d/%d] %s → %d tracks", i, len(clips), clip.name, rec["tracks"])

    # Rank: most active first (tracks, then peak blobs).
    ranked = sorted(done.values(),
                    key=lambda r: (r.get("tracks", 0), r.get("blob_count_max", 0)),
                    reverse=True)
    total = len(ranked)
    n_top = max(1, math.ceil(total * args.percentile / 100.0))
    top = ranked[:n_top]

    out = {
        "top_clips": [
            {"clip": r["clip"], "tracks": r.get("tracks", 0),
             "blob_count_max": r.get("blob_count_max", 0),
             "frame_count": r.get("frame_count", 0)}
            for r in top
        ],
        "total_analyzed": total,
        "percentile": args.percentile,
    }
    top_file = args.output / "top_activity.json"
    top_file.write_text(json.dumps(out, indent=2))
    log.info("Wrote %s — top %g%% = %d of %d clips (%.1fs)",
             top_file, args.percentile, n_top, total, time.time() - t0)


if __name__ == "__main__":
    main()
