"""Unit tests for the dispatch-bridge glue in ``app.websockets``.

These tests focus on the 5 hardening fixes from Vera + Hawke's review of
``ba415f5``:

  1. Vera HIGH — argv "--" separator (also covered by
     test_dispatcher.test_task_spec_with_leading_dashes_reaches_subprocess_as_prompt;
     here we validate the glue-level rejection path).
  2. Hawke CRITICAL — per-connection send lock serializes concurrent WS
     writes across json + bytes frames.
  3. Hawke CRITICAL — inbound "cancel" supersedes the current turn before
     routing dispatcher cancel.  (Behavioral — covered by
     test_cancel_inbound_supersedes_turn.)
  4. Hawke HIGH — _narrate frame ordering: token -> tts_start -> bytes ->
     tts_end -> message_done (only if terminal).
  5. Hawke HIGH — _route_task surfaces dispatcher exceptions to the user
     rather than dying silently.

We use a fake WebSocket object so tests don't require a live server.
"""

from __future__ import annotations

import asyncio
import sys
import types
from typing import Any

import pytest


# ----------------------------------------------------------------- stubs
#
# conftest.py stubs the ``services`` package so tests can import individual
# service modules (classifier, dispatcher, repo_map) without pulling in the
# STT / TTS ML stack. But ``app.websockets`` does
# ``from services import stt_service, tts_service`` at import time, so we
# need to install tiny stand-ins on the stub package *before* the first
# ``from app.websockets import ...`` happens inside any test.


def _install_service_stubs() -> None:
    svc_pkg = sys.modules.get("services")
    if svc_pkg is None:
        # conftest not loaded yet — nothing to do; it'll run first when
        # pytest imports our conftest before this module.
        return
    if not hasattr(svc_pkg, "stt_service"):
        svc_pkg.stt_service = types.SimpleNamespace(  # type: ignore[attr-defined]
            transcribe=lambda data: "",
        )
    if not hasattr(svc_pkg, "tts_service"):
        async def _no_chunks(text: str):
            if False:
                yield b""
            return
        svc_pkg.tts_service = types.SimpleNamespace(  # type: ignore[attr-defined]
            synthesize_stream=_no_chunks,
        )
    # `verify_token` only needed at call time; import guarded via submodule.
    if "services.auth" not in sys.modules:
        auth_mod = types.ModuleType("services.auth")
        auth_mod.verify_token = lambda token: False  # type: ignore[attr-defined]
        sys.modules["services.auth"] = auth_mod


_install_service_stubs()


# ------------------------------------------------------------------ fakes

class FakeWebSocket:
    """Minimal WebSocket stub exposing the attributes/methods used by the glue.

    Records the sequence of outbound frames (either dict-for-json or
    bytes-for-binary) so tests can assert both ordering and concurrency.
    The ``send_delay_s`` knob simulates a slow network so a racing concurrent
    write would be observable in ``max_concurrent``.
    """

    def __init__(self, send_delay_s: float = 0.0) -> None:
        self.sent: list[Any] = []
        self._send_calls_in_flight = 0
        self.max_concurrent = 0
        self._inflight_lock = asyncio.Lock()
        self._send_delay_s = send_delay_s
        # Mirror Starlette's instance-attribute behavior — the glue stashes
        # its lock on the ws object itself.
        self._send_lock: asyncio.Lock | None = None

    async def _record(self, frame: Any) -> None:
        async with self._inflight_lock:
            self._send_calls_in_flight += 1
            self.max_concurrent = max(self.max_concurrent, self._send_calls_in_flight)
        if self._send_delay_s:
            await asyncio.sleep(self._send_delay_s)
        self.sent.append(frame)
        async with self._inflight_lock:
            self._send_calls_in_flight -= 1

    async def send_json(self, frame: dict) -> None:
        await self._record(dict(frame))

    async def send_bytes(self, data: bytes) -> None:
        await self._record(bytes(data))


# --------------------------------------------------------------- fix #2 test

@pytest.mark.asyncio
async def test_ws_send_helpers_serialize_mixed_frames() -> None:
    """Concurrent ws_send_json + ws_send_bytes from separate tasks never
    interleave; they all go through a single per-connection lock.

    Hawke CRITICAL — Starlette's WebSocket.send_{json,bytes} is not
    concurrency-safe. Two producers (e.g. a dispatcher pump emitting
    task_output while _narrate is streaming TTS bytes) could otherwise
    corrupt frames on the wire.
    """
    from app.websockets import ws_send_json, ws_send_bytes

    ws = FakeWebSocket(send_delay_s=0.005)

    async def json_producer(prefix: str) -> None:
        for i in range(10):
            await ws_send_json(ws, {"type": prefix, "i": i})

    async def bytes_producer(tag: int) -> None:
        for i in range(10):
            await ws_send_bytes(ws, bytes([tag, i]))

    await asyncio.gather(
        json_producer("a"),
        json_producer("b"),
        bytes_producer(0xAA),
        bytes_producer(0xBB),
    )

    assert len(ws.sent) == 40
    assert ws.max_concurrent == 1, (
        f"expected serialized sends, saw {ws.max_concurrent} concurrent"
    )
    # Per-producer order preserved (single-task FIFO).
    a_order = [f["i"] for f in ws.sent if isinstance(f, dict) and f.get("type") == "a"]
    b_order = [f["i"] for f in ws.sent if isinstance(f, dict) and f.get("type") == "b"]
    aa_order = [f[1] for f in ws.sent if isinstance(f, (bytes, bytearray)) and f[0] == 0xAA]
    bb_order = [f[1] for f in ws.sent if isinstance(f, (bytes, bytearray)) and f[0] == 0xBB]
    assert a_order == list(range(10))
    assert b_order == list(range(10))
    assert aa_order == list(range(10))
    assert bb_order == list(range(10))


@pytest.mark.asyncio
async def test_send_lock_is_per_connection() -> None:
    """Two distinct ws objects get independent locks — a slow send on one
    does NOT block sends on the other.
    """
    from app.websockets import _get_send_lock

    ws_a = FakeWebSocket()
    ws_b = FakeWebSocket()
    lock_a = _get_send_lock(ws_a)
    lock_b = _get_send_lock(ws_b)
    assert lock_a is not lock_b
    # Same ws returns same lock (stashed on instance).
    assert _get_send_lock(ws_a) is lock_a


# --------------------------------------------------------------- fix #4 tests

@pytest.mark.asyncio
async def test_narrate_terminal_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """_narrate(terminal=True) emits:
        token -> tts_start -> audio bytes -> tts_end -> message_done

    Hawke HIGH — previously message_done was emitted BEFORE the TTS bytes,
    which told the frontend the assistant was done while audio was still
    streaming. Chat path emits message_done last; narration must match.
    """
    from app import websockets as ws_mod

    async def fake_synth(text: str, **_: Any):
        # Simulate two audio chunks.
        yield b"chunk-1"
        yield b"chunk-2"

    monkeypatch.setattr(ws_mod.tts_service, "synthesize_stream", fake_synth)

    ws = FakeWebSocket()
    await ws_mod._narrate(ws, "all done", terminal=True)

    # Collapse the stream into a type-sequence: we want the order independent
    # of the exact string payloads.
    seq: list[str] = []
    for frame in ws.sent:
        if isinstance(frame, dict):
            seq.append(frame["type"])
        else:
            seq.append("bytes")

    assert seq == [
        "token",
        "tts_start",
        "bytes",
        "bytes",
        "tts_end",
        "message_done",
    ], f"unexpected narration frame order: {seq}"


@pytest.mark.asyncio
async def test_narrate_non_terminal_omits_message_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_narrate(terminal=False) must NOT emit message_done.

    Used for the initial "Dispatching..." narration before the task actually
    completes — firing message_done up front would clear the frontend's
    "assistant speaking" state and race with the later task_complete +
    terminal narration.
    """
    from app import websockets as ws_mod

    async def fake_synth(text: str):
        yield b"chunk"

    monkeypatch.setattr(ws_mod.tts_service, "synthesize_stream", fake_synth)

    ws = FakeWebSocket()
    await ws_mod._narrate(ws, "dispatching...", terminal=False)

    types = [f["type"] for f in ws.sent if isinstance(f, dict)]
    assert "message_done" not in types, (
        f"non-terminal narration leaked message_done: {types}"
    )
    # tts_end is still emitted.
    assert types[-1] == "tts_end"


# --------------------------------------------------------------- fix #5 test

@pytest.mark.asyncio
async def test_route_task_dispatch_failure_narrates_and_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If _dispatcher.dispatch() raises an unexpected exception (e.g.
    FileNotFoundError because `claude` isn't on PATH), _route_task must:

      1. Narrate a user-visible failure message.
      2. Fall back to chat so Chief still responds.

    Hawke HIGH — without this wrapper the exception dies silently in the
    background task and the user sees nothing.
    """
    from app import websockets as ws_mod

    # Point the glue at a repo that exists so we reach the dispatcher call.
    monkeypatch.setattr(
        ws_mod,
        "get_repo_path",
        lambda project: __import__("pathlib").Path("/tmp"),
    )

    async def boom_dispatch(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError("claude: command not found")

    monkeypatch.setattr(ws_mod._dispatcher, "dispatch", boom_dispatch)

    narrations: list[str] = []
    chat_fallbacks: list[str] = []

    async def fake_narrate(ws: Any, text: str, *, terminal: bool = True, **_: Any) -> None:
        narrations.append(text)

    async def fake_handle_text_turn(
        ws: Any, session_id: str, history: list, text: str, project_scope: str,
        *args: Any, **kwargs: Any,
    ) -> None:
        # *args/**kwargs absorb current_speed + stt_seconds threaded through
        # from _route_task's fallback path (so the fallback LLM turn narrates
        # at the user's rate and bills the STT leg against the turn row).
        chat_fallbacks.append(text)

    monkeypatch.setattr(ws_mod, "_narrate", fake_narrate)
    monkeypatch.setattr(ws_mod, "_handle_text_turn", fake_handle_text_turn)

    ws = FakeWebSocket()
    await ws_mod._route_task(
        ws=ws,
        session_id="sess-err",
        history=[],
        task_spec="add tests for parser",
        current_project="chief",
    )

    # User-visible narration about the failure.
    assert any("Task dispatch failed" in n for n in narrations), (
        f"no failure narration emitted: {narrations}"
    )
    # Fell back to chat with the same user intent so they aren't left stranded.
    assert chat_fallbacks == ["add tests for parser"], (
        f"chat fallback didn't fire: {chat_fallbacks}"
    )


# --------------------------------------------------------------- fix #1 test

@pytest.mark.asyncio
async def test_route_task_rejects_leading_dash_task_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vera HIGH defense-in-depth: a task_spec starting with '-' is
    rejected with a clarifying narration and never reaches dispatch.
    """
    from app import websockets as ws_mod

    dispatch_calls: list[Any] = []

    async def should_not_dispatch(*args: Any, **kwargs: Any) -> None:
        dispatch_calls.append((args, kwargs))

    monkeypatch.setattr(ws_mod._dispatcher, "dispatch", should_not_dispatch)

    narrations: list[str] = []

    async def fake_narrate(ws: Any, text: str, *, terminal: bool = True, **_: Any) -> None:
        narrations.append(text)

    monkeypatch.setattr(ws_mod, "_narrate", fake_narrate)

    ws = FakeWebSocket()
    await ws_mod._route_task(
        ws=ws,
        session_id="sess-dash",
        history=[],
        task_spec="--help me out",
        current_project="chief",
    )

    assert dispatch_calls == [], "leading-dash task_spec should not reach dispatch"
    assert any("command flag" in n for n in narrations), (
        f"expected clarifying narration, got: {narrations}"
    )


# ---------------------------------------- fix: TTS tally on actual send
#
# Before the tally was moved into tts_worker, every sentence enqueued counted
# against tts_char_total whether or not synthesize_stream ultimately billed
# for it. If Google raised mid-turn (logged as "TTS failed for sentence ..."
# in the worker), the dropped sentence still showed up on the user's usage
# bill. This test mocks synthesize_stream to fail on the first sentence and
# succeed on the second, then asserts record_tts_usage received chars for
# ONLY the successful sentence.


@pytest.mark.asyncio
async def test_tts_char_tally_excludes_failed_synthesis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If synthesize_stream raises for one sentence and succeeds for another,
    tts_char_total (recorded via record_tts_usage.chars) reflects ONLY the
    successful sentence — we don't over-count dropped audio.
    """
    from app import websockets as ws_mod

    # Force non-deep routing so no bridge phrase is emitted; the bridge is
    # tallied through the same worker path, but isolating the two stream_turn
    # sentences keeps the assertion simple.
    monkeypatch.setattr(
        ws_mod, "classify_and_route", lambda text: ("haiku", False),
    )

    # stream_turn emits two sentences: the first will fail in TTS, the second
    # will succeed. Returns a minimal usage dict for the downstream record_turn
    # call.
    async def fake_stream_turn(
        *, history: list, model: str, send_token: Any, send_tts_sentence: Any,
        project_scope: str, system_blocks: Any,
    ) -> dict:
        await send_tts_sentence("first sentence")   # 14 chars — will FAIL
        await send_tts_sentence("second sentence of text")  # 23 chars — OK
        return {
            "assistant_text": "first sentence second sentence of text",
            "input_tokens": 1,
            "output_tokens": 1,
            "cache_read_input_tokens": 0,
        }

    monkeypatch.setattr(ws_mod, "stream_turn", fake_stream_turn)

    # build_chief_system is called via to_thread — return a simple blob.
    monkeypatch.setattr(
        ws_mod, "build_chief_system", lambda scope: [{"type": "text", "text": "x"}],
    )

    # synthesize_stream: raise on the first sentence, yield on the second.
    async def flaky_synth(sentence: str, **_: Any):
        if sentence == "first sentence":
            raise RuntimeError("simulated Google TTS outage")
        yield b"audio-chunk"

    monkeypatch.setattr(ws_mod.tts_service, "synthesize_stream", flaky_synth)

    # Persistence + usage stubs — capture tts_chars out of record_tts_usage.
    async def noop_append_turn(*args: Any, **kwargs: Any) -> None:
        return None

    async def fake_record_turn(**kwargs: Any) -> dict:
        return {"id": 1, "cost_cents": 0}

    async def fake_record_stt_usage(**kwargs: Any) -> dict:
        return {
            "stt_seconds": kwargs.get("audio_seconds", 0.0),
            "stt_cost_usd": 0.0,
            "stt_provider": kwargs.get("provider", "local"),
        }

    captured: dict[str, Any] = {}

    async def fake_record_tts_usage(**kwargs: Any) -> dict:
        captured["chars"] = kwargs.get("chars")
        return {
            "tts_chars": kwargs.get("chars", 0),
            "tts_cost_usd": 0.0,
            "tts_provider": kwargs.get("provider", "local"),
        }

    async def fake_get_session_totals(session_id: str) -> dict:
        return {"cost_cents": 0, "voice": {"total_usd": 0.0}}

    monkeypatch.setattr(ws_mod, "append_turn", noop_append_turn)
    monkeypatch.setattr(ws_mod, "record_turn", fake_record_turn)
    monkeypatch.setattr(ws_mod, "record_stt_usage", fake_record_stt_usage)
    monkeypatch.setattr(ws_mod, "record_tts_usage", fake_record_tts_usage)
    monkeypatch.setattr(ws_mod, "get_session_totals", fake_get_session_totals)

    ws = FakeWebSocket()
    await ws_mod._run_llm_turn(
        ws=ws,
        session_id="sess-tally",
        history=[],
        user_text="hi",
        project_scope="chief",
    )

    # Only the successful sentence should be tallied. "first sentence" (14)
    # would have been billed in the old code — we assert it's NOT.
    assert captured["chars"] == len("second sentence of text"), (
        f"expected tally to reflect only the successful synth, got {captured['chars']!r}"
    )


# ---------------------------------------- fix: cancel-path billing
#
# Riggs CRITICAL 2026-04-24 — barge-in / supersede / task-cancel raise
# CancelledError inside _run_llm_turn before the success-path persistence.
# Google has already billed for the audio seconds we sent to STT and the
# chars we sent to TTS, so we must mirror that on disk. The finally guard
# in _run_llm_turn writes a partial-turn row + STT/TTS usage whenever the
# success path didn't.


@pytest.mark.asyncio
async def test_run_llm_turn_records_voice_usage_on_cancel_midstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel after one TTS sentence has been synthesized → record_tts_usage
    fires on the cancel path with the chars synthesized so far.

    Mirrors the user-visible behavior: the user barges in mid-reply, Google
    has already billed for the first sentence's TTS chars, and the partial
    audio seconds for the user's utterance are real spend. /api/usage rollups
    must reflect both legs.
    """
    from app import websockets as ws_mod

    monkeypatch.setattr(
        ws_mod, "classify_and_route", lambda text: ("haiku", False),
    )

    # Slow stream: emit the first sentence, then sleep long enough for the
    # outer cancel to land BEFORE the second sentence is enqueued.
    sentence_one_event = asyncio.Event()

    async def slow_stream_turn(
        *, history: list, model: str, send_token: Any, send_tts_sentence: Any,
        project_scope: str, system_blocks: Any,
    ) -> dict:
        await send_tts_sentence("first cancelled sentence")  # 24 chars
        sentence_one_event.set()
        # Hold long enough that the test's cancel lands first. If cancel
        # doesn't reach us we'd happily keep streaming.
        await asyncio.sleep(5.0)
        await send_tts_sentence("second never reaches tts")
        return {
            "assistant_text": "first cancelled sentence",
            "input_tokens": 1, "output_tokens": 1,
            "cache_read_input_tokens": 0,
        }

    monkeypatch.setattr(ws_mod, "stream_turn", slow_stream_turn)
    monkeypatch.setattr(
        ws_mod, "build_chief_system", lambda scope: [{"type": "text", "text": "x"}],
    )

    # synthesize_stream succeeds quickly so the first sentence's chars do
    # land in tts_char_total before cancel.
    async def fast_synth(sentence: str, **_: Any):
        yield b"audio"

    monkeypatch.setattr(ws_mod.tts_service, "synthesize_stream", fast_synth)

    # Persistence stubs — capture all record_* calls so we can assert the
    # cancel path wrote both STT and TTS rows.
    record_turn_calls: list[dict] = []
    record_stt_calls: list[dict] = []
    record_tts_calls: list[dict] = []

    async def noop_append_turn(*args: Any, **kwargs: Any) -> None:
        return None

    async def fake_record_turn(**kwargs: Any) -> dict:
        record_turn_calls.append(kwargs)
        return {"id": len(record_turn_calls), "cost_cents": 0}

    async def fake_record_stt_usage(**kwargs: Any) -> dict:
        record_stt_calls.append(kwargs)
        return {
            "stt_seconds": kwargs.get("audio_seconds", 0.0),
            "stt_cost_usd": 0.0,
            "stt_provider": kwargs.get("provider", "local"),
        }

    async def fake_record_tts_usage(**kwargs: Any) -> dict:
        record_tts_calls.append(kwargs)
        return {
            "tts_chars": kwargs.get("chars", 0),
            "tts_cost_usd": 0.0,
            "tts_provider": kwargs.get("provider", "local"),
        }

    monkeypatch.setattr(ws_mod, "append_turn", noop_append_turn)
    monkeypatch.setattr(ws_mod, "record_turn", fake_record_turn)
    monkeypatch.setattr(ws_mod, "record_stt_usage", fake_record_stt_usage)
    monkeypatch.setattr(ws_mod, "record_tts_usage", fake_record_tts_usage)

    ws = FakeWebSocket()

    async def run_turn():
        await ws_mod._run_llm_turn(
            ws=ws,
            session_id="sess-cancel",
            history=[],
            user_text="hello",
            project_scope="chief",
            stt_seconds=2.5,  # real audio spend that must be billed
        )

    task = asyncio.create_task(run_turn())
    # Wait until the first sentence has been enqueued + synthesized.
    await asyncio.wait_for(sentence_one_event.wait(), timeout=2.0)
    # Give tts_worker a tick to actually run synthesize_stream and bump
    # tts_char_total before we cancel.
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Cancel-path billing — the success path never ran, but the finally
    # guard must have written ONE turn row with the partial assistant text
    # + STT seconds + TTS chars for the synthesized sentence.
    assert len(record_turn_calls) == 1, (
        f"expected exactly one partial turn row on cancel; got {len(record_turn_calls)}"
    )
    partial = record_turn_calls[0]
    # Cost-model expectation: zero LLM tokens (we didn't get a final usage).
    assert partial["usage_dict"]["input_tokens"] == 0
    assert partial["usage_dict"]["output_tokens"] == 0

    # STT leg recorded with real seconds.
    assert len(record_stt_calls) == 1, (
        f"expected STT usage write on cancel; got {record_stt_calls}"
    )
    assert record_stt_calls[0]["audio_seconds"] == 2.5

    # TTS leg recorded with the chars we actually synthesized before cancel.
    assert len(record_tts_calls) == 1, (
        f"expected TTS usage write on cancel; got {record_tts_calls}"
    )
    assert record_tts_calls[0]["chars"] == len("first cancelled sentence"), (
        f"expected {len('first cancelled sentence')} TTS chars billed on cancel, "
        f"got {record_tts_calls[0]['chars']}"
    )


@pytest.mark.asyncio
async def test_run_llm_turn_skips_partial_billing_on_clean_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No partial-turn row when the success path already wrote one.

    Guards against the finally guard double-billing a fully-completed turn.
    """
    from app import websockets as ws_mod

    monkeypatch.setattr(
        ws_mod, "classify_and_route", lambda text: ("haiku", False),
    )

    async def fake_stream_turn(
        *, history: list, model: str, send_token: Any, send_tts_sentence: Any,
        project_scope: str, system_blocks: Any,
    ) -> dict:
        await send_tts_sentence("only sentence")
        return {
            "assistant_text": "only sentence",
            "input_tokens": 5, "output_tokens": 3,
            "cache_read_input_tokens": 0,
        }

    monkeypatch.setattr(ws_mod, "stream_turn", fake_stream_turn)
    monkeypatch.setattr(
        ws_mod, "build_chief_system", lambda scope: [{"type": "text", "text": "x"}],
    )

    async def fast_synth(sentence: str, **_: Any):
        yield b"audio"

    monkeypatch.setattr(ws_mod.tts_service, "synthesize_stream", fast_synth)

    record_turn_calls: list[dict] = []
    record_stt_calls: list[dict] = []
    record_tts_calls: list[dict] = []

    async def noop_append_turn(*args: Any, **kwargs: Any) -> None:
        return None

    async def fake_record_turn(**kwargs: Any) -> dict:
        record_turn_calls.append(kwargs)
        return {"id": len(record_turn_calls), "cost_cents": 0}

    async def fake_record_stt_usage(**kwargs: Any) -> dict:
        record_stt_calls.append(kwargs)
        return {"stt_seconds": 0.0, "stt_cost_usd": 0.0, "stt_provider": "local"}

    async def fake_record_tts_usage(**kwargs: Any) -> dict:
        record_tts_calls.append(kwargs)
        return {"tts_chars": 0, "tts_cost_usd": 0.0, "tts_provider": "local"}

    async def fake_get_session_totals(session_id: str) -> dict:
        return {"cost_cents": 0, "voice": {"total_usd": 0.0}}

    monkeypatch.setattr(ws_mod, "append_turn", noop_append_turn)
    monkeypatch.setattr(ws_mod, "record_turn", fake_record_turn)
    monkeypatch.setattr(ws_mod, "record_stt_usage", fake_record_stt_usage)
    monkeypatch.setattr(ws_mod, "record_tts_usage", fake_record_tts_usage)
    monkeypatch.setattr(ws_mod, "get_session_totals", fake_get_session_totals)

    ws = FakeWebSocket()
    await ws_mod._run_llm_turn(
        ws=ws,
        session_id="sess-clean",
        history=[],
        user_text="hi",
        project_scope="chief",
        stt_seconds=1.0,
    )

    # Exactly ONE turn row + one of each usage call — finally guard saw
    # ``persisted=True`` and skipped the partial-record path.
    assert len(record_turn_calls) == 1
    assert len(record_stt_calls) == 1
    assert len(record_tts_calls) == 1


# ---------------------------------------- fix: narrate billing
#
# Riggs CRITICAL 2026-04-24 — every _narrate() call is real Google TTS
# spend. Before this fix, dispatch / status / cancel narrations went out
# unbilled. Now the helper writes a synthetic narration turn row +
# record_tts_usage when synthesis completes successfully and a session_id
# is provided.


@pytest.mark.asyncio
async def test_narrate_records_tts_usage_when_session_id_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful narration with session_id produces exactly one turn
    row tagged 'narration' + one record_tts_usage call with the right
    char count."""
    from app import websockets as ws_mod

    async def fake_synth(text: str, **_: Any):
        yield b"audio-chunk"

    monkeypatch.setattr(ws_mod.tts_service, "synthesize_stream", fake_synth)

    record_turn_calls: list[dict] = []
    record_tts_calls: list[dict] = []

    async def fake_record_turn(**kwargs: Any) -> dict:
        record_turn_calls.append(kwargs)
        return {"id": len(record_turn_calls), "cost_cents": 0}

    async def fake_record_tts_usage(**kwargs: Any) -> dict:
        record_tts_calls.append(kwargs)
        return {"tts_chars": kwargs.get("chars", 0), "tts_cost_usd": 0.0, "tts_provider": "local"}

    monkeypatch.setattr(ws_mod, "record_turn", fake_record_turn)
    monkeypatch.setattr(ws_mod, "record_tts_usage", fake_record_tts_usage)

    ws = FakeWebSocket()
    text = "Task complete. Exit code 0."
    await ws_mod._narrate(ws, text, terminal=True, session_id="sess-narrate")

    assert len(record_turn_calls) == 1, (
        f"expected one narration turn row, got {len(record_turn_calls)}"
    )
    assert record_turn_calls[0]["model"] == "narration"
    assert record_turn_calls[0]["assistant_text"] == text

    assert len(record_tts_calls) == 1
    assert record_tts_calls[0]["chars"] == len(text)


@pytest.mark.asyncio
async def test_narrate_no_billing_when_session_id_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backwards-compat: callers that don't pass session_id still work
    without crashing AND don't produce phantom turn rows.

    Defends the existing test suite (which calls _narrate without a session
    in test_narrate_terminal_order / test_narrate_non_terminal_omits_message_done)
    from regressing.
    """
    from app import websockets as ws_mod

    async def fake_synth(text: str, **_: Any):
        yield b"audio"

    monkeypatch.setattr(ws_mod.tts_service, "synthesize_stream", fake_synth)

    record_turn_calls: list[dict] = []
    record_tts_calls: list[dict] = []

    async def fake_record_turn(**kwargs: Any) -> dict:
        record_turn_calls.append(kwargs)
        return {"id": 1, "cost_cents": 0}

    async def fake_record_tts_usage(**kwargs: Any) -> dict:
        record_tts_calls.append(kwargs)
        return {"tts_chars": 0, "tts_cost_usd": 0.0, "tts_provider": "local"}

    monkeypatch.setattr(ws_mod, "record_turn", fake_record_turn)
    monkeypatch.setattr(ws_mod, "record_tts_usage", fake_record_tts_usage)

    ws = FakeWebSocket()
    await ws_mod._narrate(ws, "no session here", terminal=True)

    assert record_turn_calls == []
    assert record_tts_calls == []


@pytest.mark.asyncio
async def test_narrate_no_billing_on_synthesis_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If synthesize_stream raises, no chars went to Google → no billing."""
    from app import websockets as ws_mod

    async def boom_synth(text: str, **_: Any):
        raise RuntimeError("Google TTS outage")
        yield b""  # pragma: no cover — async generator structural requirement

    monkeypatch.setattr(ws_mod.tts_service, "synthesize_stream", boom_synth)

    record_turn_calls: list[dict] = []
    record_tts_calls: list[dict] = []

    async def fake_record_turn(**kwargs: Any) -> dict:
        record_turn_calls.append(kwargs)
        return {"id": 1, "cost_cents": 0}

    async def fake_record_tts_usage(**kwargs: Any) -> dict:
        record_tts_calls.append(kwargs)
        return {"tts_chars": 0, "tts_cost_usd": 0.0, "tts_provider": "local"}

    monkeypatch.setattr(ws_mod, "record_turn", fake_record_turn)
    monkeypatch.setattr(ws_mod, "record_tts_usage", fake_record_tts_usage)

    ws = FakeWebSocket()
    await ws_mod._narrate(ws, "this won't synth", session_id="sess-fail")

    # Not billed: synth raised before any chunk made it out, so Google
    # didn't charge us either.
    assert record_turn_calls == []
    assert record_tts_calls == []
