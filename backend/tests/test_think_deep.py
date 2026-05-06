"""Tests for the think_deep tool — direct Anthropic API escalation.

Stage 3 of the Gemini Live pivot.

Coverage:
  * Sonnet path returns text; Opus path is selectable
  * Cost is recorded via record_think_deep_cost
  * Timeout fires after 30s
  * Bad model arg falls back to Sonnet (allowlist guard)
  * Empty prompt rejects without hitting the API
  * Missing API key fails closed
  * dispatch_tool routes "think_deep" to execute_think_deep
  * dispatch_tool path-fence is bypassed for think_deep (it doesn't touch fs)
"""

from __future__ import annotations

import asyncio
import os
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("OWNER_PASSWORD", "test")
os.environ.setdefault("JWT_SECRET", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")


# ---------------------------------------------------------------------------
# Fakes for the Anthropic SDK
# ---------------------------------------------------------------------------
class _FakeUsage:
    def __init__(self, input_tokens=100, output_tokens=200, cache_read=0, cache_creation=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read
        self.cache_creation_input_tokens = cache_creation


class _FakeBlock:
    def __init__(self, text: str):
        self.text = text


class _FakeMessage:
    def __init__(self, text: str, usage: _FakeUsage | None = None):
        self.content = [_FakeBlock(text)]
        self.usage = usage or _FakeUsage()


def _make_fake_anthropic(reply_text: str = "Sonnet's careful answer.", *, raises=None, sleep_seconds: float = 0.0):
    """Build a fake AsyncAnthropic class whose messages.create returns one
    FakeMessage (or sleeps / raises as configured).
    """
    class _FakeMessages:
        async def create(self, *, model, max_tokens, system, messages):
            self.last_call = {  # type: ignore[attr-defined]
                "model": model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": messages,
            }
            if sleep_seconds > 0:
                await asyncio.sleep(sleep_seconds)
            if raises is not None:
                raise raises
            return _FakeMessage(reply_text)

    class _FakeClient:
        def __init__(self, *, api_key=None):
            self.api_key = api_key
            self.messages = _FakeMessages()

    return _FakeClient


@pytest.fixture
def fake_chief_context(monkeypatch):
    """Stub build_chief_system_string so the test isn't tied to memory dirs."""
    def _fake(scope, prior_summary=None, *, for_live=False):
        return f"[CHIEF system scope={scope}]"
    # Patch BOTH the module level and the import target inside agent_tools.
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


# ---------------------------------------------------------------------------
# Core tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_execute_think_deep_returns_assistant_text(fake_chief_context, fake_record_cost):
    """Happy path: Sonnet returns text → ToolResult.output carries it."""
    from services import agent_tools

    fake_cls = _make_fake_anthropic("Here is the careful answer.")
    fake_module = types.SimpleNamespace(AsyncAnthropic=fake_cls)
    with patch.dict(sys.modules, {"anthropic": fake_module}):
        result = await agent_tools.execute_think_deep(
            "How should I structure the migration?",
            scope="Chief Command",
        )
    assert result.error is False
    assert result.output == "Here is the careful answer."


@pytest.mark.asyncio
async def test_execute_think_deep_default_model_is_opus(fake_chief_context, fake_record_cost):
    """No model arg → defaults to claude-opus-4-7 (flipped 2026-05-05).

    Owner uses think_deep almost exclusively for hard reasoning where Opus's
    depth gap matters more than Sonnet's ~1s latency win. The bridge-phrase
    rule (chief_context #13) covers the perceived latency.
    """
    from services import agent_tools

    fake_cls = _make_fake_anthropic()
    fake_module = types.SimpleNamespace(AsyncAnthropic=fake_cls)
    captured: dict = {}

    class _CapturingClient:
        def __init__(self, *, api_key=None):
            self.messages = self  # short-circuit

        async def create(self, *, model, max_tokens, system, messages):
            captured["model"] = model
            captured["system"] = system
            return _FakeMessage("ok")

    fake_module.AsyncAnthropic = _CapturingClient

    with patch.dict(sys.modules, {"anthropic": fake_module}):
        result = await agent_tools.execute_think_deep("walk me through it", scope="Arch")
    assert result.error is False
    assert captured["model"] == agent_tools.THINK_DEEP_DEFAULT_MODEL
    assert captured["model"] == "claude-opus-4-7"
    # System prompt carries the active scope.
    assert "scope=Arch" in captured["system"]


@pytest.mark.asyncio
async def test_execute_think_deep_sonnet_opt_in(fake_chief_context, fake_record_cost):
    """Sonnet is a valid opt-in model; the executor forwards it verbatim.

    Used by the Live brain when the ask is light enough that 1-2s pacing
    matters more than reasoning depth.
    """
    from services import agent_tools

    captured: dict = {}

    class _CapturingClient:
        def __init__(self, *, api_key=None):
            self.messages = self

        async def create(self, *, model, max_tokens, system, messages):
            captured["model"] = model
            return _FakeMessage("sonnet answer")

    fake_module = types.SimpleNamespace(AsyncAnthropic=_CapturingClient)
    with patch.dict(sys.modules, {"anthropic": fake_module}):
        result = await agent_tools.execute_think_deep(
            "lighter quick reasoning",
            scope="Chief Command",
            model="claude-sonnet-4-6",
        )
    assert result.output == "sonnet answer"
    assert captured["model"] == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_execute_think_deep_opus_explicit(fake_chief_context, fake_record_cost):
    """Explicit Opus call still works (matches the new default but the
    executor forwards the explicit value verbatim)."""
    from services import agent_tools

    captured: dict = {}

    class _CapturingClient:
        def __init__(self, *, api_key=None):
            self.messages = self

        async def create(self, *, model, max_tokens, system, messages):
            captured["model"] = model
            return _FakeMessage("opus answer")

    fake_module = types.SimpleNamespace(AsyncAnthropic=_CapturingClient)
    with patch.dict(sys.modules, {"anthropic": fake_module}):
        result = await agent_tools.execute_think_deep(
            "design tradeoffs",
            scope="Chief Command",
            model="claude-opus-4-7",
        )
    assert result.output == "opus answer"
    assert captured["model"] == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_execute_think_deep_invalid_model_falls_back_to_default(
    fake_chief_context, fake_record_cost,
):
    """A model not in the allowlist is silently downgraded to the default
    (Opus, post-2026-05-05 flip — defense-in-depth; the schema enum should
    already catch it server-side).
    """
    from services import agent_tools

    captured: dict = {}

    class _CapturingClient:
        def __init__(self, *, api_key=None):
            self.messages = self

        async def create(self, *, model, max_tokens, system, messages):
            captured["model"] = model
            return _FakeMessage("answer")

    fake_module = types.SimpleNamespace(AsyncAnthropic=_CapturingClient)
    with patch.dict(sys.modules, {"anthropic": fake_module}):
        result = await agent_tools.execute_think_deep(
            "plan",
            scope="Chief Command",
            model="claude-haiku-4-5",  # not in allowlist
        )
    assert result.error is False
    assert captured["model"] == agent_tools.THINK_DEEP_DEFAULT_MODEL
    assert captured["model"] == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_execute_think_deep_records_cost(fake_chief_context, fake_record_cost):
    """The executor invokes record_think_deep_cost with the right shape."""
    from services import agent_tools

    fake_cls = _make_fake_anthropic("answer")
    fake_module = types.SimpleNamespace(AsyncAnthropic=fake_cls)
    with patch.dict(sys.modules, {"anthropic": fake_module}):
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
async def test_execute_think_deep_empty_prompt_errors_without_api_call(
    fake_chief_context, fake_record_cost,
):
    """Empty / whitespace prompt rejected without hitting Anthropic."""
    from services import agent_tools

    api_called = False

    class _NeverCalledClient:
        def __init__(self, *, api_key=None):
            self.messages = self

        async def create(self, *args, **kwargs):  # noqa: ARG002
            nonlocal api_called
            api_called = True
            return _FakeMessage("should not run")

    fake_module = types.SimpleNamespace(AsyncAnthropic=_NeverCalledClient)
    with patch.dict(sys.modules, {"anthropic": fake_module}):
        result = await agent_tools.execute_think_deep("   ", scope="Arch")
    assert result.error is True
    assert "prompt is required" in result.output
    assert api_called is False


@pytest.mark.asyncio
async def test_execute_think_deep_missing_api_key_fails_closed(
    fake_chief_context, fake_record_cost, monkeypatch,
):
    """ANTHROPIC_API_KEY unset → returns error ToolResult, never hits SDK."""
    from services import agent_tools
    from config.settings import settings

    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", None)

    api_called = False

    class _NeverCalledClient:
        def __init__(self, *, api_key=None):
            self.messages = self

        async def create(self, *args, **kwargs):  # noqa: ARG002
            nonlocal api_called
            api_called = True
            return _FakeMessage("should not run")

    fake_module = types.SimpleNamespace(AsyncAnthropic=_NeverCalledClient)
    with patch.dict(sys.modules, {"anthropic": fake_module}):
        result = await agent_tools.execute_think_deep("plan", scope="Arch")
    assert result.error is True
    assert "no Anthropic API key" in result.output.lower() or "api key" in result.output.lower()
    assert api_called is False


@pytest.mark.asyncio
async def test_execute_think_deep_timeout_returns_error(
    fake_chief_context, fake_record_cost, monkeypatch,
):
    """A slow API call is killed at the configured timeout."""
    from services import agent_tools

    # Force the timeout to a tiny value so the test runs fast.
    monkeypatch.setattr(agent_tools, "THINK_DEEP_TIMEOUT_S", 0.1)

    fake_cls = _make_fake_anthropic("never returned", sleep_seconds=2.0)
    fake_module = types.SimpleNamespace(AsyncAnthropic=fake_cls)
    with patch.dict(sys.modules, {"anthropic": fake_module}):
        result = await agent_tools.execute_think_deep("slow think", scope="Arch")
    assert result.error is True
    assert "timed out" in result.output.lower()


@pytest.mark.asyncio
async def test_execute_think_deep_api_failure_returns_generic_error(
    fake_chief_context, fake_record_cost,
):
    """Auth / rate-limit / 5xx surfaces a generic error to the model."""
    from services import agent_tools

    fake_cls = _make_fake_anthropic(raises=RuntimeError("rate limit"))
    fake_module = types.SimpleNamespace(AsyncAnthropic=fake_cls)
    with patch.dict(sys.modules, {"anthropic": fake_module}):
        result = await agent_tools.execute_think_deep("plan", scope="Arch")
    assert result.error is True
    # Generic message — does NOT leak "rate limit" raw text.
    assert "rate limit" not in result.output.lower()
    assert "failed" in result.output.lower()


# ---------------------------------------------------------------------------
# dispatch_tool routing
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dispatch_tool_routes_think_deep(fake_chief_context, fake_record_cost, tmp_path):
    """``dispatch_tool("think_deep", ...)`` calls execute_think_deep."""
    from services import agent_tools

    fake_cls = _make_fake_anthropic("dispatched answer")
    fake_module = types.SimpleNamespace(AsyncAnthropic=fake_cls)
    with patch.dict(sys.modules, {"anthropic": fake_module}):
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
    fake_chief_context, fake_record_cost,
):
    """think_deep doesn't touch the filesystem so the $HOME-fallback guard
    that refuses Read/Bash/Grep/dispatch_agent must NOT block it.
    """
    from services import agent_tools

    fake_cls = _make_fake_anthropic("answer-from-home")
    fake_module = types.SimpleNamespace(AsyncAnthropic=fake_cls)
    with patch.dict(sys.modules, {"anthropic": fake_module}):
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
