"""Phase 2 — services.llm is a thin wrapper over services.gemini_brain.

The legacy Anthropic-specific cancellation tests (which mocked
``client.messages.stream(...)``) live on in spirit at
``tests/test_gemini_brain.py::TestCancellation``. This module verifies the
WRAPPER preserves the contract:

  1. CancelledError raised by gemini_brain.stream propagates verbatim.
  2. The wrapper does NOT do extra awaits between gemini_brain raising
     CancelledError and the caller seeing it.
  3. On a clean run, the wrapper passes through Gemini's usage_dict
     unchanged.

Both tests stub ``services.gemini_brain.stream`` so we don't need a live
Vertex AI client.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


from services import gemini_brain, llm  # noqa: E402


@pytest.mark.asyncio
async def test_cancel_propagates_through_wrapper(monkeypatch):
    """If gemini_brain.stream raises CancelledError, llm.stream_turn must
    re-raise without swallowing or wrapping it."""

    cancel_signal = asyncio.Event()
    tokens: list[str] = []

    async def fake_brain_stream(**kwargs):
        send_token = kwargs["send_token"]
        # Emit a couple of tokens, then block until cancel lands.
        await send_token("Hi ")
        await send_token("there. ")
        cancel_signal.set()
        await asyncio.sleep(60)  # would never complete
        await send_token("LATE")  # should not happen
        return {}

    monkeypatch.setattr(gemini_brain, "stream", fake_brain_stream)

    async def send_token(t):
        tokens.append(t)

    async def send_tts_sentence(s):
        pass

    async def run_turn():
        await llm.stream_turn(
            history=[{"role": "user", "content": "hi"}],
            model="ignored",
            send_token=send_token,
            send_tts_sentence=send_tts_sentence,
            system_blocks=[{"type": "text", "text": "sys"}],
        )

    task = asyncio.create_task(run_turn())
    # Wait for the brain to emit the two tokens, then cancel.
    await asyncio.wait_for(cancel_signal.wait(), timeout=2.0)
    tokens_at_cancel = list(tokens)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Settle any stragglers.
    await asyncio.sleep(0.05)
    assert list(tokens) == tokens_at_cancel
    assert tokens_at_cancel == ["Hi ", "there. "]


@pytest.mark.asyncio
async def test_normal_stream_returns_usage_dict(monkeypatch):
    """Happy path — the wrapper returns the usage_dict gemini_brain produced."""

    async def fake_brain_stream(**kwargs):
        send_token = kwargs["send_token"]
        send_tts = kwargs["send_tts_sentence"]
        await send_token("Done.")
        await send_tts("Done.")
        return {
            "input_tokens": 100,
            "output_tokens": 5,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "model": "gemini-2.5-pro",
            "stop_reason": "stop",
            "assistant_text": "Done.",
            "cost_cents": 1,
        }

    monkeypatch.setattr(gemini_brain, "stream", fake_brain_stream)

    seen_tokens: list[str] = []
    seen_sentences: list[str] = []

    async def send_token(t):
        seen_tokens.append(t)

    async def send_tts_sentence(s):
        seen_sentences.append(s)

    usage = await llm.stream_turn(
        history=[{"role": "user", "content": "hi"}],
        model="ignored",
        send_token=send_token,
        send_tts_sentence=send_tts_sentence,
        system_blocks=[{"type": "text", "text": "sys"}],
    )
    assert usage["assistant_text"] == "Done."
    assert usage["model"] == "gemini-2.5-pro"
    assert seen_tokens == ["Done."]
    assert seen_sentences == ["Done."]


@pytest.mark.asyncio
async def test_wrapper_pops_user_turn_from_history(monkeypatch):
    """The wrapper extracts the trailing user-role entry off ``history`` and
    passes it as ``user_text`` to gemini_brain. Ensures we don't double-count
    the user turn in the brain's contents list."""

    captured = {}

    async def fake_brain_stream(**kwargs):
        captured["history"] = kwargs["history"]
        captured["user_text"] = kwargs["user_text"]
        return {
            "input_tokens": 0, "output_tokens": 0,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            "model": "gemini-2.5-pro", "stop_reason": "stop",
            "assistant_text": "", "cost_cents": 0,
        }

    monkeypatch.setattr(gemini_brain, "stream", fake_brain_stream)

    async def noop(*a, **kw):
        pass

    history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "second"},
        {"role": "user", "content": "third"},  # last → user_text
    ]
    await llm.stream_turn(
        history=history,
        model="ignored",
        send_token=noop,
        send_tts_sentence=noop,
        system_blocks=[{"type": "text", "text": "sys"}],
    )
    assert captured["user_text"] == "third"
    assert captured["history"] == history[:-1]


@pytest.mark.asyncio
async def test_wrapper_flattens_system_blocks(monkeypatch):
    captured = {}

    async def fake_brain_stream(**kwargs):
        captured["system_prompt"] = kwargs["system_prompt"]
        return {
            "input_tokens": 0, "output_tokens": 0,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            "model": "gemini-2.5-pro", "stop_reason": "stop",
            "assistant_text": "", "cost_cents": 0,
        }

    monkeypatch.setattr(gemini_brain, "stream", fake_brain_stream)

    async def noop(*a, **kw):
        pass

    await llm.stream_turn(
        history=[{"role": "user", "content": "hi"}],
        model="ignored",
        send_token=noop,
        send_tts_sentence=noop,
        system_blocks=[
            {"type": "text", "text": "Block A"},
            {"type": "text", "text": "Block B"},
            {"type": "text", "text": "Block C"},
        ],
    )
    assert captured["system_prompt"] == "Block A\n\nBlock B\n\nBlock C"
