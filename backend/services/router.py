"""Model routing heuristics for Chief Command v2."""

import re

HAIKU_MODEL = "claude-haiku-4-5"
SONNET_MODEL = "claude-sonnet-4-6"

_SIMPLE_PATTERN = re.compile(
    r"^(what|status|is|are|hey|how much|how many|when|where|who|ping|hi|hello|yo)\b",
    re.IGNORECASE,
)


def classify_and_route(user_text: str) -> str:
    """Return the model ID to use for this turn.

    Both branches return Haiku — Haiku itself can call escalate_to_sonnet
    if the question warrants it. This function exists so callers can wire
    in future heuristics without touching websocket code.
    """
    text = user_text.strip()
    word_count = len(text.split())

    if word_count < 8 or len(text) < 50 or _SIMPLE_PATTERN.match(text):
        return HAIKU_MODEL

    return HAIKU_MODEL
