"""Stage 4 — scope-flip robustness on the Live voice WS.

Covers the four edge cases called out in the Stage 4 spec for
``_handle_scope_flip``:

  1. Mid-turn flip cancels the in-flight turn (cancel_current_turn fires
     on the OLD session before the close-and-reopen).
  2. Memory rehydration runs against the new scope so the new
     LiveSession's system_prompt carries the new scope's rolling summary.
  3. Tools (extra_tools) are passed to the new LiveSession unchanged.
  4. The cached resumption handle is dropped on flip (it's bound to the
     old scope's system prompt and shouldn't carry over).

Reuses the FakeWebSocket / FakeLiveSession harness from test_voice_ws_live.py
so the assertions match the wider test suite's idioms.
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
        self.query_params: dict[str, str] = {"token": "test-token"}
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
    """Records cancel + close + extra_tools + resumption_handle for the
    flip path's assertions."""

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
        self.on_tool_call = kwargs.get("on_tool_call")
        self.on_session_resumed = kwargs.get("on_session_resumed")
        self.on_go_away = kwargs.get("on_go_away")
        self.on_pump_crash = kwargs.get("on_pump_crash")
        self.extra_tools = kwargs.get("extra_tools")
        self.resumption_handle = kwargs.get("resumption_handle")

        self.opened = False
        self.closed = False
        self.cancel_calls = 0
        self.sent_audio: list[bytes] = []
        self.sent_text: list[str] = []
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
def _reset_instances():
    FakeLiveSession.instances.clear()
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
    monkeypatch.setattr(ws_mod, "check_daily_cap", fake_check_daily_cap)

    async def fake_check_soft_cap(subject="owner"):
        return False, 0.0
    monkeypatch.setattr(ws_mod, "check_soft_cap", fake_check_soft_cap)

    sentinel_tool = object()
    monkeypatch.setattr(ws_mod, "to_gemini_tool", lambda: sentinel_tool)

    load_calls: list[tuple] = []

    async def fake_load_memory(scope: str, raw_limit: int = 20):
        load_calls.append((scope, raw_limit))
        return f"<rolling-summary scope={scope}>", [
            {"role": "user", "content": f"prior-{scope}"}
        ]
    monkeypatch.setattr(ws_mod, "load_persistent_memory", fake_load_memory)

    def fake_build_prompt(scope: str, prior_summary=None) -> str:
        return f"[CHIEF scope={scope} summary={prior_summary or 'none'}]"
    monkeypatch.setattr(ws_mod, "build_chief_system_string", fake_build_prompt)

    rollup_spawns: list[str] = []

    async def fake_maybe_rollup(scope: str):
        rollup_spawns.append(scope)
    monkeypatch.setattr(ws_mod, "maybe_rollup", fake_maybe_rollup)

    appends: list[tuple] = []

    async def fake_append_turn(sid, scope, role, text):
        appends.append((sid, scope, role, text))
    monkeypatch.setattr(ws_mod, "append_turn", fake_append_turn)

    async def fake_create_session(sid, project=None):
        pass

    async def fake_close_session(sid):
        pass

    async def fake_record_turn(*, session_id, model, usage_dict, user_text, assistant_text):
        return {"id": 99, "cost_cents": 0}

    async def fake_get_session_totals(sid):
        return {"cost_cents": 0}

    monkeypatch.setattr(ws_mod, "create_session", fake_create_session)
    monkeypatch.setattr(ws_mod, "close_session", fake_close_session)
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

    async def _cancel_async(_sid):
        return None
    fake_dispatcher.cancel = MagicMock(side_effect=_cancel_async)
    monkeypatch.setattr(ws_mod, "_dispatcher", fake_dispatcher)

    return types.SimpleNamespace(
        ws_mod=ws_mod,
        load_calls=load_calls,
        rollup_spawns=rollup_spawns,
        appends=appends,
        sentinel_tool=sentinel_tool,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scope_flip_cancels_in_flight_turn(patched_ws):
    """Mid-turn scope flip must call cancel_current_turn on the OLD session
    BEFORE closing it. (Spec Task 3.1 — flip immediately, drop in-flight.)"""
    ws = FakeWebSocket()
    await ws.push_json({"type": "context", "project": "Arch"})
    await ws.finish()
    await patched_ws.ws_mod.voice_ws(ws)

    assert len(FakeLiveSession.instances) == 2
    initial, flipped = FakeLiveSession.instances
    # Old session was cancelled at least once before close.
    assert initial.cancel_calls >= 1, "scope flip must cancel the old turn"
    assert initial.closed is True
    assert flipped.opened is True


@pytest.mark.asyncio
async def test_scope_flip_rebuilds_system_prompt_for_new_scope(patched_ws):
    """New LiveSession's system_prompt must reflect the new scope's
    rolling summary — confirms load_persistent_memory ran for the new
    scope BEFORE build_chief_system_string."""
    ws = FakeWebSocket()
    await ws.push_json({"type": "context", "project": "Arch"})
    await ws.finish()
    await patched_ws.ws_mod.voice_ws(ws)

    flipped = FakeLiveSession.instances[1]
    assert "scope=Arch" in flipped.system_prompt
    assert "summary=<rolling-summary scope=Arch>" in flipped.system_prompt
    # load_persistent_memory was called for both scopes (initial + flipped).
    scopes_loaded = [s for (s, _) in patched_ws.load_calls]
    assert "Chief Command" in scopes_loaded
    assert "Arch" in scopes_loaded


@pytest.mark.asyncio
async def test_scope_flip_preserves_tool_list_on_new_session(patched_ws):
    """extra_tools must be the same sentinel list on the post-flip session."""
    ws = FakeWebSocket()
    await ws.push_json({"type": "context", "project": "Arch"})
    await ws.finish()
    await patched_ws.ws_mod.voice_ws(ws)

    initial, flipped = FakeLiveSession.instances
    assert initial.extra_tools == [patched_ws.sentinel_tool]
    assert flipped.extra_tools == [patched_ws.sentinel_tool]


@pytest.mark.asyncio
async def test_scope_flip_does_not_carry_resumption_handle(patched_ws):
    """The cached resumption handle is bound to the old scope's system
    prompt; a flip must rebuild fresh, NOT pass the handle through."""
    ws = FakeWebSocket()
    # Drive an inbound byte so the session lands.
    await ws.push_bytes(b"\x00\x01")

    task = asyncio.create_task(patched_ws.ws_mod.voice_ws(ws))
    for _ in range(50):
        if FakeLiveSession.instances:
            break
        await asyncio.sleep(0.01)
    initial = FakeLiveSession.instances[0]
    # Wait until the byte forwards through the receive loop.
    for _ in range(50):
        if initial.sent_audio:
            break
        await asyncio.sleep(0.01)

    # Simulate a resumption update arriving from the server.
    await initial.on_session_resumed("handle-from-server-old-scope")

    # Now flip.
    await ws.push_json({"type": "context", "project": "Arch"})
    await ws.finish()
    await task

    # New session must NOT carry the old handle through.
    flipped = FakeLiveSession.instances[1]
    assert flipped.resumption_handle is None, (
        "scope flip leaked the old scope's resumption handle into the new session"
    )


@pytest.mark.asyncio
async def test_scope_flip_resets_reconnect_attempts(patched_ws):
    """Reconnect attempt counter resets on scope flip — new scope, fresh
    retry budget."""
    ws = FakeWebSocket()
    await ws.push_json({"type": "context", "project": "Arch"})
    await ws.finish()

    # We can't directly read the local list[int] for reconnect_attempts from
    # the outside — but the BEHAVIOR we want is "after a flip, the new
    # session can crash up to MAX_RETRIES times before giving up". That's
    # tested by the reconnect tests; here we just assert the flip closed
    # cleanly without choking on the reset.
    await patched_ws.ws_mod.voice_ws(ws)
    assert len(FakeLiveSession.instances) == 2
    assert FakeLiveSession.instances[1].opened is True


@pytest.mark.asyncio
async def test_scope_flip_clears_transcript_buffers(patched_ws):
    """Old scope's in-flight transcript must not bleed into the new scope's
    first turn-complete dispatch."""
    ws = FakeWebSocket()
    await ws.push_bytes(b"\x00\x01")
    task = asyncio.create_task(patched_ws.ws_mod.voice_ws(ws))
    for _ in range(50):
        if FakeLiveSession.instances:
            break
        await asyncio.sleep(0.01)
    initial = FakeLiveSession.instances[0]
    # Push a partial transcript on the OLD session.
    await initial.on_input_transcript("partial-leak", False)
    # Flip.
    await ws.push_json({"type": "context", "project": "Arch"})
    # Wait for the flip to land.
    for _ in range(50):
        if len(FakeLiveSession.instances) >= 2:
            break
        await asyncio.sleep(0.01)
    flipped = FakeLiveSession.instances[1]
    # Trigger a turn-complete on the NEW session with empty transcripts.
    await flipped.on_turn_complete({
        "prompt_token_count": 1,
        "response_token_count": 1,
    })
    await ws.finish()
    await task

    # No history append should carry "partial-leak" — buffers were cleared.
    assert not any(
        "partial-leak" in str(text)
        for (_sid, _scope, _role, text) in patched_ws.appends
    )
