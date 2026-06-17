"""Data models for the beehive monitor."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np


@dataclass
class Blob:
    """A single moving object detected in one frame."""

    centroid: tuple[float, float]
    area: float
    bbox: tuple[int, int, int, int]  # x, y, w, h
    color_hist: np.ndarray
    frame_idx: int


@dataclass
class Track:
    """A blob tracked across multiple frames."""

    track_id: int
    blobs: list[Blob] = field(default_factory=list)

    @property
    def duration_frames(self) -> int:
        if len(self.blobs) < 2:
            return 1
        return self.blobs[-1].frame_idx - self.blobs[0].frame_idx + 1

    @property
    def mean_area(self) -> float:
        return float(np.mean([b.area for b in self.blobs]))

    @property
    def max_area(self) -> float:
        return float(max(b.area for b in self.blobs))

    @property
    def mean_speed(self) -> float:
        """Mean displacement (pixels) between consecutive observations."""
        if len(self.blobs) < 2:
            return 0.0
        speeds = []
        for i in range(1, len(self.blobs)):
            dx = self.blobs[i].centroid[0] - self.blobs[i - 1].centroid[0]
            dy = self.blobs[i].centroid[1] - self.blobs[i - 1].centroid[1]
            dt = self.blobs[i].frame_idx - self.blobs[i - 1].frame_idx
            if dt > 0:
                speeds.append(np.sqrt(dx**2 + dy**2) / dt)
        return float(np.mean(speeds)) if speeds else 0.0

    @property
    def max_speed(self) -> float:
        if len(self.blobs) < 2:
            return 0.0
        speeds = []
        for i in range(1, len(self.blobs)):
            dx = self.blobs[i].centroid[0] - self.blobs[i - 1].centroid[0]
            dy = self.blobs[i].centroid[1] - self.blobs[i - 1].centroid[1]
            dt = self.blobs[i].frame_idx - self.blobs[i - 1].frame_idx
            if dt > 0:
                speeds.append(np.sqrt(dx**2 + dy**2) / dt)
        return float(max(speeds)) if speeds else 0.0

    @property
    def dwell_score(self) -> float:
        """Ratio of track duration to total displacement.
        High = stayed in one area (hovering). 0 if too short."""
        if len(self.blobs) < 2:
            return 0.0
        dx = self.blobs[-1].centroid[0] - self.blobs[0].centroid[0]
        dy = self.blobs[-1].centroid[1] - self.blobs[0].centroid[1]
        displacement = np.sqrt(dx**2 + dy**2)
        if displacement < 1.0:
            return float(self.duration_frames)
        return float(self.duration_frames) / displacement

    @property
    def trajectory_linearity(self) -> float:
        """Ratio of straight-line distance to total path length.
        1.0 = perfectly straight, lower = more wandering/hovering."""
        if len(self.blobs) < 2:
            return 1.0
        dx = self.blobs[-1].centroid[0] - self.blobs[0].centroid[0]
        dy = self.blobs[-1].centroid[1] - self.blobs[0].centroid[1]
        straight = np.sqrt(dx**2 + dy**2)
        path = 0.0
        for i in range(1, len(self.blobs)):
            ddx = self.blobs[i].centroid[0] - self.blobs[i - 1].centroid[0]
            ddy = self.blobs[i].centroid[1] - self.blobs[i - 1].centroid[1]
            path += np.sqrt(ddx**2 + ddy**2)
        if path < 1.0:
            return 1.0
        return float(straight / path)

    @property
    def mean_color_hist(self) -> np.ndarray:
        hists = [b.color_hist for b in self.blobs if b.color_hist is not None]
        if not hists:
            return np.zeros(144)
        return np.mean(hists, axis=0)


@dataclass
class ClipFeatures:
    """Aggregate features computed from all tracks in a clip."""

    blob_count_median: float
    blob_count_max: int
    blob_area_median: float
    blob_area_max: float
    blob_area_std: float
    blob_speed_median: float
    blob_speed_max: float
    dwell_max: float
    linearity_min: float
    track_count: int
    large_blob_ratio: float  # max_area / median_area


@dataclass
class ClipAnalysis:
    """Full analysis result for a single video clip."""

    clip_path: Path
    timestamp: datetime
    fps: float
    frame_count: int
    features: ClipFeatures | None
    tracks: list[Track]
    per_frame_blob_counts: list[int] = field(default_factory=list)
    anomaly_score: float = 0.0
    flagged: bool = False
    anomaly_reasons: list[str] = field(default_factory=list)


@dataclass
class FlaggedEvent:
    """An anomaly surfaced for review."""

    clip_path: Path
    timestamp: datetime
    frame_indices: list[int]
    track: Track | None
    anomaly_score: float
    reasons: list[str]
    crop_paths: list[Path] = field(default_factory=list)
    level2_response: str = ""
    level2_confirmed: bool | None = None


@dataclass
class BucketStats:
    """Normal-traffic statistics for a time-of-day bucket."""

    bucket_name: str
    clip_count: int
    blob_count_median: float
    blob_count_iqr: float
    blob_area_median: float
    blob_area_iqr: float
    blob_speed_median: float
    blob_speed_iqr: float
    dwell_max_median: float
    dwell_max_iqr: float
    linearity_min_median: float
    linearity_min_iqr: float
    large_blob_ratio_median: float
    large_blob_ratio_iqr: float
