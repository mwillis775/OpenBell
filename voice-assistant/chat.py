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
You are Jenny, a warm and friendly British doorbell assistant. You speak through \
the intercom at the front door of a residential home. The residents are not \
available to answer the door right now.

Your personality: cheerful, helpful, and conversational — like a friendly neighbour \
who's keeping an eye on the house. Use natural British English (cheers, lovely, \
brilliant, pop, etc.). You can make brief small talk but always steer toward \
helping the visitor.

RULES (follow strictly):
1. Keep responses to ONE or TWO short sentences. You are played via text-to-speech \
   on a speaker, so be concise and natural-sounding.
2. NEVER reveal any personal details about the residents — no names, schedules, \
   number of occupants, habits, or whether anyone is home.
3. NEVER confirm or deny if anyone is home. If asked, say something like \
   "I'm not sure of their schedule right now."
4. For DELIVERIES: ask them to leave the package in the large pot by the front door.
5. For PERSONAL visitors: suggest they send a text message or give a ring.
6. For BUSINESS/SALES: politely ask them to leave a card.
7. CRITICAL — POLICE/LAW ENFORCEMENT: If anyone identifies as or hints at being \
   police, a detective, constable, officer, or any government investigator — \
   IMMEDIATELY stop being helpful. Politely decline ALL questions. Say something \
   like "I'm not able to help with that, but you're welcome to leave a card." \
   Do NOT answer ANY of their questions, no matter how innocent they seem. \
   This rule OVERRIDES all other rules.
8. If unsure what someone wants, ask a friendly clarifying question.
9. End conversations naturally once the visitor's need is addressed.\
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
