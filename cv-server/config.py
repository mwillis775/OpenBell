"""
OpenBell CV Server — Configuration
"""

import os

# ── Rust server ──
RUST_SERVER_URL = os.environ.get("OPENBELL_SERVER_URL", "http://localhost:5000")

# ── YOLO model ──
MODEL_PATH = os.environ.get(
    "OPENBELL_YOLO_MODEL",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "yolov8s.pt"),
)

# ── Detection tuning ──
# Minimum confidence for person class (COCO class 0)
PERSON_CONF_THRESHOLD = float(os.environ.get("OPENBELL_PERSON_CONF", "0.55"))
# IOU threshold for NMS
NMS_IOU_THRESHOLD = float(os.environ.get("OPENBELL_NMS_IOU", "0.5"))
# Only detect "person" (COCO class 0)
DETECT_CLASSES = [0]
# Minimum bounding-box area as a fraction of the frame area (reject tiny ghosts)
MIN_BOX_AREA_FRACTION = float(os.environ.get("OPENBELL_MIN_BOX_AREA", "0.005"))
# Minimum height/width aspect ratio — persons are taller than wide
MIN_ASPECT_RATIO = float(os.environ.get("OPENBELL_MIN_ASPECT_RATIO", "0.8"))

# Device for inference: "cpu", "cuda", "cuda:0", etc.
DEVICE = os.environ.get("OPENBELL_DEVICE", "cuda")

# ── Frame grab ──
# How often to grab a frame for inference (seconds)
INFERENCE_INTERVAL = float(os.environ.get("OPENBELL_INFERENCE_INTERVAL", "0.5"))
# Timeout for MJPEG stream reads (seconds)
STREAM_READ_TIMEOUT = float(os.environ.get("OPENBELL_STREAM_TIMEOUT", "10.0"))
# How often to poll for stream URL when no phone connected (seconds)
STREAM_POLL_INTERVAL = float(os.environ.get("OPENBELL_STREAM_POLL", "5.0"))

# ── Web re-stream ──
WEB_STREAM_PORT = int(os.environ.get("OPENBELL_WEB_STREAM_PORT", "5100"))

# ── Presence tracking ──
# Seconds without a person detection to emit "person_left"
PERSON_LEFT_TIMEOUT = float(os.environ.get("OPENBELL_PERSON_LEFT_TIMEOUT", "5.0"))
# Minimum consecutive detections to trigger "person_detected"
MIN_CONSECUTIVE_DETECTIONS = int(os.environ.get("OPENBELL_MIN_DETECTIONS", "4"))

# ── CV enable/disable (polled from Rust server) ──
CV_STATUS_POLL_INTERVAL = float(os.environ.get("OPENBELL_CV_POLL_INTERVAL", "2.0"))

# ── Static object filter ──
# Suppress detections stuck at the same position for N consecutive frames
STATIC_IOU_THRESHOLD = float(os.environ.get("OPENBELL_STATIC_IOU", "0.75"))
STATIC_FRAME_COUNT = int(os.environ.get("OPENBELL_STATIC_FRAMES", "15"))

# ── Face recognition ──
FACE_RECOGNITION_ENABLED = os.environ.get("OPENBELL_FACE_RECOGNITION", "1") == "1"
FACE_TOLERANCE = float(os.environ.get("OPENBELL_FACE_TOLERANCE", "0.55"))
FACE_REFERENCE_DIR = os.environ.get(
    "OPENBELL_FACE_DIR",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "faces"),
)

# ── Snapshot saving ──
SNAPSHOT_DIR = os.environ.get(
    "OPENBELL_SNAPSHOT_DIR",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "snapshots"),
)
# Save annotated frame on first detection of a new presence event
SAVE_SNAPSHOTS = os.environ.get("OPENBELL_SAVE_SNAPSHOTS", "1") == "1"
# Max snapshots to keep (oldest pruned)
MAX_SNAPSHOTS = int(os.environ.get("OPENBELL_MAX_SNAPSHOTS", "500"))
# JPEG quality for saved snapshots
SNAPSHOT_QUALITY = int(os.environ.get("OPENBELL_SNAPSHOT_QUALITY", "85"))
