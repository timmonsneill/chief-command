"""Voice conversation history persistence.

Single-table SQLite store for voice WS turns so that reloading the backend
(or a reconnecting WS) doesn't wipe the conversation. Survives uvicorn
``--reload`` restarts, which the in-process ``history: list[dict]`` does not.

Scope: append-only persistence of (role, content) tuples tagged with
session_id + project. No edit/delete — the voice path treats history as
immutable once written.

DB path resolution (in priority order):
    1. ``VOICE_HISTORY_DB_PATH`` env var — tests override this.
    2. Fallback: ``<backend>/data/voice_history.db`` alongside ``PROJECTS_DATA_DIR``.

aiosqlite is already in requirements.txt for ``db.py``, so we reuse the
same async driver for consistency (instead of ``asyncio.to_thread`` +
stdlib ``sqlite3``).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)


def _resolve_db_path() -> Path:
    """Resolve the voice-history DB file path.

    Env override wins. Otherwise defaults to the repo-local
    ``backend/data/voice_history.db`` — versioned directory, matches the
    PROJECTS dashboard-data convention already in ``settings.py``. Parent
    dir is created lazily on first write.
    """
    env = os.environ.get("VOICE_HISTORY_DB_PATH")
    if env:
        return Path(env)
    backend_root = Path(__file__).resolve().parents[1]
    return backend_root / "data" / "voice_history.db"


_DDL = """
CREATE TABLE IF NOT EXISTS voice_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    project TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_voice_turns_session_id
    ON voice_turns (session_id, id);
"""


_initialized = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_schema(db: aiosqlite.Connection) -> None:
    """Lazy schema init — cheap after the first call (idempotent DDL)."""
    global _initialized
    if _initialized:
        return
    await db.executescript(_DDL)
    await db.commit()
    _initialized = True


async def _connect() -> aiosqlite.Connection:
    """Open a connection, ensuring parent dir + schema exist.

    Caller is responsible for closing (use ``async with`` at call sites)."""
    path = _resolve_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    await _ensure_schema(db)
    return db


async def _do_append(
    session_id: str, project: str, role: str, content: str
) -> None:
    db = await _connect()
    try:
        await db.execute(
            """INSERT INTO voice_turns
                 (session_id, project, role, content, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (session_id, project, role, content, _now_iso()),
        )
        await db.commit()
    finally:
        await db.close()


async def append_turn(
    session_id: str,
    project: str,
    role: str,
    content: str,
) -> None:
    """Append a single turn row.

    No-ops on empty content. Role is not validated — caller passes
    ``"user"`` / ``"assistant"``.

    The DB write is wrapped in ``asyncio.shield`` so a barge-in cancel on
    the calling turn task can't interrupt an in-flight commit. Without the
    shield, memory and DB would diverge when the user barged in between
    the in-memory ``history.append`` and this write (memory has the turn,
    DB doesn't). The shield makes the persist atomic from the cancel's
    perspective.
    """
    if not content:
        return
    await asyncio.shield(_do_append(session_id, project, role, content))


async def load_recent(session_id: str, limit: int = 50) -> list[dict]:
    """Return the last ``limit`` turns for ``session_id``, oldest-first.

    Shape: ``[{"role": str, "content": str}, ...]`` — drop-in replacement
    for the in-memory ``history`` list the voice WS already feeds into
    ``stream_turn``.
    """
    if limit <= 0:
        return []
    db = await _connect()
    try:
        # ORDER BY id DESC + LIMIT gets the latest N; caller wants oldest-first
        # for prompt ordering, so we reverse on return.
        cur = await db.execute(
            """SELECT role, content FROM voice_turns
               WHERE session_id = ?
               ORDER BY id DESC
               LIMIT ?""",
            (session_id, limit),
        )
        rows = await cur.fetchall()
    finally:
        await db.close()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


async def load_recent_for_project(
    project: str,
    limit: int = 20,
) -> list[dict]:
    """Return the last ``limit`` turns for the given ``project`` scope,
    oldest-first, ACROSS sessions.

    Used at WS connect-time to rehydrate conversation context after an
    uvicorn --reload without reusing a stale session_id. The caller starts
    a FRESH usage session; the history lookup just pulls recent context
    from prior sessions in the same project so Chief doesn't feel amnesiac.

    Scoping by project (not session_id) avoids two failure modes Hawke
    flagged:
      1. Cross-project context bleed (resuming a Chief Command history
         while the live scope is Arch).
      2. Ghost-session cost-tracking drift (reusing a session_id that
         has no matching ``sessions`` row in the usage tracker).
    """
    if limit <= 0:
        return []
    db = await _connect()
    try:
        cur = await db.execute(
            """SELECT role, content FROM voice_turns
               WHERE project = ?
               ORDER BY id DESC
               LIMIT ?""",
            (project, limit),
        )
        rows = await cur.fetchall()
    finally:
        await db.close()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
