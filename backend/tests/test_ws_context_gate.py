"""Unit tests for the asyncio.Event-based context gate used in app.websockets.

We don't boot a full WebSocket in these tests — the gate is just an Event
+ a timeout guard, and mirroring the real pattern keeps the tests fast and
deterministic. If the gate semantics change in websockets.py, these tests
must change in lockstep.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# Mirror the gate shape used in voice_ws() — one Event + deadline-aware waiter.
async def _await_gate(
    gate: asyncio.Event,
    accepted_at: float,
    timeout_s: float,
    logger: logging.Logger,
) -> bool:
    """Return True if we returned because the gate fired, False if we fell
    through on the deadline path. Models the real ``_await_context_gate``."""
    if gate.is_set():
        return True
    loop = asyncio.get_event_loop()
    remaining = (accepted_at + timeout_s) - loop.time()
    if remaining <= 0:
        logger.warning("gate past deadline, falling back")
        gate.set()
        return False
    try:
        await asyncio.wait_for(gate.wait(), timeout=remaining)
        return True
    except asyncio.TimeoutError:
        logger.warning("gate timed out, falling back")
        gate.set()
        return False


@pytest.mark.asyncio
async def test_gate_fires_when_context_arrives_early() -> None:
    gate = asyncio.Event()
    accepted_at = asyncio.get_event_loop().time()
    logger = logging.getLogger("test.gate")

    async def context_frame_later():
        await asyncio.sleep(0.05)
        gate.set()

    asyncio.create_task(context_frame_later())
    fired = await _await_gate(gate, accepted_at, timeout_s=1.0, logger=logger)
    assert fired is True


@pytest.mark.asyncio
async def test_gate_falls_through_on_timeout(caplog) -> None:
    gate = asyncio.Event()
    accepted_at = asyncio.get_event_loop().time()
    logger = logging.getLogger("test.gate")
    caplog.set_level(logging.WARNING, logger="test.gate")

    fired = await _await_gate(gate, accepted_at, timeout_s=0.05, logger=logger)
    assert fired is False
    assert gate.is_set(), "gate must be latched set after timeout to avoid re-warning"
    assert any("timed out" in r.getMessage() or "past deadline" in r.getMessage()
               for r in caplog.records), (
        "expected a 'timed out' or 'past deadline' warning"
    )


@pytest.mark.asyncio
async def test_gate_is_idempotent_after_set() -> None:
    """Calling the waiter again after the gate fired returns immediately."""
    gate = asyncio.Event()
    gate.set()
    accepted_at = asyncio.get_event_loop().time()
    logger = logging.getLogger("test.gate")

    fired = await _await_gate(gate, accepted_at, timeout_s=10.0, logger=logger)
    assert fired is True


@pytest.mark.asyncio
async def test_multiple_waiters_wake_on_single_set() -> None:
    """Two turns racing the same context frame must both unblock together."""
    gate = asyncio.Event()
    accepted_at = asyncio.get_event_loop().time()
    logger = logging.getLogger("test.gate")

    results = []

    async def waiter():
        fired = await _await_gate(gate, accepted_at, timeout_s=1.0, logger=logger)
        results.append(fired)

    t1 = asyncio.create_task(waiter())
    t2 = asyncio.create_task(waiter())
    await asyncio.sleep(0.02)
    gate.set()
    await asyncio.gather(t1, t2)
    assert results == [True, True]
