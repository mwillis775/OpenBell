"""
OpenBell CV Server — Presence tracker

Tracks person presence state across frames to emit clean
person_detected / person_left events with debouncing.
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from detector import Detection
from config import MIN_CONSECUTIVE_DETECTIONS, PERSON_LEFT_TIMEOUT

log = logging.getLogger("openbell.cv.tracker")


class PresenceState(Enum):
    ABSENT = "absent"
    DETECTED = "detected"
    PRESENT = "present"


@dataclass
class PresenceEvent:
    """An event to send to the Rust server."""
    event_type: str  # "person_detected" or "person_left"
    timestamp: float
    person_count: int = 0
    max_confidence: float = 0.0
    snapshot_file: Optional[str] = None
    detections: List[dict] = field(default_factory=list)


class PresenceTracker:
    """
    Debounced person presence tracker.

    - Requires MIN_CONSECUTIVE_DETECTIONS frames with persons to trigger
      a `person_detected` event.
    - Requires PERSON_LEFT_TIMEOUT seconds without any detection to trigger
      a `person_left` event.
    """

    def __init__(self):
        self.state = PresenceState.ABSENT
        self.consecutive_detections = 0
        self.last_detection_time: float = 0.0
        self.presence_start_time: float = 0.0
        self._snapshot_saved = False

    def update(self, detections: List[Detection], now: Optional[float] = None) -> Optional[PresenceEvent]:
        """
        Feed new detection results. Returns a PresenceEvent if a state
        transition occurred, otherwise None.
        """
        now = now or time.time()
        has_person = len(detections) > 0

        if has_person:
            self.last_detection_time = now
            self.consecutive_detections += 1
        else:
            self.consecutive_detections = 0

        # ── State machine ──
        if self.state == PresenceState.ABSENT:
            if has_person and self.consecutive_detections >= MIN_CONSECUTIVE_DETECTIONS:
                self.state = PresenceState.PRESENT
                self.presence_start_time = now
                self._snapshot_saved = False
                log.info(
                    "Person DETECTED (%d persons, max conf %.2f)",
                    len(detections),
                    max(d.confidence for d in detections),
                )
                return PresenceEvent(
                    event_type="person_detected",
                    timestamp=now,
                    person_count=len(detections),
                    max_confidence=max(d.confidence for d in detections),
                    detections=[d.to_dict() for d in detections],
                )

        elif self.state == PresenceState.PRESENT:
            if not has_person and (now - self.last_detection_time) >= PERSON_LEFT_TIMEOUT:
                self.state = PresenceState.ABSENT
                duration = now - self.presence_start_time
                log.info("Person LEFT (was present for %.1fs)", duration)
                return PresenceEvent(
                    event_type="person_left",
                    timestamp=now,
                    person_count=0,
                    max_confidence=0.0,
                )

        return None

    @property
    def needs_snapshot(self) -> bool:
        """True if we just transitioned to PRESENT and haven't saved a snapshot yet."""
        if self.state == PresenceState.PRESENT and not self._snapshot_saved:
            return True
        return False

    def mark_snapshot_saved(self):
        self._snapshot_saved = True
