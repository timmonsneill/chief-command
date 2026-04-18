"""Unit tests for services.repo_map."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from services import repo_map


def test_get_repo_path_valid_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A configured project whose directory exists inside the allowlist returns
    that absolute path."""
    # Put fake repo under the allowlist root so containment check passes.
    root = repo_map._ALLOWED_ROOT
    fake_repo = root / f"__nova_test_{os.getpid()}_valid"
    fake_repo.mkdir(exist_ok=True)
    monkeypatch.setitem(repo_map._REPO_PATHS, "TestProj", fake_repo)
    try:
        result = repo_map.get_repo_path("TestProj")
        assert result == fake_repo.resolve()
        assert result.is_absolute()
    finally:
        try:
            fake_repo.rmdir()
        except OSError:
            pass


def test_get_repo_path_unknown_project() -> None:
    """A project name not in the map returns None."""
    assert repo_map.get_repo_path("NotAThing") is None
    assert repo_map.get_repo_path("") is None


def test_get_repo_path_configured_but_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project whose configured path doesn't exist returns None."""
    missing = tmp_path / "does-not-exist"
    monkeypatch.setitem(repo_map._REPO_PATHS, "Ghost", missing)

    assert repo_map.get_repo_path("Ghost") is None


def test_list_configured_projects_filters_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """list_configured_projects returns only projects whose repo exists."""
    here = tmp_path / "here"
    here.mkdir()
    gone = tmp_path / "gone"
    monkeypatch.setattr(
        repo_map, "_REPO_PATHS", {"Here": here, "Gone": gone}, raising=False
    )

    names = repo_map.list_configured_projects()
    assert "Here" in names
    assert "Gone" not in names


def test_get_repo_path_rejects_symlink_outside_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Symlink that resolves outside ~/Desktop is rejected.

    The attack scenario: a repo entry points at a benign-looking name under
    ~/Desktop but that name is a symlink to, e.g., /etc. An attacker who can
    influence the classifier into dispatching there would have `claude` CLI
    spawning in the wrong cwd with elevated access to system files.
    """
    root = repo_map._ALLOWED_ROOT
    evil_link = root / f"__nova_test_{os.getpid()}_evil"
    # Remove any leftover from a prior failed run.
    try:
        evil_link.unlink()
    except (OSError, FileNotFoundError):
        pass
    # Point at /etc — a directory we know exists outside ~/Desktop.
    evil_link.symlink_to("/etc")
    monkeypatch.setitem(repo_map._REPO_PATHS, "EvilRepo", evil_link)
    try:
        result = repo_map.get_repo_path("EvilRepo")
        assert result is None, (
            f"symlink to /etc should have been rejected, got {result}"
        )
    finally:
        try:
            evil_link.unlink()
        except (OSError, FileNotFoundError):
            pass


def test_get_repo_path_accepts_symlink_inside_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Symlink that resolves INSIDE ~/Desktop is allowed — this keeps the
    allowlist check from breaking legitimate symlinked project dirs."""
    root = repo_map._ALLOWED_ROOT
    real_dir = root / f"__nova_test_{os.getpid()}_real"
    link = root / f"__nova_test_{os.getpid()}_link"
    real_dir.mkdir(exist_ok=True)
    try:
        try:
            link.unlink()
        except (OSError, FileNotFoundError):
            pass
        link.symlink_to(real_dir)
        monkeypatch.setitem(repo_map._REPO_PATHS, "LinkedRepo", link)
        result = repo_map.get_repo_path("LinkedRepo")
        assert result == real_dir.resolve()
    finally:
        try:
            link.unlink()
        except (OSError, FileNotFoundError):
            pass
        try:
            real_dir.rmdir()
        except OSError:
            pass
