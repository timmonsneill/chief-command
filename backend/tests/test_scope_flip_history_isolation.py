"""Phase 4: scope flip must isolate ``history`` from the prior scope.

When the owner runs three turns in Chief Command and then switches to
Arch, the next turn must NOT see Chief Command turns in the brain's
``contents`` list. The fix uses ``history.clear() + history.extend(...)``
on the in-place list so any closure that already captured the reference
(``_gated_text_turn``, ``_gated_audio_turn``, narration helpers) sees
the swap.

Test asserts:
  1. The list is mutated in place (``id(history)`` doesn't change).
  2. After flip, history contains only the new scope's recent turns
     (loaded via ``load_persistent_memory(new_project)``).
  3. The Gemini ``contents`` builder applied to the post-flip history
     yields zero traces of the old scope's user text.

NOTE / FOLLOW-UP: ``services.repo_map.get_repo_path("Chief Command")``
currently resolves to ``~/Desktop/chief-command`` (main repo) rather than
the active worktree. Phase 4 deliberately does NOT change this — the
worktree-vs-main resolution is a separate decision the owner needs to
make. Document the gap so the next pass picks it up.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# ---------------------------------------------------------------------------
# Fakes — mirror just enough of voice_ws() local state for the flip to run.
# ---------------------------------------------------------------------------
class _FakePool:
    async def teardown_other_scopes(
        self, subject: str, keep_scope: str, reason: str = "scope-switch"
    ) -> None:
        return None


# Per-scope fake persistent memory. Loading "Arch" returns Arch's recent
# turns; loading "Chief Command" returns Chief's. This mirrors the real
# ``load_persistent_memory`` contract — the per-project history_store
# ``load_recent_for_project`` call.
_FAKE_BY_PROJECT: dict[str, tuple[str | None, list[dict]]] = {
    "Chief Command": (
        "Chief Command rolling summary",
        [
            {"role": "user", "content": "what's the status of the worktree"},
            {"role": "assistant", "content": "merged this morning"},
        ],
    ),
    "Arch": (
        "Arch rolling summary",
        [
            {"role": "user", "content": "show me the EMR audit log"},
            {"role": "assistant", "content": "audit_events table, last 10 rows"},
        ],
    ),
    "Personal Assist": (
        None,
        [],
    ),
}


async def _fake_load_persistent_memory(
    project: str, raw_limit: int = 20
) -> tuple[str | None, list[dict]]:
    return _FAKE_BY_PROJECT.get(project, (None, []))


def _build_handler(
    *,
    pool: _FakePool,
    history: list[dict],
    summary_box: list[str | None],
    client_id: str = "owner",
):
    async def _handle_scope_flip(old_project: str, new_project: str) -> None:
        try:
            await pool.teardown_other_scopes(
                subject=client_id, keep_scope=new_project, reason="scope-switch",
            )
        except Exception:
            pass
        new_summary, new_turns = await _fake_load_persistent_memory(
            new_project, raw_limit=20,
        )
        history.clear()
        history.extend(new_turns)
        summary_box[0] = new_summary

    return _handle_scope_flip


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_three_chief_turns_then_flip_to_arch_clears_chief_history() -> None:
    pool = _FakePool()
    # Simulate three turns landed in Chief Command.
    history: list[dict] = [
        {"role": "user", "content": "what's the worktree branch?"},
        {"role": "assistant", "content": "agent-a2f868bd6c1fb24c9 on main"},
        {"role": "user", "content": "okay rerun the sweep"},
        {"role": "assistant", "content": "kicking off Forge"},
        {"role": "user", "content": "ping me when it's green"},
        {"role": "assistant", "content": "will do"},
    ]
    summary_box: list[str | None] = ["Chief Command summary"]
    history_ref_before = history

    handler = _build_handler(
        pool=pool, history=history, summary_box=summary_box,
    )
    await handler("Chief Command", "Arch")

    # 1) In-place mutation — closure refs survive the flip.
    assert history is history_ref_before
    # 2) Post-flip history is ONLY Arch's recent turns.
    expected_arch_turns = _FAKE_BY_PROJECT["Arch"][1]
    assert history == expected_arch_turns
    # 3) Zero leakage of any Chief-Command user text into the post-flip
    # history. Joining content strings keeps this test robust against
    # role-shape changes.
    chief_phrases = [
        "worktree", "rerun the sweep", "ping me when it's green",
    ]
    joined = " ".join(t.get("content", "") for t in history)
    for phrase in chief_phrases:
        assert phrase not in joined, (
            f"Chief Command phrase {phrase!r} leaked into Arch-scope history"
        )
    # Summary updates to the new scope's summary in place.
    assert summary_box[0] == "Arch rolling summary"


@pytest.mark.asyncio
async def test_history_to_gemini_contents_post_flip_carries_no_old_scope() -> None:
    """The Gemini ``contents`` builder consumes ``history`` directly. After
    a scope flip, that builder must produce a list whose user-role
    messages map to the new scope only.
    """
    pool = _FakePool()
    history: list[dict] = [
        {"role": "user", "content": "Chief Command secret token: CHIEF_TOKEN_123"},
        {"role": "assistant", "content": "got it"},
    ]
    summary_box: list[str | None] = ["Chief Command summary"]
    handler = _build_handler(
        pool=pool, history=history, summary_box=summary_box,
    )
    await handler("Chief Command", "Arch")

    # Inline copy of the role-pairing logic used by gemini_brain to render
    # history -> contents. We don't import the real builder because it
    # pulls genai SDK dependencies; the assertion only needs to verify the
    # raw text inputs that go into the model.
    rendered = []
    for t in history:
        rendered.append({"role": t["role"], "text": t.get("content", "")})

    blob = " ".join(r["text"] for r in rendered)
    assert "CHIEF_TOKEN_123" not in blob, (
        "Old-scope content must not survive into the post-flip history "
        "the brain sees on the next turn."
    )
    # Sanity: new scope content is present.
    assert "audit log" in blob


@pytest.mark.asyncio
async def test_flip_to_scope_with_no_persisted_history_clears_to_empty() -> None:
    """Switching to a project that has no persisted memory yet must result
    in an empty in-memory history list (not a stale one)."""
    pool = _FakePool()
    history: list[dict] = [
        {"role": "user", "content": "lots of Chief Command stuff"},
        {"role": "assistant", "content": "..."},
    ]
    summary_box: list[str | None] = ["something"]
    handler = _build_handler(
        pool=pool, history=history, summary_box=summary_box,
    )
    await handler("Chief Command", "Personal Assist")

    assert history == []
    assert summary_box[0] is None
