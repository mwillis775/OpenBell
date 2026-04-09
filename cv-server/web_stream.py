"""
OpenBell CV Server — MJPEG web re-streamer

Serves the live camera feed as an MJPEG stream over HTTP so any device
on the local network can view it from a browser without an app.

    http://<server-ip>:5100/        → viewer page
    http://<server-ip>:5100/stream  → raw MJPEG stream
"""

import logging
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import cv2

from config import WEB_STREAM_PORT
from stream import FrameBuffer

log = logging.getLogger("openbell.cv.web")

BOUNDARY = b"--openbell-frame"
JPEG_QUALITY = 60

_HTML_PAGE = b"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenBell Live</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #111; display: flex; flex-direction: column;
         align-items: center; justify-content: center; min-height: 100vh;
         font-family: system-ui, sans-serif; color: #eee; }
  h1 { margin-bottom: 12px; font-size: 1.4rem; letter-spacing: 1px; }
  img { max-width: 100%%; max-height: 85vh; border-radius: 8px;
        box-shadow: 0 0 30px rgba(0,0,0,.6); }
  .hint { margin-top: 10px; font-size: .8rem; color: #666; }
</style>
</head>
<body>
<h1>OpenBell Live</h1>
<img src="/stream" alt="Live feed">
<p class="hint">MJPEG stream &mdash; open <code>/stream</code> directly for raw feed</p>
</body>
</html>
"""


class _StreamHandler(BaseHTTPRequestHandler):
    frame_buffer: FrameBuffer  # set on the class before starting

    def do_GET(self):
        if self.path == "/":
            self._serve_page()
        elif self.path == "/stream":
            self._serve_mjpeg()
        else:
            self.send_error(404)

    def _serve_page(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_HTML_PAGE)))
        self.end_headers()
        self.wfile.write(_HTML_PAGE)

    def _serve_mjpeg(self):
        self.send_response(200)
        self.send_header(
            "Content-Type",
            f"multipart/x-mixed-replace; boundary={BOUNDARY.decode()}",
        )
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        try:
            while True:
                frame = self.frame_buffer.get()
                if frame is None:
                    time.sleep(0.1)
                    continue
                ok, jpeg = cv2.imencode(
                    ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
                )
                if not ok:
                    continue
                data = jpeg.tobytes()
                self.wfile.write(BOUNDARY + b"\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(data)}\r\n\r\n".encode())
                self.wfile.write(data)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
                time.sleep(0.066)  # ~15 fps cap for web viewers
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # client disconnected

    def log_message(self, fmt, *args):
        # Suppress per-request access logs
        pass


def start_web_stream(frame_buffer: FrameBuffer) -> ThreadingHTTPServer:
    """Start the MJPEG web server in a daemon thread. Returns the server."""
    _StreamHandler.frame_buffer = frame_buffer
    server = ThreadingHTTPServer(("0.0.0.0", WEB_STREAM_PORT), _StreamHandler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # Resolve a routable IP for the log message
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = "localhost"

    log.info("Web viewer at http://%s:%d/", local_ip, WEB_STREAM_PORT)
    return server
