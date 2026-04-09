"""
OpenBell CV Server — Snapshot manager

Saves annotated detection frames to disk and prunes old ones.
Includes a cooldown to avoid excessive snapshots of the same scene.
"""

import logging
import os
import time
from pathlib import Path
from typing import List

import cv2
import numpy as np

from config import MAX_SNAPSHOTS, SNAPSHOT_DIR, SNAPSHOT_QUALITY
from detector import Detection

log = logging.getLogger("openbell.cv.snapshots")

# Ensure snapshot directory exists
os.makedirs(SNAPSHOT_DIR, exist_ok=True)

# ── Snapshot cooldown ──
# Minimum seconds between snapshots (avoids 10 snapshots of stepdad mowing)
SNAPSHOT_COOLDOWN_SECS = float(os.environ.get("OPENBELL_SNAPSHOT_COOLDOWN", "300"))  # 5 min default
_last_snapshot_time: float = 0.0


def draw_detections(frame: np.ndarray, detections: List[Detection]) -> np.ndarray:
    """Draw bounding boxes on a copy of the frame."""
    annotated = frame.copy()
    for det in detections:
        x1, y1, x2, y2 = int(det.x1), int(det.y1), int(det.x2), int(det.y2)
        # Green box
        cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
        # Label
        label = f"person {det.confidence:.0%}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
        cv2.rectangle(annotated, (x1, y1 - th - 8), (x1 + tw + 4, y1), (0, 255, 0), -1)
        cv2.putText(annotated, label, (x1 + 2, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1)
    return annotated


def save_snapshot(frame: np.ndarray, detections: List[Detection]) -> str:
    """Save an annotated snapshot, return the filename. Returns '' if cooldown active."""
    global _last_snapshot_time

    now = time.time()
    elapsed = now - _last_snapshot_time
    if _last_snapshot_time > 0 and elapsed < SNAPSHOT_COOLDOWN_SECS:
        log.debug("Snapshot cooldown active (%.0fs remaining)",
                  SNAPSHOT_COOLDOWN_SECS - elapsed)
        return ""

    annotated = draw_detections(frame, detections)
    ts = time.strftime("%Y%m%d_%H%M%S")
    filename = f"person_{ts}.jpg"
    filepath = os.path.join(SNAPSHOT_DIR, filename)

    cv2.imwrite(filepath, annotated, [cv2.IMWRITE_JPEG_QUALITY, SNAPSHOT_QUALITY])
    _last_snapshot_time = now
    log.info("Snapshot saved: %s (%d detections, next in %.0fs)",
             filename, len(detections), SNAPSHOT_COOLDOWN_SECS)

    prune_old_snapshots()
    return filename


def prune_old_snapshots():
    """Remove oldest snapshots if over the limit."""
    try:
        files = sorted(
            Path(SNAPSHOT_DIR).glob("person_*.jpg"),
            key=lambda p: p.stat().st_mtime,
        )
        while len(files) > MAX_SNAPSHOTS:
            oldest = files.pop(0)
            oldest.unlink()
            log.debug("Pruned old snapshot: %s", oldest.name)
    except Exception as e:
        log.warning("Snapshot prune error: %s", e)
