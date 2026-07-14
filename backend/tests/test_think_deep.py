"""Tests for the think_deep tool — `claude` CLI subprocess escalation.

Routed through the CLI on 2026-05-12 so escalations use the Max-subscription
OAuth login (flat-rate) instead of per-token API billing. Same auth trick
``dispatcher.py`` uses for ``dispatch_agent``.

Coverage:
  * Happy path: CLI returns JSON → ToolResult.output carries the result
  * Default model is Opus; Sonnet opt-in is forwarded verbatim
  * Bad model arg falls back to Opus (allowlist guard)
  * Empty prompt rejects without spawning the subprocess
  * Missing claude CLI fails closed
  * Timeout fires and returns an error ToolResult
  * Non-zero exit / CLI error payload / malformed JSON all surface generic errors
  * Subprocess env is stripped of ANTHROPIC_API_KEY (Max OAuth path)
  * argv carries the expected flags (--print, --tools "", --output-format json, ...)
  * Cost is recorded via record_think_deep_cost
  * dispatch_tool routes "think_deep" to execute_think_deep
  * dispatch_tool path-fence is bypassed for think_deep (it doesn't touch fs)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("OWNER_PASSWORD", "test")
os.environ.setdefault("JWT_SECRET", "test")


# ---------------------------------------------------------------------------
# Fixtures: stub the chief_context loader, the usage recorder, the claude
# binary resolver, and the subprocess runner.
# ---------------------------------------------------------------------------
@pytest.fixture
def fake_chief_context(monkeypatch):
    """Stub build_chief_system_string so tests aren't tied to memory dirs."""
    def _fake(scope, prior_summary=None, *, for_live=False):
        return f"[CHIEF system scope={scope}]"
    import services.chief_context as cc_mod
    monkeypatch.setattr(cc_mod, "build_chief_system_string", _fake)


@pytest.fixture
def fake_record_cost(monkeypatch):
    """Stub record_think_deep_cost; capture call args."""
    calls: list[dict] = []

    async def _fake(**kwargs):
        calls.append(dict(kwargs))
        return {"cost_cents": 0}

    import services.usage_tracker as ut
    monkeypatch.setattr(ut, "record_think_deep_cost", _fake)
    return calls


@pytest.fixture
def fake_claude_bin(monkeypatch):
    """Pretend the `claude` CLI exists on PATH at a fixed fake path."""
    import services.agent_tools as at
    monkeypatch.setattr(at.shutil, "which", lambda name: "/fake/bin/claude")


def _success_payload(text: str = "Sonnet's careful answer.", *, input_tokens=100, output_tokens=200) -> dict:
    """Shape matches the actual `claude -p --output-format json` payload."""
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "duration_ms": 1234,
        "num_turns": 1,
        "result": text,
        "session_id": "test-session",
        "total_cost_usd": 0.0,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
    }


@pytest.fixture
def fake_cli(monkeypatch):
    """Install a fake _run_claude_cli that returns canned (rc, stdout, stderr).

    Tests configure it by calling ``fake_cli.set(...)`` BEFORE invoking
    execute_think_deep. Captures the last call's argv/cwd/env for assertions.
    """
    import services.agent_tools as at

    state = {
        "returncode": 0,
        "stdout": json.dumps(_success_payload()).encode(),
        "stderr": b"",
        "sleep_seconds": 0.0,
        "raises": None,
        "last_argv": None,
        "last_cwd": None,
        "last_env": None,
    }

    async def _fake_run_cli(argv, *, cwd, env, timeout):
        state["last_argv"] = list(argv)
        state["last_cwd"] = cwd
        state["last_env"] = dict(env)
        if state["sleep_seconds"] > 0:
            await asyncio.sleep(state["sleep_seconds"])
        if state["raises"] is not None:
            raise state["raises"]
        return state["returncode"], state["stdout"], state["stderr"]

    monkeypatch.setattr(at, "_run_claude_cli", _fake_run_cli)

    class _FakeCli:
        def set(self, *, returncode=0, stdout=None, stderr=b"", sleep_seconds=0.0, raises=None, payload=None):
            if payload is not None:
                stdout = json.dumps(payload).encode()
            state["returncode"] = returncode
            state["stdout"] = stdout if stdout is not None else json.dumps(_success_payload()).encode()
            state["stderr"] = stderr
            state["sleep_seconds"] = sleep_seconds
            state["raises"] = raises

        @property
        def last_argv(self):
            return state["last_argv"]

        @property
        def last_cwd(self):
            return state["last_cwd"]

        @property
        def last_env(self):
            return state["last_env"]

    return _FakeCli()


# ---------------------------------------------------------------------------
# Core tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_execute_think_deep_returns_assistant_text(
    fake_chief_context, fake_record_cost, fake_claude_bin, fake_cli,
):
    """Happy path: CLI JSON payload's ``result`` lands in ToolResult.output."""
    from services import agent_tools

    fake_cli.set(payload=_success_payload("Here is the careful answer."))
    result = await agent_tools.execute_think_deep(
        "How should I structure the migration?",
        scope="Chief Command",
    )
    assert result.error is False
    assert result.output == "Here is the careful answer."


@pytest.mark.asyncio
async def test_execute_think_deep_default_model_is_opus(
    fake_chief_context, fake_record_cost, fake_claude_bin, fake_cli,
):
    """No model arg → defaults to claude-opus-4-7 (flipped 2026-05-05).

    Owner uses think_deep almost exclusively for hard reasoning where Opus's
    depth gap matters more than Sonnet's ~1s latency win. The bridge-phrase
    rule (chief_context #13) covers the perceived latency.
    """
    from services import agent_tools

    result = await agent_tools.execute_think_deep("walk me through it", scope="Arch")
    assert result.error is False

    argv = fake_cli.last_argv
    model_idx = argv.index("--model")
    assert argv[model_idx + 1] == agent_tools.THINK_DEEP_DEFAULT_MODEL
    assert argv[model_idx + 1] == "claude-opus-4-7"
    # System prompt carries the active scope.
    sys_idx = argv.index("--system-prompt")
    assert "scope=Arch" in argv[sys_idx + 1]


@pytest.mark.asyncio
async def test_execute_think_deep_sonnet_opt_in(
    fake_chief_context, fake_record_cost, fake_claude_bin, fake_cli,
):
    """Sonnet is a valid opt-in model; the executor forwards it verbatim.

    Used by the Live brain when the ask is light enough that 1-2s pacing
    matters more than reasoning depth.
    """
    from services import agent_tools

    fake_cli.set(payload=_success_payload("sonnet answer"))
    result = await agent_tools.execute_think_deep(
        "lighter quick reasoning",
        scope="Chief Command",
        model="claude-sonnet-4-6",
    )
    assert result.output == "sonnet answer"
    argv = fake_cli.last_argv
    model_idx = argv.index("--model")
    assert argv[model_idx + 1] == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_execute_think_deep_opus_explicit(
    fake_chief_context, fake_record_cost, fake_claude_bin, fake_cli,
):
    """Explicit Opus call still works (matches the new default but the
    executor forwards the explicit value verbatim)."""
    from services import agent_tools

    fake_cli.set(payload=_success_payload("opus answer"))
    result = await agent_tools.execute_think_deep(
        "design tradeoffs",
        scope="Chief Command",
        model="claude-opus-4-7",
    )
    assert result.output == "opus answer"
    argv = fake_cli.last_argv
    model_idx = argv.index("--model")
    assert argv[model_idx + 1] == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_execute_think_deep_invalid_model_falls_back_to_default(
    fake_chief_context, fake_record_cost, fake_claude_bin, fake_cli,
):
    """A model not in the allowlist is silently downgraded to the default
    (Opus, post-2026-05-05 flip — defense-in-depth; the schema enum should
    already catch it server-side).
    """
    from services import agent_tools

    result = await agent_tools.execute_think_deep(
        "plan",
        scope="Chief Command",
        model="claude-haiku-4-5",  # not in allowlist
    )
    assert result.error is False
    argv = fake_cli.last_argv
    model_idx = argv.index("--model")
    assert argv[model_idx + 1] == agent_tools.THINK_DEEP_DEFAULT_MODEL
    assert argv[model_idx + 1] == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_execute_think_deep_records_cost(
    fake_chief_context, fake_record_cost, fake_claude_bin, fake_cli,
):
    """The executor invokes record_think_deep_cost with the right shape.

    Cost is informational only on Max sub (flat-rate) but the daily
    dashboard still tracks escalation volume.
    """
    from services import agent_tools

    fake_cli.set(payload=_success_payload("answer", input_tokens=100, output_tokens=200))
    await agent_tools.execute_think_deep("plan it", scope="Arch")
    assert len(fake_record_cost) == 1
    call = fake_record_cost[0]
    # Default flipped to Opus 2026-05-05 — cost rows should reflect.
    assert call["model"] == "claude-opus-4-7"
    assert call["scope"] == "Arch"
    assert call["input_tokens"] == 100
    assert call["output_tokens"] == 200
    assert call["prompt"] == "plan it"
    assert call["assistant_text"] == "answer"


@pytest.mark.asyncio
async def test_execute_think_deep_empty_prompt_errors_without_spawning(
    fake_chief_context, fake_record_cost, fake_claude_bin, fake_cli,
):
    """Empty / whitespace prompt rejected without spawning the CLI."""
    from services import agent_tools

    result = await agent_tools.execute_think_deep("   ", scope="Arch")
    assert result.error is True
    assert "prompt is required" in result.output
    # CLI never spawned.
    assert fake_cli.last_argv is None


@pytest.mark.asyncio
async def test_execute_think_deep_missing_claude_cli_fails_closed(
    fake_chief_context, fake_record_cost, fake_cli, monkeypatch,
):
    """`claude` not on PATH → returns error ToolResult, never spawns."""
    from services import agent_tools

    monkeypatch.setattr(agent_tools.shutil, "which", lambda name: None)

    result = await agent_tools.execute_think_deep("plan", scope="Arch")
    assert result.error is True
    assert "claude cli not found" in result.output.lower()
    # CLI never spawned.
    assert fake_cli.last_argv is None


@pytest.mark.asyncio
async def test_execute_think_deep_timeout_returns_error(
    fake_chief_context, fake_record_cost, fake_claude_bin, fake_cli, monkeypatch,
):
    """A slow subprocess is killed at the configured timeout."""
    from services import agent_tools

    # Force the timeout to a tiny value so the test runs fast.
    monkeypatch.setattr(agent_tools, "THINK_DEEP_TIMEOUT_S", 0.05)
    # But the fake CLI helper sleeps longer than the timeout. The fake
    # itself raises TimeoutError when the cap fires (since we set it up
    # to surface via raises rather than relying on asyncio.timeout to
    # wrap the fake).
    async def _slow(*args, **kwargs):
        raise asyncio.TimeoutError()
    monkeypatch.setattr(agent_tools, "_run_claude_cli", _slow)

    result = await agent_tools.execute_think_deep("slow think", scope="Arch")
    assert result.error is True
    assert "timed out" in result.output.lower()


@pytest.mark.asyncio
async def test_execute_think_deep_nonzero_exit_returns_error(
    fake_chief_context, fake_record_cost, fake_claude_bin, fake_cli,
):
    """CLI exits non-zero → generic error, no stderr leakage to voice."""
    from services import agent_tools

    fake_cli.set(returncode=2, stdout=b"", stderr=b"Error: rate limit hit\n")
    result = await agent_tools.execute_think_deep("plan", scope="Arch")
    assert result.error is True
    assert "rate limit" not in result.output.lower()
    assert "failed" in result.output.lower()


@pytest.mark.asyncio
async def test_execute_think_deep_cli_error_payload_returns_error(
    fake_chief_context, fake_record_cost, fake_claude_bin, fake_cli,
):
    """Exit 0 but ``is_error: true`` in the payload → generic error."""
    from services import agent_tools

    payload = _success_payload("never read")
    payload["is_error"] = True
    payload["subtype"] = "error_during_execution"
    fake_cli.set(payload=payload)
    result = await agent_tools.execute_think_deep("plan", scope="Arch")
    assert result.error is True
    assert "failed" in result.output.lower()


@pytest.mark.asyncio
async def test_execute_think_deep_malformed_json_returns_error(
    fake_chief_context, fake_record_cost, fake_claude_bin, fake_cli,
):
    """CLI returned exit 0 but garbage stdout → generic malformed error."""
    from services import agent_tools

    fake_cli.set(returncode=0, stdout=b"not json at all")
    result = await agent_tools.execute_think_deep("plan", scope="Arch")
    assert result.error is True
    assert "malformed" in result.output.lower()


@pytest.mark.asyncio
async def test_execute_think_deep_empty_text_returns_error(
    fake_chief_context, fake_record_cost, fake_claude_bin, fake_cli,
):
    """Success frame with empty/whitespace ``result`` → error, not silent."""
    from services import agent_tools

    fake_cli.set(payload=_success_payload(text="   "))
    result = await agent_tools.execute_think_deep("plan", scope="Arch")
    assert result.error is True
    assert "empty" in result.output.lower()


@pytest.mark.asyncio
async def test_execute_think_deep_env_strips_anthropic_key(
    fake_chief_context, fake_record_cost, fake_claude_bin, fake_cli, monkeypatch,
):
    """The subprocess env MUST NOT carry ANTHROPIC_API_KEY (Max-sub OAuth)."""
    from services import agent_tools

    # Put a fake key into the parent env; the executor's allowlist should drop it.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-be-stripped")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "should-be-stripped")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "also-stripped")
    monkeypatch.setenv("GITHUB_TOKEN", "also-stripped")

    await agent_tools.execute_think_deep("plan", scope="Arch")
    env = fake_cli.last_env
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "GITHUB_TOKEN" not in env
    # PATH / HOME survive (CLI needs them to find node + creds dir).
    assert "PATH" in env
    assert "HOME" in env


@pytest.mark.asyncio
async def test_execute_think_deep_argv_disables_tools_and_uses_json_output(
    fake_chief_context, fake_record_cost, fake_claude_bin, fake_cli,
):
    """argv carries the flags that make think_deep pure-reasoning + parseable.

    Regression guard: if someone drops --tools "" the CLI will start trying
    to Read/Bash from the scratch CWD, and if someone drops the JSON output
    flag we'll fail to parse the result.
    """
    from services import agent_tools

    await agent_tools.execute_think_deep("plan", scope="Arch")
    argv = fake_cli.last_argv
    assert "--print" in argv
    # --tools "" is two adjacent argv elements.
    tools_idx = argv.index("--tools")
    assert argv[tools_idx + 1] == ""
    fmt_idx = argv.index("--output-format")
    assert argv[fmt_idx + 1] == "json"
    # End-of-options marker before the prompt (Vera HIGH guard).
    assert "--" in argv
    assert argv[-1] == "plan"


# ---------------------------------------------------------------------------
# dispatch_tool routing
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dispatch_tool_routes_think_deep(
    fake_chief_context, fake_record_cost, fake_claude_bin, fake_cli, tmp_path,
):
    """``dispatch_tool("think_deep", ...)`` calls execute_think_deep."""
    from services import agent_tools

    fake_cli.set(payload=_success_payload("dispatched answer"))
    result = await agent_tools.dispatch_tool(
        "think_deep",
        {"prompt": "walk through this", "model": "claude-sonnet-4-6"},
        cwd=tmp_path,
        subject="owner",
        scope="Chief Command",
        system_prompt_append="",
    )
    assert result.error is False
    assert result.output == "dispatched answer"


@pytest.mark.asyncio
async def test_dispatch_tool_think_deep_bypasses_home_cwd_guard(
    fake_chief_context, fake_record_cost, fake_claude_bin, fake_cli,
):
    """think_deep doesn't touch the filesystem (it spawns the CLI in a
    scratch tmp dir) so the $HOME-fallback guard that refuses
    Read/Bash/Grep/dispatch_agent must NOT block it.
    """
    from services import agent_tools

    fake_cli.set(payload=_success_payload("answer-from-home"))
    result = await agent_tools.dispatch_tool(
        "think_deep",
        {"prompt": "plan it"},
        cwd=Path.home(),  # the unsafe fallback
        subject="owner",
        scope="Chief Command",
        system_prompt_append="",
    )
    assert result.error is False
    assert result.output == "answer-from-home"


@pytest.mark.asyncio
async def test_dispatch_tool_read_still_blocked_by_home_cwd_guard(tmp_path):
    """Read/Bash/Grep MUST still be refused when cwd is $HOME — only
    think_deep gets the bypass. Regression guard against the bypass
    accidentally widening.
    """
    from services import agent_tools

    result = await agent_tools.dispatch_tool(
        "Read",
        {"path": "anything.txt"},
        cwd=Path.home(),
        subject="owner",
        scope="Chief Command",
        system_prompt_append="",
    )
    assert result.error is True
    assert "tool dispatch refused" in result.output.lower() or "no project scope" in result.output.lower()


# ---------------------------------------------------------------------------
# Schema sanity
# ---------------------------------------------------------------------------
def test_think_deep_tool_in_all_tools():
    """ALL_TOOLS includes THINK_DEEP_TOOL so it surfaces in the Gemini
    function declaration list.
    """
    from services import agent_tools

    names = {t.name for t in agent_tools.ALL_TOOLS}
    assert "think_deep" in names


def test_think_deep_schema_required_prompt():
    """Required arg is ``prompt``; ``model`` has an enum allowlist."""
    from services import agent_tools

    schema = agent_tools.THINK_DEEP_TOOL.parameters
    assert "prompt" in schema["required"]
    assert "model" in schema["properties"]
    enum = schema["properties"]["model"].get("enum")
    assert "claude-sonnet-4-6" in enum
    assert "claude-opus-4-7" in enum
