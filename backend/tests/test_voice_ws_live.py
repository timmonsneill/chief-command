"""Stage 2 voice_ws tests — Gemini Live wiring.

Validates the WS handler's contract with ``services.gemini_live.LiveSession``:
inbound bytes go to ``send_audio``, inbound text frames route per
discriminant, scope flip closes + reopens the session against the new
scope's system prompt, audio-chunk callback bytes ride out on the WS as
binary frames, and a turn-complete dispatch triggers history persistence
+ ``maybe_rollup`` fire-and-forget.

Architecture note: ``app.websockets`` does eager imports of services
(stt_service, tts_service, gemini_brain, llm.stream_turn, etc.) that the
unit-test conftest stubs out from the ``services`` package. We install
the same stand-ins ``test_dispatch_glue.py`` uses BEFORE the first
``from app import websockets`` import in this file.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import types
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("OWNER_PASSWORD", "test")
os.environ.setdefault("JWT_SECRET", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")


# ---------------------------------------------------------------------------
# Service stubs — same pattern as test_dispatch_glue.py
# ---------------------------------------------------------------------------
def _install_service_stubs() -> None:
    svc_pkg = sys.modules.get("services")
    if svc_pkg is None:
        return
    if not hasattr(svc_pkg, "stt_service"):
        svc_pkg.stt_service = types.SimpleNamespace(  # type: ignore[attr-defined]
            transcribe=lambda data: "",
            provider_name="test",
        )
    if not hasattr(svc_pkg, "tts_service"):
        async def _no_chunks(text: str, **_kw):
            if False:
                yield b""
            return
        svc_pkg.tts_service = types.SimpleNamespace(  # type: ignore[attr-defined]
            synthesize_stream=_no_chunks,
            provider_name="test",
        )
    if "services.auth" not in sys.modules:
        auth_mod = types.ModuleType("services.auth")
        auth_mod.verify_token = lambda token: "owner"  # type: ignore[attr-defined]
        sys.modules["services.auth"] = auth_mod


_install_service_stubs()


# ---------------------------------------------------------------------------
# FakeWebSocket — duck-types fastapi.WebSocket for the bits voice_ws uses
# ---------------------------------------------------------------------------
class FakeWebSocket:
    """Minimal WebSocket stub.

    Drives ``voice_ws`` by enqueueing inbound frames and recording all
    outbound frames. The ``receive()`` coroutine pops from the inbound
    queue; when the queue is exhausted (and the test calls
    ``finish()``), the next receive() raises ``WebSocketDisconnect`` so
    voice_ws's main loop exits cleanly into its ``finally`` block.
    """

    def __init__(self) -> None:
        self.query_params: dict[str, str] = {"token": "test-token"}
        self.accepted = False
        self.closed_with: Optional[int] = None
        self.outbound_json: list[dict] = []
        self.outbound_bytes: list[bytes] = []
        self._inbound: asyncio.Queue = asyncio.Queue()
        self._finished = False
        # voice_ws stashes its serialization lock here.
        self._send_lock: Optional[asyncio.Lock] = None

    # ---- driving the test side ----
    async def push_text(self, text: str) -> None:
        await self._inbound.put({"type": "websocket.receive", "text": text})

    async def push_json(self, payload: dict) -> None:
        await self.push_text(json.dumps(payload))

    async def push_bytes(self, data: bytes) -> None:
        await self._inbound.put({"type": "websocket.receive", "bytes": data})

    async def finish(self) -> None:
        """Signal end-of-input — the next ``receive()`` raises disconnect."""
        self._finished = True
        await self._inbound.put({"type": "websocket.disconnect"})

    # ---- WebSocket-shaped surface voice_ws uses ----
    async def accept(self) -> None:
        self.accepted = True

    async def receive(self) -> dict:
        msg = await self._inbound.get()
        return msg

    async def receive_text(self) -> str:
        # Used by _authenticate_ws as a fallback; tests put the token on
        # query_params so this should never fire.
        raise asyncio.TimeoutError("not used in these tests")

    async def send_json(self, payload: dict) -> None:
        self.outbound_json.append(dict(payload))

    async def send_bytes(self, data: bytes) -> None:
        self.outbound_bytes.append(bytes(data))

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code


# ---------------------------------------------------------------------------
# Fake LiveSession — the surface voice_ws expects
# ---------------------------------------------------------------------------
class FakeLiveSession:
    """Drop-in for ``services.gemini_live.LiveSession``.

    Captures construction kwargs (so we can assert system_prompt content)
    + every send_audio / send_text / cancel / close call. Exposes the
    caller's callbacks so the test can drive on_audio_chunk /
    on_turn_complete events out-of-band.
    """

    instances: list["FakeLiveSession"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.model = kwargs.get("model")
        self.system_prompt = kwargs.get("system_prompt", "")
        self.on_audio_chunk = kwargs.get("on_audio_chunk")
        self.on_input_transcript = kwargs.get("on_input_transcript")
        self.on_output_transcript = kwargs.get("on_output_transcript")
        self.on_interrupted = kwargs.get("on_interrupted")
        self.on_turn_complete = kwargs.get("on_turn_complete")
        self.on_session_resumed = kwargs.get("on_session_resumed")
        self.on_go_away = kwargs.get("on_go_away")

        self.opened = False
        self.closed = False
        self.sent_audio: list[bytes] = []
        self.sent_text: list[str] = []
        self.cancel_calls = 0
        FakeLiveSession.instances.append(self)

    async def open(self) -> None:
        self.opened = True

    async def close(self, *, pump_grace_seconds: float = 0.0) -> None:
        self.closed = True

    async def send_audio(self, pcm: bytes) -> None:
        self.sent_audio.append(bytes(pcm))

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)

    async def cancel_current_turn(self) -> None:
        self.cancel_calls += 1
        if self.on_interrupted is not None:
            await self.on_interrupted()


@pytest.fixture(autouse=True)
def _reset_fake_live_instances():
    FakeLiveSession.instances.clear()
    yield
    FakeLiveSession.instances.clear()


# ---------------------------------------------------------------------------
# Patch the voice_ws dependencies at module level
# ---------------------------------------------------------------------------
@pytest.fixture
def patched_ws(monkeypatch):
    """Install fakes for every dependency voice_ws calls.

    Returns the imported ``app.websockets`` module so tests can directly
    invoke ``voice_ws`` and inspect FakeLiveSession instances.
    """
    from app import websockets as ws_mod

    # Auth always succeeds.
    async def fake_authenticate(_ws):
        return "owner"
    monkeypatch.setattr(ws_mod, "_authenticate_ws", fake_authenticate)

    # Replace LiveSession constructor with the fake.
    monkeypatch.setattr(ws_mod, "LiveSession", FakeLiveSession)

    # Memory load: deterministic stub. Each call returns (summary, history)
    # tagged with the scope so scope-flip-rebuild is observable.
    load_calls: list[tuple] = []

    async def fake_load_memory(scope: str, raw_limit: int = 20):
        load_calls.append((scope, raw_limit))
        return f"<rolling-summary scope={scope}>", [
            {"role": "user", "content": f"prior-{scope}"}
        ]
    monkeypatch.setattr(ws_mod, "load_persistent_memory", fake_load_memory)

    # Build the system prompt deterministically with the scope embedded so
    # we can assert that the new LiveSession on a flip carries the new
    # scope's prompt.
    def fake_build_prompt(scope: str, prior_summary=None) -> str:
        return f"[CHIEF scope={scope} summary={prior_summary or 'none'}]"
    monkeypatch.setattr(ws_mod, "build_chief_system_string", fake_build_prompt)

    # Memory rollup spawn — record calls without actually running.
    rollup_spawns: list[str] = []

    async def fake_maybe_rollup(scope: str):
        rollup_spawns.append(scope)
    monkeypatch.setattr(ws_mod, "maybe_rollup", fake_maybe_rollup)

    # History append — record calls.
    appends: list[tuple] = []

    async def fake_append_turn(sid, scope, role, text):
        appends.append((sid, scope, role, text))
    monkeypatch.setattr(ws_mod, "append_turn", fake_append_turn)

    # Usage tracker — record calls.
    sessions_created: list[tuple] = []
    sessions_closed: list[str] = []
    record_turn_calls: list[dict] = []

    async def fake_create_session(sid, project=None):
        sessions_created.append((sid, project))

    async def fake_close_session(sid):
        sessions_closed.append(sid)

    async def fake_record_turn(*, session_id, model, usage_dict, user_text, assistant_text):
        record_turn_calls.append({
            "session_id": session_id,
            "model": model,
            "usage_dict": dict(usage_dict),
            "user_text": user_text,
            "assistant_text": assistant_text,
        })
        return {"id": 42, "cost_cents": 0}

    async def fake_get_session_totals(sid):
        return {"cost_cents": 0}

    monkeypatch.setattr(ws_mod, "create_session", fake_create_session)
    monkeypatch.setattr(ws_mod, "close_session", fake_close_session)
    monkeypatch.setattr(ws_mod, "record_turn", fake_record_turn)
    monkeypatch.setattr(ws_mod, "get_session_totals", fake_get_session_totals)

    # CC pool teardown — no-op.
    fake_pool = MagicMock()
    fake_pool.teardown_other_scopes = MagicMock(
        side_effect=lambda **_kw: _async_none()
    )
    monkeypatch.setattr(ws_mod.cc_session, "get_pool", lambda: fake_pool)

    # Project context — make sure both Chief Command and Arch are valid.
    monkeypatch.setattr(ws_mod, "AVAILABLE_PROJECTS", {"Chief Command", "Arch"})
    monkeypatch.setattr(ws_mod, "DEFAULT_PROJECT", "Chief Command")
    monkeypatch.setattr(ws_mod, "_context_store", {})

    # Dispatcher — quiet stub.
    fake_dispatcher = MagicMock()
    fake_dispatcher.cancel = MagicMock(side_effect=lambda _sid: _async_none())
    monkeypatch.setattr(ws_mod, "_dispatcher", fake_dispatcher)

    return types.SimpleNamespace(
        ws_mod=ws_mod,
        load_calls=load_calls,
        rollup_spawns=rollup_spawns,
        appends=appends,
        sessions_created=sessions_created,
        sessions_closed=sessions_closed,
        record_turn_calls=record_turn_calls,
        dispatcher=fake_dispatcher,
    )


async def _async_none():
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_voice_ws_opens_live_session_with_initial_scope_prompt(patched_ws):
    """WS open builds + opens a LiveSession against the rehydrated scope.

    Confirms the system_prompt embeds the scope-specific rolling summary
    (i.e. ``build_chief_system_string`` runs AFTER ``load_persistent_memory``,
    so summary is populated).
    """
    ws = FakeWebSocket()
    await ws.finish()  # no inbound frames; just open + close
    await patched_ws.ws_mod.voice_ws(ws)

    assert len(FakeLiveSession.instances) == 1
    sess = FakeLiveSession.instances[0]
    assert sess.opened is True
    assert sess.closed is True  # finally closed it
    assert sess.model == patched_ws.ws_mod.LIVE_MODEL
    # System prompt carries the rehydrated summary for the initial scope.
    assert "scope=Chief Command" in sess.system_prompt
    assert "summary=<rolling-summary scope=Chief Command>" in sess.system_prompt


@pytest.mark.asyncio
async def test_voice_ws_forwards_binary_frames_to_send_audio(patched_ws):
    """Inbound 16kHz PCM frames forward to LiveSession.send_audio."""
    ws = FakeWebSocket()
    await ws.push_bytes(b"\x01\x02\x03\x04")
    await ws.push_bytes(b"\x05\x06")
    await ws.finish()
    await patched_ws.ws_mod.voice_ws(ws)

    sess = FakeLiveSession.instances[0]
    assert sess.sent_audio == [b"\x01\x02\x03\x04", b"\x05\x06"]


@pytest.mark.asyncio
async def test_voice_ws_text_frame_routes_to_send_text(patched_ws):
    """``{"type":"text","content":"hi"}`` calls LiveSession.send_text("hi")."""
    ws = FakeWebSocket()
    await ws.push_json({"type": "text", "content": "hi"})
    await ws.finish()
    await patched_ws.ws_mod.voice_ws(ws)

    sess = FakeLiveSession.instances[0]
    assert sess.sent_text == ["hi"]


@pytest.mark.asyncio
async def test_voice_ws_context_frame_triggers_scope_flip_with_new_prompt(patched_ws):
    """Context flip closes the old LiveSession and opens a new one with the
    new scope's system prompt.

    Two sessions in ``FakeLiveSession.instances`` post-flip:
      [0] initial (Chief Command), opened then closed
      [1] flipped (Arch),          opened (closed in finally)
    """
    ws = FakeWebSocket()
    await ws.push_json({"type": "context", "project": "Arch"})
    await ws.finish()
    await patched_ws.ws_mod.voice_ws(ws)

    assert len(FakeLiveSession.instances) == 2
    initial, flipped = FakeLiveSession.instances
    # Initial closed during the flip.
    assert initial.closed is True
    assert "scope=Chief Command" in initial.system_prompt
    # Flipped session has Arch's system prompt + summary.
    assert flipped.opened is True
    assert "scope=Arch" in flipped.system_prompt
    assert "summary=<rolling-summary scope=Arch>" in flipped.system_prompt
    # FE got the context_switched echo.
    types_seen = [f.get("type") for f in ws.outbound_json]
    assert "context_switched" in types_seen
    flip_frame = next(f for f in ws.outbound_json if f.get("type") == "context_switched")
    assert flip_frame["project"] == "Arch"


@pytest.mark.asyncio
async def test_voice_ws_context_frame_echoing_same_scope_is_noop(patched_ws):
    """An initial context frame matching the rehydrated scope must NOT
    close+reopen the LiveSession (would churn the warm session for nothing).
    """
    ws = FakeWebSocket()
    await ws.push_json({"type": "context", "project": "Chief Command"})
    await ws.finish()
    await patched_ws.ws_mod.voice_ws(ws)

    # Only one LiveSession ever opened — the initial one.
    assert len(FakeLiveSession.instances) == 1


@pytest.mark.asyncio
async def test_on_audio_chunk_callback_emits_binary_ws_frame(patched_ws):
    """Audio bytes from LiveSession's on_audio_chunk callback ride out on
    the WS as binary frames — this is the playback path."""
    ws = FakeWebSocket()
    await ws.finish()
    # Don't await voice_ws to completion yet — we need to fire the callback
    # while the session is open. Drive voice_ws as a task and fire
    # on_audio_chunk before the disconnect message is processed.
    task = asyncio.create_task(patched_ws.ws_mod.voice_ws(ws))
    # Wait for the LiveSession to be constructed.
    for _ in range(50):
        if FakeLiveSession.instances:
            break
        await asyncio.sleep(0.01)
    assert FakeLiveSession.instances, "LiveSession never instantiated"
    sess = FakeLiveSession.instances[0]
    # Fire two audio chunks via the callback the WS layer registered.
    await sess.on_audio_chunk(b"\xaa\xbb\xcc")
    await sess.on_audio_chunk(b"\xdd\xee")
    # Now let the disconnect process and the handler exit.
    await task

    assert ws.outbound_bytes == [b"\xaa\xbb\xcc", b"\xdd\xee"]


@pytest.mark.asyncio
async def test_on_turn_complete_persists_history_and_spawns_rollup(patched_ws):
    """A turn-complete callback drains transcript buffers into history +
    fires maybe_rollup. Records billing via record_turn."""
    ws = FakeWebSocket()
    # Need at least one inbound frame so ensure_session_id() runs and a
    # session_id exists when on_turn_complete tries to persist.
    await ws.push_bytes(b"\x00\x01")
    await ws.finish()
    task = asyncio.create_task(patched_ws.ws_mod.voice_ws(ws))
    # Wait for LiveSession.
    for _ in range(50):
        if FakeLiveSession.instances:
            break
        await asyncio.sleep(0.01)
    assert FakeLiveSession.instances
    sess = FakeLiveSession.instances[0]

    # Wait until the byte frame got forwarded to send_audio (proves the
    # receive loop ran at least once and ensure_session_id was called).
    for _ in range(50):
        if sess.sent_audio:
            break
        await asyncio.sleep(0.01)
    assert sess.sent_audio, "byte frame never forwarded — session_id won't be set"

    # Push transcripts then fire generation_complete via callbacks.
    await sess.on_input_transcript("hello chief", True)
    await sess.on_output_transcript("hi neill", True)
    await sess.on_turn_complete({
        "prompt_token_count": 100,
        "response_token_count": 40,
        "cached_content_token_count": 10,
        "total_token_count": 150,
        "audio_input_tokens": 80,
        "audio_output_tokens": 30,
        "text_input_tokens": 20,
        "text_output_tokens": 10,
    })
    # Allow the fire-and-forget rollup to schedule.
    await asyncio.sleep(0.05)
    await task

    # History persisted both halves.
    roles_persisted = [(scope, role, text) for (_sid, scope, role, text) in patched_ws.appends]
    assert ("Chief Command", "user", "hello chief") in roles_persisted
    assert ("Chief Command", "assistant", "hi neill") in roles_persisted

    # maybe_rollup spawned for the current scope.
    assert "Chief Command" in patched_ws.rollup_spawns

    # record_turn called with mapped usage + Live model.
    assert len(patched_ws.record_turn_calls) == 1
    call = patched_ws.record_turn_calls[0]
    assert call["model"] == patched_ws.ws_mod.LIVE_MODEL
    assert call["user_text"] == "hello chief"
    assert call["assistant_text"] == "hi neill"
    assert call["usage_dict"]["input_tokens"] == 100
    assert call["usage_dict"]["output_tokens"] == 40
    assert call["usage_dict"]["cache_read_input_tokens"] == 10

    # FE saw generation_complete + usage frames.
    types_seen = [f.get("type") for f in ws.outbound_json]
    assert "generation_complete" in types_seen
    assert "usage" in types_seen
    usage_frame = next(f for f in ws.outbound_json if f.get("type") == "usage")
    assert usage_frame["audio_input_tokens"] == 80
    assert usage_frame["audio_output_tokens"] == 30


@pytest.mark.asyncio
async def test_interrupt_frame_calls_cancel_current_turn(patched_ws):
    """``{"type":"interrupt"}`` calls LiveSession.cancel_current_turn."""
    ws = FakeWebSocket()
    await ws.push_json({"type": "interrupt"})
    await ws.finish()
    await patched_ws.ws_mod.voice_ws(ws)

    sess = FakeLiveSession.instances[0]
    assert sess.cancel_calls == 1


@pytest.mark.asyncio
async def test_input_and_output_transcripts_emit_ws_frames(patched_ws):
    """Live's transcription callbacks translate to ``input_transcript`` /
    ``output_transcript`` JSON frames on the WS."""
    ws = FakeWebSocket()
    await ws.finish()
    task = asyncio.create_task(patched_ws.ws_mod.voice_ws(ws))
    for _ in range(50):
        if FakeLiveSession.instances:
            break
        await asyncio.sleep(0.01)
    sess = FakeLiveSession.instances[0]
    await sess.on_input_transcript("partial", False)
    await sess.on_output_transcript("reply chunk", False)
    await sess.on_output_transcript("reply chunk done", True)
    await task

    in_frames = [f for f in ws.outbound_json if f.get("type") == "input_transcript"]
    out_frames = [f for f in ws.outbound_json if f.get("type") == "output_transcript"]
    assert in_frames == [{"type": "input_transcript", "text": "partial", "is_final": False}]
    assert out_frames == [
        {"type": "output_transcript", "text": "reply chunk", "is_final": False},
        {"type": "output_transcript", "text": "reply chunk done", "is_final": True},
    ]


@pytest.mark.asyncio
async def test_initial_open_failure_closes_with_4002(patched_ws):
    """If LiveSession.open() raises, the WS sends an error frame and closes
    with code 4002 (distinct from auth-fail 4001)."""
    class BadLiveSession(FakeLiveSession):
        async def open(self) -> None:
            raise RuntimeError("vertex auth failed")

    monkeypatch_target = patched_ws.ws_mod
    original = monkeypatch_target.LiveSession
    monkeypatch_target.LiveSession = BadLiveSession
    try:
        ws = FakeWebSocket()
        await ws.finish()
        await patched_ws.ws_mod.voice_ws(ws)
        assert ws.closed_with == 4002
        # Error frame went out.
        errors = [f for f in ws.outbound_json if f.get("type") == "error"]
        assert errors, "no error frame on initial open failure"
    finally:
        monkeypatch_target.LiveSession = original
