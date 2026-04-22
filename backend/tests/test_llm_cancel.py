"""Track B #4 — stream_turn must NOT emit tokens after cancel.

We stub the Anthropic client so we can drive a fake event stream and assert
zero tokens leak out after a cancel. The real goal: after CancelledError,
no further `send_token` / `send_tts_sentence` calls occur, and we don't
await `get_final_message()` (which would pay for the remote stream to
drain before we release).
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# Stub `anthropic.AsyncAnthropic` before importing services.llm so import
# doesn't require the real SDK shape. The real module is installed in the
# runtime venv but we don't want real API calls from unit tests.
def _install_anthropic_stub():
    if "anthropic" in sys.modules:
        return
    anthropic_mod = types.ModuleType("anthropic")

    class _FakeAsyncAnthropic:
        def __init__(self, **kwargs):
            self.messages = MagicMock()

    anthropic_mod.AsyncAnthropic = _FakeAsyncAnthropic
    sys.modules["anthropic"] = anthropic_mod


_install_anthropic_stub()

from services import llm  # noqa: E402


class _FakeEvent:
    def __init__(self, text: str):
        self.type = "content_block_delta"
        self.delta = types.SimpleNamespace(type="text_delta", text=text)


class _FakeFinalUsage:
    def __init__(self):
        self.input_tokens = 10
        self.output_tokens = 20
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0


class _FakeFinalMessage:
    def __init__(self):
        self.usage = _FakeFinalUsage()


class _SlowStreamContext:
    """Fake ``client.messages.stream(...)`` context manager.

    Yields fake text_delta events with a small delay between each so a cancel
    mid-stream can take effect. If cancel doesn't break the iterator cleanly,
    the test will see tokens emitted AFTER the cancel point.
    """

    def __init__(self, texts: list[str], per_event_sleep: float = 0.02):
        self._texts = texts
        self._sleep = per_event_sleep
        self._get_final_called = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for t in self._texts:
            await asyncio.sleep(self._sleep)
            yield _FakeEvent(t)

    async def get_final_message(self):
        # Flag so the test can assert this was NOT called on cancel.
        self._get_final_called = True
        # Also block briefly so an accidental call after cancel still
        # shows up as measurable extra latency.
        await asyncio.sleep(0.1)
        return _FakeFinalMessage()


class _FakeMessagesClient:
    def __init__(self, stream_ctx):
        self._ctx = stream_ctx

    def stream(self, **kwargs):
        return self._ctx


class _FakeAsyncAnthropic:
    def __init__(self, stream_ctx):
        self.messages = _FakeMessagesClient(stream_ctx)


@pytest.mark.asyncio
async def test_cancel_midstream_emits_no_tokens_after_cancel(monkeypatch):
    stream_ctx = _SlowStreamContext(
        ["Hel", "lo, ", "this ", "is ", "a ", "long ", "reply."],
        per_event_sleep=0.03,
    )
    fake_client = _FakeAsyncAnthropic(stream_ctx)
    monkeypatch.setattr(llm, "_get_client", lambda: fake_client)

    tokens: list[str] = []
    sentences: list[str] = []

    async def send_token(text: str) -> None:
        tokens.append(text)

    async def send_tts_sentence(s: str) -> None:
        sentences.append(s)

    async def run_turn():
        await llm.stream_turn(
            history=[{"role": "user", "content": "hi"}],
            model="claude-haiku-4-5",
            send_token=send_token,
            send_tts_sentence=send_tts_sentence,
            system_blocks=[{"type": "text", "text": "sys"}],
        )

    task = asyncio.create_task(run_turn())
    await asyncio.sleep(0.08)  # let ~2–3 tokens through
    tokens_at_cancel = len(tokens)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Allow any loose straggler callbacks to settle.
    await asyncio.sleep(0.15)
    tokens_after_cancel_settled = len(tokens)

    # Strict: no additional tokens after the cancel point.
    assert tokens_after_cancel_settled == tokens_at_cancel, (
        f"Expected zero tokens after cancel; saw {tokens_after_cancel_settled - tokens_at_cancel} "
        f"extra. tokens_at_cancel={tokens_at_cancel} "
        f"final={tokens_after_cancel_settled}"
    )

    # Must NOT have called get_final_message on the cancel path.
    assert stream_ctx._get_final_called is False


@pytest.mark.asyncio
async def test_normal_stream_returns_usage_and_calls_final(monkeypatch):
    stream_ctx = _SlowStreamContext(["Done."], per_event_sleep=0.0)
    fake_client = _FakeAsyncAnthropic(stream_ctx)
    monkeypatch.setattr(llm, "_get_client", lambda: fake_client)

    async def send_token(text: str) -> None:
        pass

    async def send_tts_sentence(s: str) -> None:
        pass

    usage = await llm.stream_turn(
        history=[{"role": "user", "content": "hi"}],
        model="claude-haiku-4-5",
        send_token=send_token,
        send_tts_sentence=send_tts_sentence,
        system_blocks=[{"type": "text", "text": "sys"}],
    )
    assert usage["assistant_text"] == "Done."
    assert usage["input_tokens"] == 10
    assert stream_ctx._get_final_called is True
