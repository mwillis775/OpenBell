"""
OpenBell Voice Assistant — Speech-to-text via faster-whisper

Runs Whisper locally (CTranslate2 backend).  Uses CPU by default to
leave the GPU free for YOLOv8 in the CV server.
"""

import logging
from typing import Optional

import numpy as np
from faster_whisper import WhisperModel

import config

log = logging.getLogger("openbell.va.stt")

_model: Optional[WhisperModel] = None


def load_model():
    global _model
    if _model is not None:
        return
    log.info(
        "Loading Whisper model=%s device=%s compute=%s",
        config.WHISPER_MODEL,
        config.WHISPER_DEVICE,
        config.WHISPER_COMPUTE,
    )
    _model = WhisperModel(
        config.WHISPER_MODEL,
        device=config.WHISPER_DEVICE,
        compute_type=config.WHISPER_COMPUTE,
    )
    log.info("Whisper model loaded")


def transcribe(audio_f32: np.ndarray, sr: int = 16_000) -> str:
    """
    Transcribe a numpy float32 audio array.

    Args:
        audio_f32: 1-D float32 array, normalised to [-1, 1]
        sr: sample rate of audio_f32 (must be 16000 for Whisper)

    Returns:
        Transcription string (may be empty).
    """
    if _model is None:
        load_model()

    if len(audio_f32) < sr * 0.3:
        # Less than 300 ms — too short to transcribe
        return ""

    segments, info = _model.transcribe(
        audio_f32,
        language=config.WHISPER_LANGUAGE,
        beam_size=3,
        vad_filter=True,
        vad_parameters=dict(
            min_silence_duration_ms=600,
            speech_pad_ms=200,
        ),
    )

    text = " ".join(seg.text.strip() for seg in segments).strip()
    log.info("Transcription (%.1fs, lang=%s p=%.2f): %r",
             len(audio_f32) / sr, info.language, info.language_probability, text)
    return text
