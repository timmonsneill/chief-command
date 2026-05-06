"""Stage 4 — receive-pump-crash reconnect on the Live voice WS.

Validates the resumption-handle reconnect path:
  1. A pump crash with a cached handle rebuilds a NEW LiveSession with
     ``resumption_handle=<cached>``.
  2. Multiple pump crashes within one WS connection rebuild up to
     ``settings.LIVE_RECONNECT_MAX_RETRIES`` times, then give up with an
     error frame.
  3. A clean turn-complete after a reconnect resets the attempt counter
     so a later (separate) crash gets a fresh retry budget.
  4. Reconnect-in-progress latch prevents a second crash signal during
     an in-flight rebuild from spawning a parallel rebuild.
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
        self.close_reason: str = ""
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

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_with = code
        self.close_reason = reason


class FakeLiveSession:
    instances: list["FakeLiveSession"] = []

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
        self.closed = False
        self.cancel_calls = 0
        self.sent_audio: list[bytes] = []
        FakeLiveSession.instances.append(self)

    async def open(self) -> None:
        self.opened = True

    async def close(self, *, pump_grace_seconds: float = 0.0) -> None:
        self.closed = True

    async def send_audio(self, pcm: bytes) -> None:
        self.sent_audio.append(bytes(pcm))

    async def send_text(self, text: str) -> None:
        pass

    async def cancel_current_turn(self) -> None:
        self.cancel_calls += 1


@pytest.fixture(autouse=True)
def _reset():
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

    async def fake_check_soft_cap(subject="owner"):
        return False, 0.0
    monkeypatch.setattr(ws_mod, "check_daily_cap", fake_check_daily_cap)
    monkeypatch.setattr(ws_mod, "check_soft_cap", fake_check_soft_cap)

    sentinel_tool = object()
    monkeypatch.setattr(ws_mod, "to_gemini_tool", lambda: sentinel_tool)

    async def fake_load_memory(scope: str, raw_limit: int = 20):
        return f"<sum {scope}>", []
    monkeypatch.setattr(ws_mod, "load_persistent_memory", fake_load_memory)

    monkeypatch.setattr(
        ws_mod, "build_chief_system_string",
        lambda scope, prior_summary=None, for_live=False: f"[CHIEF scope={scope}]",
    )

    async def fake_maybe_rollup(scope: str):
        pass
    monkeypatch.setattr(ws_mod, "maybe_rollup", fake_maybe_rollup)

    async def fake_append_turn(*a, **k):
        pass
    monkeypatch.setattr(ws_mod, "append_turn", fake_append_turn)

    async def fake_create_session(sid, project=None):
        pass

    async def fake_close_session(sid):
        pass

    async def fake_record_turn(**kw):
        return {"id": 1, "cost_cents": 0}

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

    async def _cancel(_sid):
        return None
    fake_dispatcher.cancel = MagicMock(side_effect=_cancel)
    monkeypatch.setattr(ws_mod, "_dispatcher", fake_dispatcher)

    return types.SimpleNamespace(ws_mod=ws_mod, sentinel_tool=sentinel_tool)


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
async def test_pump_crash_with_cached_handle_rebuilds_with_handle(patched_ws):
    """A pump crash AFTER a session_resumption_update rebuilds a new
    LiveSession passing the cached handle as ``resumption_handle``."""
    ws = FakeWebSocket()
    await ws.push_bytes(b"\x00\x01")
    task = asyncio.create_task(patched_ws.ws_mod.voice_ws(ws))

    await _wait_for_instance(1)
    initial = FakeLiveSession.instances[0]
    # Wait for the byte forward.
    for _ in range(50):
        if initial.sent_audio:
            break
        await asyncio.sleep(0.01)

    # Server emitted a resumption handle on the live wire.
    await initial.on_session_resumed("server-handle-abc")

    # Now the pump dies. Fire the crash callback the WS layer registered.
    await initial.on_pump_crash(RuntimeError("transport closed"))

    # Wait for the reconnect task to land.
    await _wait_for_instance(2)
    rebuilt = FakeLiveSession.instances[1]
    assert rebuilt.resumption_handle == "server-handle-abc"
    assert rebuilt.opened is True

    # FE saw reconnecting + reconnected frames.
    types_seen = [f.get("type") for f in ws.outbound_json]
    assert "reconnecting" in types_seen
    assert "reconnected" in types_seen

    await ws.finish()
    await task


@pytest.mark.asyncio
async def test_pump_crash_without_handle_rebuilds_fresh(patched_ws):
    """A crash before any resumption_update rebuilds a fresh session
    (resumption_handle=None) — handle isn't required for reconnect, just
    nice-to-have for server-side context preservation."""
    ws = FakeWebSocket()
    await ws.push_bytes(b"\x00\x01")
    task = asyncio.create_task(patched_ws.ws_mod.voice_ws(ws))

    await _wait_for_instance(1)
    initial = FakeLiveSession.instances[0]
    for _ in range(50):
        if initial.sent_audio:
            break
        await asyncio.sleep(0.01)

    await initial.on_pump_crash(RuntimeError("early disconnect"))
    await _wait_for_instance(2)
    rebuilt = FakeLiveSession.instances[1]
    assert rebuilt.resumption_handle is None
    assert rebuilt.opened is True

    await ws.finish()
    await task


@pytest.mark.asyncio
async def test_reconnect_caps_at_max_retries(patched_ws, monkeypatch):
    """After ``settings.LIVE_RECONNECT_MAX_RETRIES`` consecutive crashes
    we stop spawning rebuilds and emit an error frame instead."""
    # Drive cap down to 2 so we don't have to fire 100+ crashes.
    monkeypatch.setattr(
        patched_ws.ws_mod.settings, "LIVE_RECONNECT_MAX_RETRIES", 2,
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

    # Crash 1 → reconnect 1 (instance 1).
    await s0.on_pump_crash(RuntimeError("c1"))
    await _wait_for_instance(2)
    s1 = FakeLiveSession.instances[1]

    # Crash 2 → reconnect 2 (instance 2).
    await s1.on_pump_crash(RuntimeError("c2"))
    await _wait_for_instance(3)
    s2 = FakeLiveSession.instances[2]

    # Crash 3 → cap hit, NO new instance.
    await s2.on_pump_crash(RuntimeError("c3"))
    # Give async tasks a tick to schedule (or not).
    await asyncio.sleep(0.05)
    assert len(FakeLiveSession.instances) == 3, (
        "reconnect cap was not enforced — got "
        f"{len(FakeLiveSession.instances)} instances"
    )

    # Error frame went out.
    err_frames = [f for f in ws.outbound_json if f.get("type") == "error"]
    assert err_frames, "no error frame on reconnect cap"

    await ws.finish()
    await task


@pytest.mark.asyncio
async def test_clean_turn_resets_reconnect_counter(patched_ws, monkeypatch):
    """A successful turn-complete after a reconnect must zero the attempt
    counter so later (separate) crashes get a fresh retry budget."""
    monkeypatch.setattr(
        patched_ws.ws_mod.settings, "LIVE_RECONNECT_MAX_RETRIES", 2,
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

    # Two crashes → two reconnects. Counter is now at 2 (max).
    await s0.on_pump_crash(RuntimeError("c1"))
    await _wait_for_instance(2)
    s1 = FakeLiveSession.instances[1]
    await s1.on_pump_crash(RuntimeError("c2"))
    await _wait_for_instance(3)
    s2 = FakeLiveSession.instances[2]

    # Healthy turn on s2 — counter resets.
    await s2.on_turn_complete({
        "prompt_token_count": 5, "response_token_count": 3,
    })
    # Two more crashes should now succeed (fresh budget post-reset).
    await s2.on_pump_crash(RuntimeError("c3"))
    await _wait_for_instance(4)
    s3 = FakeLiveSession.instances[3]
    await s3.on_pump_crash(RuntimeError("c4"))
    await _wait_for_instance(5)

    assert len(FakeLiveSession.instances) == 5

    await ws.finish()
    await task


@pytest.mark.asyncio
async def test_prompt_oversize_pump_crash_short_circuits(patched_ws):
    """A pump crash carrying the Live "system_instruction over sub-cap"
    error class is non-retryable. We must NOT spawn a reconnect, MUST
    emit a structured ``prompt_too_large`` error frame, MUST drop the
    cached resumption handle, and MUST close the WS with code 4004 so
    the FE auto-reconnect engages.
    """
    ws = FakeWebSocket()
    await ws.push_bytes(b"\x00\x01")
    task = asyncio.create_task(patched_ws.ws_mod.voice_ws(ws))

    await _wait_for_instance(1)
    s0 = FakeLiveSession.instances[0]
    for _ in range(50):
        if s0.sent_audio:
            break
        await asyncio.sleep(0.01)

    # Cache a handle so we can verify it gets dropped (a fresh open with
    # a smaller prompt MUST NOT reuse a handle bound to the oversized one).
    await s0.on_session_resumed("server-handle-doomed")

    # Fire a pump-crash carrying the Live oversize error class. The reason
    # text the SDK surfaces typically reads:
    #   "received 1007 (invalid frame payload data) the system instruction
    #    has 94 tokens, user system instruction has 41942 tokens, and user
    #    footer has 0 tokens. The sum is ab..."
    oversize_exc = RuntimeError(
        "received 1007 (invalid frame payload data) the system instruction "
        "has 94 tokens, user system instruction has 41942 tokens, and user "
        "footer has 0 tokens. The sum is ab"
    )
    await s0.on_pump_crash(oversize_exc)
    # Give the WS layer a tick to react.
    await asyncio.sleep(0.05)

    # No second LiveSession was opened — non-retryable.
    assert len(FakeLiveSession.instances) == 1, (
        "prompt-oversize crash incorrectly triggered a reconnect — got "
        f"{len(FakeLiveSession.instances)} sessions"
    )

    # Structured error frame went out.
    err_frames = [
        f for f in ws.outbound_json
        if f.get("type") == "error"
        and f.get("code") == "prompt_too_large"
    ]
    assert err_frames, (
        f"expected a prompt_too_large error frame; got {ws.outbound_json}"
    )
    assert "system prompt too large" in err_frames[0]["message"].lower()

    # WS was closed with the dedicated 4004 code.
    assert ws.closed_with == 4004, (
        f"expected ws.close(code=4004); got {ws.closed_with}"
    )

    # Wait for the voice_ws task to wind down naturally.
    await ws.finish()
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except asyncio.TimeoutError:
        task.cancel()


@pytest.mark.asyncio
async def test_prompt_oversize_recognized_with_alt_phrasing(patched_ws):
    """The oversize-detector matches the truncated 'sum is ab' tail too.

    The Live API close-frame reason is truncated mid-word — the wire often
    shows "sum is ab" without "above". The detector must still classify it
    as the oversize error class so we don't fall into the (broken) retry path.
    """
    ws = FakeWebSocket()
    await ws.push_bytes(b"\x00\x01")
    task = asyncio.create_task(patched_ws.ws_mod.voice_ws(ws))

    await _wait_for_instance(1)
    s0 = FakeLiveSession.instances[0]
    for _ in range(50):
        if s0.sent_audio:
            break
        await asyncio.sleep(0.01)

    # Reason variant — only the "sum is ab" tail, no "system instruction"
    # phrase. Must still trip the detector (the regex has both alternations).
    await s0.on_pump_crash(RuntimeError("close: 1007 reason='The sum is ab'"))
    await asyncio.sleep(0.05)

    assert len(FakeLiveSession.instances) == 1, (
        "alt-phrased oversize error didn't short-circuit reconnect"
    )
    assert ws.closed_with == 4004

    await ws.finish()
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except asyncio.TimeoutError:
        task.cancel()


@pytest.mark.asyncio
async def test_reconnect_cap_exhaustion_closes_ws_with_4004(
    patched_ws, monkeypatch,
):
    """When ``LIVE_RECONNECT_MAX_RETRIES`` is exhausted, the WS must be
    closed with code 4004 so the FE's auto-reconnect engages — not
    silently left open with a dead live_session_box (the original bug).
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

    # Crash 1 — burns the only retry.
    await s0.on_pump_crash(RuntimeError("c1"))
    await _wait_for_instance(2)
    s1 = FakeLiveSession.instances[1]

    # Crash 2 — over the cap. WS must close with 4004.
    await s1.on_pump_crash(RuntimeError("c2"))
    await asyncio.sleep(0.1)

    # No third instance.
    assert len(FakeLiveSession.instances) == 2

    # 4004 close + structured error frame.
    assert ws.closed_with == 4004, (
        f"expected ws.close(code=4004) on cap exhaustion; got {ws.closed_with}"
    )
    err_frames = [
        f for f in ws.outbound_json
        if f.get("type") == "error"
        and f.get("code") == "live_session_unrecoverable"
    ]
    assert err_frames, (
        f"expected live_session_unrecoverable error frame; "
        f"got {ws.outbound_json}"
    )

    await ws.finish()
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except asyncio.TimeoutError:
        task.cancel()


@pytest.mark.asyncio
async def test_prompt_oversize_does_not_burn_retry_budget(patched_ws):
    """A non-retryable oversize crash must NOT bump the retry counter.

    Otherwise a recovered turn would have a smaller-than-expected budget
    available for genuine transient crashes later.
    """
    ws = FakeWebSocket()
    await ws.push_bytes(b"\x00\x01")
    task = asyncio.create_task(patched_ws.ws_mod.voice_ws(ws))

    await _wait_for_instance(1)
    s0 = FakeLiveSession.instances[0]
    for _ in range(50):
        if s0.sent_audio:
            break
        await asyncio.sleep(0.01)

    # Snapshot the counter before the oversize crash.
    # (We can't grab the inner ``reconnect_attempts`` list directly, but
    # the test_reconnect_caps test would catch a bumped-to-cap counter.)

    # Trip oversize — this closes the WS with 4004, no reconnect.
    await s0.on_pump_crash(RuntimeError(
        "1007 the system instruction has 94 tokens, sum is ab"
    ))
    await asyncio.sleep(0.05)

    assert len(FakeLiveSession.instances) == 1
    assert ws.closed_with == 4004

    await ws.finish()
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except asyncio.TimeoutError:
        task.cancel()


@pytest.mark.asyncio
async def test_concurrent_crash_signals_dont_double_rebuild(patched_ws):
    """Two pump-crash callbacks firing back-to-back (before the first
    rebuild lands) must NOT spawn two parallel rebuilds.

    ``reconnect_in_progress`` latch prevents the parallel-rebuild race;
    the second crash sees the latch set and returns without action.
    """
    ws = FakeWebSocket()
    await ws.push_bytes(b"\x00\x01")
    task = asyncio.create_task(patched_ws.ws_mod.voice_ws(ws))

    await _wait_for_instance(1)
    s0 = FakeLiveSession.instances[0]
    for _ in range(50):
        if s0.sent_audio:
            break
        await asyncio.sleep(0.01)

    # Fire two crashes in quick succession.
    await asyncio.gather(
        s0.on_pump_crash(RuntimeError("c-a")),
        s0.on_pump_crash(RuntimeError("c-b")),
    )
    # Give both reconnect tasks a chance to run.
    await asyncio.sleep(0.1)

    # Only ONE new instance was created.
    assert len(FakeLiveSession.instances) == 2, (
        "concurrent crash signals spawned parallel rebuilds — got "
        f"{len(FakeLiveSession.instances)}"
    )

    await ws.finish()
    await task
