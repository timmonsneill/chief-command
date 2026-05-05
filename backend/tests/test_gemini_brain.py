"""Unit tests for services.gemini_brain.

These tests stub the google-genai SDK with fake stream chunks so we can
verify the brain's behavior without hitting Vertex AI:
  * Text deltas → send_token + send_tts_sentence (sentence-flush regex).
  * Function-call deltas → tool dispatch → function_response → next round.
  * Cancellation propagates without extra awaits.
  * Cost computation produces the right cents at end of turn.
  * MAX_TOOL_ROUNDS guard fires when the model loops indefinitely.

Each test installs a fake genai client into ``gemini_brain._client`` so
the lazy ``_get_client`` shortcut returns the stub. We never actually
import the real google-genai SDK during these tests — instead, the SDK is
stubbed at sys.modules level so ``from google import genai`` succeeds with
our fake.
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
# Fake genai chunk + stream helpers
# ---------------------------------------------------------------------------
class FakeFinishReason:
    def __init__(self, name: str):
        self.name = name


class FakeUsageMetadata:
    def __init__(self, prompt: int, candidates: int, cached: int = 0):
        self.prompt_token_count = prompt
        self.candidates_token_count = candidates
        self.cached_content_token_count = cached


class FakePart:
    def __init__(self, text=None, function_call=None):
        self.text = text
        self.function_call = function_call


class FakeContent:
    def __init__(self, parts):
        self.parts = parts


class FakeCandidate:
    def __init__(self, parts, finish_reason=None):
        self.content = FakeContent(parts)
        self.finish_reason = finish_reason


class FakeChunk:
    def __init__(self, parts, *, finish_reason=None, usage_metadata=None):
        self.candidates = [FakeCandidate(parts, finish_reason)]
        self.usage_metadata = usage_metadata


class FakeFunctionCall:
    def __init__(self, name: str, args: dict, fc_id: str = "call-1"):
        self.name = name
        self.args = args
        self.id = fc_id


def _make_text_chunk(text: str, *, finish=None, usage=None) -> FakeChunk:
    return FakeChunk([FakePart(text=text)], finish_reason=finish, usage_metadata=usage)


def _make_fcall_chunk(name: str, args: dict, fc_id="call-1") -> FakeChunk:
    return FakeChunk([FakePart(function_call=FakeFunctionCall(name, args, fc_id))])


def _make_async_iter(chunks):
    """Wrap a list of chunks as an async iterator for the fake stream."""
    async def _gen():
        for c in chunks:
            yield c
    return _gen()


class FakeStreamResponse:
    """Mimics what client.aio.models.generate_content_stream returns —
    an awaitable that resolves to an async iterator."""

    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        return _make_async_iter(self._chunks)


class FakeAsyncModels:
    """Stub for ``client.aio.models`` — multi-round support: each call to
    generate_content_stream pops the next list of chunks off ``rounds``."""

    def __init__(self, rounds: list[list[FakeChunk]]):
        self._rounds = rounds
        self.calls: list[dict] = []

    async def generate_content_stream(self, **kwargs):
        self.calls.append(kwargs)
        chunks = self._rounds.pop(0) if self._rounds else []
        return FakeStreamResponse(chunks)


class FakeAio:
    def __init__(self, models):
        self.models = models


class FakeClient:
    def __init__(self, rounds):
        self._models = FakeAsyncModels(rounds)
        self.aio = FakeAio(self._models)

    @property
    def call_log(self):
        return self._models.calls


def _install_genai_stub():
    """Stub the google-genai SDK so ``from google import genai`` and
    ``from google.genai import types`` work without the real package."""
    if "google.genai" in sys.modules and "google.genai.types" in sys.modules:
        return  # already installed (real or stub)

    google_pkg = sys.modules.get("google") or types.ModuleType("google")
    sys.modules["google"] = google_pkg

    genai_mod = types.ModuleType("google.genai")
    types_mod = types.ModuleType("google.genai.types")

    class _Client:
        def __init__(self, **kwargs):
            raise RuntimeError("FakeClient should be installed by tests")

    genai_mod.Client = _Client

    # Minimal types we use. Each one is a tiny pydantic-free struct.
    class _FunctionDeclaration:
        def __init__(self, name, description, parameters_json_schema=None,
                     parameters=None):
            self.name = name
            self.description = description
            self.parameters_json_schema = parameters_json_schema or parameters

    class _Tool:
        def __init__(self, function_declarations=None):
            self.function_declarations = function_declarations or []

    class _FunctionResponse:
        def __init__(self, id=None, name=None, response=None):
            self.id = id
            self.name = name
            self.response = response

    class _Part:
        def __init__(self, text=None, function_call=None,
                     function_response=None, thought=None):
            self.text = text
            self.function_call = function_call
            self.function_response = function_response
            self.thought = thought

        @classmethod
        def from_function_response(cls, name, response):
            # Legacy shim — production code now constructs
            # FunctionResponse directly so the id can be plumbed through.
            return cls(function_response={"name": name, "response": response})

    class _Content:
        def __init__(self, role=None, parts=None):
            self.role = role
            self.parts = parts or []

    class _GenerateContentConfig:
        def __init__(self, system_instruction=None, max_output_tokens=None,
                     tools=None, **kw):
            self.system_instruction = system_instruction
            self.max_output_tokens = max_output_tokens
            self.tools = tools or []
            for k, v in kw.items():
                setattr(self, k, v)

    types_mod.FunctionDeclaration = _FunctionDeclaration
    types_mod.Tool = _Tool
    types_mod.Part = _Part
    types_mod.FunctionResponse = _FunctionResponse
    types_mod.Content = _Content
    types_mod.GenerateContentConfig = _GenerateContentConfig

    genai_mod.types = types_mod
    sys.modules["google.genai"] = genai_mod
    sys.modules["google.genai.types"] = types_mod
    google_pkg.genai = genai_mod


# Install stub IF the real SDK isn't present. If it IS present the stub
# is skipped — these tests should work either way because we mock
# gemini_brain._client.
try:
    from google import genai as _real_genai  # noqa: F401
except ImportError:
    _install_genai_stub()


from services import gemini_brain  # noqa: E402
from services.agent_tools import ToolResult  # noqa: E402


# ---------------------------------------------------------------------------
# Streaming text-only path
# ---------------------------------------------------------------------------
class TestTextOnlyStreaming:
    @pytest.mark.asyncio
    async def test_text_deltas_emit_tokens_and_sentences(
        self, tmp_path: Path, monkeypatch,
    ):
        rounds = [[
            _make_text_chunk("Hello, "),
            _make_text_chunk("world. "),
            _make_text_chunk(
                "How are you?",
                finish=FakeFinishReason("STOP"),
                usage=FakeUsageMetadata(prompt=120, candidates=10),
            ),
        ]]
        client = FakeClient(rounds)
        monkeypatch.setattr(gemini_brain, "_client", client)

        tokens: list[str] = []
        sentences: list[str] = []

        async def send_token(t):
            tokens.append(t)

        async def send_tts_sentence(s):
            sentences.append(s)

        usage = await gemini_brain.stream(
            history=[],
            user_text="hi",
            system_prompt="you are chief",
            send_token=send_token,
            send_tts_sentence=send_tts_sentence,
            cwd=tmp_path,
            subject="owner",
            scope="Chief Command",
            system_prompt_append="",
        )

        # Tokens were emitted in order.
        assert "".join(tokens) == "Hello, world. How are you?"
        # The sentence-flush regex split on ".\\s" / "?$".
        # Expected sentences: ["Hello, world.", "How are you?"]
        assert sentences[0] == "Hello, world."
        assert sentences[-1] == "How are you?"
        # Usage came from the last chunk.
        assert usage["input_tokens"] == 120
        assert usage["output_tokens"] == 10
        assert usage["model"] == "gemini-2.5-pro"
        # cost_cents is deliberately NOT populated by the brain — the caller
        # (usage_tracker.record_turn) recomputes from token counts. The brain
        # is the single source of token counts; usage_tracker is the single
        # source of pricing.
        assert "cost_cents" not in usage

    @pytest.mark.asyncio
    async def test_empty_assistant_entry_filtered_from_history(
        self, tmp_path: Path, monkeypatch,
    ):
        """If the WS handler ever appends an empty assistant turn (e.g. on a
        barge-in that landed before tokens streamed), the history filter
        drops it, leaving consecutive user-role entries on the wire — which
        Gemini rejects / behaves badly on.

        The PRODUCTION fix is in app/websockets.py (don't append empty
        assistant). This test pins the brain-side filter behaviour so a
        future change can't silently widen the failure mode.
        """
        rounds = [[_make_text_chunk("ok", finish=FakeFinishReason("STOP"))]]
        client = FakeClient(rounds)
        monkeypatch.setattr(gemini_brain, "_client", client)

        async def noop(*a, **kw):
            pass

        # Owner says A; barge-in produces empty assistant entry; owner says B.
        await gemini_brain.stream(
            history=[
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": ""},  # the bad shape
                {"role": "user", "content": "second"},
            ],
            user_text="third",
            system_prompt="sys",
            send_token=noop,
            send_tts_sentence=noop,
            cwd=tmp_path,
            subject="owner",
            scope="Chief Command",
            system_prompt_append="",
        )

        contents = client.call_log[0]["contents"]
        # Roles seen: user (first), user (second), user (third). Three turns,
        # and the empty assistant was correctly filtered. NOTE: This is the
        # bug shape the WS-layer fix prevents from hitting Gemini in
        # production — even with the brain-side filter, two consecutive
        # user turns is wrong. Real fix: WS handler doesn't append empty
        # assistant_text in the first place.
        roles = [c.role for c in contents]
        assert roles == ["user", "user", "user"]
        assert "" not in [c.parts[0].text for c in contents]

    @pytest.mark.asyncio
    async def test_history_round_trip(self, tmp_path: Path, monkeypatch):
        # Verify prior history is converted to Content with role mapping.
        rounds = [[_make_text_chunk("ok", finish=FakeFinishReason("STOP"))]]
        client = FakeClient(rounds)
        monkeypatch.setattr(gemini_brain, "_client", client)

        async def noop(*a, **kw):
            pass

        await gemini_brain.stream(
            history=[
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "second"},
            ],
            user_text="third",
            system_prompt="sys",
            send_token=noop,
            send_tts_sentence=noop,
            cwd=tmp_path,
            subject="owner",
            scope="Chief Command",
            system_prompt_append="",
        )

        call = client.call_log[0]
        contents = call["contents"]
        # 2 prior + 1 new user turn
        assert len(contents) == 3
        assert contents[0].role == "user" and contents[0].parts[0].text == "first"
        assert contents[1].role == "model" and contents[1].parts[0].text == "second"
        assert contents[2].role == "user" and contents[2].parts[0].text == "third"


# ---------------------------------------------------------------------------
# Tool-call loop
# ---------------------------------------------------------------------------
class TestToolCallLoop:
    @pytest.mark.asyncio
    async def test_function_call_triggers_tool_then_continues(
        self, tmp_path: Path, monkeypatch,
    ):
        # Round 1: function_call(Bash, command="pwd")
        # Round 2: text reply consuming the tool result
        rounds = [
            [_make_fcall_chunk("Bash", {"command": "pwd"}, fc_id="c1")],
            [_make_text_chunk(
                "Working dir is here.",
                finish=FakeFinishReason("STOP"),
                usage=FakeUsageMetadata(prompt=200, candidates=20),
            )],
        ]
        client = FakeClient(rounds)
        monkeypatch.setattr(gemini_brain, "_client", client)

        # Stub dispatch_tool so we don't actually run pwd.
        executed = []

        async def fake_dispatch(name, args, **kwargs):
            executed.append((name, dict(args)))
            return ToolResult(output="/path/to/repo", error=False)

        monkeypatch.setattr(gemini_brain, "dispatch_tool", fake_dispatch)

        tool_frames = []

        async def send_tool_call(payload):
            tool_frames.append(payload)

        async def noop(*a, **kw):
            pass

        usage = await gemini_brain.stream(
            history=[],
            user_text="where am i",
            system_prompt="sys",
            send_token=noop,
            send_tts_sentence=noop,
            send_tool_call=send_tool_call,
            cwd=tmp_path,
            subject="owner",
            scope="Chief Command",
            system_prompt_append="",
        )

        # Tool dispatched.
        assert executed == [("Bash", {"command": "pwd"})]
        # Two frames: start + complete.
        assert len(tool_frames) == 2
        assert tool_frames[0]["status"] == "running"
        assert tool_frames[1]["status"] == "complete"
        assert "duration_ms" in tool_frames[1]
        assert tool_frames[1]["preview"] == "/path/to/repo"

        # Second call to generate_content_stream included the
        # function_response in the contents list.
        assert len(client.call_log) == 2
        second_contents = client.call_log[1]["contents"]
        # Last entry is the user-role function_response Content.
        last = second_contents[-1]
        assert last.role == "user"
        # The Part.from_function_response part. Real SDK exposes a
        # FunctionResponse object with .name; the stub stores a dict.
        fresp = last.parts[0]
        function_response = getattr(fresp, "function_response", None)
        assert function_response is not None
        if isinstance(function_response, dict):
            assert function_response["name"] == "Bash"
        else:
            assert function_response.name == "Bash"

    @pytest.mark.asyncio
    async def test_tool_error_still_continues(
        self, tmp_path: Path, monkeypatch,
    ):
        rounds = [
            [_make_fcall_chunk("Read", {"path": "/etc/passwd"})],
            [_make_text_chunk(
                "Couldn't read that.",
                finish=FakeFinishReason("STOP"),
            )],
        ]
        client = FakeClient(rounds)
        monkeypatch.setattr(gemini_brain, "_client", client)

        async def fake_dispatch(name, args, **kwargs):
            return ToolResult(output="error: outside cwd", error=True)

        monkeypatch.setattr(gemini_brain, "dispatch_tool", fake_dispatch)

        frames = []

        async def send_tool_call(p):
            frames.append(p)

        async def noop(*a, **kw):
            pass

        await gemini_brain.stream(
            history=[],
            user_text="show passwd",
            system_prompt="sys",
            send_token=noop,
            send_tts_sentence=noop,
            send_tool_call=send_tool_call,
            cwd=tmp_path,
            subject="owner",
            scope="Chief Command",
            system_prompt_append="",
        )
        assert frames[1]["status"] == "error"

    @pytest.mark.asyncio
    async def test_max_tool_rounds_guard(self, tmp_path: Path, monkeypatch):
        # Make every round emit yet another function call. Cap rounds via
        # patching MAX_TOOL_ROUNDS to keep the test fast.
        monkeypatch.setattr(gemini_brain, "MAX_TOOL_ROUNDS", 3)
        rounds = [
            [_make_fcall_chunk("Bash", {"command": "pwd"})] for _ in range(4)
        ]
        client = FakeClient(rounds)
        monkeypatch.setattr(gemini_brain, "_client", client)

        async def fake_dispatch(name, args, **kwargs):
            return ToolResult(output="x")

        monkeypatch.setattr(gemini_brain, "dispatch_tool", fake_dispatch)

        sentences = []

        async def send_tts_sentence(s):
            sentences.append(s)

        async def noop(*a, **kw):
            pass

        usage = await gemini_brain.stream(
            history=[],
            user_text="loop",
            system_prompt="sys",
            send_token=noop,
            send_tts_sentence=send_tts_sentence,
            cwd=tmp_path,
            subject="owner",
            scope="Chief Command",
            system_prompt_append="",
        )

        # Exactly 3 rounds were issued (the guard).
        assert len(client.call_log) == 3
        # The truncation marker fires.
        assert any("max tool rounds" in s.lower() for s in sentences) or any(
            "stop here" in s.lower() for s in sentences
        )

    @pytest.mark.asyncio
    async def test_token_counts_sum_across_rounds(
        self, tmp_path: Path, monkeypatch,
    ):
        """Multi-round tool-using turn must SUM per-round usage, not max() it.

        Vertex returns per-request token counts (NOT cumulative across the
        turn). Round 1 bills 100/10, round 2 bills 200/20 → total should be
        300/30. Using max() across rounds would silently report 200/20 and
        under-bill round 1 entirely.
        """
        rounds = [
            [
                _make_fcall_chunk("Bash", {"command": "pwd"}, fc_id="c1"),
                FakeChunk(
                    [],
                    usage_metadata=FakeUsageMetadata(
                        prompt=100, candidates=10, cached=5,
                    ),
                ),
            ],
            [_make_text_chunk(
                "After tool.",
                finish=FakeFinishReason("STOP"),
                usage=FakeUsageMetadata(prompt=200, candidates=20, cached=15),
            )],
        ]
        client = FakeClient(rounds)
        monkeypatch.setattr(gemini_brain, "_client", client)

        async def fake_dispatch(name, args, **kwargs):
            return ToolResult(output="ok")

        monkeypatch.setattr(gemini_brain, "dispatch_tool", fake_dispatch)

        async def noop(*a, **kw):
            pass

        usage = await gemini_brain.stream(
            history=[],
            user_text="x",
            system_prompt="sys",
            send_token=noop,
            send_tts_sentence=noop,
            cwd=tmp_path,
            subject="owner",
            scope="Chief Command",
            system_prompt_append="",
        )

        # 100 + 200 = 300 input; 10 + 20 = 30 output; 5 + 15 = 20 cached.
        assert usage["input_tokens"] == 300
        assert usage["output_tokens"] == 30
        assert usage["cache_read_input_tokens"] == 20

    @pytest.mark.asyncio
    async def test_function_response_carries_id_for_parallel_calls(
        self, tmp_path: Path, monkeypatch,
    ):
        """Parallel tool calls of the same name MUST be correlated by id on
        the way back. The brain builds FunctionResponse with id=fc.id; if
        we drop the id, Gemini can't match request → response and the
        ordering breaks for parallel-of-same-name calls."""
        # Two parallel Bash calls in the same round — different ids.
        rounds = [
            [
                FakeChunk([
                    FakePart(function_call=FakeFunctionCall(
                        "Bash", {"command": "ls"}, fc_id="call-A",
                    )),
                    FakePart(function_call=FakeFunctionCall(
                        "Bash", {"command": "pwd"}, fc_id="call-B",
                    )),
                ]),
            ],
            [_make_text_chunk("Done.", finish=FakeFinishReason("STOP"))],
        ]
        client = FakeClient(rounds)
        monkeypatch.setattr(gemini_brain, "_client", client)

        async def fake_dispatch(name, args, **kwargs):
            return ToolResult(output=f"out-for-{args.get('command')}")

        monkeypatch.setattr(gemini_brain, "dispatch_tool", fake_dispatch)

        async def noop(*a, **kw):
            pass

        await gemini_brain.stream(
            history=[],
            user_text="run both",
            system_prompt="sys",
            send_token=noop,
            send_tts_sentence=noop,
            cwd=tmp_path,
            subject="owner",
            scope="Chief Command",
            system_prompt_append="",
        )

        # Round 2 contents include the function_response Content. Find it
        # and verify each part carries the matching id from the request.
        second_contents = client.call_log[1]["contents"]
        last = second_contents[-1]
        assert last.role == "user"
        assert len(last.parts) == 2
        # Real SDK builds FunctionResponse pydantic objects; the test stub
        # accepts the same kwargs and stashes them on the part. Whether
        # function_response is a pydantic object or our stub's dict, both
        # carry .id / ["id"].
        for part, expected_id in zip(last.parts, ["call-A", "call-B"]):
            fr = getattr(part, "function_response", None)
            assert fr is not None
            actual_id = (
                fr.id if hasattr(fr, "id")
                else fr.get("id") if isinstance(fr, dict)
                else None
            )
            assert actual_id == expected_id, (
                f"Expected FunctionResponse id={expected_id!r}, got {actual_id!r}"
            )

    @pytest.mark.asyncio
    async def test_thought_parts_are_skipped(
        self, tmp_path: Path, monkeypatch,
    ):
        """Parts with thought=True must NOT be streamed as tokens or TTS —
        they're internal reasoning the user shouldn't hear."""
        # Mix a thought part (which would leak chain-of-thought) and a
        # regular text part. Only the regular text should reach the
        # callbacks.
        rounds = [[
            FakeChunk([
                FakePart(text="SECRET REASONING"),  # but with thought=True
                FakePart(text="Actual answer."),
            ], finish_reason=FakeFinishReason("STOP")),
        ]]
        # Set thought=True on the first part. The fake's __init__ doesn't
        # take thought, so we set it after construction.
        rounds[0][0].candidates[0].content.parts[0].thought = True

        client = FakeClient(rounds)
        monkeypatch.setattr(gemini_brain, "_client", client)

        tokens = []
        sentences = []

        async def send_token(t):
            tokens.append(t)

        async def send_tts_sentence(s):
            sentences.append(s)

        await gemini_brain.stream(
            history=[],
            user_text="x",
            system_prompt="sys",
            send_token=send_token,
            send_tts_sentence=send_tts_sentence,
            cwd=tmp_path,
            subject="owner",
            scope="Chief Command",
            system_prompt_append="",
        )

        assert "SECRET REASONING" not in "".join(tokens)
        assert "Actual answer." in "".join(tokens)
        # Sentence flush at the period gave us the answer; the reasoning
        # never made it to TTS.
        assert all("SECRET" not in s for s in sentences)


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------
class TestCancellation:
    @pytest.mark.asyncio
    async def test_cancel_raises_and_doesnt_emit_more_tokens(
        self, tmp_path: Path, monkeypatch,
    ):
        # Use an explicit cancel_event flipped after first token.
        cancel_event = asyncio.Event()
        rounds = [[
            _make_text_chunk("first. "),
            _make_text_chunk("second. "),
            _make_text_chunk("third.",
                             finish=FakeFinishReason("STOP")),
        ]]
        client = FakeClient(rounds)
        monkeypatch.setattr(gemini_brain, "_client", client)

        tokens = []

        async def send_token(t):
            tokens.append(t)
            cancel_event.set()  # flip after first chunk

        async def noop(*a, **kw):
            pass

        with pytest.raises(asyncio.CancelledError):
            await gemini_brain.stream(
                history=[],
                user_text="x",
                system_prompt="sys",
                send_token=send_token,
                send_tts_sentence=noop,
                cwd=tmp_path,
                subject="owner",
                scope="Chief Command",
                system_prompt_append="",
                cancel_event=cancel_event,
            )

        # First token landed; subsequent chunks must NOT have been emitted.
        assert tokens == ["first. "]

    @pytest.mark.asyncio
    async def test_cancel_during_running_chip_send_emits_cancelled_frame(
        self, tmp_path: Path, monkeypatch,
    ):
        """Regression: if CancelledError lands on the ``status: running``
        send (or between that send and the dispatch_tool entry), the
        terminal ``cancelled`` chip frame must still be emitted so the
        frontend can transition the chip out of running state.

        Pre-fix the running send happened OUTSIDE the try/finally, so a
        cancel here would skip the terminal frame and leave a stuck
        spinner on the UI.
        """
        # Round 1 yields a function call; we never reach round 2 because
        # the running-chip send raises CancelledError.
        rounds = [
            [_make_fcall_chunk("Bash", {"command": "pwd"}, fc_id="c1")],
        ]
        client = FakeClient(rounds)
        monkeypatch.setattr(gemini_brain, "_client", client)

        dispatched = []

        async def fake_dispatch(name, args, **kwargs):
            dispatched.append(name)
            return ToolResult(output="ok", error=False)

        monkeypatch.setattr(gemini_brain, "dispatch_tool", fake_dispatch)

        frames: list[dict] = []

        async def send_tool_call(payload):
            frames.append(payload)
            # First send is the "running" chip. Simulate a barge-in
            # cancelling the task right as the chip ships.
            if payload.get("status") == "running":
                raise asyncio.CancelledError()

        async def noop(*a, **kw):
            pass

        with pytest.raises(asyncio.CancelledError):
            await gemini_brain.stream(
                history=[],
                user_text="where am i",
                system_prompt="sys",
                send_token=noop,
                send_tts_sentence=noop,
                send_tool_call=send_tool_call,
                cwd=tmp_path,
                subject="owner",
                scope="Chief Command",
                system_prompt_append="",
            )

        # Dispatch never ran — cancel landed first.
        assert dispatched == []
        # Two frames: the "running" we bailed from, and the terminal
        # "cancelled" emitted by the finally block.
        assert len(frames) == 2
        assert frames[0]["status"] == "running"
        assert frames[1]["status"] == "cancelled"
        assert "duration_ms" in frames[1]


# ---------------------------------------------------------------------------
# Auth-path selection in _get_client
# ---------------------------------------------------------------------------
class TestGetClientAuthSelection:
    """Verify _get_client picks AI Studio when an API key is set, and
    falls back to Vertex AI service-account auth otherwise."""

    def test_uses_ai_studio_when_api_key_set(self, monkeypatch):
        # Clear cached client + creds env so the resolver runs fresh.
        monkeypatch.setattr(gemini_brain, "_client", None)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "fake-key-abc")
        # Settings was loaded at import time without the key — patch the
        # attribute so our resolver sees it (mirrors the .env-loaded path).
        monkeypatch.setattr(
            gemini_brain.settings, "GEMINI_API_KEY", "fake-key-abc",
            raising=False,
        )

        captured: dict = {}

        class _FakeAIStudioClient:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        # Patch the genai module that _get_client imports.
        from google import genai as real_genai
        monkeypatch.setattr(real_genai, "Client", _FakeAIStudioClient)

        client = gemini_brain._get_client()
        assert isinstance(client, _FakeAIStudioClient)
        assert captured == {"api_key": "fake-key-abc"}
        assert "vertexai" not in captured
        assert "project" not in captured

    def test_falls_back_to_vertex_when_no_api_key(self, monkeypatch):
        monkeypatch.setattr(gemini_brain, "_client", None)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.setattr(
            gemini_brain.settings, "GEMINI_API_KEY", None, raising=False,
        )
        monkeypatch.setattr(
            gemini_brain.settings, "GOOGLE_API_KEY", None, raising=False,
        )
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
        monkeypatch.setattr(
            gemini_brain.settings, "VERTEX_AI_PROJECT", "test-project",
            raising=False,
        )
        monkeypatch.setattr(
            gemini_brain.settings, "VERTEX_AI_LOCATION", "us-central1",
            raising=False,
        )

        captured: dict = {}

        class _FakeVertexClient:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        from google import genai as real_genai
        monkeypatch.setattr(real_genai, "Client", _FakeVertexClient)

        client = gemini_brain._get_client()
        assert isinstance(client, _FakeVertexClient)
        assert captured["vertexai"] is True
        assert captured["project"] == "test-project"
        assert captured["location"] == "us-central1"
        assert "api_key" not in captured

    def test_raises_when_neither_configured(self, monkeypatch):
        monkeypatch.setattr(gemini_brain, "_client", None)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        monkeypatch.setattr(
            gemini_brain.settings, "GEMINI_API_KEY", None, raising=False,
        )
        monkeypatch.setattr(
            gemini_brain.settings, "GOOGLE_API_KEY", None, raising=False,
        )

        with pytest.raises(RuntimeError, match="no auth configured"):
            gemini_brain._get_client()


# Cost computation lives in services.usage_tracker (single source of truth).
# Brain-side pricing helpers were removed when the populate-cost_cents path
# was deleted — see TestTextOnlyStreaming for the assertion that
# usage_dict["cost_cents"] is intentionally unset.
