"""Model routing for Chief Command v2.

Hybrid strategy:
- Default voice: Sonnet 4.6 (consistent personality, ~1.2s TTFT).
- Opus 4.7 on-demand: triggered by "think through", planning, reasoning, or
  explicit "chief think about this". Caller emits a bridge TTS phrase
  ("let me think on that…") so the ~2-3s Opus latency feels intentional.
- Haiku 4.5 is intentionally NOT in the voice loop — keeps voice persona
  consistent. Still exported for future non-voice paths.
"""

import random
import re

HAIKU_MODEL = "claude-haiku-4-5"
SONNET_MODEL = "claude-sonnet-4-6"
OPUS_MODEL = "claude-opus-4-7"

THINKING_PHRASES = [
    "Let me think on that for a moment.",
    "Give me a second to chew on that.",
    "Hmm, let me reason through this.",
    "Alright, let me think this one through.",
    "That deserves a real answer. One moment.",
    "Okay, thinking this through.",
]

_DEEP_PATTERNS = re.compile(
    r"\b("
    r"think\s+(?:through|about|on|over)|"
    r"reason\s+through|"
    r"help\s+me\s+(?:think|reason|understand|figure|decide|plan)|"
    r"plan\s+(?:out|for|the|a)|"
    r"architect|"
    r"tradeoffs?|trade-offs?|"
    r"should\s+i|should\s+we|"
    r"what\s+do\s+you\s+think|"
    r"deep\s+dive|"
    r"pros\s+and\s+cons|"
    r"strategy\s+for|"
    r"walk\s+me\s+through|"
    r"chief[,\s]+think"
    r")\b",
    re.IGNORECASE,
)


def classify_and_route(user_text: str) -> tuple[str, bool]:
    """Return (model_id, is_deep). is_deep triggers the bridge phrase."""
    text = user_text.strip()
    if _DEEP_PATTERNS.search(text):
        return OPUS_MODEL, True
    return SONNET_MODEL, False


def random_thinking_phrase() -> str:
    return random.choice(THINKING_PHRASES)
