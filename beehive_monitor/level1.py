"""Level 1 — motion-based anomaly detection.

Pipeline per clip:
  1. Background subtraction (MOG2) → foreground mask
  2. Contour detection → blobs (size, centroid, color histogram)
  3. Simple nearest-neighbor tracking across frames
  4. Per-track features: area, speed, dwell, trajectory linearity, color
  5. Per-clip aggregate features

Then across all clips:
  6. Group by time-of-day bucket
  7. Compute per-bucket statistics (median / IQR)
  8. Flag clips/tracks whose features are statistical outliers
  9. Multi-frame persistence filter
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from .config import Config
from .models import (
    Blob,
    BucketStats,
    ClipAnalysis,
    ClipFeatures,
    FlaggedEvent,
    Track,
)

log = logging.getLogger(__name__)


# ── Blob tracker ───────────────────────────────────────────────────────


class BlobTracker:
    """Greedy nearest-neighbor tracker for blobs across frames."""

    def __init__(self, max_distance: float = 60.0, max_gap: int = 3):
        self.max_distance = max_distance
        self.max_gap = max_gap
        self._active: dict[int, Track] = {}
        self._finished: list[Track] = []
        self._next_id = 0
        self._last_seen: dict[int, int] = {}  # track_id → last frame_idx

    def update(self, frame_idx: int, blobs: list[Blob]) -> None:
        if not self._active:
            for blob in blobs:
                self._start_track(frame_idx, blob)
            return

        # Build cost matrix: distance between each active track's last
        # centroid and each new blob's centroid.
        track_ids = list(self._active.keys())
        if not blobs or not track_ids:
            self._age_out(frame_idx)
            for blob in blobs:
                self._start_track(frame_idx, blob)
            return

        last_centroids = np.array(
            [self._active[tid].blobs[-1].centroid for tid in track_ids]
        )
        new_centroids = np.array([b.centroid for b in blobs])

        # Pairwise distances: (num_tracks, num_blobs)
        diff = last_centroids[:, None, :] - new_centroids[None, :, :]
        dists = np.sqrt((diff**2).sum(axis=2))

        matched_tracks: set[int] = set()
        matched_blobs: set[int] = set()

        # Greedy assignment: pick closest pairs first.
        order = np.argsort(dists, axis=None)
        for flat_idx in order:
            ti = int(flat_idx // len(blobs))
            bi = int(flat_idx % len(blobs))
            if ti in matched_tracks or bi in matched_blobs:
                continue
            if dists[ti, bi] > self.max_distance:
                break
            tid = track_ids[ti]
            self._active[tid].blobs.append(blobs[bi])
            self._last_seen[tid] = frame_idx
            matched_tracks.add(ti)
            matched_blobs.add(bi)

        # Start new tracks for unmatched blobs
        for bi, blob in enumerate(blobs):
            if bi not in matched_blobs:
                self._start_track(frame_idx, blob)

        self._age_out(frame_idx)

    def _start_track(self, frame_idx: int, blob: Blob) -> None:
        tid = self._next_id
        self._next_id += 1
        self._active[tid] = Track(track_id=tid, blobs=[blob])
        self._last_seen[tid] = frame_idx

    def _age_out(self, frame_idx: int) -> None:
        expired = [
            tid
            for tid, last in self._last_seen.items()
            if frame_idx - last > self.max_gap and tid in self._active
        ]
        for tid in expired:
            self._finished.append(self._active.pop(tid))
            del self._last_seen[tid]

    def finalize(self) -> list[Track]:
        self._finished.extend(self._active.values())
        self._active.clear()
        return self._finished


# ── Single-clip analysis ───────────────────────────────────────────────


def _extract_blobs(
    frame: np.ndarray, fg_mask: np.ndarray, frame_idx: int, min_area: int
) -> list[Blob]:
    contours, _ = cv2.findContours(
        fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    blobs: list[Blob] = []
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        centroid = (x + w / 2.0, y + h / 2.0)
        mask = np.zeros(fg_mask.shape, dtype=np.uint8)
        cv2.drawContours(mask, [contour], -1, 255, -1)
        hist = cv2.calcHist([hsv], [0, 1], mask, [18, 8], [0, 180, 0, 256])
        cv2.normalize(hist, hist)
        blobs.append(
            Blob(
                centroid=centroid,
                area=area,
                bbox=(x, y, w, h),
                color_hist=hist.flatten(),
                frame_idx=frame_idx,
            )
        )
    return blobs


def _compute_clip_features(tracks: list[Track], per_frame_counts: list[int]) -> ClipFeatures | None:
    if not tracks:
        return None
    areas = [t.mean_area for t in tracks]
    speeds = [t.mean_speed for t in tracks]
    dwells = [t.dwell_score for t in tracks]
    linearities = [t.trajectory_linearity for t in tracks]
    max_area = max(t.max_area for t in tracks)
    median_area = float(np.median(areas)) if areas else 0.0
    return ClipFeatures(
        blob_count_median=float(np.median(per_frame_counts)) if per_frame_counts else 0.0,
        blob_count_max=max(per_frame_counts) if per_frame_counts else 0,
        blob_area_median=median_area,
        blob_area_max=max_area,
        blob_area_std=float(np.std(areas)) if len(areas) > 1 else 0.0,
        blob_speed_median=float(np.median(speeds)) if speeds else 0.0,
        blob_speed_max=max(speeds) if speeds else 0.0,
        dwell_max=max(dwells) if dwells else 0.0,
        linearity_min=min(linearities) if linearities else 1.0,
        track_count=len(tracks),
        large_blob_ratio=max_area / median_area if median_area > 0 else 0.0,
    )


def _clip_timestamp(clip_path: Path) -> datetime:
    """Extract timestamp from file — uses mtime as a practical default."""
    return datetime.fromtimestamp(clip_path.stat().st_mtime)


def analyze_clip(clip_path: Path, cfg: Config) -> ClipAnalysis:
    """Run Level-1 analysis on a single video clip."""
    log.info("Analyzing %s", clip_path.name)
    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        log.warning("Could not open %s", clip_path)
        return ClipAnalysis(
            clip_path=clip_path,
            timestamp=_clip_timestamp(clip_path),
            fps=0.0,
            frame_count=0,
            features=None,
            tracks=[],
        )

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    bg = cv2.createBackgroundSubtractorMOG2(
        history=cfg.background.history,
        varThreshold=cfg.background.var_threshold,
        detectShadows=False,
    )
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (cfg.blob.morph_kernel, cfg.blob.morph_kernel)
    )
    tracker = BlobTracker(
        max_distance=cfg.tracker.max_distance, max_gap=cfg.tracker.max_gap
    )

    per_frame_counts: list[int] = []
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        fg_mask = bg.apply(frame)
        # Clean mask
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, kernel)
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel)
        # Threshold (MOG2 uses 255 for foreground, 127 for shadow)
        _, fg_mask = cv2.threshold(fg_mask, 200, 255, cv2.THRESH_BINARY)

        if frame_idx < cfg.background.warmup_frames:
            frame_idx += 1
            continue

        blobs = _extract_blobs(frame, fg_mask, frame_idx, cfg.blob.min_area)
        tracker.update(frame_idx, blobs)
        per_frame_counts.append(len(blobs))
        frame_idx += 1

    cap.release()
    tracks = tracker.finalize()
    # Drop very short tracks (likely noise)
    tracks = [t for t in tracks if len(t.blobs) >= 2]
    features = _compute_clip_features(tracks, per_frame_counts)

    return ClipAnalysis(
        clip_path=clip_path,
        timestamp=_clip_timestamp(clip_path),
        fps=fps,
        frame_count=frame_idx,
        features=features,
        tracks=tracks,
        per_frame_blob_counts=per_frame_counts,
    )


# ── Cross-clip outlier detection ───────────────────────────────────────


def _time_bucket(ts: datetime, hours: int) -> str:
    bucket = (ts.hour // hours) * hours
    return f"{bucket:02d}:00-{bucket + hours:02d}:00"


def _robust_z(value: float, median: float, iqr: float) -> float:
    """Z-score using median and IQR (robust to outliers).
    IQR/1.35 ≈ std for a normal distribution."""
    if iqr < 1e-6:
        return 0.0
    return abs(value - median) / (iqr / 1.35)


def _compute_bucket_stats(analyses: list[ClipAnalysis]) -> BucketStats | None:
    feats = [a.features for a in analyses if a.features is not None]
    if not feats:
        return None

    def _med_iqr(values: list[float]) -> tuple[float, float]:
        arr = np.array(values)
        med = float(np.median(arr))
        iqr = float(np.percentile(arr, 75) - np.percentile(arr, 25))
        return med, iqr

    bc_m, bc_i = _med_iqr([f.blob_count_median for f in feats])
    ba_m, ba_i = _med_iqr([f.blob_area_median for f in feats])
    bs_m, bs_i = _med_iqr([f.blob_speed_median for f in feats])
    dm_m, dm_i = _med_iqr([f.dwell_max for f in feats])
    lm_m, lm_i = _med_iqr([f.linearity_min for f in feats])
    lr_m, lr_i = _med_iqr([f.large_blob_ratio for f in feats])

    return BucketStats(
        bucket_name="",
        clip_count=len(feats),
        blob_count_median=bc_m, blob_count_iqr=bc_i,
        blob_area_median=ba_m, blob_area_iqr=ba_i,
        blob_speed_median=bs_m, blob_speed_iqr=bs_i,
        dwell_max_median=dm_m, dwell_max_iqr=dm_i,
        linearity_min_median=lm_m, linearity_min_iqr=lm_i,
        large_blob_ratio_median=lr_m, large_blob_ratio_iqr=lr_i,
    )


def _check_absolute_thresholds(
    analysis: ClipAnalysis, cfg: Config
) -> list[str]:
    """Flag obvious anomalies even without statistical context."""
    reasons: list[str] = []
    if analysis.features is None:
        return reasons
    f = analysis.features
    if f.blob_area_max > cfg.outlier.abs_max_blob_area:
        reasons.append(
            f"Large blob: {f.blob_area_max:.0f} px² "
            f"(threshold {cfg.outlier.abs_max_blob_area:.0f})"
        )
    if f.dwell_max > cfg.outlier.abs_max_dwell_frames:
        reasons.append(
            f"Blob hovering: dwell score {f.dwell_max:.1f} "
            f"(threshold {cfg.outlier.abs_max_dwell_frames})"
        )
    if f.large_blob_ratio > cfg.outlier.large_blob_ratio:
        reasons.append(
            f"Size outlier: largest blob {f.large_blob_ratio:.1f}× median "
            f"(threshold {cfg.outlier.large_blob_ratio:.1f}×)"
        )
    return reasons


def _check_statistical_outliers(
    analysis: ClipAnalysis, stats: BucketStats, cfg: Config
) -> list[str]:
    """Flag clips whose features deviate from the bucket norm."""
    reasons: list[str] = []
    if analysis.features is None:
        return reasons
    f = analysis.features
    z = cfg.outlier.z_threshold

    checks = [
        ("blob area", f.blob_area_max, stats.blob_area_median, stats.blob_area_iqr),
        ("dwell", f.dwell_max, stats.dwell_max_median, stats.dwell_max_iqr),
        ("size ratio", f.large_blob_ratio, stats.large_blob_ratio_median, stats.large_blob_ratio_iqr),
    ]
    for name, val, med, iqr in checks:
        zs = _robust_z(val, med, iqr)
        if zs > z:
            reasons.append(f"Statistical outlier — {name}: z={zs:.1f} (val={val:.1f}, median={med:.1f})")

    # Low linearity = wandering/hovering (flag if below norm)
    if stats.linearity_min_iqr > 1e-6:
        lin_z = (stats.linearity_min_median - f.linearity_min) / (stats.linearity_min_iqr / 1.35)
        if lin_z > z:
            reasons.append(
                f"Non-linear trajectory: linearity={f.linearity_min:.2f} "
                f"(bucket median={stats.linearity_min_median:.2f}, z={lin_z:.1f})"
            )

    return reasons


def _find_anomalous_tracks(analysis: ClipAnalysis, cfg: Config) -> list[Track]:
    """Identify which specific tracks in a clip are anomalous."""
    if not analysis.tracks or analysis.features is None:
        return []
    median_area = analysis.features.blob_area_median
    anomalous = []
    for track in analysis.tracks:
        if len(track.blobs) < cfg.outlier.min_persistence_frames:
            continue
        is_large = (
            median_area > 0
            and track.max_area / median_area > cfg.outlier.large_blob_ratio
        )
        is_hovering = track.dwell_score > cfg.outlier.abs_max_dwell_frames
        is_big_abs = track.max_area > cfg.outlier.abs_max_blob_area
        if is_large or is_hovering or is_big_abs:
            anomalous.append(track)
    return anomalous


def detect_anomalies(
    analyses: list[ClipAnalysis], cfg: Config
) -> list[FlaggedEvent]:
    """Run outlier detection across a batch of clip analyses."""
    # Group by time bucket
    buckets: dict[str, list[ClipAnalysis]] = {}
    for a in analyses:
        key = _time_bucket(a.timestamp, cfg.outlier.time_bucket_hours)
        buckets.setdefault(key, []).append(a)

    flagged: list[FlaggedEvent] = []

    for bucket_name, bucket_clips in buckets.items():
        stats = None
        if len(bucket_clips) >= cfg.outlier.min_clips_for_stats:
            stats = _compute_bucket_stats(bucket_clips)
            if stats:
                stats.bucket_name = bucket_name
                log.info(
                    "Bucket %s: %d clips, median area=%.0f, median dwell=%.1f",
                    bucket_name, stats.clip_count,
                    stats.blob_area_median, stats.dwell_max_median,
                )

        for analysis in bucket_clips:
            reasons: list[str] = []
            # Always check absolute thresholds
            reasons.extend(_check_absolute_thresholds(analysis, cfg))
            # Statistical outliers (if we have enough data)
            if stats:
                reasons.extend(_check_statistical_outliers(analysis, stats, cfg))

            if not reasons:
                continue

            analysis.flagged = True
            analysis.anomaly_reasons = reasons

            # Find the specific anomalous tracks for crop extraction
            bad_tracks = _find_anomalous_tracks(analysis, cfg)

            # Anomaly score = number of reasons (simple but effective)
            score = float(len(reasons))

            if bad_tracks:
                for track in bad_tracks:
                    frame_indices = [b.frame_idx for b in track.blobs]
                    flagged.append(
                        FlaggedEvent(
                            clip_path=analysis.clip_path,
                            timestamp=analysis.timestamp,
                            frame_indices=frame_indices,
                            track=track,
                            anomaly_score=score,
                            reasons=reasons,
                        )
                    )
            else:
                # Clip-level anomaly without a specific track
                flagged.append(
                    FlaggedEvent(
                        clip_path=analysis.clip_path,
                        timestamp=analysis.timestamp,
                        frame_indices=[],
                        track=None,
                        anomaly_score=score,
                        reasons=reasons,
                    )
                )

    flagged.sort(key=lambda e: e.anomaly_score, reverse=True)
    return flagged


def extract_crops(
    event: FlaggedEvent, padding: int = 30
) -> list[np.ndarray]:
    """Extract BGR image crops for the anomalous blob in its key frames."""
    if not event.frame_indices or event.track is None:
        return []

    cap = cv2.VideoCapture(str(event.clip_path))
    if not cap.isOpened():
        return []

    # Pick up to 5 evenly-spaced frames from the track
    indices = event.frame_indices
    if len(indices) > 5:
        step = len(indices) // 5
        indices = indices[::step][:5]

    blob_lookup = {b.frame_idx: b for b in event.track.blobs}
    crops: list[np.ndarray] = []
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx in blob_lookup:
            b = blob_lookup[frame_idx]
            x, y, w, h = b.bbox
            h_img, w_img = frame.shape[:2]
            x1 = max(0, x - padding)
            y1 = max(0, y - padding)
            x2 = min(w_img, x + w + padding)
            y2 = min(h_img, y + h + padding)
            crops.append(frame[y1:y2, x1:x2].copy())
        frame_idx += 1
        if frame_idx > max(indices):
            break

    cap.release()
    return crops
