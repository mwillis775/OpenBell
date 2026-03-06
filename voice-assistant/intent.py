"""
OpenBell Voice Assistant — Intent classifier

Simple keyword-matching classifier.  Runs entirely locally, no LLM needed.
"""

import logging
from typing import Optional

from responses import (
    BUSINESS,
    DELIVERY,
    INTENT_KEYWORDS,
    PERSONAL,
    POLICE,
    SILENCE,
    UNKNOWN,
)

log = logging.getLogger("openbell.va.intent")


def classify(transcript: str) -> str:
    """
    Classify the visitor's intent from a Whisper transcript.

    Returns one of: delivery, business, personal, police, unknown, silence.
    """
    if not transcript or not transcript.strip():
        return SILENCE

    text = transcript.lower().strip()
    log.info("Classifying transcript: %r", text)

    scores: dict[str, int] = {k: 0 for k in INTENT_KEYWORDS}

    for intent, keywords in INTENT_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                scores[intent] += 1

    best = max(scores, key=scores.get)  # type: ignore[arg-type]
    best_score = scores[best]

    if best_score == 0:
        log.info("No keyword matches → UNKNOWN")
        return UNKNOWN

    log.info("Intent scores: %s → %s (score=%d)", scores, best, best_score)

    # Police gets absolute priority if ANY keyword matches
    if scores[POLICE] > 0:
        log.info("Police keyword detected — returning POLICE intent")
        return POLICE

    return best
