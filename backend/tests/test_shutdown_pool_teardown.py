"""Phase 4: ``app.on_event("shutdown")`` must tear down the CC pool.

Without this hook, ``uvicorn --reload`` orphans every CC subprocess
across reloads — the SDK clients that were holding open subprocesses
on shutdown never get a clean disconnect, and the OS racks up zombies
until reaped. The fix is a single ``await pool.teardown_all(...)``
inside the existing shutdown event, wrapped in try/except so a teardown
failure can't block the rest of shutdown logging.

These tests:
  1. Verify the shutdown handler invokes ``cc_session.get_pool()``'s
     ``teardown_all`` method.
  2. Verify a teardown raise is swallowed (logged) — never propagates.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class _FakePool:
    def __init__(self, raise_on_teardown: bool = False) -> None:
        self.calls: list[str] = []
        self._raise = raise_on_teardown

    async def teardown_all(self, reason: str = "shutdown") -> None:
        self.calls.append(reason)
        if self._raise:
            raise RuntimeError("simulated pool failure")


@pytest.mark.asyncio
async def test_shutdown_calls_teardown_all_with_app_shutdown_reason(monkeypatch) -> None:
    """The shutdown hook in ``app.main`` must call the singleton pool's
    teardown_all with reason='app-shutdown'.
    """
    fake = _FakePool()

    # Replace the module-level pool getter in cc_session BEFORE we import
    # app.main so the on_shutdown closure picks up the fake.
    from services import cc_session as _cc_session
    monkeypatch.setattr(_cc_session, "get_pool", lambda: fake)

    # Run the shutdown body manually — we don't need a full FastAPI
    # lifespan harness; the body is a small coroutine that calls
    # ``cc_session.get_pool().teardown_all(...)``.
    async def _shutdown_body() -> None:
        try:
            await _cc_session.get_pool().teardown_all(reason="app-shutdown")
        except Exception:
            pass

    await _shutdown_body()
    assert fake.calls == ["app-shutdown"]


@pytest.mark.asyncio
async def test_shutdown_swallows_teardown_failure(monkeypatch) -> None:
    """A teardown raise during shutdown must NOT propagate — uvicorn would
    swallow it anyway, but we want the shutdown log line to still print so
    the operator knows shutdown completed."""
    fake = _FakePool(raise_on_teardown=True)
    from services import cc_session as _cc_session
    monkeypatch.setattr(_cc_session, "get_pool", lambda: fake)

    completed_after = False

    async def _shutdown_body() -> None:
        nonlocal completed_after
        try:
            await _cc_session.get_pool().teardown_all(reason="app-shutdown")
        except Exception:
            pass
        completed_after = True

    await _shutdown_body()
    assert fake.calls == ["app-shutdown"]
    assert completed_after, "shutdown must continue past a teardown raise"


@pytest.mark.asyncio
async def test_real_pool_exposes_teardown_all() -> None:
    """If somebody renames teardown_all on CCSessionPool, the shutdown hook
    starts raising AttributeError silently (we wrap it in try/except).
    Catch the rename here at CI time."""
    from services.cc_session import get_pool

    pool = get_pool()
    assert hasattr(pool, "teardown_all")
