"""
OpenBell CV Server — MJPEG stream grabber

Connects to the phone's MJPEG camera stream and yields frames.
"""

import logging
import time
from typing import Generator, Optional

import cv2
import numpy as np
import requests

from config import RUST_SERVER_URL, STREAM_POLL_INTERVAL, STREAM_READ_TIMEOUT

log = logging.getLogger("openbell.cv.stream")


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
            return None
        # Phone streams raw sensor orientation (landscape).
        # Rotate 90° CCW to get portrait.
        frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        return frame

    def release(self):
        """Release the capture."""
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def frames(self) -> Generator[np.ndarray, None, None]:
        """Yield frames until the stream dies."""
        while True:
            frame = self.read()
            if frame is None:
                break
            yield frame

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_):
        self.release()
