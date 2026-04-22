"""Unit test for the Track A #3 scope-fallback behavior in chief_context.

The silent fallback used to be a WARNING — if the upstream scope plumbing
regressed, it was invisible in logs. It's now an ERROR with stack_info so
the regression surfaces immediately. The function still returns a usable
block list (never raises) so Chief stays functional in prod.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.chief_context import build_chief_system, DEFAULT_SCOPE  # noqa: E402


@pytest.mark.parametrize("bad_scope", ["", "   ", None])
def test_empty_scope_logs_error_and_falls_back(caplog, bad_scope) -> None:
    """Empty/whitespace/None scope must log ERROR (not WARNING) and return blocks."""
    caplog.set_level(logging.ERROR, logger="services.chief_context")
    blocks = build_chief_system(bad_scope or "")
    # Still returns something usable — never raises.
    assert isinstance(blocks, list)
    assert len(blocks) >= 1
    # An ERROR record must be present with the regression marker.
    matching = [
        r for r in caplog.records
        if r.name == "services.chief_context"
        and r.levelno == logging.ERROR
        and "empty scope" in r.getMessage().lower()
    ]
    assert matching, (
        "Expected ERROR-level 'empty scope' log from chief_context, got: "
        + "\n".join(f"{r.levelname} {r.name} {r.getMessage()}" for r in caplog.records)
    )


def test_valid_scope_does_not_error(caplog) -> None:
    caplog.set_level(logging.ERROR, logger="services.chief_context")
    blocks = build_chief_system(DEFAULT_SCOPE)
    assert isinstance(blocks, list) and blocks
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert not errors, (
        "Unexpected ERROR logs on happy-path build_chief_system: "
        + "\n".join(r.getMessage() for r in errors)
    )
