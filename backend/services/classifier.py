"""Intent classifier — routes voice turns to chat / task / status / cancel.

Uses Claude Haiku 4.5 with a short, cached system prompt. Cheap enough to run on
every turn (~150 input tokens after cache, ~20 output). Designed so Chief can
branch:
- chat   → Anthropic API answer (current LLM pipeline)
- task   → dispatch to local `claude` CLI with Max subscription (zero API cost)
- status → summarize a running dispatched task
- cancel → kill the running dispatched task

If the API call fails or returns unparseable output, defaults to `chat` — the
safe, non-destructive option (no subprocess spawned, no cancellation applied).
"""

import json
import logging
import re
from typing import Literal, Optional, TypedDict

from anthropic import AsyncAnthropic

from config.settings import settings

logger = logging.getLogger(__name__)

Intent = Literal["chat", "task", "status", "cancel"]


class ClassificationResult(TypedDict):
    intent: Intent
    task_spec: Optional[str]
    confidence: float  # 1.0 for deterministic shortcuts or clean JSON, 0.0 on fallback


_CLASSIFIER_MODEL = "claude-haiku-4-5"

# System prompt is large-ish (~500 tokens) because the examples do heavy lifting
# for a Haiku-sized model. cache_control keeps per-turn cost under a tenth of a
# cent after the first call in any 5-minute window.
_SYSTEM_PROMPT = {
    "type": "text",
    "text": (
        "You classify one short user utterance addressed to \"Chief\", a voice "
        "assistant for Neill (software owner). Chief is one of four things:\n"
        "\n"
        "- chat: answer a question, explain, converse, describe, list, "
        "summarize. Includes questions about agent roles, past builds, "
        "architecture, plans, opinions.\n"
        "- task: dispatch an imperative build/fix/write/implement/test/review/"
        "refactor/deploy task to Claude Code running locally on Neill's Mac.\n"
        "- status: query the currently-running dispatched task's progress — "
        "\"how's it going\", \"what's happening\", \"still working\", "
        "\"progress\", \"how long left\".\n"
        "- cancel: kill the currently-running dispatched task — \"stop\", "
        "\"cancel\", \"never mind\", \"kill it\", \"abort\".\n"
        "\n"
        "Return JSON only, no prose:\n"
        "{\"intent\": <\"chat\"|\"task\"|\"status\"|\"cancel\">, "
        "\"task_spec\": <string if task, null otherwise>}\n"
        "\n"
        "Guidelines:\n"
        "- When ambiguous between chat and task, prefer chat (safer, cheaper).\n"
        "- task_spec is the utterance with the \"Chief,\" prefix stripped and "
        "light cleanup; keep the command verbatim.\n"
        "- \"explain X\" / \"tell me about X\" / \"what is X\" → chat.\n"
        "- \"build X\" / \"fix X\" / \"write tests for X\" / \"implement X\" / "
        "\"review X\" / \"refactor X\" / \"ship it\" / \"deploy X\" → task.\n"
        "- \"plan X\" or \"spec X\" WITHOUT explicit build intent → chat "
        "(planning is a conversation).\n"
        "- \"do the auth refactor\" (agent-work implied) → task.\n"
        "- Questions about past or recent work (\"what did Riggs build\") → "
        "chat, NOT status.\n"
        "- Bare \"stop\" / \"cancel\" / \"never mind\" → cancel.\n"
        "\n"
        "Examples:\n"
        "\"What's the plan for auth?\" → {\"intent\":\"chat\",\"task_spec\":null}\n"
        "\"Build the auth refactor.\" → {\"intent\":\"task\",\"task_spec\":\"Build the auth refactor.\"}\n"
        "\"Chief, write tests for billing.\" → {\"intent\":\"task\",\"task_spec\":\"Write tests for billing.\"}\n"
        "\"How's it going?\" → {\"intent\":\"status\",\"task_spec\":null}\n"
        "\"Stop.\" → {\"intent\":\"cancel\",\"task_spec\":null}\n"
        "\"What did Riggs build last night?\" → {\"intent\":\"chat\",\"task_spec\":null}\n"
        "\"Fix the VAD bug on the voice page.\" → {\"intent\":\"task\",\"task_spec\":\"Fix the VAD bug on the voice page.\"}\n"
        "\"Tell me about the dispatch bridge.\" → {\"intent\":\"chat\",\"task_spec\":null}\n"
        "\"Run the full sweep on this.\" → {\"intent\":\"task\",\"task_spec\":\"Run the full sweep on this.\"}\n"
        "\"Status?\" → {\"intent\":\"status\",\"task_spec\":null}\n"
        "\"Never mind, cancel that.\" → {\"intent\":\"cancel\",\"task_spec\":null}\n"
        "\"Can you refactor the auth middleware?\" → {\"intent\":\"task\",\"task_spec\":\"Refactor the auth middleware.\"}\n"
        "\"Why did we use bcrypt?\" → {\"intent\":\"chat\",\"task_spec\":null}\n"
        "\"Show me the top-level TODOs.\" → {\"intent\":\"chat\",\"task_spec\":null}\n"
        "\"Deploy to staging.\" → {\"intent\":\"task\",\"task_spec\":\"Deploy to staging.\"}\n"
        "\"Plan the billing refactor.\" → {\"intent\":\"chat\",\"task_spec\":null}\n"
    ),
    "cache_control": {"type": "ephemeral"},
}


_client: Optional[AsyncAnthropic] = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


# Grab the first balanced {...} from the response, tolerant of trailing chatter.
_JSON_RE = re.compile(r"\{.*?\}", re.DOTALL)

# Zero-API-call shortcuts. The model already handles these, but skipping the
# round-trip cuts ~300ms off the user's perceived latency on common utterances.
_CANCEL_SHORTCUTS = {
    "stop", "stop.", "cancel", "cancel.", "abort", "abort.", "kill it",
    "kill that", "never mind", "nevermind", "never mind.", "nvm",
}
_STATUS_SHORTCUTS = {
    "status", "status?", "status.", "progress", "progress?", "progress.",
    "how's it going", "how's it going?", "hows it going", "hows it going?",
    "what's happening", "what's happening?", "whats happening",
    "still working", "still working?", "how long", "how long?",
}


async def classify_intent(user_text: str, current_project: str) -> ClassificationResult:
    """Classify one voice/chat turn into chat / task / status / cancel.

    Args:
        user_text: The transcribed or typed user message.
        current_project: Currently-scoped project name. Not yet used in the
            prompt — reserved for future scope-aware intent hints.

    Returns:
        ClassificationResult with intent + optional task_spec + confidence.
        Always returns a value; never raises. Defaults to `chat` on any error.
    """
    text = (user_text or "").strip()
    if not text:
        return {"intent": "chat", "task_spec": None, "confidence": 0.0}

    lower = text.lower()
    if lower in _CANCEL_SHORTCUTS:
        return {"intent": "cancel", "task_spec": None, "confidence": 1.0}
    if lower in _STATUS_SHORTCUTS:
        return {"intent": "status", "task_spec": None, "confidence": 1.0}

    try:
        resp = await _get_client().messages.create(
            model=_CLASSIFIER_MODEL,
            max_tokens=80,
            system=[_SYSTEM_PROMPT],
            messages=[{"role": "user", "content": text}],
        )
    except Exception as exc:
        logger.warning("classifier API call failed, defaulting to chat: %s", exc)
        return {"intent": "chat", "task_spec": None, "confidence": 0.0}

    raw = "".join(
        getattr(block, "text", "") for block in resp.content if getattr(block, "type", None) == "text"
    )
    match = _JSON_RE.search(raw)
    if not match:
        logger.warning("classifier returned non-JSON: %r", raw[:160])
        return {"intent": "chat", "task_spec": None, "confidence": 0.0}

    try:
        parsed = json.loads(match.group())
    except json.JSONDecodeError:
        logger.warning("classifier JSON parse failed: %r", match.group()[:160])
        return {"intent": "chat", "task_spec": None, "confidence": 0.0}

    intent_raw = parsed.get("intent")
    if intent_raw not in ("chat", "task", "status", "cancel"):
        logger.warning("classifier returned invalid intent: %r", intent_raw)
        return {"intent": "chat", "task_spec": None, "confidence": 0.0}

    task_spec = parsed.get("task_spec") if intent_raw == "task" else None
    if intent_raw == "task" and not isinstance(task_spec, str):
        # Model chose task but didn't give a spec — fall back to raw text.
        task_spec = text

    return {"intent": intent_raw, "task_spec": task_spec, "confidence": 1.0}
