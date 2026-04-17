"""Anthropic API streaming integration for Chief Command v2."""

import logging
import os
import re
from typing import Awaitable, Callable, Optional

from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)

_client: Optional[AsyncAnthropic] = None

SENTENCE_FLUSH_RE = re.compile(r"(?<=[.!?])\s+|(?<=[.!?])$")

SYSTEM_PROMPT = {
    "type": "text",
    "text": (
        "You are Chief, a sharp personal AI assistant for a software owner and entrepreneur. "
        "Be concise and direct. Prefer one-sentence answers for simple questions. "
        "You have access to project status, code context, and business metrics when asked."
    ),
    "cache_control": {"type": "ephemeral"},
}

ESCALATE_TOOL = {
    "name": "escalate_to_sonnet",
    "description": (
        "Call this when the question requires nuanced reasoning, multi-step planning, "
        "complex code generation, or any task where haiku-quality output is insufficient. "
        "Provide a brief reason."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {"type": "string", "description": "Why Sonnet is needed."}
        },
        "required": ["reason"],
    },
}

UsageRecord = dict


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        _client = AsyncAnthropic(api_key=api_key)
    return _client


def _compute_cost_cents(model: str, usage: dict) -> int:
    PRICING = {
        "claude-haiku-4-5":  {"in": 1.0,  "out": 5.0,  "cached_in": 0.1},
        "claude-sonnet-4-6": {"in": 3.0,  "out": 15.0, "cached_in": 0.3},
        "claude-opus-4-7":   {"in": 5.0,  "out": 25.0, "cached_in": 0.5},
    }
    rates = PRICING.get(model, PRICING["claude-haiku-4-5"])
    input_tok = usage.get("input_tokens", 0)
    output_tok = usage.get("output_tokens", 0)
    cached_tok = usage.get("cache_read_input_tokens", 0)
    creation_tok = usage.get("cache_creation_input_tokens", 0)

    billable_input = max(0, input_tok - cached_tok)
    cost_dollars = (
        (billable_input / 1_000_000) * rates["in"]
        + (output_tok / 1_000_000) * rates["out"]
        + (cached_tok / 1_000_000) * rates["cached_in"]
        + (creation_tok / 1_000_000) * rates["in"]
    )
    return round(cost_dollars * 100)


async def stream_turn(
    history: list[dict],
    model: str,
    send_token: Callable[[str], Awaitable[None]],
    send_tts_sentence: Callable[[str], Awaitable[None]],
    max_tokens: int = 1024,
) -> UsageRecord:
    """Stream one conversation turn via the Anthropic API.

    Calls send_token for each text delta.
    Buffers text and flushes complete sentences to send_tts_sentence.
    Returns a usage dict with token counts, model, stop_reason, and cost_cents.
    """
    client = _get_client()
    full_text: list[str] = []
    sentence_buf: list[str] = []
    stop_reason = "end_turn"
    escalate_reason: Optional[str] = None

    tools = [ESCALATE_TOOL] if model == "claude-haiku-4-5" else []

    async with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=[SYSTEM_PROMPT],
        messages=history,
        tools=tools if tools else [],
    ) as stream:
        async for event in stream:
            event_type = getattr(event, "type", None)

            if event_type == "content_block_delta":
                delta = getattr(event, "delta", None)
                if delta and getattr(delta, "type", None) == "text_delta":
                    text = delta.text
                    full_text.append(text)
                    await send_token(text)
                    sentence_buf.append(text)

                    joined = "".join(sentence_buf)
                    parts = SENTENCE_FLUSH_RE.split(joined)
                    if len(parts) > 1:
                        for sentence in parts[:-1]:
                            sentence = sentence.strip()
                            if sentence:
                                await send_tts_sentence(sentence)
                        sentence_buf.clear()
                        if parts[-1]:
                            sentence_buf.append(parts[-1])

            elif event_type == "content_block_stop":
                pass

            elif event_type == "message_delta":
                delta = getattr(event, "delta", None)
                if delta:
                    stop_reason = getattr(delta, "stop_reason", stop_reason) or stop_reason

        final_msg = await stream.get_final_message()

        if stop_reason == "tool_use":
            for block in final_msg.content:
                if getattr(block, "type", None) == "tool_use" and block.name == "escalate_to_sonnet":
                    escalate_reason = block.input.get("reason", "escalation requested")

        remainder = "".join(sentence_buf).strip()
        if remainder:
            await send_tts_sentence(remainder)

        usage = final_msg.usage
        usage_dict: UsageRecord = {
            "input_tokens": getattr(usage, "input_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0),
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
            "model": model,
            "stop_reason": stop_reason,
            "assistant_text": "".join(full_text),
            "escalate_reason": escalate_reason,
        }
        usage_dict["cost_cents"] = _compute_cost_cents(model, usage_dict)
        return usage_dict
