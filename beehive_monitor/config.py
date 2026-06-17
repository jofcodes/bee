"""Configuration loading and defaults."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class BlobConfig:
    min_area: int = 100
    morph_kernel: int = 5


@dataclass
class BackgroundConfig:
    history: int = 200
    var_threshold: int = 40
    warmup_frames: int = 10


@dataclass
class TrackerConfig:
    max_distance: float = 60.0
    max_gap: int = 3


@dataclass
class OutlierConfig:
    z_threshold: float = 2.5
    min_persistence_frames: int = 5
    time_bucket_hours: int = 2
    min_clips_for_stats: int = 5
    abs_max_blob_area: float = 5000.0
    abs_max_dwell_frames: int = 45
    large_blob_ratio: float = 3.0


@dataclass
class VisionConfig:
    enabled: bool = True
    model: str = "llava:13b"
    host: str = "http://localhost:11434"
    max_crops_per_clip: int = 5
    prompt: str = (
        "This image is a crop from a beehive entrance camera. "
        "Is there anything other than normal honeybee activity visible? "
        "Look specifically for: wasps, hornets, yellow jackets, robbing behavior "
        "(many bees fighting at the entrance), predators (birds, mice, bears), "
        "or any unusual objects. "
        'Reply with a JSON object: {"anomaly": true/false, "description": "...", '
        '"confidence": "high/medium/low"}'
    )


@dataclass
class ReportConfig:
    thumbnail_width: int = 400
    max_events: int = 200


@dataclass
class Config:
    blob: BlobConfig = field(default_factory=BlobConfig)
    background: BackgroundConfig = field(default_factory=BackgroundConfig)
    tracker: TrackerConfig = field(default_factory=TrackerConfig)
    outlier: OutlierConfig = field(default_factory=OutlierConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    report: ReportConfig = field(default_factory=ReportConfig)


def _apply_dict(obj: object, d: dict) -> None:
    for key, val in d.items():
        if hasattr(obj, key):
            setattr(obj, key, val)


def load_config(path: Path | None = None) -> Config:
    cfg = Config()
    if path is None or not path.exists():
        return cfg
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    for section_name in ("blob", "background", "tracker", "outlier", "vision", "report"):
        if section_name in raw and isinstance(raw[section_name], dict):
            _apply_dict(getattr(cfg, section_name), raw[section_name])
    return cfg
