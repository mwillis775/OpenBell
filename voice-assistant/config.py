"""
OpenBell Voice Assistant — Configuration
"""

import os

# ── Rust server ──
RUST_SERVER_WS = os.environ.get("OPENBELL_WS_URL", "ws://localhost:5000/ws")
RUST_SERVER_HTTP = os.environ.get("OPENBELL_SERVER_URL", "http://localhost:5000")

# ── Audio format (must match Rust server) ──
SAMPLE_RATE = 48_000
CHANNELS = 1
BITS_PER_SAMPLE = 16
BYTES_PER_SAMPLE = BITS_PER_SAMPLE // 8
PACKET_SAMPLES = 480          # 10 ms at 48 kHz
PACKET_PCM_BYTES = PACKET_SAMPLES * BYTES_PER_SAMPLE  # 960
SEQ_HEADER_SIZE = 4

# ── UDP ports ──
ASSISTANT_LISTEN_PORT = int(os.environ.get("OPENBELL_ASSISTANT_LISTEN_PORT", "5004"))  # Receive phone mic audio from Rust server
ASSISTANT_SEND_PORT = int(os.environ.get("OPENBELL_ASSISTANT_SEND_PORT", "5005"))    # Send TTS audio to Rust server
RUST_SEND_HOST = os.environ.get("OPENBELL_RUST_HOST", "127.0.0.1")
RUST_SEND_TARGET = (RUST_SEND_HOST, ASSISTANT_SEND_PORT)   # Where Rust listens for our TTS audio
# Note: we send TO the Rust server's port 5005 listener.  We actually send
# to the phone via the Rust server's outgoing socket, so we send TO localhost:5005.
# The Rust server forwards from 5005 → phone.

# ── Whisper STT ──
WHISPER_MODEL = os.environ.get("OPENBELL_WHISPER_MODEL", "base")
WHISPER_DEVICE = os.environ.get("OPENBELL_WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE = os.environ.get("OPENBELL_WHISPER_COMPUTE", "int8")
WHISPER_LANGUAGE = "en"

# ── Piper TTS ──
PIPER_MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")
PIPER_VOICE = os.environ.get("OPENBELL_PIPER_VOICE", "en_GB-jenny_dioco-medium")
PIPER_SPEAKER_ID = None  # None = default speaker
TTS_CACHE_DIR = os.environ.get(
    "OPENBELL_TTS_CACHE",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "tts_cache"),
)

# ── Conversation ──
AUTO_ANSWER_TIMEOUT = int(os.environ.get("OPENBELL_AUTO_ANSWER_SECS", "5"))
LISTEN_TIMEOUT = float(os.environ.get("OPENBELL_LISTEN_TIMEOUT", "8.0"))
SILENCE_THRESHOLD = float(os.environ.get("OPENBELL_SILENCE_THRESHOLD", "0.02"))
SILENCE_DURATION = float(os.environ.get("OPENBELL_SILENCE_DURATION", "2.0"))
MAX_TURNS = int(os.environ.get("OPENBELL_MAX_TURNS", "3"))
MAX_SESSION_SECS = float(os.environ.get("OPENBELL_MAX_SESSION", "90.0"))
