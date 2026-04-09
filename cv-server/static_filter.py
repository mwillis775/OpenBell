"""
OpenBell CV Server — Static object filter

Tracks detection bounding boxes across frames to identify and suppress
static objects (e.g., garden gnomes, fire hydrants) that YOLO falsely
classifies as persons.

A detection is considered "static" when it overlaps with a tracked
position (IoU > threshold) for more than N consecutive inference cycles.
Real people move — even standing still, they shift, gesture, and fidget.
Static objects don't.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import List

from detector import Detection

log = logging.getLogger("openbell.cv.static_filter")


def _iou(a: Detection, b_box: tuple) -> float:
    """Compute IoU between a Detection and a (x1, y1, x2, y2) tuple."""
    bx1, by1, bx2, by2 = b_box
    ix1 = max(a.x1, bx1)
    iy1 = max(a.y1, by1)
    ix2 = min(a.x2, bx2)
    iy2 = min(a.y2, by2)

    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0

    inter = (ix2 - ix1) * (iy2 - iy1)
    area_a = max(0, a.x2 - a.x1) * max(0, a.y2 - a.y1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter

    return inter / union if union > 0 else 0.0


@dataclass
class TrackedBox:
    """A bounding box tracked across frames."""
    x1: float
    y1: float
    x2: float
    y2: float
    frame_count: int = 1
    last_seen: float = 0.0
    suppressed: bool = False

    @property
    def box(self) -> tuple:
        return (self.x1, self.y1, self.x2, self.y2)

    def update_position(self, det: Detection):
        """Smooth the tracked position with exponential moving average."""
        alpha = 0.3
        self.x1 = alpha * det.x1 + (1 - alpha) * self.x1
        self.y1 = alpha * det.y1 + (1 - alpha) * self.y1
        self.x2 = alpha * det.x2 + (1 - alpha) * self.x2
        self.y2 = alpha * det.y2 + (1 - alpha) * self.y2


class StaticObjectFilter:
    """
    Filters out static objects that YOLO falsely classifies as persons.

    Tracks bounding boxes across frames. When a detection stays in the
    same position for more than `static_frames` consecutive inference
    cycles, it's marked as a static object and suppressed.
    """

    def __init__(
        self,
        iou_threshold: float = 0.75,
        static_frames: int = 15,
        stale_timeout: float = 120.0,
    ):
        self.iou_threshold = iou_threshold
        self.static_frames = static_frames
        self.stale_timeout = stale_timeout
        self._tracked: List[TrackedBox] = []

    def filter(self, detections: List[Detection]) -> List[Detection]:
        """Filter detections, removing those that match static objects."""
        now = time.time()
        matched_tracks = set()
        result = []

        for det in detections:
            best_iou = 0.0
            best_idx = -1
            for i, tracked in enumerate(self._tracked):
                iou = _iou(det, tracked.box)
                if iou > best_iou:
                    best_iou = iou
                    best_idx = i

            if best_iou >= self.iou_threshold and best_idx >= 0:
                tracked = self._tracked[best_idx]
                tracked.frame_count += 1
                tracked.last_seen = now
                tracked.update_position(det)
                matched_tracks.add(best_idx)

                if tracked.frame_count >= self.static_frames:
                    if not tracked.suppressed:
                        tracked.suppressed = True
                        log.info(
                            "Static object suppressed at [%.0f,%.0f,%.0f,%.0f] "
                            "after %d frames — probably not a person",
                            tracked.x1, tracked.y1, tracked.x2, tracked.y2,
                            tracked.frame_count,
                        )
                    continue  # suppress this detection
            else:
                self._tracked.append(TrackedBox(
                    x1=det.x1, y1=det.y1, x2=det.x2, y2=det.y2,
                    frame_count=1, last_seen=now,
                ))

            result.append(det)

        # Prune stale tracks (not seen recently)
        self._tracked = [
            t for t in self._tracked
            if now - t.last_seen < self.stale_timeout
        ]

        if len(detections) != len(result):
            log.debug(
                "Static filter: %d → %d detections (suppressed %d static objects)",
                len(detections), len(result), len(detections) - len(result),
            )

        return result

    @property
    def static_count(self) -> int:
        """Number of currently suppressed static objects."""
        return sum(1 for t in self._tracked if t.suppressed)
