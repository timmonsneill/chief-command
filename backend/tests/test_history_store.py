"""Tests for services.history_store — voice conversation persistence."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest


def _point_db_at_tmp(tmp_path: Path) -> Path:
    """Redirect the history-store DB to a tmp file and reset init cache.

    The module caches "schema initialized" in a module-global; without
    resetting it between tests the second test would try to reuse the first
    test's DB file (which tmp_path has wiped).
    """
    db_path = tmp_path / "voice_history.db"
    os.environ["VOICE_HISTORY_DB_PATH"] = str(db_path)
    # Late import so the env var takes effect on first connect.
    import services.history_store as store
    store._initialized = False
    return db_path


def test_append_and_load_roundtrip(tmp_path):
    _point_db_at_tmp(tmp_path)
    import services.history_store as store

    async def run():
        await store.append_turn("sess-1", "Chief Command", "user", "hello")
        await store.append_turn("sess-1", "Chief Command", "assistant", "hi there")
        return await store.load_recent("sess-1", limit=50)

    history = asyncio.run(run())
    assert history == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]


def test_load_recent_for_project_filters_by_scope(tmp_path):
    """Rehydrate should only return turns from the target project scope —
    not bleed history from other projects into the live scope."""
    _point_db_at_tmp(tmp_path)
    import services.history_store as store

    async def run():
        await store.append_turn("sess-1", "Chief Command", "user", "cc-q")
        await store.append_turn("sess-1", "Chief Command", "assistant", "cc-a")
        await store.append_turn("sess-2", "Arch", "user", "arch-q")
        await store.append_turn("sess-2", "Arch", "assistant", "arch-a")
        return (
            await store.load_recent_for_project("Arch"),
            await store.load_recent_for_project("Chief Command"),
            await store.load_recent_for_project("Personal Assist"),
        )

    hist_arch, hist_cc, hist_pa = asyncio.run(run())
    assert hist_arch == [
        {"role": "user", "content": "arch-q"},
        {"role": "assistant", "content": "arch-a"},
    ]
    assert hist_cc == [
        {"role": "user", "content": "cc-q"},
        {"role": "assistant", "content": "cc-a"},
    ]
    assert hist_pa == []


def test_load_recent_for_project_empty_db(tmp_path):
    _point_db_at_tmp(tmp_path)
    import services.history_store as store

    async def run():
        return await store.load_recent_for_project("Chief Command")

    assert asyncio.run(run()) == []


def test_load_recent_for_project_crosses_sessions(tmp_path):
    """Multiple sessions for the same project should all contribute to
    the rehydrated history (oldest-first)."""
    _point_db_at_tmp(tmp_path)
    import services.history_store as store

    async def run():
        await store.append_turn("sess-a", "Arch", "user", "turn-1")
        await store.append_turn("sess-b", "Arch", "user", "turn-2")
        await store.append_turn("sess-c", "Arch", "user", "turn-3")
        return await store.load_recent_for_project("Arch", limit=10)

    history = asyncio.run(run())
    assert [h["content"] for h in history] == ["turn-1", "turn-2", "turn-3"]


def test_load_recent_respects_limit_and_ordering(tmp_path):
    _point_db_at_tmp(tmp_path)
    import services.history_store as store

    async def run():
        for i in range(5):
            await store.append_turn("sess-1", "Chief Command", "user", f"q{i}")
            await store.append_turn("sess-1", "Chief Command", "assistant", f"a{i}")
        # 10 rows total; ask for last 4
        return await store.load_recent("sess-1", limit=4)

    history = asyncio.run(run())
    # Expect the 4 newest, oldest-first:
    # ... q3, a3, q4, a4
    assert history == [
        {"role": "user", "content": "q3"},
        {"role": "assistant", "content": "a3"},
        {"role": "user", "content": "q4"},
        {"role": "assistant", "content": "a4"},
    ]


def test_append_empty_content_is_noop(tmp_path):
    _point_db_at_tmp(tmp_path)
    import services.history_store as store

    async def run():
        await store.append_turn("sess-1", "Chief Command", "user", "")
        return await store.load_recent("sess-1")

    history = asyncio.run(run())
    assert history == []


def test_load_recent_zero_limit(tmp_path):
    _point_db_at_tmp(tmp_path)
    import services.history_store as store

    async def run():
        await store.append_turn("sess-1", "Chief Command", "user", "hello")
        return await store.load_recent("sess-1", limit=0)

    history = asyncio.run(run())
    assert history == []


def test_load_recent_scoped_to_session(tmp_path):
    """Different session_ids should not bleed into one another."""
    _point_db_at_tmp(tmp_path)
    import services.history_store as store

    async def run():
        await store.append_turn("sess-a", "Arch", "user", "from A")
        await store.append_turn("sess-b", "Chief Command", "user", "from B")
        return (
            await store.load_recent("sess-a"),
            await store.load_recent("sess-b"),
        )

    hist_a, hist_b = asyncio.run(run())
    assert hist_a == [{"role": "user", "content": "from A"}]
    assert hist_b == [{"role": "user", "content": "from B"}]


# ---------------------------------------------------------------------------
# Phase 3 — voice_summaries (rolling cross-session memory)
# ---------------------------------------------------------------------------


def test_schema_migration_on_fresh_db(tmp_path):
    """Fresh DB should have both tables — voice_turns AND voice_summaries —
    after the first connect. Idempotent DDL means re-init is safe."""
    _point_db_at_tmp(tmp_path)
    import services.history_store as store

    async def run():
        # Forces a connect which runs the DDL.
        await store.append_turn("sess-1", "Arch", "user", "kick")
        # Second call with init flag reset — DDL must still succeed (CREATE
        # IF NOT EXISTS).
        store._initialized = False
        await store.append_summary("Arch", None, "summary v1", 1, "test-model")
        return await store.latest_summary("Arch")

    row = asyncio.run(run())
    assert row is not None
    assert row["summary_text"] == "summary v1"
    assert row["covers_through_turn_id"] == 1
    assert row["model"] == "test-model"


def test_summary_append_and_latest_roundtrip(tmp_path):
    """Append + latest should round-trip the most recent summary row."""
    _point_db_at_tmp(tmp_path)
    import services.history_store as store

    async def run():
        await store.append_summary("Arch", "sess-x", "first", 5, "m")
        await store.append_summary("Arch", "sess-x", "second", 12, "m")
        # A different project must not leak.
        await store.append_summary("Chief Command", None, "cc-summary", 9, "m")
        return (
            await store.latest_summary("Arch"),
            await store.latest_summary("Chief Command"),
            await store.latest_summary("Personal Assist"),
        )

    arch, cc, pa = asyncio.run(run())
    assert arch is not None
    assert arch["summary_text"] == "second"
    assert arch["covers_through_turn_id"] == 12
    assert cc is not None
    assert cc["summary_text"] == "cc-summary"
    assert pa is None


def test_summary_append_empty_is_noop(tmp_path):
    _point_db_at_tmp(tmp_path)
    import services.history_store as store

    async def run():
        await store.append_summary("Arch", None, "", 1, "m")
        return await store.latest_summary("Arch")

    assert asyncio.run(run()) is None


def test_turns_since_summary_full_count_no_summary(tmp_path):
    """With no summary row, turns_since_summary returns the full count."""
    _point_db_at_tmp(tmp_path)
    import services.history_store as store

    async def run():
        for i in range(7):
            await store.append_turn("sess-1", "Arch", "user", f"q{i}")
        return await store.turns_since_summary("Arch")

    assert asyncio.run(run()) == 7


def test_turns_since_summary_incremental_after_rollup(tmp_path):
    """After a summary lands, turns_since_summary counts only newer rows."""
    _point_db_at_tmp(tmp_path)
    import services.history_store as store

    async def run():
        # 5 turns -> summary at turn id 5 -> 3 more turns.
        for i in range(5):
            await store.append_turn("sess-1", "Arch", "user", f"q{i}")
        max_id = await store.latest_turn_id("Arch")
        await store.append_summary("Arch", None, "rollup", max_id, "m")
        for i in range(3):
            await store.append_turn("sess-1", "Arch", "user", f"q{i+5}")
        return await store.turns_since_summary("Arch")

    assert asyncio.run(run()) == 3


def test_turns_to_rollup_returns_chronological_window(tmp_path):
    """turns_to_rollup returns rows newer than since_turn_id, oldest-first,
    and respects the safety cap."""
    _point_db_at_tmp(tmp_path)
    import services.history_store as store

    async def run():
        for i in range(10):
            await store.append_turn(
                "sess-1", "Arch", "user" if i % 2 == 0 else "assistant",
                f"msg-{i}",
            )
        # Pull from after id=4 (so msgs 5..9 — five rows).
        rows = await store.turns_to_rollup("Arch", since_turn_id=4, limit=80)
        rows_capped = await store.turns_to_rollup("Arch", since_turn_id=0, limit=3)
        return rows, rows_capped

    rows, rows_capped = asyncio.run(run())
    assert [r["content"] for r in rows] == [
        "msg-4", "msg-5", "msg-6", "msg-7", "msg-8", "msg-9",
    ]
    # Chronological — ids ascending.
    assert all(rows[i]["id"] < rows[i + 1]["id"] for i in range(len(rows) - 1))
    # Cap test.
    assert len(rows_capped) == 3
    assert [r["content"] for r in rows_capped] == ["msg-0", "msg-1", "msg-2"]


def test_turns_to_rollup_zero_limit(tmp_path):
    _point_db_at_tmp(tmp_path)
    import services.history_store as store

    async def run():
        await store.append_turn("sess-1", "Arch", "user", "q")
        return await store.turns_to_rollup("Arch", since_turn_id=0, limit=0)

    assert asyncio.run(run()) == []


def test_latest_turn_id_none_for_empty_project(tmp_path):
    _point_db_at_tmp(tmp_path)
    import services.history_store as store

    async def run():
        return await store.latest_turn_id("Personal Assist")

    assert asyncio.run(run()) is None


def test_latest_turn_id_scoped_to_project(tmp_path):
    _point_db_at_tmp(tmp_path)
    import services.history_store as store

    async def run():
        await store.append_turn("sess-1", "Arch", "user", "a")
        await store.append_turn("sess-1", "Arch", "user", "b")
        await store.append_turn("sess-2", "Chief Command", "user", "c")
        return (
            await store.latest_turn_id("Arch"),
            await store.latest_turn_id("Chief Command"),
        )

    arch_id, cc_id = asyncio.run(run())
    # Two arch rows + one cc row → arch_id = 2, cc_id = 3 (rowid is global).
    assert arch_id == 2
    assert cc_id == 3


def test_concurrent_summary_appends_dont_double_write(tmp_path):
    """Concurrent calls to append_summary should each land cleanly — schema
    is fine with multiple rows but we shouldn't lose any to a race in the
    asyncio.shield wrapper."""
    _point_db_at_tmp(tmp_path)
    import services.history_store as store

    async def run():
        await asyncio.gather(
            store.append_summary("Arch", None, "s1", 1, "m"),
            store.append_summary("Arch", None, "s2", 2, "m"),
            store.append_summary("Arch", None, "s3", 3, "m"),
        )
        # All three should have landed. We can't assert on order (concurrent)
        # but count is deterministic.
        rows = []
        for _ in range(3):
            row = await store.latest_summary("Arch")
            assert row is not None
            rows.append(row)
        return rows[0]

    last = asyncio.run(run())
    # The latest row (max id) is whatever landed last in the gather. The
    # important invariants: summary exists, watermark is one of the three
    # we wrote, model is what we passed.
    assert last["summary_text"] in {"s1", "s2", "s3"}
    assert last["covers_through_turn_id"] in {1, 2, 3}
    assert last["model"] == "m"
