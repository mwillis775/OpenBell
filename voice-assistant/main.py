#!/usr/bin/env python3
"""
OpenBell Voice Assistant — Main entry point

Connects to the Rust server via WebSocket and waits for an
``assistant_activate`` message (sent when nobody answers the doorbell
within the auto-answer timeout).

On activation:
  1.  Plays a British-accented greeting over the phone speaker.
  2.  Listens to the visitor via Whisper STT.
  3.  If an LLM model is configured, engages in natural conversation.
      Otherwise falls back to keyword-based intent classification.
  4.  Always refuses to help police (keyword safety net + LLM system prompt).
  5.  Plays a farewell, then ends the session.

All inference (Whisper, Piper TTS, optional LLM) runs locally.
"""

import asyncio
import json
import logging
import signal
import sys
import time

import numpy as np

import chat
import config
import intent as intent_clf
import responses
import stt
import tts
from audio_io import AudioReceiver, AudioSender

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("openbell.va")

_running = True


def _shutdown(sig, _frame):
    global _running
    log.info("Received signal %s — shutting down", sig)
    _running = False


signal.signal(signal.SIGINT, _shutdown)
signal.signal(signal.SIGTERM, _shutdown)

# ── Audio I/O singletons ──
receiver = AudioReceiver()
sender = AudioSender()


# ═══════════════════════════════════════════════════════════════
#  Conversation engine
# ═══════════════════════════════════════════════════════════════

def run_conversation():
    """
    Blocking conversation loop.  Called when assistant is activated.

    Flow:
      greeting → listen → [police check] → respond (LLM or keyword) → ...
      Repeats up to MAX_TURNS, then farewell.
    """
    log.info("=== Conversation started ===")
    session_start = time.time()
    conversation_history = []  # [(role, text), ...]

    receiver.clear()
    receiver.start()

    try:
        # 1. Play greeting
        greeting_audio = tts.speak(responses.GREETING, cache_key="greeting")
        sender.send_audio(greeting_audio, realtime=True)
        log.info("Greeting sent (%.1fs)", len(greeting_audio) / config.SAMPLE_RATE)
        conversation_history.append(("assistant", responses.GREETING))

        # 2. Listen + respond loop (max MAX_TURNS)
        for turn in range(config.MAX_TURNS):
            if time.time() - session_start > config.MAX_SESSION_SECS:
                log.info("Session time limit reached")
                break

            # Clear buffer and wait for visitor to speak
            receiver.clear()
            transcript = listen_for_speech()

            if not transcript:
                # No speech — give a second chance on turn 0, end otherwise
                log.info("No speech detected (turn %d)", turn + 1)
                if turn == 0:
                    # Second chance: prompt and listen once more
                    nudge = "Hello? Are you still there?"
                    audio = tts.speak(nudge)
                    sender.send_audio(audio, realtime=True)
                    receiver.clear()
                    transcript = listen_for_speech()
                    if not transcript:
                        audio = tts.speak(responses.RESPONSES[responses.SILENCE],
                                          cache_key=responses.SILENCE)
                        sender.send_audio(audio, realtime=True)
                        break
                    # Fall through to process the transcript
                else:
                    break

            # 3. Check for police keywords FIRST (safety net — always runs)
            detected_intent = intent_clf.classify(transcript)
            log.info("Turn %d — intent=%s transcript=%r", turn + 1, detected_intent, transcript)

            if detected_intent == responses.POLICE:
                # Stone wall — canned response, no LLM, end conversation
                audio = tts.speak(responses.RESPONSES[responses.POLICE],
                                  cache_key=responses.POLICE)
                sender.send_audio(audio, realtime=True)
                log.info("Police detected — stone-walled, ending conversation")
                break

            # 4. Generate response — LLM chat or keyword fallback
            if chat.is_available():
                conversation_history.append(("visitor", transcript))
                response_text = chat.generate_response(
                    conversation_history[:-1],
                    transcript,
                )
                if response_text:
                    conversation_history.append(("assistant", response_text))
                    audio = tts.speak(response_text)
                    sender.send_audio(audio, realtime=True)
                    log.info("Chat response (%.1fs): %r",
                             len(audio) / config.SAMPLE_RATE, response_text)
                    continue  # keep the conversation going
                # LLM returned empty — fall through to keyword response

            # Keyword-based fallback
            unknown_count = sum(1 for r, _ in conversation_history if r == "unknown")
            if detected_intent in responses.RESPONSES and detected_intent != responses.UNKNOWN:
                resp_text = responses.RESPONSES[detected_intent]
                audio = tts.speak(resp_text, cache_key=detected_intent)
            elif unknown_count >= 2:
                # Repeated unknowns — wrap up gracefully
                resp_text = responses.FOLLOWUP
                audio = tts.speak(resp_text, cache_key="followup")
                sender.send_audio(audio, realtime=True)
                log.info("Too many unknowns — wrapping up")
                break
            else:
                conversation_history.append(("unknown", transcript))
                resp_text = ("Sorry, I didn't quite catch that. "
                             "Could you let me know if you're delivering something, "
                             "or is there something else I can help with?")
                audio = tts.speak(resp_text)

            sender.send_audio(audio, realtime=True)
            log.info("Response sent: %s (%.1fs)",
                     detected_intent, len(audio) / config.SAMPLE_RATE)

            # For clear intents — one response is enough
            if detected_intent in (responses.DELIVERY, responses.BUSINESS,
                                   responses.PERSONAL):
                break

        # 5. Farewell
        farewell = tts.speak(responses.FAREWELL, cache_key="farewell")
        sender.send_audio(farewell, realtime=True)

    finally:
        receiver.stop()

    elapsed = time.time() - session_start
    log.info("=== Conversation ended (%.1fs) ===", elapsed)


def listen_for_speech() -> str:
    """
    Wait for the visitor to speak, then transcribe.

    Waits up to LISTEN_TIMEOUT seconds.  Uses RMS energy to detect
    when speech starts and when it ends (SILENCE_DURATION of quiet).
    """
    log.info("Listening for speech (timeout=%.1fs)...", config.LISTEN_TIMEOUT)

    start = time.time()
    speech_started = False
    last_speech_time = start

    while time.time() - start < config.LISTEN_TIMEOUT:
        time.sleep(0.1)  # Check every 100 ms

        rms = receiver.rms(last_secs=0.3)

        if rms > config.SILENCE_THRESHOLD:
            if not speech_started:
                log.info("Speech detected (rms=%.4f)", rms)
                speech_started = True
            last_speech_time = time.time()
        elif speech_started:
            # Speech was happening but now it's quiet
            silence_elapsed = time.time() - last_speech_time
            if silence_elapsed >= config.SILENCE_DURATION:
                log.info("End of speech (%.1fs silence)", silence_elapsed)
                break

    if not speech_started:
        log.info("No speech detected within timeout")
        return ""

    # Grab accumulated audio and transcribe
    audio_16k = receiver.get_audio_16k()
    if len(audio_16k) < 16_000 * 0.3:
        return ""

    return stt.transcribe(audio_16k)


# ═══════════════════════════════════════════════════════════════
#  WebSocket client — connects to Rust server
# ═══════════════════════════════════════════════════════════════

async def ws_client():
    """
    Connect to the Rust server's WebSocket and wait for
    ``assistant_activate`` messages.
    """
    import websockets

    while _running:
        try:
            log.info("Connecting to Rust server: %s", config.RUST_SERVER_WS)
            async with websockets.connect(config.RUST_SERVER_WS) as ws:
                log.info("WebSocket connected")
                async for raw in ws:
                    if not _running:
                        break
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if msg.get("type") == "assistant_activate":
                        log.info("Received assistant_activate — starting conversation")
                        # Run blocking conversation in a thread
                        await asyncio.get_event_loop().run_in_executor(
                            None, run_conversation
                        )
                        # Tell server we're done
                        await ws.send(json.dumps({
                            "type": "end_call",
                        }))
                        log.info("Sent end_call after conversation")

        except Exception as e:
            if _running:
                log.warning("WebSocket error: %s — reconnecting in 3s", e)
                await asyncio.sleep(3)


# ═══════════════════════════════════════════════════════════════
#  Startup
# ═══════════════════════════════════════════════════════════════

def main():
    log.info("=" * 54)
    log.info("  OpenBell Voice Assistant — Whisper + Piper TTS")
    log.info("=" * 54)
    log.info("  Whisper model: %s (device=%s)", config.WHISPER_MODEL, config.WHISPER_DEVICE)
    log.info("  Piper voice:   %s", config.PIPER_VOICE)
    log.info("  Auto-answer:   %ds timeout", config.AUTO_ANSWER_TIMEOUT)
    log.info("  Audio in:      UDP %d (from Rust server)", config.ASSISTANT_LISTEN_PORT)
    log.info("  Audio out:     UDP 5005 (to Rust server → phone)")
    log.info("=" * 54)

    # Load models
    log.info("Loading models (first run may download)...")
    tts.init()
    stt.load_model()

    # Try to load LLM for chat mode (optional)
    if config.LLM_MODEL_PATH:
        chat.init()
    if chat.is_available():
        log.info("  Chat mode:     ENABLED (LLM loaded)")
    else:
        log.info("  Chat mode:     disabled (keyword responses only)")

    # Pre-cache all standard TTS responses
    cache_texts = {
        "greeting": responses.GREETING,
        "farewell": responses.FAREWELL,
        "followup": responses.FOLLOWUP,
        responses.DELIVERY: responses.RESPONSES[responses.DELIVERY],
        responses.BUSINESS: responses.RESPONSES[responses.BUSINESS],
        responses.PERSONAL: responses.RESPONSES[responses.PERSONAL],
        responses.POLICE: responses.RESPONSES[responses.POLICE],
        responses.UNKNOWN: responses.RESPONSES[responses.UNKNOWN],
        responses.SILENCE: responses.RESPONSES[responses.SILENCE],
    }
    tts.precache_responses(cache_texts)
    log.info("All TTS responses pre-cached in %s", config.TTS_CACHE_DIR)

    # Run WebSocket client
    log.info("Voice assistant ready — waiting for activation")
    asyncio.run(ws_client())
    log.info("Voice assistant stopped.")


if __name__ == "__main__":
    main()
