"""
OpenBell CV Server — MJPEG web re-streamer

Serves the live camera feed as an MJPEG stream over HTTP so any device
on the local network can view it from a browser without an app.

    http://<server-ip>:5100/        → viewer page
    http://<server-ip>:5100/stream  → raw MJPEG stream
"""

import json
import logging
import os
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import unquote

import cv2

from config import SNAPSHOT_DIR, WEB_STREAM_PORT
from recorder import RECORDINGS_DIR, VideoRecorder, get_storage_stats
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
    recorder: VideoRecorder = None  # set on the class before starting

    def do_GET(self):
        if self.path == "/":
            self._serve_page()
        elif self.path == "/stream":
            self._serve_mjpeg()
        elif self.path == "/media/list":
            self._serve_media_list()
        elif self.path == "/snapshots/list":
            self._serve_snapshot_list()
        elif self.path.startswith("/snapshots/file/"):
            self._serve_snapshot_file(self.path[len("/snapshots/file/"):])
        elif self.path == "/recordings/list":
            self._serve_recordings_list()
        elif self.path.startswith("/recordings/file/"):
            self._serve_recording_file(self.path[len("/recordings/file/"):])
        elif self.path == "/storage/stats":
            self._serve_storage_stats()
        else:
            self.send_error(404)

    def do_DELETE(self):
        if self.path.startswith("/snapshots/file/"):
            self._delete_snapshot(self.path[len("/snapshots/file/"):])
        elif self.path.startswith("/recordings/file/"):
            self._delete_recording(self.path[len("/recordings/file/"):])
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/recording/start":
            self._recording_start()
        elif self.path == "/recording/stop":
            self._recording_stop()
        else:
            self.send_error(404)

    def _recording_start(self):
        if self.recorder:
            self.recorder.start_event()
        self._json_response({"status": "recording"})

    def _recording_stop(self):
        if self.recorder:
            self.recorder.stop_event()
        self._json_response({"status": "stopped"})

    def _json_response(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self._add_cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._add_cors()
        self.end_headers()

    def _add_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

    def _serve_media_list(self):
        """Return combined JSON list of all snapshots and recordings."""
        items = []
        snap_dir = Path(SNAPSHOT_DIR)
        if snap_dir.is_dir():
            for f in sorted(snap_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                if f.suffix.lower() in (".jpg", ".jpeg", ".png"):
                    st = f.stat()
                    items.append({
                        "filename": f.name,
                        "type": "snapshot",
                        "size": st.st_size,
                        "timestamp": int(st.st_mtime * 1000),
                    })
        rec_dir = Path(RECORDINGS_DIR)
        if rec_dir.is_dir():
            for f in sorted(rec_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                if f.suffix.lower() in (".mp4", ".avi", ".mkv"):
                    st = f.stat()
                    items.append({
                        "filename": f.name,
                        "type": "recording",
                        "size": st.st_size,
                        "timestamp": int(st.st_mtime * 1000),
                    })
        # Sort combined list newest-first
        items.sort(key=lambda x: x["timestamp"], reverse=True)
        stats = get_storage_stats()
        body = json.dumps({"media": items, "storage": stats}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._add_cors()
        self.end_headers()
        self.wfile.write(body)

    def _delete_snapshot(self, filename: str):
        """Delete a snapshot by filename."""
        safe_name = os.path.basename(unquote(filename))
        filepath = Path(SNAPSHOT_DIR) / safe_name
        if not filepath.is_file() or filepath.suffix.lower() not in (".jpg", ".jpeg", ".png"):
            self.send_error(404, "Snapshot not found")
            return
        try:
            filepath.unlink()
            log.info("Deleted snapshot: %s", safe_name)
            body = json.dumps({"deleted": safe_name}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self._add_cors()
            self.end_headers()
            self.wfile.write(body)
        except OSError as e:
            log.warning("Failed to delete snapshot %s: %s", safe_name, e)
            self.send_error(500, "Failed to delete")

    def _delete_recording(self, filename: str):
        """Delete a recording by filename."""
        safe_name = os.path.basename(unquote(filename))
        filepath = Path(RECORDINGS_DIR) / safe_name
        if not filepath.is_file() or filepath.suffix.lower() not in (".mp4", ".avi", ".mkv"):
            self.send_error(404, "Recording not found")
            return
        try:
            filepath.unlink()
            log.info("Deleted recording: %s", safe_name)
            body = json.dumps({"deleted": safe_name}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self._add_cors()
            self.end_headers()
            self.wfile.write(body)
        except OSError as e:
            log.warning("Failed to delete recording %s: %s", safe_name, e)
            self.send_error(500, "Failed to delete")

    def _serve_snapshot_list(self):
        """Return JSON list of snapshot files in the snapshots directory."""
        snap_dir = Path(SNAPSHOT_DIR)
        items = []
        if snap_dir.is_dir():
            for f in sorted(snap_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                if f.suffix.lower() in (".jpg", ".jpeg", ".png"):
                    st = f.stat()
                    items.append({
                        "filename": f.name,
                        "type": "snapshot",
                        "size": st.st_size,
                        "timestamp": int(st.st_mtime * 1000),
                    })
        body = json.dumps({"media": items}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._add_cors()
        self.end_headers()
        self.wfile.write(body)

    def _serve_snapshot_file(self, filename: str):
        """Serve a single snapshot image by filename."""
        # URL-decode then sanitize: prevent path traversal
        safe_name = os.path.basename(unquote(filename))
        filepath = Path(SNAPSHOT_DIR) / safe_name
        if not filepath.is_file() or not filepath.suffix.lower() in (".jpg", ".jpeg", ".png"):
            self.send_error(404, "Snapshot not found")
            return
        data = filepath.read_bytes()
        ctype = "image/jpeg" if filepath.suffix.lower() in (".jpg", ".jpeg") else "image/png"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self._add_cors()
        self.end_headers()
        self.wfile.write(data)

    def _serve_recordings_list(self):
        """Return JSON list of recording files."""
        rec_dir = Path(RECORDINGS_DIR)
        items = []
        if rec_dir.is_dir():
            for f in sorted(rec_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                if f.suffix.lower() in (".mp4", ".avi", ".mkv"):
                    st = f.stat()
                    items.append({
                        "filename": f.name,
                        "type": "recording",
                        "size": st.st_size,
                        "timestamp": int(st.st_mtime * 1000),
                    })
        body = json.dumps({"media": items}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._add_cors()
        self.end_headers()
        self.wfile.write(body)

    def _serve_recording_file(self, filename: str):
        """Serve a recording video by filename."""
        safe_name = os.path.basename(unquote(filename))
        filepath = Path(RECORDINGS_DIR) / safe_name
        if not filepath.is_file() or filepath.suffix.lower() not in (".mp4", ".avi", ".mkv"):
            self.send_error(404, "Recording not found")
            return
        data = filepath.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(data)))
        self._add_cors()
        self.end_headers()
        self.wfile.write(data)

    def _serve_storage_stats(self):
        """Return storage usage statistics."""
        stats = get_storage_stats()
        body = json.dumps(stats).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._add_cors()
        self.end_headers()
        self.wfile.write(body)

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


def start_web_stream(frame_buffer: FrameBuffer, recorder: VideoRecorder = None) -> ThreadingHTTPServer:
    """Start the MJPEG web server in a daemon thread. Returns the server."""
    _StreamHandler.frame_buffer = frame_buffer
    _StreamHandler.recorder = recorder
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
