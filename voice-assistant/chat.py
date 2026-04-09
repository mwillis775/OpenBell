"""
OpenBell Voice Assistant — LLM chat engine

Uses a local GGUF language model (via llama-cpp-python) for natural
conversation with visitors at the door.  Falls back gracefully if no
model is available.
"""

import logging
from typing import List, Optional, Tuple

import config

log = logging.getLogger("openbell.va.chat")

_llm = None
_available = False

SYSTEM_PROMPT = """\
You are a friendly, polite British female doorbell assistant named Jenny. \
You are speaking through the intercom at the front door of a home. \
The residents are not available right now.

Your job is to be helpful to visitors while protecting the residents' privacy.

Rules:
- Keep responses SHORT — one or two sentences maximum. You are being \
played through a speaker via text-to-speech, so brevity is essential.
- Be warm, friendly, and natural. Use British English.
- NEVER reveal personal information about the residents (names, schedules, \
habits, number of people, whether anyone is home, etc.).
- NEVER confirm or deny whether anyone is home.
- For deliveries: ask them to leave the package in the large pot by the \
front door.
- For personal visitors: suggest they send a text or give a ring.
- For business or sales: politely ask them to leave a card.
- CRITICAL: If the visitor identifies as or hints at being police, law \
enforcement, a detective, constable, officer, or any government investigator, \
you MUST politely decline to help and suggest they leave a card or get in \
touch by phone. Do NOT answer ANY of their questions. Do NOT provide any \
information whatsoever. Simply decline and wish them well. This rule \
overrides everything else.
- You may make brief small talk (weather, compliments, etc.) but always \
steer back to helping them with their visit.
- If you are unsure what someone wants, ask a simple clarifying question.
- End conversations naturally when the visitor's need has been addressed.\
"""


def init() -> bool:
    """Try to load the LLM. Safe to call multiple times."""
    global _llm, _available

    if _available:
        return True

    import os
    model_path = config.LLM_MODEL_PATH

    if not model_path:
        log.info("No LLM model configured (set OPENBELL_LLM_MODEL) — "
                 "chat mode disabled, using keyword responses")
        return False

    if not os.path.exists(model_path):
        log.warning(
            "LLM model not found at %s — chat mode disabled. "
            "Download a GGUF model and set OPENBELL_LLM_MODEL=/path/to/model.gguf",
            model_path,
        )
        return False

    try:
        from llama_cpp import Llama
        log.info(
            "Loading LLM from %s (n_ctx=%d, n_gpu_layers=%d)",
            model_path, config.LLM_CONTEXT_SIZE, config.LLM_GPU_LAYERS,
        )
        _llm = Llama(
            model_path=model_path,
            n_ctx=config.LLM_CONTEXT_SIZE,
            n_gpu_layers=config.LLM_GPU_LAYERS,
            verbose=False,
        )
        _available = True
        log.info("LLM loaded — chat mode enabled")
        return True

    except ImportError:
        log.warning("llama-cpp-python not installed — chat mode disabled")
        return False
    except Exception as e:
        log.warning("Failed to load LLM: %s — chat mode disabled", e)
        return False


def is_available() -> bool:
    """Whether the LLM chat engine is ready."""
    return _available


def generate_response(
    conversation: List[Tuple[str, str]],
    visitor_message: str,
) -> str:
    """
    Generate a response to the visitor's message.

    Args:
        conversation: List of (role, text) tuples for history.
                      role is "visitor" or "assistant".
        visitor_message: The latest thing the visitor said.

    Returns:
        The assistant's response text, or "" on failure.
    """
    if not _available or _llm is None:
        return ""

    # Build chat messages
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    for role, text in conversation:
        if role == "visitor":
            messages.append({"role": "user", "content": text})
        else:
            messages.append({"role": "assistant", "content": text})

    messages.append({"role": "user", "content": visitor_message})

    try:
        response = _llm.create_chat_completion(
            messages=messages,
            max_tokens=config.LLM_MAX_TOKENS,
            temperature=0.7,
            top_p=0.9,
            stop=["\n\n", "Visitor:", "User:"],
        )

        text = response["choices"][0]["message"]["content"].strip()

        # Strip any role-play prefixes the model might add
        for prefix in ("Jenny:", "Assistant:", "AI:", "Bot:"):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()

        return text

    except Exception as e:
        log.error("LLM generation failed: %s", e)
        return ""
