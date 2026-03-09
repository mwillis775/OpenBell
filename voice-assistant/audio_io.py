"""
OpenBell Voice Assistant — UDP audio I/O

Receives phone mic PCM from the Rust server (port 5004) and sends
TTS PCM back (port 5005→Rust forwards to phone).

Audio format: [4-byte BE seq][PCM s16le] at 48 kHz mono.
"""

import logging
import socket
import struct
import threading
import time
from collections import deque
from typing import Optional

import numpy as np
from scipy.signal import resample

import config

log = logging.getLogger("openbell.va.audio_io")


class AudioReceiver:
    """
    Receives phone mic audio from the Rust server on UDP 5004.

    Accumulates PCM into a ring buffer.  The caller can snapshot the
    buffer for Whisper transcription.
    """

    def __init__(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("0.0.0.0", config.ASSISTANT_LISTEN_PORT))
        self._sock.settimeout(0.5)

        # Ring buffer: last N seconds of 48 kHz int16 samples
        self._max_samples = config.SAMPLE_RATE * 30  # 30 seconds
        self._buffer = deque(maxlen=self._max_samples)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._total_packets = 0

    def start(self):
        """Start the receiver thread."""
        self._running = True
        self._thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._thread.start()
        log.info("Audio receiver started on UDP %d", config.ASSISTANT_LISTEN_PORT)

    def stop(self):
        """Stop the receiver thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        log.info("Audio receiver stopped (%d packets received)", self._total_packets)

    def clear(self):
        """Clear the audio buffer."""
        self._buffer.clear()

    def get_audio_16k(self) -> np.ndarray:
        """
        Snapshot the current buffer and return as 16 kHz float32
        (format Whisper expects).
        """
        if len(self._buffer) == 0:
            return np.array([], dtype=np.float32)

        samples = np.array(list(self._buffer), dtype=np.int16)
        # Convert to float32 normalised [-1, 1]
        audio_f32 = samples.astype(np.float32) / 32768.0
        # Resample 48k → 16k
        n_out = int(len(audio_f32) * 16_000 / config.SAMPLE_RATE)
        if n_out < 1:
            return np.array([], dtype=np.float32)
        resampled = resample(audio_f32.astype(np.float64), n_out).astype(np.float32)
        return resampled

    def rms(self, last_secs: float = 0.5) -> float:
        """Compute RMS of the most recent `last_secs` of audio."""
        n = int(config.SAMPLE_RATE * last_secs)
        if len(self._buffer) < n:
            n = len(self._buffer)
        if n == 0:
            return 0.0
        samples = np.array(list(self._buffer)[-n:], dtype=np.float32) / 32768.0
        return float(np.sqrt(np.mean(samples ** 2)))

    def _recv_loop(self):
        while self._running:
            try:
                data, _ = self._sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break

            if len(data) <= config.SEQ_HEADER_SIZE:
                continue

            pcm = data[config.SEQ_HEADER_SIZE:]
            samples = np.frombuffer(pcm, dtype=np.int16)
            self._buffer.extend(samples.tolist())
            self._total_packets += 1


class AudioSender:
    """
    Sends TTS audio to the Rust server on UDP 5005, which forwards
    it to the phone.

    Packetises 48 kHz int16 PCM into the same [4B seq | PCM] format.
    """

    def __init__(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._seq: int = 0
        self._target = (config.RUST_SEND_HOST, config.ASSISTANT_SEND_PORT)

    def send_audio(self, audio_48k: np.ndarray, realtime: bool = True):
        """
        Send an int16 48 kHz audio array to the phone via the Rust server.

        If realtime=True, pace the sending to match real-time playback
        (prevents flooding the phone's jitter buffer).
        """
        pcm = audio_48k.tobytes()
        chunk_size = config.PACKET_PCM_BYTES  # 960 bytes = 10ms

        total = len(pcm)
        offset = 0
        pkt_count = 0
        start = time.monotonic()

        while offset < total:
            end = min(offset + chunk_size, total)
            chunk = pcm[offset:end]

            # Pad last chunk if short
            if len(chunk) < chunk_size:
                chunk = chunk + b'\x00' * (chunk_size - len(chunk))

            header = struct.pack(">I", self._seq & 0xFFFFFFFF)
            self._sock.sendto(header + chunk, self._target)

            self._seq += 1
            pkt_count += 1
            offset = end

            if realtime:
                # 10 ms per packet — pace to real time
                expected = pkt_count * 0.01
                elapsed = time.monotonic() - start
                if expected > elapsed:
                    time.sleep(expected - elapsed)

        log.info("Sent %d audio packets (%.1fs) to phone",
                 pkt_count, pkt_count * 0.01)

    def close(self):
        self._sock.close()
