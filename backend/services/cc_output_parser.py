"""Parse Claude Agent SDK message stream into typed Chief events.

The SDK yields a heterogeneous mix:
  - SystemMessage(subtype="init", data={"session_id": ..., "tools": ...})
  - StreamEvent(event=<raw API event>) — only when include_partial_messages=True
  - AssistantMessage(content=[TextBlock | ToolUseBlock | ThinkingBlock])
  - UserMessage with ToolResultBlock content (tool result)
  - ResultMessage(session_id, total_cost_usd, usage, num_turns, ...)

Chief's WS layer only cares about a smaller, typed event vocabulary:
  - text_delta(text)              — token-by-token text for TTS streaming
  - thinking_delta(text)          — thinking-block deltas (logged, not spoken)
  - tool_use_start(id, name, in)  — Bash/Read/Grep started
  - tool_use_complete(id, ok, preview)  — tool finished, with truncated stdout
  - turn_complete(session_id, num_turns, ...)  — ready for the next user input
  - error(message)                — SDK or remote error

This module is a *pure parser*: no I/O, no awaits inside. Callers wrap it in
async iteration over the SDK's receive_response().

The text-delta path is critical for TTS: we need each token as it arrives so
the existing sentence-flush regex in llm.stream_turn can keep speaking the
reply mid-generation. The SDK's AssistantMessage arrives only at block close
(too late for TTS), so we extract text deltas from StreamEvent's raw API
events instead. For models with thinking, we surface thinking deltas
separately so the WS layer can log them without piping them to TTS.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Typed events emitted to Chief's WS layer
# ---------------------------------------------------------------------------
@dataclass
class TextDelta:
    """Incremental text token from a content_block_delta -> text_delta event."""
    text: str


@dataclass
class ThinkingDelta:
    """Incremental thinking-block text. Logged, not piped to TTS."""
    text: str


@dataclass
class ToolUseStart:
    """A tool call started. Emitted on content_block_start with tool_use type
    OR on the first AssistantMessage that carries the ToolUseBlock — whichever
    we see first. We dedupe on tool_use_id so callers can ignore duplicates.
    """
    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolUseComplete:
    """A tool call finished. ``preview`` is the first ~200 chars of the
    tool result content for the chip's expand-on-click view.
    """
    id: str
    ok: bool
    preview: str = ""


@dataclass
class TurnComplete:
    """The current turn finished. session_id is the SDK's session id —
    capture it so we can resume across crashes / idle teardown.
    """
    session_id: str
    total_cost_usd: float
    num_turns: int
    is_error: bool
    stop_reason: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


@dataclass
class SessionInit:
    """First message after connect — gives us the session_id BEFORE the first
    turn completes so a crash mid-turn can still find the right session to
    resume.
    """
    session_id: str


@dataclass
class ParsedError:
    """Surface SDK or remote errors without crashing the WS turn."""
    message: str


ParsedEvent = (
    TextDelta
    | ThinkingDelta
    | ToolUseStart
    | ToolUseComplete
    | TurnComplete
    | SessionInit
    | ParsedError
)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------
def _truncate(text: str, n: int = 200) -> str:
    """Truncate to ``n`` chars with an ellipsis suffix when cut."""
    if not text:
        return ""
    if len(text) <= n:
        return text
    return text[: n - 1].rstrip() + "…"


def _stringify_tool_result(content: Any) -> str:
    """Best-effort flattening of a tool result for the chip preview.

    SDK tool result content may be a string, a list of {"type":"text","text":..}
    dicts, or None. We stringify everything to a single str so the chip can
    show *something* without the frontend caring about block shapes.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if "text" in block and isinstance(block["text"], str):
                    parts.append(block["text"])
                else:
                    parts.append(str(block))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)


def parse_stream_event(raw_event: dict[str, Any]) -> list[ParsedEvent]:
    """Parse a single StreamEvent.event payload.

    The ``event`` field on StreamEvent is the raw Anthropic API event dict.
    We care about:
      - content_block_start with type=tool_use → ToolUseStart
      - content_block_delta with delta.type=text_delta → TextDelta
      - content_block_delta with delta.type=thinking_delta → ThinkingDelta

    Returns 0 or 1 events. List for symmetry with parse_message().
    """
    if not isinstance(raw_event, dict):
        return []

    event_type = raw_event.get("type")

    if event_type == "content_block_start":
        # Tool-use start arrives here with EMPTY input — the actual args
        # arrive via subsequent ``input_json_delta`` events. Emitting a
        # ToolUseStart now would fire a chip with no useful info. Wait for
        # the AssistantMessage's ToolUseBlock (which carries the assembled
        # input dict) instead — see ``parse_message`` below.
        return []

    if event_type == "content_block_delta":
        delta = raw_event.get("delta") or {}
        delta_type = delta.get("type")
        if delta_type == "text_delta":
            text = delta.get("text") or ""
            if text:
                return [TextDelta(text=text)]
            return []
        if delta_type == "thinking_delta":
            thinking = delta.get("thinking") or ""
            if thinking:
                return [ThinkingDelta(text=thinking)]
            return []
        # input_json_delta (tool_use input streaming) is intentionally ignored
        # — we capture the full input on content_block_start above OR on the
        # AssistantMessage ToolUseBlock (whichever arrives first).
        return []

    return []


def parse_message(message: Any) -> list[ParsedEvent]:
    """Parse a top-level SDK message into Chief events.

    Handles SystemMessage, AssistantMessage, UserMessage (tool results),
    ResultMessage. StreamEvent is unwrapped via ``parse_stream_event`` on its
    .event payload.

    Imports are deferred so this module loads cleanly even when the SDK is
    not installed (unit tests stub it out).
    """
    # Short-circuit: a fake / test client may yield ParsedEvent values
    # directly (skipping the SDK message → parsed-event translation). Pass
    # those through unchanged so test fixtures don't need to construct full
    # SDK message dataclasses.
    if isinstance(
        message,
        (TextDelta, ThinkingDelta, ToolUseStart, ToolUseComplete,
         TurnComplete, SessionInit, ParsedError),
    ):
        return [message]
    # Local import — keeps this parser importable without SDK.
    try:
        from claude_agent_sdk.types import (
            AssistantMessage,
            ResultMessage,
            StreamEvent,
            SystemMessage,
            TextBlock,
            ThinkingBlock,
            ToolResultBlock,
            ToolUseBlock,
            UserMessage,
        )
    except ImportError:
        # If the SDK isn't installed we can still consume hand-crafted
        # mock messages by attribute name. Fall back to attribute checks.
        AssistantMessage = ResultMessage = StreamEvent = SystemMessage = None  # type: ignore[assignment]
        TextBlock = ThinkingBlock = ToolResultBlock = ToolUseBlock = UserMessage = None  # type: ignore[assignment]

    out: list[ParsedEvent] = []

    # SystemMessage (init) → SessionInit (capture session_id early)
    if SystemMessage is not None and isinstance(message, SystemMessage):
        if message.subtype == "init":
            data = message.data or {}
            session_id = data.get("session_id") or ""
            if session_id:
                out.append(SessionInit(session_id=session_id))
        return out

    # StreamEvent — unwrap raw event
    if StreamEvent is not None and isinstance(message, StreamEvent):
        return parse_stream_event(message.event or {})

    # AssistantMessage — extract any tool_use blocks (ToolUseStart) we haven't
    # already seen via content_block_start. Text is already streamed via
    # StreamEvent so we DO NOT re-emit text deltas here (would double-speak).
    if AssistantMessage is not None and isinstance(message, AssistantMessage):
        for block in message.content or []:
            if ToolUseBlock is not None and isinstance(block, ToolUseBlock):
                out.append(ToolUseStart(
                    id=block.id, name=block.name, input=block.input or {},
                ))
        return out

    # UserMessage carrying tool results → ToolUseComplete
    if UserMessage is not None and isinstance(message, UserMessage):
        content = message.content
        if isinstance(content, list):
            for block in content:
                # SDK delivers ToolResultBlock instances OR plain dicts depending
                # on how the agent loop emitted them; handle both.
                if ToolResultBlock is not None and isinstance(block, ToolResultBlock):
                    preview = _truncate(_stringify_tool_result(block.content))
                    out.append(ToolUseComplete(
                        id=block.tool_use_id,
                        ok=not bool(block.is_error),
                        preview=preview,
                    ))
                elif isinstance(block, dict) and block.get("type") == "tool_result":
                    preview = _truncate(_stringify_tool_result(block.get("content")))
                    out.append(ToolUseComplete(
                        id=block.get("tool_use_id") or "",
                        ok=not bool(block.get("is_error")),
                        preview=preview,
                    ))
        return out

    # ResultMessage — turn complete + final usage
    if ResultMessage is not None and isinstance(message, ResultMessage):
        usage = message.usage or {}
        out.append(TurnComplete(
            session_id=message.session_id or "",
            total_cost_usd=float(message.total_cost_usd or 0.0),
            num_turns=int(message.num_turns or 0),
            is_error=bool(message.is_error),
            stop_reason=message.stop_reason,
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
            cache_creation_tokens=int(usage.get("cache_creation_input_tokens", 0) or 0),
        ))
        return out

    # Duck-typed fallback for stubbed SDK messages in tests. Match by
    # attribute presence — any object exposing the dataclass shape works.
    cls_name = type(message).__name__
    if cls_name == "SystemMessage" and getattr(message, "subtype", None) == "init":
        sid = (getattr(message, "data", {}) or {}).get("session_id") or ""
        if sid:
            out.append(SessionInit(session_id=sid))
        return out
    if cls_name == "StreamEvent":
        return parse_stream_event(getattr(message, "event", {}) or {})
    if cls_name == "ResultMessage":
        usage = getattr(message, "usage", {}) or {}
        out.append(TurnComplete(
            session_id=getattr(message, "session_id", "") or "",
            total_cost_usd=float(getattr(message, "total_cost_usd", 0.0) or 0.0),
            num_turns=int(getattr(message, "num_turns", 0) or 0),
            is_error=bool(getattr(message, "is_error", False)),
            stop_reason=getattr(message, "stop_reason", None),
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
            cache_creation_tokens=int(usage.get("cache_creation_input_tokens", 0) or 0),
        ))
        return out

    return out


__all__ = [
    "ParsedEvent",
    "TextDelta",
    "ThinkingDelta",
    "ToolUseStart",
    "ToolUseComplete",
    "TurnComplete",
    "SessionInit",
    "ParsedError",
    "parse_message",
    "parse_stream_event",
]
