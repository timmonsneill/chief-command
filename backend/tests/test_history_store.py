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
