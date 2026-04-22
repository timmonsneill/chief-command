"""Track B #6 — tts_queue drain helper used by cancel_current_turn.

We can't import ``app.websockets`` directly in this test because
conftest.py replaces ``services`` with an empty namespace for unit-test
isolation, and websockets.py pulls in the full ``services`` package.

Instead we import the helper via importlib with a minimal services shim
so the websockets module's other imports resolve.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("OWNER_PASSWORD", "test")
os.environ.setdefault("JWT_SECRET", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")


def _load_drain_queue():
    """Load ``_drain_queue`` from app/websockets.py without running all its
    top-level imports. The helper is a pure function — no decorators, no
    service dependencies — so we can compile and execute just its body.
    """
    path = BACKEND_DIR / "app" / "websockets.py"
    src = path.read_text()
    # Extract the def + body. Slice from the `def _drain_queue` line to the
    # next blank-after-return-or-dedent. Simplest robust-enough extraction:
    # find the def line and take lines while they belong to the function.
    lines = src.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("def _drain_queue"))
    # Collect until we hit the next top-level def/class/blank-line-followed-by-def
    body_lines = [lines[start]]
    for j in range(start + 1, len(lines)):
        line = lines[j]
        if line and not line.startswith(" ") and not line.startswith("\t"):
            break
        body_lines.append(line)
    code = "\n".join(body_lines)
    ns = {"asyncio": asyncio}
    exec(code, ns)
    return ns["_drain_queue"]


_drain_queue = _load_drain_queue()


@pytest.mark.asyncio
async def test_drain_empty_queue_returns_zero() -> None:
    q: asyncio.Queue = asyncio.Queue()
    assert _drain_queue(q) == 0
    assert q.empty()


@pytest.mark.asyncio
async def test_drain_populated_queue_returns_count() -> None:
    q: asyncio.Queue = asyncio.Queue()
    for i in range(5):
        await q.put(f"item-{i}")
    assert _drain_queue(q) == 5
    assert q.empty()


@pytest.mark.asyncio
async def test_drain_mixed_with_none_sentinel() -> None:
    """tts_queue uses None as a stop sentinel; drain must still count it."""
    q: asyncio.Queue = asyncio.Queue()
    await q.put("a")
    await q.put("b")
    await q.put(None)  # stop sentinel
    assert _drain_queue(q) == 3
    assert q.empty()
