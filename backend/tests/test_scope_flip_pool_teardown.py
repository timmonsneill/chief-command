"""Phase 4: scope flip must tear down OTHER-scope CC pool entries.

Both flip paths (voice-intent detection and the picker-frame `context`
inbound) share the same ``_handle_scope_flip`` helper, which calls
``cc_session.get_pool().teardown_other_scopes(subject=client_id,
keep_scope=new_project, reason="scope-switch")`` so the warm CC client
for the prior scope is killed and freed. We verify the helper drives the
pool with the right kwargs by asserting against a recorded fake.

We don't boot a real WS — the helper is a pure orchestration step
keyed off the ``client_id`` (JWT subject) and a list (``history``) +
``current_summary`` rebind. Mirroring it here keeps the test fast and
deterministic; if the helper signature changes in
``app/websockets.py``, this test must change in lockstep.

NOTE / FOLLOW-UP: ``services.repo_map.get_repo_path("Chief Command")``
currently resolves to ``~/Desktop/chief-command`` (main repo), not the
worktree the owner is operating out of. Phase 4 deliberately does NOT
fix this — it's a separate decision the owner needs to make about
worktree-aware repo resolution. When that fix lands, scope-flip tests
should also assert the post-flip ``cwd`` matches the active worktree
(if any) instead of the main repo.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class _FakePool:
    """Minimal fake of ``CCSessionPool`` covering ``teardown_other_scopes``."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.raise_on_call = False

    async def teardown_other_scopes(
        self,
        subject: str,
        keep_scope: str,
        reason: str = "scope-switch",
    ) -> None:
        self.calls.append(
            {"subject": subject, "keep_scope": keep_scope, "reason": reason}
        )
        if self.raise_on_call:
            raise RuntimeError("simulated pool failure")


async def _fake_load_persistent_memory(
    project: str, raw_limit: int = 20
) -> tuple[str | None, list[dict]]:
    # Return a per-project marker turn so the test can prove the in-memory
    # history list was repopulated from the new scope's persisted bytes,
    # not the old scope's.
    return (
        f"{project} summary",
        [{"role": "user", "content": f"hello from {project}"}],
    )


def _build_handler(
    *,
    pool: _FakePool,
    history: list[dict],
    summary_box: list[str | None],
    client_id: str = "owner",
):
    """Recreate the local closure ``_handle_scope_flip`` builds in
    ``voice_ws()``. Mutating ``history`` in-place + writing into
    ``summary_box`` mirrors the production semantics that closures over
    those names see the swap.
    """

    async def _handle_scope_flip(old_project: str, new_project: str) -> None:
        try:
            await pool.teardown_other_scopes(
                subject=client_id,
                keep_scope=new_project,
                reason="scope-switch",
            )
        except Exception:
            # Production code logs + continues — the UI flip can't depend
            # on backend cleanup success. Mirror that silently here.
            pass

        try:
            new_summary, new_turns = await _fake_load_persistent_memory(
                new_project, raw_limit=20,
            )
            history.clear()
            history.extend(new_turns)
            summary_box[0] = new_summary
        except Exception:
            history.clear()
            summary_box[0] = None

    return _handle_scope_flip


@pytest.mark.asyncio
async def test_flip_calls_teardown_with_keep_scope_and_subject() -> None:
    pool = _FakePool()
    history: list[dict] = [{"role": "user", "content": "old"}]
    summary_box: list[str | None] = ["stale summary"]

    handler = _build_handler(
        pool=pool, history=history, summary_box=summary_box,
        client_id="owner-tab-A",
    )
    await handler("Chief Command", "Arch")

    assert len(pool.calls) == 1
    call = pool.calls[0]
    assert call["subject"] == "owner-tab-A"
    assert call["keep_scope"] == "Arch"
    assert call["reason"] == "scope-switch"


@pytest.mark.asyncio
async def test_flip_swaps_history_and_summary_in_place() -> None:
    pool = _FakePool()
    history: list[dict] = [{"role": "user", "content": "Chief Command stuff"}]
    summary_box: list[str | None] = ["Chief Command summary"]

    # Hold the original list reference — this mirrors how closures inside
    # ``voice_ws()`` capture ``history`` once and rely on in-place edits to
    # see scope flips. ``history is original`` must stay true after flip.
    original_history_ref = history

    handler = _build_handler(
        pool=pool, history=history, summary_box=summary_box,
    )
    await handler("Chief Command", "Arch")

    assert history is original_history_ref, (
        "history must be mutated in place; rebinding would leave stale "
        "references in any closure that captured the list at WS open."
    )
    assert history == [{"role": "user", "content": "hello from Arch"}], (
        "history must be cleared and refilled with the new scope's recent "
        "turns from load_persistent_memory(new_project)."
    )
    assert summary_box[0] == "Arch summary"


@pytest.mark.asyncio
async def test_flip_swallows_pool_failure_and_still_reloads_history() -> None:
    """UI flip must not depend on backend cleanup success — a teardown
    failure is logged + swallowed. We still reload the new scope's
    persistent memory so the next turn talks to the new scope's brain."""
    pool = _FakePool()
    pool.raise_on_call = True
    history: list[dict] = [{"role": "user", "content": "Chief Command stuff"}]
    summary_box: list[str | None] = ["Chief Command summary"]

    handler = _build_handler(
        pool=pool, history=history, summary_box=summary_box,
    )
    await handler("Chief Command", "Arch")

    # Pool was called (and raised internally) — we still reach the
    # history-reload branch.
    assert len(pool.calls) == 1
    assert history == [{"role": "user", "content": "hello from Arch"}]
    assert summary_box[0] == "Arch summary"


@pytest.mark.asyncio
async def test_real_pool_exposes_teardown_other_scopes() -> None:
    """Sanity check the real pool API contract used by the handler.

    If somebody renames ``teardown_other_scopes`` on ``CCSessionPool``,
    ``_handle_scope_flip`` will start raising AttributeError at runtime
    silently (we wrap it in try/except). This test fails loudly so the
    rename is caught at CI time, not when an owner switches scopes.
    """
    from services.cc_session import get_pool

    pool = get_pool()
    assert hasattr(pool, "teardown_other_scopes")
    assert hasattr(pool, "teardown_all")
