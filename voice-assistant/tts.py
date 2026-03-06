"""
OpenBell Voice Assistant — Text-to-speech via Piper

British female voice (jenny_dioco), fully local ONNX inference.
Pre-generates and caches common responses at startup for instant playback.
"""

import io
import logging
import os
import struct
import wave
from pathlib import Path
from typing import Optional
from urllib.request import urlretrieve

import numpy as np

import config

log = logging.getLogger("openbell.va.tts")

# Will be set by init()
_voice = None

# HuggingFace URLs for Piper voice models
_HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"


def _model_urls(voice_name: str):
    """Return (onnx_url, json_url) for a Piper voice."""
    # voice_name like "en_GB-jenny_dioco-medium"
    parts = voice_name.split("-")
    lang_region = parts[0]  # en_GB
    lang = lang_region.split("_")[0]  # en
    name = parts[1]  # jenny_dioco
    quality = parts[2] if len(parts) > 2 else "medium"
    base = f"{_HF_BASE}/{lang}/{lang_region}/{name}/{quality}"
    return (
        f"{base}/{voice_name}.onnx",
        f"{base}/{voice_name}.onnx.json",
    )


def _ensure_model() -> tuple[str, str]:
    """Download the Piper model if not present. Returns (onnx_path, json_path)."""
    os.makedirs(config.PIPER_MODEL_DIR, exist_ok=True)
    onnx_path = os.path.join(config.PIPER_MODEL_DIR, f"{config.PIPER_VOICE}.onnx")
    json_path = onnx_path + ".json"

    if os.path.exists(onnx_path) and os.path.exists(json_path):
        return onnx_path, json_path

    onnx_url, json_url = _model_urls(config.PIPER_VOICE)
    log.info("Downloading Piper voice model: %s", config.PIPER_VOICE)

    log.info("  Downloading %s", onnx_url)
    urlretrieve(onnx_url, onnx_path)
    log.info("  Downloading %s", json_url)
    urlretrieve(json_url, json_path)
    log.info("  Voice model downloaded to %s", config.PIPER_MODEL_DIR)

    return onnx_path, json_path


def init():
    """Load the Piper voice model."""
    global _voice
    if _voice is not None:
        return

    onnx_path, json_path = _ensure_model()
    log.info("Loading Piper voice: %s", config.PIPER_VOICE)

    from piper import PiperVoice
    _voice = PiperVoice.load(onnx_path, config_path=json_path)
    log.info("Piper voice loaded (sample rate: %d)", _voice.config.sample_rate)


def synthesize(text: str) -> tuple[np.ndarray, int]:
    """
    Synthesize speech from text.

    Returns:
        (audio_int16, sample_rate) — mono int16 numpy array at the voice's
        native sample rate.
    """
    if _voice is None:
        init()

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        _voice.synthesize(text, wf, speaker_id=config.PIPER_SPEAKER_ID)

    buf.seek(0)
    with wave.open(buf, "rb") as wf:
        sr = wf.getframerate()
        frames = wf.readframes(wf.getnframes())

    audio = np.frombuffer(frames, dtype=np.int16)
    return audio, sr


def synthesize_to_48k(text: str) -> np.ndarray:
    """
    Synthesize and resample to 48 kHz mono int16 (matching the doorbell
    audio pipeline).
    """
    audio, sr = synthesize(text)
    if sr != config.SAMPLE_RATE:
        from scipy.signal import resample
        n_samples = int(len(audio) * config.SAMPLE_RATE / sr)
        audio_f = audio.astype(np.float64)
        resampled = resample(audio_f, n_samples)
        audio = np.clip(resampled, -32768, 32767).astype(np.int16)
    return audio


# ── Pre-cached responses ──

_cache: dict[str, np.ndarray] = {}


def precache_responses(texts: dict[str, str]):
    """Pre-generate and cache TTS for common response texts."""
    os.makedirs(config.TTS_CACHE_DIR, exist_ok=True)

    for key, text in texts.items():
        cache_path = os.path.join(config.TTS_CACHE_DIR, f"{key}.npy")
        if os.path.exists(cache_path):
            _cache[key] = np.load(cache_path)
            log.info("Loaded cached TTS: %s (%.1fs)",
                     key, len(_cache[key]) / config.SAMPLE_RATE)
        else:
            log.info("Generating TTS: %s ...", key)
            audio = synthesize_to_48k(text)
            np.save(cache_path, audio)
            _cache[key] = audio
            log.info("Cached TTS: %s (%.1fs)", key, len(audio) / config.SAMPLE_RATE)


def get_cached(key: str) -> Optional[np.ndarray]:
    """Get a pre-cached audio array by key."""
    return _cache.get(key)


def speak(text: str, cache_key: Optional[str] = None) -> np.ndarray:
    """
    Get 48 kHz int16 audio for text — from cache if available,
    otherwise synthesize on the fly.
    """
    if cache_key and cache_key in _cache:
        return _cache[cache_key]
    return synthesize_to_48k(text)
