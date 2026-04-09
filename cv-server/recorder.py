"""
OpenBell CV Server — Video recorder

Records short video clips around detection events.

When a person is detected, starts recording. Continues for a configurable
duration after the last detection. Videos are stored as MP4 (H.264) in
the recordings/ directory with timestamps.

Storage management: automatically prunes old recordings and snapshots
when total disk usage exceeds configured limits.
"""

import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger("openbell.cv.recorder")

# ── Configuration ──
RECORDINGS_DIR = os.environ.get(
    "OPENBELL_RECORDINGS_DIR",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "recordings"),
)
SNAPSHOT_DIR = os.environ.get(
    "OPENBELL_SNAPSHOT_DIR",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "snapshots"),
)

# Max recording duration per clip (seconds)
MAX_CLIP_DURATION = int(os.environ.get("OPENBELL_MAX_CLIP_SECS", "60"))
# Seconds to keep recording after last detection
POST_DETECTION_SECS = int(os.environ.get("OPENBELL_POST_DETECT_SECS", "10"))
# Video codec and quality
VIDEO_FPS = int(os.environ.get("OPENBELL_VIDEO_FPS", "10"))
VIDEO_QUALITY = int(os.environ.get("OPENBELL_VIDEO_QUALITY", "23"))  # CRF

# ── Storage limits ──
# Max total storage for recordings (MB)
MAX_RECORDINGS_MB = int(os.environ.get("OPENBELL_MAX_RECORDINGS_MB", "500"))
# Max total storage for snapshots (MB)
MAX_SNAPSHOTS_MB = int(os.environ.get("OPENBELL_MAX_SNAPSHOTS_MB", "100"))
# Max individual recording file age (days)
MAX_RECORDING_AGE_DAYS = int(os.environ.get("OPENBELL_MAX_RECORDING_AGE", "7"))
MAX_SNAPSHOT_AGE_DAYS = int(os.environ.get("OPENBELL_MAX_SNAPSHOT_AGE", "30"))

os.makedirs(RECORDINGS_DIR, exist_ok=True)


class VideoRecorder:
    """
    Records short video clips triggered by person detection.

    Feed frames continuously via `feed_frame()`. Call `on_detection()`
    when persons are detected to start/extend recording.
    """

    def __init__(self):
        self._writer: Optional[cv2.VideoWriter] = None
        self._recording = False
        self._last_detection_time: float = 0.0
        self._recording_start: float = 0.0
        self._current_file: Optional[str] = None
        self._frame_count = 0
        self._lock = threading.Lock()
        self._frame_size: Optional[tuple] = None
        self._event_active = False
        log.info("Video recorder ready (recordings → %s)", RECORDINGS_DIR)

    def on_detection(self):
        """Legacy: Signal that a person was detected (unused — kept for compat)."""
        pass

    def start_event(self):
        """Start recording (called on doorbell press)."""
        log.info("Recording triggered by doorbell press")
        self._event_active = True

    def stop_event(self):
        """Stop recording (called on call end)."""
        log.info("Recording stopped (call ended)")
        self._event_active = False

    def feed_frame(self, frame: np.ndarray):
        """Feed a frame. If recording is active, write it to disk."""
        now = time.time()

        with self._lock:
            should_record = self._event_active

            if should_record and not self._recording:
                self._start_recording(frame)
            elif self._recording and not should_record:
                self._stop_recording()
            elif self._recording and (now - self._recording_start) > MAX_CLIP_DURATION:
                # Max duration reached — stop and potentially restart
                self._stop_recording()
                if should_record:
                    self._start_recording(frame)

            if self._recording and self._writer is not None:
                # Resize frame if dimensions changed
                h, w = frame.shape[:2]
                if self._frame_size and (w, h) != self._frame_size:
                    frame = cv2.resize(frame, self._frame_size)
                self._writer.write(frame)
                self._frame_count += 1

    def _start_recording(self, frame: np.ndarray):
        """Start a new recording."""
        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = f"event_{ts}.mp4"
        filepath = os.path.join(RECORDINGS_DIR, filename)

        h, w = frame.shape[:2]
        self._frame_size = (w, h)

        # Use mp4v codec (widely compatible)
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        self._writer = cv2.VideoWriter(filepath, fourcc, VIDEO_FPS, (w, h))

        if not self._writer.isOpened():
            log.warning("Failed to open video writer for %s", filepath)
            self._writer = None
            return

        self._recording = True
        self._recording_start = time.time()
        self._current_file = filepath
        self._frame_count = 0
        log.info("Recording started: %s (%dx%d @ %d fps)", filename, w, h, VIDEO_FPS)

    def _stop_recording(self):
        """Stop current recording."""
        if self._writer is not None:
            self._writer.release()
            self._writer = None

        duration = time.time() - self._recording_start
        if self._current_file:
            size_kb = os.path.getsize(self._current_file) / 1024
            log.info(
                "Recording saved: %s (%.1fs, %d frames, %.0f KB)",
                os.path.basename(self._current_file),
                duration, self._frame_count, size_kb,
            )

            # Delete tiny recordings (< 1 second, likely glitches)
            if self._frame_count < VIDEO_FPS:
                try:
                    os.unlink(self._current_file)
                    log.info("Deleted too-short recording (%d frames)", self._frame_count)
                except OSError:
                    pass

        self._recording = False
        self._current_file = None
        self._frame_count = 0
        self._frame_size = None

    def is_recording(self) -> bool:
        return self._recording

    def stop(self):
        """Clean shutdown."""
        with self._lock:
            if self._recording:
                self._stop_recording()


def prune_storage():
    """
    Enforce storage limits by pruning old recordings and snapshots.
    Called periodically from the main loop.
    """
    _prune_dir(RECORDINGS_DIR, MAX_RECORDINGS_MB, MAX_RECORDING_AGE_DAYS, "recording")
    _prune_dir(SNAPSHOT_DIR, MAX_SNAPSHOTS_MB, MAX_SNAPSHOT_AGE_DAYS, "snapshot")


def _prune_dir(directory: str, max_mb: int, max_age_days: int, label: str):
    """Prune a directory by size and age limits."""
    dir_path = Path(directory)
    if not dir_path.exists():
        return

    now = time.time()
    max_age_secs = max_age_days * 86400
    max_bytes = max_mb * 1024 * 1024

    # Get all media files sorted by modification time (oldest first)
    files = []
    for f in dir_path.iterdir():
        if f.is_dir():
            continue
        if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".mp4", ".avi", ".mkv"):
            try:
                st = f.stat()
                files.append((f, st.st_mtime, st.st_size))
            except OSError:
                continue

    files.sort(key=lambda x: x[1])  # oldest first

    # Phase 1: Remove files older than max age
    removed = 0
    for f, mtime, size in files[:]:
        if (now - mtime) > max_age_secs:
            try:
                f.unlink()
                files.remove((f, mtime, size))
                removed += 1
            except OSError:
                pass

    if removed:
        log.info("Pruned %d old %ss (> %d days)", removed, label, max_age_days)

    # Phase 2: Remove oldest files until under size limit
    total_size = sum(s for _, _, s in files)
    removed = 0
    while total_size > max_bytes and files:
        f, mtime, size = files.pop(0)
        try:
            f.unlink()
            total_size -= size
            removed += 1
        except OSError:
            pass

    if removed:
        log.info(
            "Pruned %d %ss to stay under %d MB (now %.1f MB)",
            removed, label, max_mb, total_size / (1024 * 1024),
        )


def get_storage_stats() -> dict:
    """Return current storage usage stats."""
    def dir_size(path):
        total = 0
        count = 0
        p = Path(path)
        if p.exists():
            for f in p.iterdir():
                if f.is_file():
                    total += f.stat().st_size
                    count += 1
        return total, count

    rec_size, rec_count = dir_size(RECORDINGS_DIR)
    snap_size, snap_count = dir_size(SNAPSHOT_DIR)

    return {
        "recordings_mb": round(rec_size / (1024 * 1024), 1),
        "recordings_count": rec_count,
        "recordings_limit_mb": MAX_RECORDINGS_MB,
        "snapshots_mb": round(snap_size / (1024 * 1024), 1),
        "snapshots_count": snap_count,
        "snapshots_limit_mb": MAX_SNAPSHOTS_MB,
    }
