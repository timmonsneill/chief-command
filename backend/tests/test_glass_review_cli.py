"""Tests for backend/scripts/glass_review_cli.py — the CLI wrapper around
``services.agent_tools.execute_code_review`` used by the /review sweep.

Coverage:
  * inline-target happy path: stdout is the review text, exit 0
  * executor error: stderr carries the error, exit 2
  * scope resolution: 'chief-command' → Chief Command repo path
  * scope resolution: unknown scope → exit 1, no executor call
  * focus argument is forwarded to execute_code_review
  * stdout discipline: nothing besides the review text on stdout
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BACKEND_DIR / "scripts"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

os.environ.setdefault("OWNER_PASSWORD", "test")
os.environ.setdefault("JWT_SECRET", "test")


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------
def _import_cli():
    """Import the CLI module fresh so monkeypatch resets between tests.

    The module imports services.agent_tools lazily inside _run_review, so
    re-importing the CLI module itself doesn't recreate that dep — but it
    does reset module-level state if any is added later. Cheap insurance.
    """
    if "glass_review_cli" in sys.modules:
        return importlib.reload(sys.modules["glass_review_cli"])
    return importlib.import_module("glass_review_cli")


@pytest.fixture
def fake_execute_code_review(monkeypatch):
    """Stub services.agent_tools.execute_code_review.

    Returns a list the test can read — each call appends a dict of kwargs so
    assertions can verify cwd / scope / focus / target plumbing.
    """
    from services import agent_tools

    calls: list[dict] = []

    async def _fake(target, *, cwd, scope, focus="general", system_prompt_append=""):
        calls.append({
            "target": target,
            "cwd": cwd,
            "scope": scope,
            "focus": focus,
        })
        # Default: success. Tests that need failure override this fixture.
        return agent_tools.ToolResult(
            output="### Critical issues\n(none)\n\n### Suggestions\n- looks fine",
            error=False,
            truncated=False,
        )

    monkeypatch.setattr(agent_tools, "execute_code_review", _fake)
    return calls


@pytest.fixture
def fake_repo_map(monkeypatch, tmp_path):
    """Stub services.repo_map.get_repo_path so tests don't depend on the host
    having the real repos checked out at canonical paths.

    'Chief Command' → tmp_path / "chief-command"
    'Arch'          → tmp_path / "arch-to-freedom-emr"
    Anything else   → None (mirrors the real failure mode for unknown / missing).
    """
    chief_dir = tmp_path / "chief-command"
    arch_dir = tmp_path / "arch-to-freedom-emr"
    chief_dir.mkdir()
    arch_dir.mkdir()
    mapping = {
        "Chief Command": chief_dir,
        "Arch": arch_dir,
    }

    def _fake_get_repo_path(project: str):
        return mapping.get(project)

    from services import repo_map
    monkeypatch.setattr(repo_map, "get_repo_path", _fake_get_repo_path)
    return mapping


# ---------------------------------------------------------------------------
# Happy path — inline target
# ---------------------------------------------------------------------------
def test_inline_target_happy_path(
    fake_execute_code_review, fake_repo_map, capsys,
):
    """Inline code → executor called, stdout has review text, exit 0."""
    cli = _import_cli()

    rc = cli.main([
        "--target", "def foo(): return 1/0",
        "--focus", "general",
        "--scope", "chief-command",
    ])
    assert rc == 0

    captured = capsys.readouterr()
    # Review text on stdout — verbatim from the executor's ToolResult.output.
    assert "### Critical issues" in captured.out
    assert "looks fine" in captured.out
    # Diagnostics on stderr.
    assert "scope='Chief Command'" in captured.err
    assert "completed in" in captured.err

    # Executor received the target verbatim + correct cwd + canonical scope.
    assert len(fake_execute_code_review) == 1
    call = fake_execute_code_review[0]
    assert call["target"] == "def foo(): return 1/0"
    assert call["focus"] == "general"
    assert call["scope"] == "Chief Command"
    assert call["cwd"] == fake_repo_map["Chief Command"]


# ---------------------------------------------------------------------------
# Failure mode — executor returned ToolResult(error=True)
# ---------------------------------------------------------------------------
def test_executor_error_exits_nonzero(
    fake_repo_map, monkeypatch, capsys,
):
    """When execute_code_review returns error=True, exit 2 + stderr message."""
    from services import agent_tools

    async def _failing(*args, **kwargs):
        return agent_tools.ToolResult(
            output="error: code_review timed out after 45s",
            error=True,
        )

    monkeypatch.setattr(agent_tools, "execute_code_review", _failing)

    cli = _import_cli()

    rc = cli.main([
        "--target", "anything",
        "--scope", "chief-command",
    ])
    assert rc == 2

    captured = capsys.readouterr()
    # Stdout MUST stay clean on error so the tunnel doesn't relay an error
    # as if it were a successful review.
    assert captured.out == ""
    assert "code_review timed out" in captured.err


# ---------------------------------------------------------------------------
# Scope resolution — happy path
# ---------------------------------------------------------------------------
def test_scope_chief_command_resolves(
    fake_execute_code_review, fake_repo_map, capsys,
):
    """'chief-command' alias → canonical 'Chief Command' + correct cwd."""
    cli = _import_cli()

    rc = cli.main([
        "--target", "snippet",
        "--scope", "chief-command",
    ])
    assert rc == 0
    assert fake_execute_code_review[0]["cwd"] == fake_repo_map["Chief Command"]
    assert fake_execute_code_review[0]["scope"] == "Chief Command"


def test_scope_arch_resolves(
    fake_execute_code_review, fake_repo_map, capsys,
):
    """'arch' alias → canonical 'Arch' + correct cwd."""
    cli = _import_cli()

    rc = cli.main([
        "--target", "snippet",
        "--scope", "arch",
    ])
    assert rc == 0
    assert fake_execute_code_review[0]["cwd"] == fake_repo_map["Arch"]
    assert fake_execute_code_review[0]["scope"] == "Arch"


def test_canonical_scope_name_also_works(
    fake_execute_code_review, fake_repo_map, capsys,
):
    """Passing the canonical 'Chief Command' string directly is supported."""
    cli = _import_cli()

    rc = cli.main([
        "--target", "snippet",
        "--scope", "Chief Command",
    ])
    assert rc == 0
    assert fake_execute_code_review[0]["scope"] == "Chief Command"


# ---------------------------------------------------------------------------
# Scope resolution — failure
# ---------------------------------------------------------------------------
def test_unknown_scope_exits_one_without_executor_call(
    fake_execute_code_review, fake_repo_map, capsys,
):
    """Unknown scope → exit 1, executor NEVER invoked."""
    cli = _import_cli()

    rc = cli.main([
        "--target", "snippet",
        "--scope", "made-up-project",
    ])
    assert rc == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "does not resolve" in captured.err
    # Executor MUST NOT have been called when scope resolution fails.
    assert fake_execute_code_review == []


# ---------------------------------------------------------------------------
# Focus argument plumbing
# ---------------------------------------------------------------------------
def test_focus_argument_forwarded(
    fake_execute_code_review, fake_repo_map, capsys,
):
    """--focus security → executor receives focus='security'."""
    cli = _import_cli()

    rc = cli.main([
        "--target", "snippet",
        "--focus", "security",
        "--scope", "chief-command",
    ])
    assert rc == 0
    assert fake_execute_code_review[0]["focus"] == "security"


def test_focus_default_is_general(
    fake_execute_code_review, fake_repo_map, capsys,
):
    """No --focus → executor receives focus='general'."""
    cli = _import_cli()

    rc = cli.main([
        "--target", "snippet",
        "--scope", "chief-command",
    ])
    assert rc == 0
    assert fake_execute_code_review[0]["focus"] == "general"


def test_invalid_focus_rejected_by_argparse(
    fake_execute_code_review, fake_repo_map, capsys,
):
    """argparse choices guard rejects off-enum focus values with SystemExit."""
    cli = _import_cli()

    with pytest.raises(SystemExit) as exc_info:
        cli.main([
            "--target", "snippet",
            "--focus", "made-up",
            "--scope", "chief-command",
        ])
    # argparse exits 2 on invalid args.
    assert exc_info.value.code == 2
    # Executor never called.
    assert fake_execute_code_review == []


# ---------------------------------------------------------------------------
# Stdout discipline
# ---------------------------------------------------------------------------
def test_stdout_contains_only_review_text(
    fake_execute_code_review, fake_repo_map, capsys,
):
    """No diagnostic chatter on stdout — only the executor's review text.

    The Claude tunnel relays stdout verbatim as the review, so any stray
    'connecting...' / 'completed in...' line on stdout would corrupt the
    review for downstream consumers.
    """
    cli = _import_cli()

    rc = cli.main([
        "--target", "snippet",
        "--scope", "chief-command",
    ])
    assert rc == 0
    captured = capsys.readouterr()
    # Stdout must equal exactly the executor's output + a trailing newline
    # from print(). The fixture seeds 'looks fine' as the success text.
    assert captured.out.strip() == (
        "### Critical issues\n(none)\n\n### Suggestions\n- looks fine"
    )
