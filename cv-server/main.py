#!/usr/bin/env python3
"""
OpenBell CV Server — Main entry point

Grabs frames from the phone's MJPEG camera stream, runs YOLOv8 person
detection, tracks presence, saves snapshots, and posts events to the
Rust coordination server.

Usage:
    python main.py
    # or
    OPENBELL_PERSON_CONF=0.5 python main.py
"""

import logging
import signal
import sys
import threading
import time
from typing import Optional

import requests

import config
from detector import PersonDetector
from snapshots import save_snapshot
from stream import MJPEGGrabber, wait_for_stream
from tracker import PresenceEvent, PresenceTracker

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("openbell.cv")


# ── Graceful shutdown ──
_running = True
_cv_enabled = True        # toggled via Rust server API
_cv_lock = threading.Lock()


def _shutdown(sig, _frame):
    global _running
    log.info("Received signal %s — shutting down", sig)
    _running = False


signal.signal(signal.SIGINT, _shutdown)
signal.signal(signal.SIGTERM, _shutdown)


def _poll_cv_enabled():
    """Background thread: poll Rust server for CV enabled state."""
    global _cv_enabled
    while _running:
        try:
            resp = requests.get(
                f"{config.RUST_SERVER_URL}/api/cv/status",
                timeout=2,
            )
            if resp.ok:
                data = resp.json()
                with _cv_lock:
                    _cv_enabled = data.get("enabled", True)
        except Exception:
            pass
        time.sleep(config.CV_STATUS_POLL_INTERVAL)


def post_event(event: PresenceEvent):
    """Send a detection event to the Rust server."""
    payload = {
        "event_type": event.event_type,
        "timestamp": event.timestamp,
        "person_count": event.person_count,
        "max_confidence": event.max_confidence,
        "snapshot_file": event.snapshot_file,
        "detections": event.detections,
    }
    try:
        resp = requests.post(
            f"{config.RUST_SERVER_URL}/api/cv/event",
            json=payload,
            timeout=3,
        )
        if resp.ok:
            log.info("Event posted to server: %s", event.event_type)
        else:
            log.warning("Server returned %s for CV event", resp.status_code)
    except Exception as e:
        log.warning("Failed to post CV event: %s", e)


def run_detection_loop():
    """Main detection loop."""
    log.info("=" * 52)
    log.info("  OpenBell CV Server — YOLOv8 Person Detection")
    log.info("=" * 52)
    log.info("  Model: %s", config.MODEL_PATH)
    log.info("  Confidence threshold: %.2f", config.PERSON_CONF_THRESHOLD)
    log.info("  Inference interval: %.1fs", config.INFERENCE_INTERVAL)
    log.info("  Snapshot dir: %s", config.SNAPSHOT_DIR)
    log.info("=" * 52)

    # Load model
    detector = PersonDetector()
    tracker = PresenceTracker()

    # Start background thread to poll CV enabled state
    poller = threading.Thread(target=_poll_cv_enabled, daemon=True)
    poller.start()

    while _running:
        # Wait for a stream URL from the Rust server
        stream_url = wait_for_stream()
        if not _running:
            break

        grabber = MJPEGGrabber(stream_url)
        if not grabber.open():
            log.warning("Could not open stream — retrying in 5s")
            time.sleep(5)
            continue

        log.info("Detection loop running on %s", stream_url)
        last_inference = 0.0
        frame_count = 0
        detection_count = 0

        try:
            for frame in grabber.frames():
                if not _running:
                    break

                now = time.time()
                # Throttle inference to configured interval
                if (now - last_inference) < config.INFERENCE_INTERVAL:
                    continue

                last_inference = now
                frame_count += 1

                # Skip inference when CV is disabled (GPU freed)
                with _cv_lock:
                    enabled = _cv_enabled
                if not enabled:
                    continue

                # Run YOLO
                detections = detector.detect(frame)

                if detections:
                    detection_count += 1

                # Update presence tracker
                event: Optional[PresenceEvent] = tracker.update(detections, now)

                # Save snapshot on new presence
                if tracker.needs_snapshot and config.SAVE_SNAPSHOTS and detections:
                    snapshot_file = save_snapshot(frame, detections)
                    tracker.mark_snapshot_saved()
                    if event:
                        event.snapshot_file = snapshot_file

                # Post event to Rust server if state changed
                if event:
                    post_event(event)

                # Periodic stats
                if frame_count % 100 == 0:
                    log.info(
                        "Stats: %d frames processed, %d with persons (%.0f%%)",
                        frame_count,
                        detection_count,
                        100 * detection_count / max(1, frame_count),
                    )

        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error("Detection loop error: %s", e, exc_info=True)
        finally:
            grabber.release()

        if _running:
            log.warning("Stream lost — reconnecting in 3s")
            time.sleep(3)

    log.info("CV server stopped.")


if __name__ == "__main__":
    run_detection_loop()
