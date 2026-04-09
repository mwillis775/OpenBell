"""
OpenBell CV Server — MJPEG stream grabber

Connects to the phone's MJPEG camera stream and yields frames.
"""

import logging
import threading
import time
from typing import Generator, Optional

import cv2
import numpy as np
import requests

from config import RUST_SERVER_URL, STREAM_POLL_INTERVAL, STREAM_READ_TIMEOUT

log = logging.getLogger("openbell.cv.stream")

# Maximum consecutive read failures before declaring the stream dead
MAX_CONSECUTIVE_FAILURES = 30


class FrameBuffer:
    """Thread-safe container for the latest camera frame."""

    def __init__(self):
        self._frame: Optional[np.ndarray] = None
        self._lock = threading.Lock()

    def update(self, frame: np.ndarray):
        with self._lock:
            self._frame = frame

    def get(self) -> Optional[np.ndarray]:
        with self._lock:
            return self._frame


def get_stream_url() -> Optional[str]:
    """Fetch the current camera stream URL from the Rust server."""
    try:
        resp = requests.get(f"{RUST_SERVER_URL}/api/status", timeout=3)
        resp.raise_for_status()
        data = resp.json()
        url = data.get("stream_url")
        if url:
            return url
    except Exception as e:
        log.debug("Could not fetch stream URL: %s", e)
    return None


def wait_for_stream() -> str:
    """Block until a stream URL is available from the Rust server."""
    log.info("Waiting for phone camera stream...")
    while True:
        url = get_stream_url()
        if url:
            log.info("Got stream URL: %s", url)
            return url
        time.sleep(STREAM_POLL_INTERVAL)


class MJPEGGrabber:
    """
    Connects to an MJPEG stream (from the phone's camera server) and
    yields decoded BGR frames.
    """

    def __init__(self, url: str):
        self.url = url
        self._cap: Optional[cv2.VideoCapture] = None
        self._consecutive_failures = 0

    def open(self) -> bool:
        """Open the MJPEG stream. Returns True on success."""
        if self._cap is not None:
            self._cap.release()

        log.info("Opening MJPEG stream: %s", self.url)
        # Open with FFMPEG backend and minimal buffering for lowest latency
        self._cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)

        if not self._cap.isOpened():
            log.warning("Failed to open stream: %s", self.url)
            self._cap = None
            return False

        # Minimise internal buffering — we always want the latest frame
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        # Short read timeout so we detect a dead stream quickly
        self._cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
        self._cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)
        log.info("Stream opened successfully")
        return True

    def read(self) -> Optional[np.ndarray]:
        """Read a single frame. Returns BGR ndarray or None on failure."""
        if self._cap is None:
            return None
        ret, frame = self._cap.read()
        if not ret or frame is None:
            self._consecutive_failures += 1
            return None
        self._consecutive_failures = 0
        # Phone streams raw sensor orientation (landscape).
        # Rotate 90° CCW to get portrait.
        frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        return frame

    @property
    def is_dead(self) -> bool:
        """True if the stream has had too many consecutive read failures."""
        return self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES

    def release(self):
        """Release the capture."""
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def frames(self) -> Generator[np.ndarray, None, None]:
        """Yield frames, tolerating transient failures."""
        while True:
            frame = self.read()
            if frame is None:
                if self.is_dead:
                    log.warning(
                        "Stream dead after %d consecutive failures",
                        MAX_CONSECUTIVE_FAILURES,
                    )
                    break
                # Brief pause before retrying a transient failure
                time.sleep(0.05)
                continue
            yield frame

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_):
        self.release()
