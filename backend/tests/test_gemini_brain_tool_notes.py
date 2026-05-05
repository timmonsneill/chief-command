"""Tests for the Phase 3 synthetic tool-round notes in gemini_brain.

The brain emits a one-line ``[tool: <name> · <args> · <preview>]`` note
to ``on_tool_round_complete`` after each tool round. The WS layer
persists these to ``voice_turns`` so cross-reconnect tool memory
survives.

Tests cover:
  * _build_tool_note shape, length cap, preview snippet
  * Callback fires once per tool call in a round
  * Multiple tool rounds produce multiple notes
  * Errors in the callback don't break the turn
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# Reuse the SDK stubs + fakes from test_gemini_brain.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_gemini_brain import (  # noqa: E402
    FakeClient,
    FakeFinishReason,
    FakeUsageMetadata,
    _make_fcall_chunk,
    _make_text_chunk,
)

from services import gemini_brain  # noqa: E402
from services.agent_tools import ToolResult  # noqa: E402


# ---------------------------------------------------------------------------
# _build_tool_note unit tests
# ---------------------------------------------------------------------------
class TestBuildToolNote:
    def test_basic_shape(self):
        note = gemini_brain._build_tool_note(
            tool_name="Bash",
            args={"command": "git log -5"},
            output="commit abc123\nfix(auth): foo",
            error=False,
        )
        assert note.startswith("[")
        assert note.endswith("]")
        assert "tool: Bash" in note
        assert "git log -5" in note
        # Preview should be the FIRST line of output, not a multi-line dump.
        assert "commit abc123" in note
        assert "fix(auth)" not in note
        # Status marker.
        assert "ok:" in note

    def test_error_status(self):
        note = gemini_brain._build_tool_note(
            tool_name="Read",
            args={"file_path": "/etc/passwd"},
            output="",
            error=True,
        )
        assert "tool: Read" in note
        assert "error" in note

    def test_long_arg_is_truncated(self):
        long_cmd = "x" * 500
        note = gemini_brain._build_tool_note(
            tool_name="Bash",
            args={"command": long_cmd},
            output="ok",
            error=False,
        )
        # Total note must be capped at TOOL_NOTE_MAX_CHARS.
        assert len(note) <= gemini_brain.TOOL_NOTE_MAX_CHARS
        # Arg snippet specifically must be truncated.
        assert "..." in note

    def test_long_output_preview_truncated(self):
        long_out = "y" * 500
        note = gemini_brain._build_tool_note(
            tool_name="Bash",
            args={"command": "echo y"},
            output=long_out,
            error=False,
        )
        assert len(note) <= gemini_brain.TOOL_NOTE_MAX_CHARS
        # The preview portion got truncated with an ellipsis.
        assert "..." in note

    def test_multiline_output_collapsed_to_first_line(self):
        note = gemini_brain._build_tool_note(
            tool_name="Bash",
            args={"command": "ls"},
            output="line one\nline two\nline three",
            error=False,
        )
        assert "line one" in note
        assert "line two" not in note
        assert "line three" not in note

    def test_picks_primary_arg_for_each_tool(self):
        # Bash → command. Read → file_path. Grep → pattern.
        bash_note = gemini_brain._build_tool_note(
            tool_name="Bash", args={"command": "pwd", "extra": "ignored"},
            output="x", error=False,
        )
        assert "pwd" in bash_note
        # 'ignored' shouldn't appear because we picked 'command' first.
        assert "ignored" not in bash_note

        read_note = gemini_brain._build_tool_note(
            tool_name="Read", args={"file_path": "src/app.py"},
            output="...", error=False,
        )
        assert "src/app.py" in read_note

        grep_note = gemini_brain._build_tool_note(
            tool_name="Grep", args={"pattern": "TODO"},
            output="found 3", error=False,
        )
        assert "TODO" in grep_note


# ---------------------------------------------------------------------------
# stream() callback integration
# ---------------------------------------------------------------------------
class TestOnToolRoundComplete:
    @pytest.mark.asyncio
    async def test_callback_fires_once_per_tool_call(
        self, tmp_path: Path, monkeypatch,
    ):
        rounds = [
            [_make_fcall_chunk("Bash", {"command": "pwd"}, fc_id="c1")],
            [_make_text_chunk(
                "ok.",
                finish=FakeFinishReason("STOP"),
                usage=FakeUsageMetadata(prompt=10, candidates=2),
            )],
        ]
        client = FakeClient(rounds)
        monkeypatch.setattr(gemini_brain, "_client", client)

        async def fake_dispatch(name, args, **kwargs):
            return ToolResult(output="/path/to/repo", error=False)

        monkeypatch.setattr(gemini_brain, "dispatch_tool", fake_dispatch)

        notes: list[str] = []

        async def on_tool_round_complete(note: str):
            notes.append(note)

        async def noop(*a, **kw):
            pass

        await gemini_brain.stream(
            history=[],
            user_text="where am i",
            system_prompt="sys",
            send_token=noop,
            send_tts_sentence=noop,
            on_tool_round_complete=on_tool_round_complete,
            cwd=tmp_path,
            subject="owner",
            scope="Chief Command",
            system_prompt_append="",
        )

        # One tool call → one note.
        assert len(notes) == 1
        note = notes[0]
        assert "tool: Bash" in note
        assert "pwd" in note
        # Preview reflects the dispatch output.
        assert "/path/to/repo" in note

    @pytest.mark.asyncio
    async def test_multiple_rounds_produce_multiple_notes(
        self, tmp_path: Path, monkeypatch,
    ):
        rounds = [
            [_make_fcall_chunk("Bash", {"command": "ls"}, fc_id="c1")],
            [_make_fcall_chunk("Bash", {"command": "pwd"}, fc_id="c2")],
            [_make_text_chunk(
                "done.",
                finish=FakeFinishReason("STOP"),
                usage=FakeUsageMetadata(prompt=50, candidates=5),
            )],
        ]
        client = FakeClient(rounds)
        monkeypatch.setattr(gemini_brain, "_client", client)

        async def fake_dispatch(name, args, **kwargs):
            return ToolResult(
                output=f"out for {args.get('command')}",
                error=False,
            )

        monkeypatch.setattr(gemini_brain, "dispatch_tool", fake_dispatch)

        notes: list[str] = []

        async def on_tool_round_complete(note: str):
            notes.append(note)

        async def noop(*a, **kw):
            pass

        await gemini_brain.stream(
            history=[],
            user_text="explore",
            system_prompt="sys",
            send_token=noop,
            send_tts_sentence=noop,
            on_tool_round_complete=on_tool_round_complete,
            cwd=tmp_path,
            subject="owner",
            scope="Chief Command",
            system_prompt_append="",
        )

        # Two tool rounds → two notes, in order.
        assert len(notes) == 2
        assert "ls" in notes[0]
        assert "pwd" in notes[1]

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_break_turn(
        self, tmp_path: Path, monkeypatch,
    ):
        rounds = [
            [_make_fcall_chunk("Bash", {"command": "ls"}, fc_id="c1")],
            [_make_text_chunk(
                "ok.",
                finish=FakeFinishReason("STOP"),
                usage=FakeUsageMetadata(prompt=10, candidates=2),
            )],
        ]
        client = FakeClient(rounds)
        monkeypatch.setattr(gemini_brain, "_client", client)

        async def fake_dispatch(name, args, **kwargs):
            return ToolResult(output="x", error=False)

        monkeypatch.setattr(gemini_brain, "dispatch_tool", fake_dispatch)

        async def boom_callback(note: str):
            raise RuntimeError("simulated WS write failure")

        async def noop(*a, **kw):
            pass

        # Must NOT raise.
        usage = await gemini_brain.stream(
            history=[],
            user_text="explore",
            system_prompt="sys",
            send_token=noop,
            send_tts_sentence=noop,
            on_tool_round_complete=boom_callback,
            cwd=tmp_path,
            subject="owner",
            scope="Chief Command",
            system_prompt_append="",
        )
        # Turn still completed; usage is present.
        assert usage["model"] == "gemini-2.5-pro"

    @pytest.mark.asyncio
    async def test_no_callback_when_kwarg_omitted(
        self, tmp_path: Path, monkeypatch,
    ):
        """Backwards-compat: existing callers without on_tool_round_complete
        must still work."""
        rounds = [
            [_make_fcall_chunk("Bash", {"command": "ls"}, fc_id="c1")],
            [_make_text_chunk(
                "ok.",
                finish=FakeFinishReason("STOP"),
                usage=FakeUsageMetadata(prompt=10, candidates=2),
            )],
        ]
        client = FakeClient(rounds)
        monkeypatch.setattr(gemini_brain, "_client", client)

        async def fake_dispatch(name, args, **kwargs):
            return ToolResult(output="x", error=False)

        monkeypatch.setattr(gemini_brain, "dispatch_tool", fake_dispatch)

        async def noop(*a, **kw):
            pass

        usage = await gemini_brain.stream(
            history=[],
            user_text="explore",
            system_prompt="sys",
            send_token=noop,
            send_tts_sentence=noop,
            cwd=tmp_path,
            subject="owner",
            scope="Chief Command",
            system_prompt_append="",
        )
        assert usage["model"] == "gemini-2.5-pro"
