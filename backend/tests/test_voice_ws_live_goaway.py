"""Stage 4 — GoAway proactive reconnect on the Live voice WS.

Live API emits ``go_away`` ~30s before forcibly closing the underlying
transport (typically at the ~10min cap). The voice WS handler uses the
warning window to spin up a parallel LiveSession with the cached handle
and atomically swap once it's open, so the audio gap is bounded by the
open latency rather than the disconnect detection latency.

Tests cover:
  1. GoAway → new LiveSession opened with the cached resumption handle.
  2. The old session is closed AFTER the new one is open (swap order).
  3. The FE sees ``go_away`` then ``reconnecting`` then ``reconnected``
     in the right order.
  4. GoAway does NOT bump ``reconnect_attempts`` — the server's healthy
     rotation shouldn't consume the crash-retry budget.
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


def _install_service_stubs() -> None:
    svc_pkg = sys.modules.get("services")
    if svc_pkg is None:
        return
    if not hasattr(svc_pkg, "stt_service"):
        svc_pkg.stt_service = types.SimpleNamespace(
            transcribe=lambda data: "",
            provider_name="test",
        )
    if not hasattr(svc_pkg, "tts_service"):
        async def _no_chunks(text: str, **_kw):
            if False:
                yield b""
            return
        svc_pkg.tts_service = types.SimpleNamespace(
            synthesize_stream=_no_chunks,
            provider_name="test",
        )
    if "services.auth" not in sys.modules:
        auth_mod = types.ModuleType("services.auth")
        auth_mod.verify_token = lambda token: "owner"
        sys.modules["services.auth"] = auth_mod


_install_service_stubs()


class FakeWebSocket:
    def __init__(self) -> None:
        self.query_params = {"token": "test-token"}
        self.accepted = False
        self.closed_with: Optional[int] = None
        self.outbound_json: list[dict] = []
        self.outbound_bytes: list[bytes] = []
        self._inbound: asyncio.Queue = asyncio.Queue()
        self._send_lock: Optional[asyncio.Lock] = None

    async def push_text(self, text: str) -> None:
        await self._inbound.put({"type": "websocket.receive", "text": text})

    async def push_json(self, payload: dict) -> None:
        await self.push_text(json.dumps(payload))

    async def push_bytes(self, data: bytes) -> None:
        await self._inbound.put({"type": "websocket.receive", "bytes": data})

    async def finish(self) -> None:
        await self._inbound.put({"type": "websocket.disconnect"})

    async def accept(self) -> None:
        self.accepted = True

    async def receive(self) -> dict:
        return await self._inbound.get()

    async def receive_text(self) -> str:
        raise asyncio.TimeoutError("not used")

    async def send_json(self, payload: dict) -> None:
        self.outbound_json.append(dict(payload))

    async def send_bytes(self, data: bytes) -> None:
        self.outbound_bytes.append(bytes(data))

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code


class FakeLiveSession:
    """Variant that records the order of close calls, so the swap-order
    test can assert "new session opened BEFORE old session closed"."""

    instances: list["FakeLiveSession"] = []
    # Monotonic counter so each instance can stamp its open/close events
    # in absolute order across the lifecycle.
    _seq: int = 0

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.system_prompt = kwargs.get("system_prompt", "")
        self.on_audio_chunk = kwargs.get("on_audio_chunk")
        self.on_input_transcript = kwargs.get("on_input_transcript")
        self.on_output_transcript = kwargs.get("on_output_transcript")
        self.on_interrupted = kwargs.get("on_interrupted")
        self.on_turn_complete = kwargs.get("on_turn_complete")
        self.on_tool_call = kwargs.get("on_tool_call")
        self.on_session_resumed = kwargs.get("on_session_resumed")
        self.on_go_away = kwargs.get("on_go_away")
        self.on_pump_crash = kwargs.get("on_pump_crash")
        self.extra_tools = kwargs.get("extra_tools")
        self.resumption_handle = kwargs.get("resumption_handle")
        self.opened = False
        self.opened_at: Optional[int] = None
        self.closed = False
        self.closed_at: Optional[int] = None
        self.cancel_calls = 0
        self.sent_audio: list[bytes] = []
        FakeLiveSession.instances.append(self)

    @classmethod
    def _next_seq(cls) -> int:
        cls._seq += 1
        return cls._seq

    async def open(self) -> None:
        self.opened = True
        self.opened_at = self._next_seq()

    async def close(self, *, pump_grace_seconds: float = 0.0) -> None:
        self.closed = True
        self.closed_at = self._next_seq()

    async def send_audio(self, pcm: bytes) -> None:
        self.sent_audio.append(bytes(pcm))

    async def send_text(self, text: str) -> None:
        pass

    async def cancel_current_turn(self) -> None:
        self.cancel_calls += 1


@pytest.fixture(autouse=True)
def _reset():
    FakeLiveSession.instances.clear()
    FakeLiveSession._seq = 0
    yield
    FakeLiveSession.instances.clear()


@pytest.fixture
def patched_ws(monkeypatch):
    from app import websockets as ws_mod

    async def fake_authenticate(_ws):
        return "owner"
    monkeypatch.setattr(ws_mod, "_authenticate_ws", fake_authenticate)
    monkeypatch.setattr(ws_mod, "LiveSession", FakeLiveSession)

    async def fake_check_daily_cap(subject="owner"):
        return False, 0.0

    async def fake_check_soft_cap(subject="owner"):
        return False, 0.0
    monkeypatch.setattr(ws_mod, "check_daily_cap", fake_check_daily_cap)
    monkeypatch.setattr(ws_mod, "check_soft_cap", fake_check_soft_cap)

    monkeypatch.setattr(ws_mod, "to_gemini_tool", lambda: object())

    async def fake_load_memory(scope: str, raw_limit: int = 20):
        return f"<sum {scope}>", []
    monkeypatch.setattr(ws_mod, "load_persistent_memory", fake_load_memory)
    monkeypatch.setattr(
        ws_mod, "build_chief_system_string",
        lambda scope, prior_summary=None: f"[CHIEF scope={scope}]",
    )

    async def _noop_async(*a, **k):
        return None
    monkeypatch.setattr(ws_mod, "maybe_rollup", _noop_async)
    monkeypatch.setattr(ws_mod, "append_turn", _noop_async)
    monkeypatch.setattr(ws_mod, "create_session", _noop_async)
    monkeypatch.setattr(ws_mod, "close_session", _noop_async)

    async def fake_record_turn(**kw):
        return {"id": 1, "cost_cents": 0}

    async def fake_get_session_totals(sid):
        return {"cost_cents": 0}
    monkeypatch.setattr(ws_mod, "record_turn", fake_record_turn)
    monkeypatch.setattr(ws_mod, "get_session_totals", fake_get_session_totals)

    fake_pool = MagicMock()

    async def _async_none(**_kw):
        return None
    fake_pool.teardown_other_scopes = MagicMock(side_effect=_async_none)
    monkeypatch.setattr(ws_mod.cc_session, "get_pool", lambda: fake_pool)

    monkeypatch.setattr(ws_mod, "AVAILABLE_PROJECTS", {"Chief Command", "Arch"})
    monkeypatch.setattr(ws_mod, "DEFAULT_PROJECT", "Chief Command")
    monkeypatch.setattr(ws_mod, "_context_store", {})

    fake_dispatcher = MagicMock()

    async def _cancel(_sid):
        return None
    fake_dispatcher.cancel = MagicMock(side_effect=_cancel)
    monkeypatch.setattr(ws_mod, "_dispatcher", fake_dispatcher)

    return types.SimpleNamespace(ws_mod=ws_mod)


async def _wait_for_instance(n: int, timeout: float = 1.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if len(FakeLiveSession.instances) >= n:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"Expected {n} LiveSession instances, got {len(FakeLiveSession.instances)}"
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_go_away_triggers_proactive_reconnect_with_handle(patched_ws):
    """A GoAway frame after a session_resumption_update spawns a new
    LiveSession with the cached resumption handle."""
    ws = FakeWebSocket()
    await ws.push_bytes(b"\x00\x01")
    task = asyncio.create_task(patched_ws.ws_mod.voice_ws(ws))

    await _wait_for_instance(1)
    s0 = FakeLiveSession.instances[0]
    for _ in range(50):
        if s0.sent_audio:
            break
        await asyncio.sleep(0.01)

    # Server emitted a resumption handle on the Live wire.
    await s0.on_session_resumed("ga-handle-xyz")
    # GoAway notification — server is about to close.
    await s0.on_go_away(28.5)

    await _wait_for_instance(2)
    s1 = FakeLiveSession.instances[1]
    assert s1.resumption_handle == "ga-handle-xyz"
    assert s1.opened is True

    await ws.finish()
    await task


@pytest.mark.asyncio
async def test_go_away_swap_order_new_opens_before_old_closes(patched_ws):
    """The old LiveSession's ``close()`` must be called AFTER the new
    session's ``open()`` lands. If we closed first, the audio gap would
    be bounded by reconnect latency instead of swap latency."""
    ws = FakeWebSocket()
    await ws.push_bytes(b"\x00\x01")
    task = asyncio.create_task(patched_ws.ws_mod.voice_ws(ws))

    await _wait_for_instance(1)
    s0 = FakeLiveSession.instances[0]
    for _ in range(50):
        if s0.sent_audio:
            break
        await asyncio.sleep(0.01)

    await s0.on_session_resumed("ga-handle")
    await s0.on_go_away(20.0)

    await _wait_for_instance(2)
    s1 = FakeLiveSession.instances[1]

    # Wait for the swap (s0 should get closed once s1 is open + swapped in).
    for _ in range(50):
        if s0.closed:
            break
        await asyncio.sleep(0.01)

    assert s1.opened_at is not None
    assert s0.closed_at is not None
    assert s1.opened_at < s0.closed_at, (
        f"new session must open BEFORE old session closes "
        f"(s1.opened_at={s1.opened_at}, s0.closed_at={s0.closed_at})"
    )

    await ws.finish()
    await task


@pytest.mark.asyncio
async def test_go_away_emits_reconnecting_then_reconnected_frames(patched_ws):
    """FE sees ``go_away`` → ``reconnecting`` → ``reconnected`` in order."""
    ws = FakeWebSocket()
    await ws.push_bytes(b"\x00\x01")
    task = asyncio.create_task(patched_ws.ws_mod.voice_ws(ws))

    await _wait_for_instance(1)
    s0 = FakeLiveSession.instances[0]
    for _ in range(50):
        if s0.sent_audio:
            break
        await asyncio.sleep(0.01)

    await s0.on_session_resumed("h")
    await s0.on_go_away(30.0)

    await _wait_for_instance(2)
    # Wait for reconnected frame.
    for _ in range(100):
        types_seen = [f.get("type") for f in ws.outbound_json]
        if "reconnected" in types_seen:
            break
        await asyncio.sleep(0.01)

    types_seen = [f.get("type") for f in ws.outbound_json]
    go_away_idx = types_seen.index("go_away")
    reconnecting_idx = types_seen.index("reconnecting")
    reconnected_idx = types_seen.index("reconnected")
    assert go_away_idx < reconnecting_idx < reconnected_idx, (
        f"expected order go_away < reconnecting < reconnected; got {types_seen}"
    )

    await ws.finish()
    await task


@pytest.mark.asyncio
async def test_go_away_reconnect_does_not_consume_crash_retry_budget(
    patched_ws, monkeypatch,
):
    """A GoAway-triggered rebuild does NOT bump the crash-retry counter.

    Set max retries = 1, fire 3 GoAway events in a row (each spins a fresh
    rebuild via the cached handle). All three should succeed because they
    don't touch ``reconnect_attempts``.
    """
    monkeypatch.setattr(
        patched_ws.ws_mod.settings, "LIVE_RECONNECT_MAX_RETRIES", 1,
    )

    ws = FakeWebSocket()
    await ws.push_bytes(b"\x00\x01")
    task = asyncio.create_task(patched_ws.ws_mod.voice_ws(ws))

    await _wait_for_instance(1)
    s0 = FakeLiveSession.instances[0]
    for _ in range(50):
        if s0.sent_audio:
            break
        await asyncio.sleep(0.01)

    await s0.on_session_resumed("h0")
    await s0.on_go_away(30.0)
    await _wait_for_instance(2)

    s1 = FakeLiveSession.instances[1]
    # Need to mark a new resumption update on s1 so the next reconnect has
    # a handle to use. Otherwise the test is just exercising "no-handle
    # reconnect" twice.
    await s1.on_session_resumed("h1")
    await s1.on_go_away(30.0)
    await _wait_for_instance(3)

    s2 = FakeLiveSession.instances[2]
    await s2.on_session_resumed("h2")
    await s2.on_go_away(30.0)
    await _wait_for_instance(4)

    # All four sessions exist (initial + 3 GoAway rebuilds).
    assert len(FakeLiveSession.instances) == 4

    await ws.finish()
    await task
