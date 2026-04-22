"""Unit tests for per-subject scope keying in services.project_context.

Track A #1 — The in-memory ``_context_store`` must be keyed per JWT subject
so a second tab / second device / restart doesn't stomp or read a stale
scope. These tests verify the get/set helpers honor per-subject isolation
and the module-level store respects subject isolation.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.project_context import (  # noqa: E402
    AVAILABLE_PROJECTS,
    DEFAULT_PROJECT,
    _context_store,
    get_context,
    set_context,
)


def _reset_store() -> None:
    _context_store.clear()


def test_default_scope_when_subject_missing() -> None:
    _reset_store()
    ctx = get_context("owner")
    assert ctx["current"] == DEFAULT_PROJECT
    assert ctx["available"] == AVAILABLE_PROJECTS


def test_set_and_get_per_subject() -> None:
    _reset_store()
    set_context("owner-tab-A", "Arch")
    assert get_context("owner-tab-A")["current"] == "Arch"
    # A different subject must not see tab-A's scope.
    assert get_context("owner-tab-B")["current"] == DEFAULT_PROJECT


def test_subject_isolation_does_not_bleed() -> None:
    """Two sessions changing scope independently must not cross-contaminate."""
    _reset_store()
    set_context("subject-1", "Arch")
    set_context("subject-2", "Personal Assist")
    assert get_context("subject-1")["current"] == "Arch"
    assert get_context("subject-2")["current"] == "Personal Assist"


def test_set_context_rejects_unknown_project() -> None:
    _reset_store()
    import pytest  # local import so collection doesn't care about asyncio mode
    with pytest.raises(ValueError):
        set_context("owner", "Not A Real Project")


def test_dict_writes_are_isolated() -> None:
    """Direct ``_context_store`` writes (what websockets.py does) must also
    isolate by key — test the store shape we rely on."""
    _reset_store()
    _context_store["alpha"] = "Arch"
    _context_store["beta"] = "Chief Command"
    assert _context_store["alpha"] == "Arch"
    assert _context_store["beta"] == "Chief Command"
    # Mutating one key doesn't touch the other.
    _context_store["alpha"] = "Personal Assist"
    assert _context_store["beta"] == "Chief Command"
