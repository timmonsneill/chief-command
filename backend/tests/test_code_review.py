"""Tests for the code_review tool — Pro on Vertex specialist (Stage 5).

Coverage:
  * File-path target: read file, sanity-check the call to gemini_brain.stream
  * Git-range target: subprocess invocation gets the right args
  * Inline target: passes through verbatim
  * Path traversal denied (../etc/passwd → error)
  * Non-existent path falls through to inline (per the auto-detect spec)
  * Focus parameter changes the review prompt's angle text
  * Timeout returns error result
  * Cost is recorded via record_code_review_cost
  * 100KB cap truncates with note
  * dispatch_tool routes "code_review" to execute_code_review
  * dispatch_tool path-fence still applies (Read-shape; cwd guard fires)
  * ALL_TOOLS / to_gemini_declarations include code_review
  * Schema has required ``target`` and enum focus
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("OWNER_PASSWORD", "test")
os.environ.setdefault("JWT_SECRET", "test")


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def fake_gemini_stream(monkeypatch):
    """Stub gemini_brain.stream — capture call args + drive a fixed reply.

    Returns the captured-call list. Each call appends a dict with the
    kwargs passed to stream() so a test can assert on user_text/cwd/etc.
    Drives a "Pro review reply." text via send_token so the buffer
    capture path is exercised.
    """
    calls: list[dict] = []
    reply_text = "Pro review reply."
    usage_dict = {
        "input_tokens": 200,
        "output_tokens": 80,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "model": "gemini-2.5-pro",
        "stop_reason": "stop",
        "assistant_text": reply_text,
    }

    async def _fake_stream(**kwargs):
        calls.append(dict(kwargs))
        send_token = kwargs.get("send_token")
        if send_token is not None:
            await send_token(reply_text)
        return dict(usage_dict)

    import services.gemini_brain as gb_mod
    monkeypatch.setattr(gb_mod, "stream", _fake_stream)
    return calls


@pytest.fixture
def fake_record_cost(monkeypatch):
    """Stub record_code_review_cost; capture call kwargs."""
    calls: list[dict] = []

    async def _fake(**kwargs):
        calls.append(dict(kwargs))
        return {"cost_cents": 7}

    import services.usage_tracker as ut
    monkeypatch.setattr(ut, "record_code_review_cost", _fake)
    return calls


# ---------------------------------------------------------------------------
# Resolution: file path
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_file_path_target_reads_and_streams(
    fake_gemini_stream, fake_record_cost, tmp_path,
):
    """File path that exists under cwd → file content fed to stream."""
    from services import agent_tools

    src = tmp_path / "service.py"
    src.write_text("def hello():\n    return 'hi'\n")

    result = await agent_tools.execute_code_review(
        target="service.py",
        cwd=tmp_path,
        scope="Chief Command",
    )
    assert result.error is False
    assert result.output == "Pro review reply."

    assert len(fake_gemini_stream) == 1
    call = fake_gemini_stream[0]
    user_text = call["user_text"]
    assert "Target type: file" in user_text
    assert "Path/range: service.py" in user_text
    assert "def hello():" in user_text


@pytest.mark.asyncio
async def test_path_traversal_denied(fake_gemini_stream, fake_record_cost, tmp_path):
    """``../etc/passwd`` → error, never hits gemini_brain.stream."""
    from services import agent_tools

    result = await agent_tools.execute_code_review(
        target="../etc/passwd",
        cwd=tmp_path,
        scope="Chief Command",
    )
    assert result.error is True
    assert "outside the project cwd" in result.output.lower()
    # Stream MUST NOT have been called for a denied path.
    assert fake_gemini_stream == []


@pytest.mark.asyncio
async def test_absolute_path_outside_cwd_denied(
    fake_gemini_stream, fake_record_cost, tmp_path,
):
    """``/etc/passwd`` → error."""
    from services import agent_tools

    result = await agent_tools.execute_code_review(
        target="/etc/passwd",
        cwd=tmp_path,
        scope="Chief Command",
    )
    assert result.error is True
    assert "outside the project cwd" in result.output.lower()
    assert fake_gemini_stream == []


@pytest.mark.asyncio
async def test_nonexistent_path_falls_through_to_inline(
    fake_gemini_stream, fake_record_cost, tmp_path,
):
    """Path-shaped string that doesn't exist → treated as inline content."""
    from services import agent_tools

    result = await agent_tools.execute_code_review(
        target="does_not_exist.py",
        cwd=tmp_path,
        scope="Chief Command",
    )
    assert result.error is False
    assert len(fake_gemini_stream) == 1
    user_text = fake_gemini_stream[0]["user_text"]
    assert "Target type: inline" in user_text
    # Inline mode does NOT print the "Path/range:" header.
    assert "Path/range:" not in user_text
    # The literal target string lands in the content block.
    assert "does_not_exist.py" in user_text


@pytest.mark.asyncio
async def test_file_target_truncates_at_100kb(
    fake_gemini_stream, fake_record_cost, tmp_path,
):
    """Files larger than CODE_REVIEW_TARGET_MAX_BYTES get truncated with note."""
    from services import agent_tools

    big = tmp_path / "huge.py"
    big.write_text("x" * (agent_tools.CODE_REVIEW_TARGET_MAX_BYTES + 1024))

    result = await agent_tools.execute_code_review(
        target="huge.py",
        cwd=tmp_path,
        scope="Chief Command",
    )
    assert result.error is False
    # ToolResult.truncated mirrors the resolver's flag.
    assert result.truncated is True

    user_text = fake_gemini_stream[0]["user_text"]
    assert "truncated at" in user_text.lower()
    assert "head slice only" in user_text


# ---------------------------------------------------------------------------
# Resolution: git range
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_git_range_target_invokes_git_diff(
    fake_gemini_stream, fake_record_cost, tmp_path, monkeypatch,
):
    """``HEAD~3..HEAD`` → ``git -C <cwd> diff HEAD~3..HEAD`` runs."""
    from services import agent_tools

    captured_argv: list[list[str]] = []

    class _FakeProc:
        def __init__(self, stdout=b"+ added line\n- removed line\n", returncode=0):
            self._stdout = stdout
            self._returncode = returncode

        async def communicate(self):
            return self._stdout, b""

        @property
        def returncode(self):
            return self._returncode

    async def _fake_create_subprocess_exec(*args, **kwargs):  # noqa: ARG001
        captured_argv.append(list(args))
        return _FakeProc()

    monkeypatch.setattr(
        agent_tools.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec,
    )

    result = await agent_tools.execute_code_review(
        target="HEAD~3..HEAD",
        cwd=tmp_path,
        scope="Chief Command",
    )
    assert result.error is False
    assert len(captured_argv) == 1
    argv = captured_argv[0]
    # ``git -C <resolved-cwd> diff HEAD~3..HEAD``
    assert argv[0] == "git"
    assert argv[1] == "-C"
    assert Path(argv[2]).resolve() == tmp_path.resolve()
    assert argv[3] == "diff"
    assert argv[4] == "HEAD~3..HEAD"

    user_text = fake_gemini_stream[0]["user_text"]
    assert "Target type: git_range" in user_text
    assert "Path/range: HEAD~3..HEAD" in user_text
    assert "+ added line" in user_text


@pytest.mark.asyncio
async def test_bare_head_normalized_to_range(
    fake_gemini_stream, fake_record_cost, tmp_path, monkeypatch,
):
    """A bare ``HEAD~2`` is normalized to ``HEAD~2..HEAD`` for git diff."""
    from services import agent_tools

    captured_argv: list[list[str]] = []

    class _FakeProc:
        async def communicate(self):
            return b"diff text", b""

        @property
        def returncode(self):
            return 0

    async def _fake_create_subprocess_exec(*args, **kwargs):  # noqa: ARG001
        captured_argv.append(list(args))
        return _FakeProc()

    monkeypatch.setattr(
        agent_tools.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec,
    )

    result = await agent_tools.execute_code_review(
        target="HEAD~2",
        cwd=tmp_path,
        scope="Chief Command",
    )
    assert result.error is False
    assert captured_argv[0][4] == "HEAD~2..HEAD"


@pytest.mark.asyncio
async def test_git_range_failure_falls_through_to_inline(
    fake_gemini_stream, fake_record_cost, tmp_path, monkeypatch,
):
    """If ``git diff`` exits non-zero (unknown ref), target is treated as inline."""
    from services import agent_tools

    class _FakeProc:
        async def communicate(self):
            return b"", b"fatal: bad revision\n"

        @property
        def returncode(self):
            return 128

    async def _fake_create_subprocess_exec(*args, **kwargs):  # noqa: ARG001
        return _FakeProc()

    monkeypatch.setattr(
        agent_tools.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec,
    )

    result = await agent_tools.execute_code_review(
        target="bogus..ref",
        cwd=tmp_path,
        scope="Chief Command",
    )
    assert result.error is False
    user_text = fake_gemini_stream[0]["user_text"]
    assert "Target type: inline" in user_text
    assert "bogus..ref" in user_text


# ---------------------------------------------------------------------------
# Resolution: inline
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_inline_target_passes_through(
    fake_gemini_stream, fake_record_cost, tmp_path,
):
    """Multi-line code blob → inline content."""
    from services import agent_tools

    code = (
        "def insecure_query(uid):\n"
        "    return db.execute(f'SELECT * FROM users WHERE id = {uid}')\n"
    )
    result = await agent_tools.execute_code_review(
        target=code,
        cwd=tmp_path,
        scope="Chief Command",
    )
    assert result.error is False
    user_text = fake_gemini_stream[0]["user_text"]
    assert "Target type: inline" in user_text
    assert "insecure_query" in user_text
    assert "SELECT * FROM users" in user_text


# ---------------------------------------------------------------------------
# Focus parameter
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_focus_changes_prompt_angle(
    fake_gemini_stream, fake_record_cost, tmp_path,
):
    """``focus='security'`` → prompt mentions security-specific angle text."""
    from services import agent_tools

    await agent_tools.execute_code_review(
        target="some snippet of code",
        cwd=tmp_path,
        scope="Chief Command",
        focus="security",
    )
    user_text = fake_gemini_stream[0]["user_text"]
    assert "security vulnerabilities" in user_text
    assert "auth gaps" in user_text


@pytest.mark.asyncio
async def test_unknown_focus_downgrades_to_general(
    fake_gemini_stream, fake_record_cost, tmp_path,
):
    """Off-enum focus value → silently uses 'general' angle."""
    from services import agent_tools

    await agent_tools.execute_code_review(
        target="snippet",
        cwd=tmp_path,
        scope="Chief Command",
        focus="zoo",
    )
    user_text = fake_gemini_stream[0]["user_text"]
    assert "correctness, readability, obvious issues" in user_text


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_timeout_returns_error_result(
    fake_record_cost, tmp_path, monkeypatch,
):
    """Slow gemini_brain.stream → killed at the wall-clock cap."""
    from services import agent_tools
    import services.gemini_brain as gb_mod

    monkeypatch.setattr(agent_tools, "CODE_REVIEW_TIMEOUT_S", 0.1)

    async def _slow_stream(**kwargs):
        await asyncio.sleep(2.0)
        return {"input_tokens": 0, "output_tokens": 0, "model": "gemini-2.5-pro"}

    monkeypatch.setattr(gb_mod, "stream", _slow_stream)

    result = await agent_tools.execute_code_review(
        target="slow snippet",
        cwd=tmp_path,
        scope="Chief Command",
    )
    assert result.error is True
    assert "timed out" in result.output.lower()


# ---------------------------------------------------------------------------
# Cost recording
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cost_is_recorded(
    fake_gemini_stream, fake_record_cost, tmp_path,
):
    """The executor invokes record_code_review_cost with token counts."""
    from services import agent_tools

    await agent_tools.execute_code_review(
        target="snippet of code",
        cwd=tmp_path,
        scope="Arch",
    )
    assert len(fake_record_cost) == 1
    call = fake_record_cost[0]
    assert call["model"] == "gemini-2.5-pro"
    assert call["scope"] == "Arch"
    assert call["input_tokens"] == 200
    assert call["output_tokens"] == 80


# ---------------------------------------------------------------------------
# Empty-target / empty-reply guards
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_empty_target_errors_without_stream(
    fake_gemini_stream, fake_record_cost, tmp_path,
):
    """Whitespace-only target → reject before stream is called."""
    from services import agent_tools

    result = await agent_tools.execute_code_review(
        target="   ",
        cwd=tmp_path,
        scope="Chief Command",
    )
    assert result.error is True
    assert fake_gemini_stream == []


@pytest.mark.asyncio
async def test_empty_stream_reply_returns_error(
    fake_record_cost, tmp_path, monkeypatch,
):
    """gemini_brain.stream emits no tokens AND empty assistant_text → error."""
    from services import agent_tools
    import services.gemini_brain as gb_mod

    async def _empty_stream(**kwargs):  # noqa: ARG001
        return {"input_tokens": 0, "output_tokens": 0, "model": "gemini-2.5-pro",
                "assistant_text": ""}

    monkeypatch.setattr(gb_mod, "stream", _empty_stream)

    result = await agent_tools.execute_code_review(
        target="snippet",
        cwd=tmp_path,
        scope="Chief Command",
    )
    assert result.error is True
    assert "empty" in result.output.lower()


# ---------------------------------------------------------------------------
# dispatch_tool routing
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dispatch_tool_routes_code_review(
    fake_gemini_stream, fake_record_cost, tmp_path,
):
    """``dispatch_tool("code_review", ...)`` calls execute_code_review."""
    from services import agent_tools

    result = await agent_tools.dispatch_tool(
        "code_review",
        {"target": "some pasted spec text", "focus": "spec"},
        cwd=tmp_path,
        subject="owner",
        scope="Chief Command",
        system_prompt_append="",
    )
    assert result.error is False
    assert result.output == "Pro review reply."
    user_text = fake_gemini_stream[0]["user_text"]
    assert "completeness, ambiguity, edge cases" in user_text


@pytest.mark.asyncio
async def test_dispatch_tool_code_review_blocked_by_home_cwd_guard(tmp_path):
    """code_review IS subject to the $HOME-fallback cwd guard.

    Unlike think_deep (which doesn't touch the filesystem), code_review's
    file-path branch reads files relative to cwd. If cwd is the unsafe
    $HOME fallback, refuse — same posture as Read/Bash/Grep.
    """
    from services import agent_tools

    result = await agent_tools.dispatch_tool(
        "code_review",
        {"target": "anything"},
        cwd=Path.home(),
        subject="owner",
        scope="Chief Command",
        system_prompt_append="",
    )
    assert result.error is True
    assert (
        "tool dispatch refused" in result.output.lower()
        or "no project scope" in result.output.lower()
    )


# ---------------------------------------------------------------------------
# Schema sanity
# ---------------------------------------------------------------------------
def test_code_review_tool_in_all_tools():
    """ALL_TOOLS includes CODE_REVIEW_TOOL — surfaces in Gemini decls."""
    from services import agent_tools

    names = {t.name for t in agent_tools.ALL_TOOLS}
    assert "code_review" in names


def test_code_review_schema_required_target_and_focus_enum():
    """Required arg is ``target``; ``focus`` has the documented enum."""
    from services import agent_tools

    schema = agent_tools.CODE_REVIEW_TOOL.parameters
    assert schema["required"] == ["target"]
    assert "target" in schema["properties"]
    focus_prop = schema["properties"].get("focus")
    assert focus_prop is not None
    enum = focus_prop.get("enum")
    assert set(enum) == {
        "general", "security", "performance", "spec", "architecture",
    }
    assert focus_prop.get("default") == "general"


def test_to_gemini_declarations_includes_code_review():
    """to_gemini_declarations() emits 6 decls including code_review."""
    from services.agent_tools import to_gemini_declarations

    decls = to_gemini_declarations()
    names = [d.name for d in decls]
    assert "code_review" in names
    assert len(decls) == 6
