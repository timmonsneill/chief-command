"""Unit tests for services.gemini_live.LiveSession.

Mock-only — no real network. We stub the genai SDK at the module level
(via the same conftest pattern as test_gemini_brain) so the lazy
``_get_client`` shortcut returns our fake. The fake AsyncSession lets
us script a series of LiveServerMessage events and assert that each
event-type dispatches to the right caller-supplied callback.

What's covered:
  * __init__ stores all callbacks + resumption handle
  * open() calls client.aio.live.connect with the right model + config
  * send_audio() forwards a Blob with mime_type=audio/pcm;rate=16000
  * send_text() routes to send_client_content with turn_complete=True
  * receive pump dispatches:
      - audio bytes      → on_audio_chunk
      - input_transcription → on_input_transcript
      - output_transcription → on_output_transcript
      - interrupted=True → on_interrupted
      - generation_complete=True → on_turn_complete (with usage dict)
      - session_resumption_update.new_handle → on_session_resumed
        AND updates self.resumption_handle
      - go_away.time_left → on_go_away
      - tool_call.function_calls → on_tool_call
  * close() cancels the pump task cleanly
  * usage_metadata accumulates across frames
  * a callback raising doesn't kill the pump

The fake server protocol is intentionally minimal: each ``FakeMessage``
sets only the fields the dispatch code reads. We rely on attribute
lookups via ``getattr(.., 'X', None)`` in production code matching
attribute presence here.
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# ---------------------------------------------------------------------------
# Fake genai SDK shapes — reused by every test
# ---------------------------------------------------------------------------
class _FakeBlob:
    def __init__(self, data=None, mime_type=None):
        self.data = data
        self.mime_type = mime_type


class _FakePart:
    def __init__(self, text=None, inline_data=None, function_call=None,
                 function_response=None):
        self.text = text
        self.inline_data = inline_data
        self.function_call = function_call
        self.function_response = function_response


class _FakeContent:
    def __init__(self, role=None, parts=None):
        self.role = role
        self.parts = parts or []


class _FakeAudioTranscriptionConfig:
    def __init__(self): pass


class _FakeContextWindowCompressionConfig:
    def __init__(self, sliding_window=None):
        self.sliding_window = sliding_window


class _FakeSlidingWindow:
    def __init__(self): pass


class _FakeSessionResumptionConfig:
    def __init__(self, handle=None, transparent=None):
        self.handle = handle
        self.transparent = transparent


class _FakeLiveConnectConfig:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        self.kwargs = kw  # for assertions


class _FakeModalityEnum:
    AUDIO = type("M", (), {"name": "AUDIO", "value": "AUDIO"})()
    TEXT = type("M", (), {"name": "TEXT", "value": "TEXT"})()


class _FakeFunctionCall:
    def __init__(self, name, args=None, fc_id=None):
        self.name = name
        self.args = args or {}
        self.id = fc_id


# ----- server-side messages the pump consumes -----
class _ServerContent:
    def __init__(self, *, model_turn=None, input_transcription=None,
                 output_transcription=None, interrupted=None,
                 generation_complete=None, turn_complete=None):
        self.model_turn = model_turn
        self.input_transcription = input_transcription
        self.output_transcription = output_transcription
        self.interrupted = interrupted
        self.generation_complete = generation_complete
        self.turn_complete = turn_complete


class _Transcription:
    def __init__(self, text=None, finished=None):
        self.text = text
        self.finished = finished


class _ToolCall:
    def __init__(self, function_calls=None):
        self.function_calls = function_calls or []


class _SessionResumptionUpdate:
    def __init__(self, new_handle=None, resumable=True,
                 last_consumed_client_message_index=None):
        self.new_handle = new_handle
        self.resumable = resumable
        self.last_consumed_client_message_index = last_consumed_client_message_index


class _GoAway:
    def __init__(self, time_left=None):
        self.time_left = time_left


class _UsageMetadata:
    def __init__(self, *, prompt=0, response=0, cached=0, total=0,
                 prompt_modalities=None, response_modalities=None):
        self.prompt_token_count = prompt
        self.response_token_count = response
        self.cached_content_token_count = cached
        self.total_token_count = total
        self.prompt_tokens_details = prompt_modalities or []
        self.response_tokens_details = response_modalities or []


class _ModalityTokenCount:
    def __init__(self, modality, token_count):
        # Production code reads .modality.name — accept the fake
        # enum-like above OR a bare string.
        self.modality = modality
        self.token_count = token_count


class _LiveServerMessage:
    def __init__(self, *, server_content=None, tool_call=None,
                 usage_metadata=None, session_resumption_update=None,
                 go_away=None):
        self.server_content = server_content
        self.tool_call = tool_call
        self.usage_metadata = usage_metadata
        self.session_resumption_update = session_resumption_update
        self.go_away = go_away


# ----- fake AsyncSession -----
class _FakeAsyncSession:
    """Drop-in stand-in for genai's live.AsyncSession.

    ``messages`` is a queue of LiveServerMessage instances the pump
    will yield. Tests put messages on the queue, run the pump, and
    then assert which callbacks fired.
    """

    def __init__(self):
        self.messages: asyncio.Queue = asyncio.Queue()
        self.sent_audio: list = []
        self.sent_client_content: list = []
        self.sent_tool_responses: list = []
        # Sentinel that signals "no more messages — block forever". The
        # pump exits via cancellation, not via end-of-stream, so
        # receive() must be an *infinite* iterator.
        self._closed = False

    async def send_realtime_input(self, *, audio=None, **_kw):
        if audio is not None:
            self.sent_audio.append(audio)

    async def send_client_content(self, *, turns=None, turn_complete=True):
        self.sent_client_content.append(
            {"turns": turns, "turn_complete": turn_complete}
        )

    async def send_tool_response(self, *, function_responses):
        self.sent_tool_responses.append(function_responses)

    async def receive(self):
        while not self._closed:
            try:
                msg = await asyncio.wait_for(self.messages.get(), timeout=0.05)
                yield msg
            except asyncio.TimeoutError:
                # Yield control so cancellation can fire.
                await asyncio.sleep(0)
                continue

    async def close(self):
        self._closed = True


class _FakeConnectCM:
    """Async context manager mimicking client.aio.live.connect(...)."""

    def __init__(self, session, *, model, config):
        self.session = session
        self.model = model
        self.config = config
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        self.exited = True
        await self.session.close()
        return False


class _FakeLive:
    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.connect_calls: list[dict] = []

    def connect(self, *, model, config):
        sess = self.session_factory()
        cm = _FakeConnectCM(sess, model=model, config=config)
        self.connect_calls.append({"model": model, "config": config, "cm": cm})
        return cm


class _FakeAio:
    def __init__(self, live):
        self.live = live


class _FakeClient:
    def __init__(self, session_factory):
        self._live = _FakeLive(session_factory)
        self.aio = _FakeAio(self._live)

    @property
    def call_log(self):
        return self._live.connect_calls


# ---------------------------------------------------------------------------
# Stub the SDK BEFORE importing the module under test
# ---------------------------------------------------------------------------
def _install_genai_live_stub():
    """Ensure ``google.genai`` + ``google.genai.types`` are importable.

    If the REAL google-genai SDK is installed (the common case in this
    repo), we leave it alone — production code uses real
    ``types.Blob`` / ``types.Content`` / etc., and our test fakes
    happily duck-type against those.

    If the SDK is NOT installed (e.g. on a stripped CI box) we fall
    back to a minimal stub so the imports inside ``gemini_live`` still
    resolve. Test assertions in this file deliberately avoid
    ``isinstance`` against fake types — they walk attributes — so the
    same tests pass under either path.
    """
    try:
        from google import genai as _real_genai  # noqa: F401
        from google.genai import types as _real_types  # noqa: F401
        # Real SDK present — guarantee the names this module needs are
        # there (older SDK builds have all of them already, but hasattr
        # checks are cheap and keep the stub usable on a partial SDK).
        return
    except ImportError:
        pass

    google_pkg = sys.modules.get("google") or types.ModuleType("google")
    sys.modules["google"] = google_pkg

    genai_mod = types.ModuleType("google.genai")
    types_mod = types.ModuleType("google.genai.types")

    class _Client:
        def __init__(self, **kwargs):
            self._kwargs = kwargs

    genai_mod.Client = _Client

    types_mod.Blob = _FakeBlob
    types_mod.Part = _FakePart
    types_mod.Content = _FakeContent
    types_mod.AudioTranscriptionConfig = _FakeAudioTranscriptionConfig
    types_mod.ContextWindowCompressionConfig = _FakeContextWindowCompressionConfig
    types_mod.SlidingWindow = _FakeSlidingWindow
    types_mod.SessionResumptionConfig = _FakeSessionResumptionConfig
    types_mod.LiveConnectConfig = _FakeLiveConnectConfig
    types_mod.Modality = _FakeModalityEnum

    genai_mod.types = types_mod
    sys.modules["google.genai"] = genai_mod
    sys.modules["google.genai.types"] = types_mod
    google_pkg.genai = genai_mod


_install_genai_live_stub()


# ---------------------------------------------------------------------------
# Import the module under test (after stub is installed)
# ---------------------------------------------------------------------------
from services import gemini_live  # noqa: E402
from services.gemini_live import (  # noqa: E402
    LIVE_MODEL,
    INPUT_AUDIO_MIME_TYPE,
    LiveSession,
)


# ---------------------------------------------------------------------------
# Helpers for individual tests
# ---------------------------------------------------------------------------
async def _noop(*_args, **_kw):
    return None


@pytest.fixture(autouse=True)
def _isolate_client_cache():
    """Reset gemini_live's module-level client cache between tests."""
    gemini_live._client = None
    yield
    gemini_live._client = None


def _install_fake_client():
    """Install a FakeClient into gemini_live._client. Returns the
    (client, session) so the test can drive the session directly."""
    fake_session = _FakeAsyncSession()
    client = _FakeClient(lambda: fake_session)
    gemini_live._client = client
    return client, fake_session


async def _drain_queue(session: _FakeAsyncSession, max_wait_s: float = 0.5):
    """Wait for the receive queue to drain (pump consumed everything)."""
    deadline = asyncio.get_event_loop().time() + max_wait_s
    while not session.messages.empty():
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError("queue did not drain")
        await asyncio.sleep(0.01)
    # Give the pump one more loop so the final dispatch completes.
    await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------
def test_init_stores_all_callbacks_and_handle():
    async def _audio(_b): pass
    async def _in_tx(_t): pass
    async def _out_tx(_t): pass
    async def _interrupted(): pass
    async def _turn_done(_u): pass
    async def _tool(_tc): pass
    async def _resumed(_h): pass
    async def _go_away(_s): pass

    sess = LiveSession(
        system_prompt="be brief",
        on_audio_chunk=_audio,
        on_input_transcript=_in_tx,
        on_output_transcript=_out_tx,
        on_interrupted=_interrupted,
        on_turn_complete=_turn_done,
        on_tool_call=_tool,
        on_session_resumed=_resumed,
        on_go_away=_go_away,
        resumption_handle="handle-abc",
    )

    assert sess.system_prompt == "be brief"
    assert sess.model == LIVE_MODEL
    assert sess.on_audio_chunk is _audio
    assert sess.on_input_transcript is _in_tx
    assert sess.on_output_transcript is _out_tx
    assert sess.on_interrupted is _interrupted
    assert sess.on_turn_complete is _turn_done
    assert sess.on_tool_call is _tool
    assert sess.on_session_resumed is _resumed
    assert sess.on_go_away is _go_away
    assert sess.resumption_handle == "handle-abc"
    # Optional callbacks not provided default to None.
    sess2 = LiveSession(system_prompt="x", on_audio_chunk=_audio)
    assert sess2.on_input_transcript is None
    assert sess2.on_interrupted is None


# ---------------------------------------------------------------------------
# open()
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_open_calls_connect_with_correct_config():
    client, fake_session = _install_fake_client()

    sess = LiveSession(
        system_prompt="hi there",
        on_audio_chunk=_noop,
        resumption_handle="prior-handle",
    )
    await sess.open()
    try:
        assert len(client.call_log) == 1
        call = client.call_log[0]
        assert call["model"] == LIVE_MODEL
        cfg = call["config"]
        # Real SDK uses pydantic models with attribute access; fake uses
        # _FakeLiveConnectConfig with the same attributes. Duck-type so
        # this test works under either path.
        rm = getattr(cfg, "response_modalities", None) or []
        # Each Modality value has a .name == "AUDIO" or it equals our
        # fake AUDIO sentinel.
        rm_names = [getattr(m, "name", str(m)) for m in rm]
        assert "AUDIO" in rm_names
        # System prompt content present
        sysi = getattr(cfg, "system_instruction", None)
        assert sysi is not None
        sys_parts = getattr(sysi, "parts", None) or []
        assert sys_parts and getattr(sys_parts[0], "text", None) == "hi there"
        # Transcription configs both enabled (presence is the assertion)
        assert getattr(cfg, "input_audio_transcription", None) is not None
        assert getattr(cfg, "output_audio_transcription", None) is not None
        # Context-window compression with a SlidingWindow
        cwc = getattr(cfg, "context_window_compression", None)
        assert cwc is not None
        assert getattr(cwc, "sliding_window", None) is not None
        # Resumption handle plumbed
        sr = getattr(cfg, "session_resumption", None)
        assert sr is not None
        assert getattr(sr, "handle", None) == "prior-handle"
        # Pump task started
        assert sess._pump_task is not None
        assert not sess._pump_task.done()
    finally:
        await sess.close()


@pytest.mark.asyncio
async def test_open_called_twice_raises():
    _install_fake_client()
    sess = LiveSession(system_prompt="x", on_audio_chunk=_noop)
    await sess.open()
    try:
        with pytest.raises(RuntimeError, match="open"):
            await sess.open()
    finally:
        await sess.close()


# ---------------------------------------------------------------------------
# send_audio / send_text / send_tool_response
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_send_audio_forwards_blob_with_mime_type():
    _, fake_session = _install_fake_client()
    sess = LiveSession(system_prompt="x", on_audio_chunk=_noop)
    await sess.open()
    try:
        chunk = b"\x00\x01" * 320
        await sess.send_audio(chunk)
        assert len(fake_session.sent_audio) == 1
        blob = fake_session.sent_audio[0]
        assert blob.data == chunk
        assert blob.mime_type == INPUT_AUDIO_MIME_TYPE  # "audio/pcm;rate=16000"
    finally:
        await sess.close()


@pytest.mark.asyncio
async def test_send_audio_drops_empty_chunks():
    _, fake_session = _install_fake_client()
    sess = LiveSession(system_prompt="x", on_audio_chunk=_noop)
    await sess.open()
    try:
        await sess.send_audio(b"")
        await sess.send_audio(None)  # type: ignore[arg-type]
        assert fake_session.sent_audio == []
    finally:
        await sess.close()


@pytest.mark.asyncio
async def test_send_audio_before_open_raises():
    _install_fake_client()
    sess = LiveSession(system_prompt="x", on_audio_chunk=_noop)
    with pytest.raises(RuntimeError, match="not open"):
        await sess.send_audio(b"\x00\x00")


@pytest.mark.asyncio
async def test_send_text_uses_send_client_content_with_turn_complete():
    _, fake_session = _install_fake_client()
    sess = LiveSession(system_prompt="x", on_audio_chunk=_noop)
    await sess.open()
    try:
        await sess.send_text("hello world")
        assert len(fake_session.sent_client_content) == 1
        msg = fake_session.sent_client_content[0]
        assert msg["turn_complete"] is True
        turns = msg["turns"]
        # Duck-type: real SDK Content vs fake _FakeContent both expose
        # role + parts[0].text.
        assert getattr(turns, "role", None) == "user"
        parts = getattr(turns, "parts", None) or []
        assert parts and getattr(parts[0], "text", None) == "hello world"
    finally:
        await sess.close()


@pytest.mark.asyncio
async def test_send_tool_response_forwards_payload():
    _, fake_session = _install_fake_client()
    sess = LiveSession(system_prompt="x", on_audio_chunk=_noop)
    await sess.open()
    try:
        payload = [{"name": "do_thing", "response": {"output": "ok"}}]
        await sess.send_tool_response(payload)
        assert fake_session.sent_tool_responses == [payload]
    finally:
        await sess.close()


# ---------------------------------------------------------------------------
# Receive pump → callback dispatch
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pump_dispatches_audio_chunk():
    _, fake_session = _install_fake_client()
    received: list[bytes] = []

    async def on_audio(b):
        received.append(b)

    sess = LiveSession(system_prompt="x", on_audio_chunk=on_audio)
    await sess.open()
    try:
        msg = _LiveServerMessage(
            server_content=_ServerContent(
                model_turn=_FakeContent(
                    role="model",
                    parts=[
                        _FakePart(
                            inline_data=_FakeBlob(
                                data=b"\xaa\xbb\xcc",
                                mime_type="audio/pcm;rate=24000",
                            )
                        )
                    ],
                )
            )
        )
        await fake_session.messages.put(msg)
        await _drain_queue(fake_session)
        assert received == [b"\xaa\xbb\xcc"]
    finally:
        await sess.close()


@pytest.mark.asyncio
async def test_pump_dispatches_input_and_output_transcripts():
    _, fake_session = _install_fake_client()
    in_tx: list[str] = []
    out_tx: list[str] = []

    async def on_in(t): in_tx.append(t)
    async def on_out(t): out_tx.append(t)

    sess = LiveSession(
        system_prompt="x",
        on_audio_chunk=_noop,
        on_input_transcript=on_in,
        on_output_transcript=on_out,
    )
    await sess.open()
    try:
        await fake_session.messages.put(_LiveServerMessage(
            server_content=_ServerContent(
                input_transcription=_Transcription(text="user said hi"),
            )
        ))
        await fake_session.messages.put(_LiveServerMessage(
            server_content=_ServerContent(
                output_transcription=_Transcription(text="model said hello"),
            )
        ))
        await _drain_queue(fake_session)
        assert in_tx == ["user said hi"]
        assert out_tx == ["model said hello"]
    finally:
        await sess.close()


@pytest.mark.asyncio
async def test_pump_dispatches_interrupted():
    _, fake_session = _install_fake_client()
    fired = asyncio.Event()

    async def on_interrupted():
        fired.set()

    sess = LiveSession(
        system_prompt="x",
        on_audio_chunk=_noop,
        on_interrupted=on_interrupted,
    )
    await sess.open()
    try:
        await fake_session.messages.put(_LiveServerMessage(
            server_content=_ServerContent(interrupted=True)
        ))
        await asyncio.wait_for(fired.wait(), timeout=0.5)
    finally:
        await sess.close()


@pytest.mark.asyncio
async def test_pump_dispatches_generation_complete_with_usage():
    _, fake_session = _install_fake_client()
    seen: list[dict] = []

    async def on_done(usage):
        seen.append(usage)

    sess = LiveSession(
        system_prompt="x",
        on_audio_chunk=_noop,
        on_turn_complete=on_done,
    )
    await sess.open()
    try:
        # First, drop a usage_metadata frame so the running totals
        # populate; then drop the generation_complete to fire the cb.
        await fake_session.messages.put(_LiveServerMessage(
            usage_metadata=_UsageMetadata(
                prompt=120, response=80, cached=10, total=210,
                prompt_modalities=[
                    _ModalityTokenCount(_FakeModalityEnum.AUDIO, 100),
                    _ModalityTokenCount(_FakeModalityEnum.TEXT, 20),
                ],
                response_modalities=[
                    _ModalityTokenCount(_FakeModalityEnum.AUDIO, 70),
                    _ModalityTokenCount(_FakeModalityEnum.TEXT, 10),
                ],
            )
        ))
        await fake_session.messages.put(_LiveServerMessage(
            server_content=_ServerContent(generation_complete=True),
        ))
        await _drain_queue(fake_session)
        assert len(seen) == 1
        usage = seen[0]
        assert usage["prompt_token_count"] == 120
        assert usage["response_token_count"] == 80
        assert usage["cached_content_token_count"] == 10
        assert usage["total_token_count"] == 210
        assert usage["audio_input_tokens"] == 100
        assert usage["text_input_tokens"] == 20
        assert usage["audio_output_tokens"] == 70
        assert usage["text_output_tokens"] == 10
    finally:
        await sess.close()


@pytest.mark.asyncio
async def test_pump_dispatches_session_resumption_and_caches_handle():
    _, fake_session = _install_fake_client()
    handles: list[str] = []

    async def on_resumed(h): handles.append(h)

    sess = LiveSession(
        system_prompt="x",
        on_audio_chunk=_noop,
        on_session_resumed=on_resumed,
    )
    await sess.open()
    try:
        await fake_session.messages.put(_LiveServerMessage(
            session_resumption_update=_SessionResumptionUpdate(
                new_handle="handle-xyz",
                resumable=True,
            )
        ))
        await _drain_queue(fake_session)
        assert handles == ["handle-xyz"]
        assert sess.resumption_handle == "handle-xyz"
    finally:
        await sess.close()


@pytest.mark.asyncio
async def test_pump_skips_resumption_update_when_not_resumable():
    _, fake_session = _install_fake_client()
    handles: list[str] = []

    async def on_resumed(h): handles.append(h)

    sess = LiveSession(
        system_prompt="x",
        on_audio_chunk=_noop,
        on_session_resumed=on_resumed,
    )
    await sess.open()
    try:
        await fake_session.messages.put(_LiveServerMessage(
            session_resumption_update=_SessionResumptionUpdate(
                new_handle="handle-stale",
                resumable=False,  # non-resumable update — ignore
            )
        ))
        await _drain_queue(fake_session)
        assert handles == []
        assert sess.resumption_handle is None
    finally:
        await sess.close()


@pytest.mark.asyncio
async def test_pump_dispatches_go_away_with_seconds():
    _, fake_session = _install_fake_client()
    seen: list[float] = []

    async def on_ga(s): seen.append(s)

    sess = LiveSession(
        system_prompt="x",
        on_audio_chunk=_noop,
        on_go_away=on_ga,
    )
    await sess.open()
    try:
        # int-seconds form
        await fake_session.messages.put(_LiveServerMessage(
            go_away=_GoAway(time_left=42)
        ))
        # proto-duration string form
        await fake_session.messages.put(_LiveServerMessage(
            go_away=_GoAway(time_left="17s")
        ))
        await _drain_queue(fake_session)
        assert seen == [42.0, 17.0]
    finally:
        await sess.close()


@pytest.mark.asyncio
async def test_pump_dispatches_tool_call():
    _, fake_session = _install_fake_client()
    captured: list = []

    async def on_tool(tc):
        captured.append(tc)
        return None

    sess = LiveSession(
        system_prompt="x",
        on_audio_chunk=_noop,
        on_tool_call=on_tool,
    )
    await sess.open()
    try:
        fcalls = [_FakeFunctionCall("read_file", {"path": "/tmp/x"}, "id-1")]
        await fake_session.messages.put(_LiveServerMessage(
            tool_call=_ToolCall(function_calls=fcalls)
        ))
        await _drain_queue(fake_session)
        assert len(captured) == 1
        assert captured[0].function_calls[0].name == "read_file"
    finally:
        await sess.close()


@pytest.mark.asyncio
async def test_callback_exception_does_not_kill_pump():
    _, fake_session = _install_fake_client()
    second_call_fired = asyncio.Event()
    call_count = {"n": 0}

    async def flaky_audio(_b):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("boom")
        second_call_fired.set()

    sess = LiveSession(
        system_prompt="x",
        on_audio_chunk=flaky_audio,
    )
    await sess.open()
    try:
        # First chunk — callback raises.
        await fake_session.messages.put(_LiveServerMessage(
            server_content=_ServerContent(
                model_turn=_FakeContent(
                    role="model",
                    parts=[_FakePart(inline_data=_FakeBlob(data=b"a"))],
                )
            )
        ))
        # Second chunk — should still fire even though the first raised.
        await fake_session.messages.put(_LiveServerMessage(
            server_content=_ServerContent(
                model_turn=_FakeContent(
                    role="model",
                    parts=[_FakePart(inline_data=_FakeBlob(data=b"b"))],
                )
            )
        ))
        await asyncio.wait_for(second_call_fired.wait(), timeout=0.5)
        # Pump still alive
        assert sess._pump_task is not None
        assert not sess._pump_task.done()
    finally:
        await sess.close()


# ---------------------------------------------------------------------------
# close()
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_close_cancels_pump_and_exits_context_manager():
    client, fake_session = _install_fake_client()
    sess = LiveSession(system_prompt="x", on_audio_chunk=_noop)
    await sess.open()
    cm = client.call_log[0]["cm"]
    pump = sess._pump_task
    assert pump is not None and not pump.done()

    await sess.close()
    # Pump done (cancelled), context exited.
    assert pump.done()
    assert cm.exited is True
    # Idempotent
    await sess.close()


@pytest.mark.asyncio
async def test_close_without_open_is_safe():
    sess = LiveSession(system_prompt="x", on_audio_chunk=_noop)
    # Should not raise even though open() was never called.
    await sess.close()
