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
from recorder import VideoRecorder, prune_storage
from recognizer import FaceRecognizer
from snapshots import save_snapshot
from static_filter import StaticObjectFilter
from stream import FrameBuffer, MJPEGGrabber, wait_for_stream
from tracker import PresenceEvent, PresenceTracker
from web_stream import start_web_stream

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
        "identities": event.identities,
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
    static_filter = StaticObjectFilter(
        iou_threshold=config.STATIC_IOU_THRESHOLD,
        static_frames=config.STATIC_FRAME_COUNT,
    )

    # Load face recognizer (optional — needs face_recognition package)
    recognizer = None
    if config.FACE_RECOGNITION_ENABLED:
        try:
            recognizer = FaceRecognizer(
                reference_dir=config.FACE_REFERENCE_DIR,
                tolerance=config.FACE_TOLERANCE,
            )
            recognizer.load()
            if not recognizer.is_loaded:
                recognizer = None
        except (ImportError, SystemExit):
            log.warning("face_recognition not installed — person identification disabled")
        except Exception as e:
            log.warning("Face recognizer init failed: %s", e)

    # Shared frame buffer for the web viewer
    frame_buffer = FrameBuffer()

    # Video recorder for event clips
    recorder = VideoRecorder()

    start_web_stream(frame_buffer, recorder)

    # Start background thread to poll CV enabled state
    poller = threading.Thread(target=_poll_cv_enabled, daemon=True)
    poller.start()

    # Periodic storage pruning
    last_prune = time.time()
    PRUNE_INTERVAL = 300  # every 5 minutes

    reconnect_delay = 3.0  # exponential backoff starting point

    while _running:
        # Re-fetch stream URL each time (phone IP may change)
        stream_url = wait_for_stream()
        if not _running:
            break

        grabber = MJPEGGrabber(stream_url)
        if not grabber.open():
            log.warning("Could not open stream — retrying in %.0fs", reconnect_delay)
            time.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 1.5, 30.0)
            continue

        # Connected successfully — reset backoff
        reconnect_delay = 3.0
        log.info("Detection loop running on %s", stream_url)
        last_inference = 0.0
        frame_count = 0
        detection_count = 0

        try:
            for frame in grabber.frames():
                if not _running:
                    break

                # Publish every frame to the web viewer + video recorder
                frame_buffer.update(frame)
                recorder.feed_frame(frame)

                now = time.time()

                # Periodic storage pruning
                if (now - last_prune) > PRUNE_INTERVAL:
                    last_prune = now
                    threading.Thread(target=prune_storage, daemon=True).start()

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

                # Filter out static objects (gnomes, hydrants, etc.)
                detections = static_filter.filter(detections)

                if detections:
                    detection_count += 1

                # Update presence tracker
                event: Optional[PresenceEvent] = tracker.update(detections, now)

                # Save snapshots on the PC when a new person is detected
                snapshot_file = None
                if tracker.needs_snapshot and detections:
                    snapshot_file = save_snapshot(frame, detections)
                    tracker.mark_snapshot_saved()

                    # Try to identify faces (only on new presence — expensive)
                    if recognizer is not None and event:
                        try:
                            faces = recognizer.identify(frame)
                            names = [n for n, d in faces if n != "unknown"]
                            if names:
                                event.identities = names
                        except Exception as e:
                            log.debug("Face recognition error: %s", e)

                # Attach snapshot filename to event
                if event and snapshot_file:
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
            log.warning("Stream lost — reconnecting in %.0fs", reconnect_delay)
            time.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 1.5, 30.0)

    recorder.stop()
    log.info("CV server stopped.")


if __name__ == "__main__":
    # Initial storage prune on startup
    prune_storage()
    run_detection_loop()
