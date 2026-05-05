"""Tests for chief_context project-dir scanning across both prefix forms.

Regression guard: owner's Arch repo lives at
``~/Documents/GitHub/arch-to-freedom-emr``, so its memory dir is
``~/.claude/projects/-Users-user-Documents-GitHub-arch-to-freedom-emr/memory``.
The original ``PROJECT_DIR_PREFIX = "-Users-user-Desktop-"`` filter dropped
that whole tree — Chief in Arch scope loaded zero scoped memory. These
tests pin the new tuple-based scan + slug-extraction so the regression
can't return.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services import chief_context  # noqa: E402
from services.memory_paths import (  # noqa: E402
    PROJECT_DIR_PREFIX,
    PROJECT_DIR_PREFIXES,
)


# ---------------------------------------------------------------------------
# Constant shape
# ---------------------------------------------------------------------------
def test_project_dir_prefixes_includes_both_trees() -> None:
    """Both repo roots Claude Code mangles into project dirs must be listed."""
    assert "-Users-user-Desktop-" in PROJECT_DIR_PREFIXES
    assert "-Users-user-Documents-GitHub-" in PROJECT_DIR_PREFIXES


def test_project_dir_prefix_alias_matches_first_tuple_entry() -> None:
    """The string alias is kept for backcompat — it must equal tuple[0]."""
    assert PROJECT_DIR_PREFIX == PROJECT_DIR_PREFIXES[0]


# ---------------------------------------------------------------------------
# Slug extraction
# ---------------------------------------------------------------------------
def test_slug_strips_desktop_prefix(tmp_path: Path) -> None:
    """Desktop-prefixed dirs lose the desktop prefix in the slug."""
    parent = tmp_path / "-Users-user-Desktop-chief-command"
    mem = parent / "memory"
    mem.mkdir(parents=True)
    assert chief_context._slug_from_dir(mem) == "chief-command"


def test_slug_strips_documents_github_prefix(tmp_path: Path) -> None:
    """Documents/GitHub-prefixed dirs lose THAT prefix in the slug."""
    parent = tmp_path / "-Users-user-Documents-GitHub-arch-to-freedom-emr"
    mem = parent / "memory"
    mem.mkdir(parents=True)
    assert chief_context._slug_from_dir(mem) == "arch-to-freedom-emr"


def test_slug_falls_back_when_neither_prefix_matches(tmp_path: Path) -> None:
    """If somehow nothing matches, return the parent name verbatim — no crash."""
    parent = tmp_path / "weird-thing-not-a-prefix"
    mem = parent / "memory"
    mem.mkdir(parents=True)
    assert chief_context._slug_from_dir(mem) == "weird-thing-not-a-prefix"


# ---------------------------------------------------------------------------
# Canonical mapping wired to slug extraction
# ---------------------------------------------------------------------------
def test_documents_github_arch_dir_canonicalizes_to_arch(tmp_path: Path) -> None:
    """The whole point of the fix: an Arch repo dir under Documents/GitHub
    must resolve to canonical scope name 'Arch'."""
    parent = tmp_path / "-Users-user-Documents-GitHub-arch-to-freedom-emr"
    mem = parent / "memory"
    mem.mkdir(parents=True)
    assert chief_context._canonical_project_name(mem) == "Arch"


def test_desktop_chief_command_still_canonicalizes(tmp_path: Path) -> None:
    """Existing happy-path must still work — Desktop chief-command -> Chief Command."""
    parent = tmp_path / "-Users-user-Desktop-chief-command"
    mem = parent / "memory"
    mem.mkdir(parents=True)
    assert chief_context._canonical_project_name(mem) == "Chief Command"


def test_chief_command_worktree_still_canonicalizes(tmp_path: Path) -> None:
    """Worktree subdirs must still longest-prefix-match to Chief Command —
    this is the regression guard the original fix put in place."""
    parent = tmp_path / "-Users-user-Desktop-chief-command--claude-worktrees-foo"
    mem = parent / "memory"
    mem.mkdir(parents=True)
    assert chief_context._canonical_project_name(mem) == "Chief Command"


# ---------------------------------------------------------------------------
# End-to-end: _project_dirs picks up dirs from both trees
# ---------------------------------------------------------------------------
def test_project_dirs_scans_both_prefixes(tmp_path: Path, monkeypatch) -> None:
    """A fake projects root with one Desktop dir + one Documents/GitHub dir
    must yield both memory paths."""
    root = tmp_path / "projects"
    desktop_mem = root / "-Users-user-Desktop-chief-command" / "memory"
    docs_mem = (
        root / "-Users-user-Documents-GitHub-arch-to-freedom-emr" / "memory"
    )
    irrelevant = root / "some-other-thing" / "memory"
    desktop_mem.mkdir(parents=True)
    docs_mem.mkdir(parents=True)
    irrelevant.mkdir(parents=True)

    monkeypatch.setattr(chief_context, "PROJECTS_ROOT", root)

    found = chief_context._project_dirs()
    assert desktop_mem in found
    assert docs_mem in found
    assert irrelevant not in found


def test_arch_documents_github_files_load_into_arch_scope(
    tmp_path: Path, monkeypatch
) -> None:
    """Files under the Documents/GitHub Arch memory dir must end up in the
    Arch scope's project-memory block — the user-visible bug Pax found."""
    root = tmp_path / "projects"
    arch_mem = (
        root / "-Users-user-Documents-GitHub-arch-to-freedom-emr" / "memory"
    )
    arch_mem.mkdir(parents=True)
    marker = arch_mem / "project_arch_marker.md"
    marker.write_text(
        "# Arch marker\n\nThis content must appear in the Arch system prompt.\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(chief_context, "PROJECTS_ROOT", root)
    # Strip out the always-on global memories so the assert only sees the
    # project-scoped block. They aren't relevant to this regression and
    # they'd just add noise.
    monkeypatch.setattr(chief_context, "_build_user_profile", lambda: "")
    monkeypatch.setattr(chief_context, "_build_feedback_memories", lambda: "")
    monkeypatch.setattr(chief_context, "_build_user_project_notes", lambda: "")
    monkeypatch.setattr(chief_context, "_build_agent_roster", lambda: "")

    blocks = chief_context.build_chief_system("Arch")
    blob = "\n\n".join(b.get("text", "") for b in blocks)
    assert "Arch marker" in blob
    assert "must appear in the Arch system prompt" in blob


def test_chief_command_scope_does_not_leak_arch_files(
    tmp_path: Path, monkeypatch
) -> None:
    """Cross-scope leak guard — Chief Command system prompt must NOT pull
    files from the Arch memory dir, even now that both prefixes are scanned."""
    root = tmp_path / "projects"
    cc_mem = root / "-Users-user-Desktop-chief-command" / "memory"
    arch_mem = (
        root / "-Users-user-Documents-GitHub-arch-to-freedom-emr" / "memory"
    )
    cc_mem.mkdir(parents=True)
    arch_mem.mkdir(parents=True)
    (cc_mem / "project_cc_marker.md").write_text(
        "# CC marker\n\nbody-cc\n", encoding="utf-8"
    )
    (arch_mem / "project_arch_secret.md").write_text(
        "# Arch secret\n\nbody-arch-must-not-leak\n", encoding="utf-8"
    )

    monkeypatch.setattr(chief_context, "PROJECTS_ROOT", root)
    monkeypatch.setattr(chief_context, "_build_user_profile", lambda: "")
    monkeypatch.setattr(chief_context, "_build_feedback_memories", lambda: "")
    monkeypatch.setattr(chief_context, "_build_user_project_notes", lambda: "")
    monkeypatch.setattr(chief_context, "_build_agent_roster", lambda: "")

    blocks = chief_context.build_chief_system("Chief Command")
    blob = "\n\n".join(b.get("text", "") for b in blocks)
    assert "body-cc" in blob
    assert "body-arch-must-not-leak" not in blob
