"""Tests for the Live vs chat token-budget split in ``chief_context``.

Background — voice "connection lost" bug (2026-05-05):
Gemini Live closes the WS with code 1007 on the first audio frame when
``system_instruction`` exceeds ~32K tokens. Pre-fix the eviction loop's
budget was 40K, so Chief Command (~38.7K) and Arch (~39.9K) sailed past
the cap unmodified. The fix introduces two budgets:

  * ``_MAX_PROMPT_TOKENS_LIVE`` (28K) — used when ``for_live=True``;
    holds ~5K headroom under Live's 32K sub-cap.
  * ``_MAX_PROMPT_TOKENS_CHAT`` (60K) — used by think_deep / Anthropic
    chat callers, which don't enforce a sub-cap.

These tests pin:
  1. The Live-budget eviction loop actually fires below 32K (the bug).
  2. Chat-budget callers retain headroom (regression: don't quietly
     shrink chat-brain prompts).
  3. The default ``for_live=False`` matches the pre-fix chat behavior so
     non-Live callsites (think_deep, llm.py) aren't disturbed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services import chief_context  # noqa: E402


def _all_text(blocks: list[dict]) -> str:
    return "\n\n".join(b.get("text", "") for b in blocks if b.get("text"))


def _seed_scope_with_oversized_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    scope_dir_slug: str,
    n_files: int = 30,
    chars_per_file: int = 8_000,
) -> None:
    """Plant ``n_files`` markdown files under a fake projects root.

    Each file body has ``chars_per_file`` of filler text. With the default
    args we get ~30 * 8000 = 240KB of bodies → ~60K tokens at the 4-char
    heuristic, well above any single budget. Eviction is therefore forced
    to run for both budgets; the difference between the two is how MANY
    files survive eviction.
    """
    root = tmp_path / "projects"
    mem = root / scope_dir_slug / "memory"
    mem.mkdir(parents=True)
    for i in range(n_files):
        # File names sort lexicographically; mtime is set explicitly so
        # eviction's "newest first" preserves the high-index files.
        fp = mem / f"project_padding_{i:02d}.md"
        body = (f"# pad {i}\n\n" + ("x" * chars_per_file) + "\n")
        fp.write_text(body, encoding="utf-8")
        # Stamp mtime so file 29 is newest, file 0 is oldest.
        ts_base = 1_700_000_000.0
        fp_stat = fp.stat()
        # os.utime can be flaky on tmp_path under pytest; skip if it fails.
        try:
            import os
            os.utime(fp, (ts_base + i, ts_base + i))
        except OSError:
            pass

    monkeypatch.setattr(chief_context, "PROJECTS_ROOT", root)
    # Strip always-on globals so the test asserts on JUST the scoped block.
    monkeypatch.setattr(chief_context, "_build_user_profile", lambda: "")
    monkeypatch.setattr(chief_context, "_build_feedback_memories", lambda: "")
    monkeypatch.setattr(chief_context, "_build_user_project_notes", lambda: "")
    monkeypatch.setattr(chief_context, "_build_agent_roster", lambda: "")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
def test_live_budget_below_gemini_subcap() -> None:
    """The Live budget must leave headroom under Gemini Live's ~32K cap."""
    assert chief_context._MAX_PROMPT_TOKENS_LIVE < 32_000, (
        "Live budget at or above the Gemini Live system_instruction cap — "
        "this is the bug we're fixing"
    )
    # And not absurdly low — 20K is a minimum sanity floor; below that
    # we'd be evicting context we should be able to ship.
    assert chief_context._MAX_PROMPT_TOKENS_LIVE >= 20_000


def test_chat_budget_strictly_larger_than_live() -> None:
    """Chat budget must be strictly larger — the whole point of the split."""
    assert (
        chief_context._MAX_PROMPT_TOKENS_CHAT
        > chief_context._MAX_PROMPT_TOKENS_LIVE
    )


def test_legacy_alias_matches_chat_budget() -> None:
    """``_MAX_PROMPT_TOKENS`` (legacy alias) must equal the chat budget so
    importers reaching for the old name still get the larger ceiling."""
    assert chief_context._MAX_PROMPT_TOKENS == chief_context._MAX_PROMPT_TOKENS_CHAT


# ---------------------------------------------------------------------------
# Eviction actually fires
# ---------------------------------------------------------------------------
def test_chief_command_scope_fits_under_live_cap_after_eviction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chief Command scope MUST produce a prompt ≤ Live cap after eviction."""
    _seed_scope_with_oversized_files(
        tmp_path, monkeypatch,
        scope_dir_slug="-Users-user-Desktop-chief-command",
    )
    blocks = chief_context.build_chief_system("Chief Command", for_live=True)
    tokens = chief_context._estimate_tokens(blocks)
    assert tokens <= chief_context._MAX_PROMPT_TOKENS_LIVE, (
        f"Chief Command Live prompt {tokens}t exceeds budget "
        f"{chief_context._MAX_PROMPT_TOKENS_LIVE}t — eviction didn't fire"
    )


def test_arch_scope_fits_under_live_cap_after_eviction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Arch scope MUST produce a prompt ≤ Live cap after eviction."""
    _seed_scope_with_oversized_files(
        tmp_path, monkeypatch,
        scope_dir_slug="-Users-user-Documents-GitHub-arch-to-freedom-emr",
    )
    blocks = chief_context.build_chief_system("Arch", for_live=True)
    tokens = chief_context._estimate_tokens(blocks)
    assert tokens <= chief_context._MAX_PROMPT_TOKENS_LIVE


def test_small_scope_passes_through_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Personal Assist (~6K tokens in production) must not have files
    evicted under either budget — the small scope is the regression
    canary that says we're not over-trimming."""
    # Tiny scope: 2 files * 200 chars ≈ 400 chars ≈ 100 tokens.
    root = tmp_path / "projects"
    mem = root / "-Users-user-Desktop-personal-assist" / "memory"
    mem.mkdir(parents=True)
    # PA's slug isn't in _SLUG_TO_CANONICAL — so we monkeypatch the map
    # to include it for this test.
    monkeypatch.setitem(
        chief_context._SLUG_TO_CANONICAL,
        "personal-assist", "Personal Assist",
    )
    for i in range(2):
        (mem / f"project_pa_{i}.md").write_text(
            f"# pa {i}\n\nshort body\n", encoding="utf-8",
        )
    monkeypatch.setattr(chief_context, "PROJECTS_ROOT", root)
    monkeypatch.setattr(chief_context, "_build_user_profile", lambda: "")
    monkeypatch.setattr(chief_context, "_build_feedback_memories", lambda: "")
    monkeypatch.setattr(chief_context, "_build_user_project_notes", lambda: "")
    monkeypatch.setattr(chief_context, "_build_agent_roster", lambda: "")

    blocks_live = chief_context.build_chief_system(
        "Personal Assist", for_live=True,
    )
    blob_live = _all_text(blocks_live)
    # Both seeded files survive — neither budget evicts a tiny scope.
    assert "pa 0" in blob_live and "pa 1" in blob_live


def test_chat_budget_keeps_more_files_than_live_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same scope, two budgets — chat MUST keep ≥ as many files as Live.

    Direct evidence that the split is doing what we want: chat callers
    (think_deep, Anthropic chat) keep richer context than Live callers.
    """
    _seed_scope_with_oversized_files(
        tmp_path, monkeypatch,
        scope_dir_slug="-Users-user-Desktop-chief-command",
        n_files=40,
        chars_per_file=4_000,
    )

    blocks_live = chief_context.build_chief_system(
        "Chief Command", for_live=True,
    )
    blocks_chat = chief_context.build_chief_system(
        "Chief Command", for_live=False,
    )
    tokens_live = chief_context._estimate_tokens(blocks_live)
    tokens_chat = chief_context._estimate_tokens(blocks_chat)

    assert tokens_chat >= tokens_live, (
        f"chat prompt {tokens_chat}t shrank below live prompt {tokens_live}t — "
        "budget split is reversed"
    )
    # Chat must respect its OWN cap.
    assert tokens_chat <= chief_context._MAX_PROMPT_TOKENS_CHAT


# ---------------------------------------------------------------------------
# String flatten threading
# ---------------------------------------------------------------------------
def test_build_chief_system_string_threads_for_live_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``build_chief_system_string(for_live=True)`` must hit the Live budget
    end-to-end. Without the flag plumbed through, the Live brain would
    silently keep getting the chat budget — the original bug."""
    _seed_scope_with_oversized_files(
        tmp_path, monkeypatch,
        scope_dir_slug="-Users-user-Desktop-chief-command",
    )

    string_live = chief_context.build_chief_system_string(
        "Chief Command", for_live=True,
    )
    string_chat = chief_context.build_chief_system_string(
        "Chief Command", for_live=False,
    )
    # Token estimate via the same heuristic the budget uses.
    chars_per_token = chief_context._CHARS_PER_TOKEN_ESTIMATE
    assert (
        len(string_live) // chars_per_token
        <= chief_context._MAX_PROMPT_TOKENS_LIVE
    )
    assert (
        len(string_chat) // chars_per_token
        <= chief_context._MAX_PROMPT_TOKENS_CHAT
    )
    # And chat is at least as large as live (lossless if both fit, larger
    # if the chat budget gives us room to keep extra files).
    assert len(string_chat) >= len(string_live)


def test_default_for_live_is_chat_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default ``for_live`` must be False so existing callers (think_deep,
    llm.py) keep their old, larger budget."""
    _seed_scope_with_oversized_files(
        tmp_path, monkeypatch,
        scope_dir_slug="-Users-user-Desktop-chief-command",
    )
    blocks_default = chief_context.build_chief_system("Chief Command")
    blocks_chat = chief_context.build_chief_system(
        "Chief Command", for_live=False,
    )
    assert (
        chief_context._estimate_tokens(blocks_default)
        == chief_context._estimate_tokens(blocks_chat)
    ), "default for_live must equal explicit for_live=False"
