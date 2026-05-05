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
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Poisoned-history filter — assistant-only.
# ---------------------------------------------------------------------------
# Pax traced Arch's amnesia loop (2026-05-04) to scoped-memory blindness
# RECURSIVELY confirmed by Chief's own prior "I don't have X loaded" replies
# replayed as context every turn. Once a scope went amnesiac, every subsequent
# turn pulled the leaky reply back into history and re-confirmed amnesia.
#
# We don't delete from the DB — those rows are still part of the audit trail —
# we just filter them out of the rehydration boundary so they don't poison the
# next turn's context. User turns are NEVER filtered: the owner's words always
# replay verbatim. The whole list is assistant-side architectural-leak
# patterns; they should never have been said and shouldn't be replayed.
_LEAK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"I don't have .* loaded", re.IGNORECASE),
    re.compile(r"never talked about", re.IGNORECASE),
    re.compile(r"I'm scoped to", re.IGNORECASE),
    re.compile(r"I'm not in .* right now", re.IGNORECASE),
    re.compile(r"don't have access to", re.IGNORECASE),
    re.compile(r"I don't have a clock", re.IGNORECASE),
)


def _is_leaky(role: str, content: str) -> bool:
    """Return True if an assistant turn matches a known architectural-leak
    pattern and should be omitted from rehydrated history.

    User turns are never leaky by definition — we always replay what the
    owner said.
    """
    if role != "assistant" or not content:
        return False
    return any(p.search(content) for p in _LEAK_PATTERNS)


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

-- Phase 3: rolling cross-session memory.
--
-- Each row captures a compressed summary of the conversation from the
-- start (or the previous summary's watermark) up through
-- ``covers_through_turn_id`` of ``voice_turns``. A project's ``latest``
-- summary is the row with the largest ``id`` for that project. Older
-- rows are kept for audit / rebuild but never read on the hot path.
--
-- ``session_id`` is nullable on purpose: a rollup spans whatever sessions
-- existed inside the watermark range (often more than one).
CREATE TABLE IF NOT EXISTS voice_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    session_id TEXT,
    summary_text TEXT NOT NULL,
    covers_through_turn_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_voice_summaries_project_created
    ON voice_summaries (project, created_at DESC);
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


# ---------------------------------------------------------------------------
# Phase 3 — voice_summaries: rolling cross-session memory.
# ---------------------------------------------------------------------------
async def _do_append_summary(
    project: str,
    session_id: Optional[str],
    summary_text: str,
    covers_through_turn_id: int,
    model: str,
) -> None:
    db = await _connect()
    try:
        await db.execute(
            """INSERT INTO voice_summaries
                 (project, session_id, summary_text, covers_through_turn_id,
                  model, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                project,
                session_id,
                summary_text,
                covers_through_turn_id,
                model,
                _now_iso(),
            ),
        )
        await db.commit()
    finally:
        await db.close()


async def append_summary(
    project: str,
    session_id: Optional[str],
    summary_text: str,
    covers_through_turn_id: int,
    model: str,
) -> None:
    """Persist a new rolling summary row for a project.

    Wrapped in ``asyncio.shield`` so a barge-in cancel landing on the rollup
    task can't tear the row mid-commit. ``covers_through_turn_id`` is the
    largest ``voice_turns.id`` included in the rolled-up window — used by
    ``turns_since_summary`` to count incremental work.
    """
    if not summary_text:
        return
    await asyncio.shield(
        _do_append_summary(
            project, session_id, summary_text, covers_through_turn_id, model,
        )
    )


async def latest_summary(project: str) -> Optional[dict]:
    """Return the most recent summary row for ``project`` or ``None``.

    Shape: ``{"id", "project", "session_id", "summary_text",
    "covers_through_turn_id", "model", "created_at"}``. Used to (a)
    inject prior summary into the next system prompt, and (b) seed the
    next rollup so we don't re-summarize already-rolled turns.
    """
    db = await _connect()
    try:
        cur = await db.execute(
            """SELECT id, project, session_id, summary_text,
                      covers_through_turn_id, model, created_at
               FROM voice_summaries
               WHERE project = ?
               ORDER BY id DESC
               LIMIT 1""",
            (project,),
        )
        row = await cur.fetchone()
    finally:
        await db.close()
    if row is None:
        return None
    return {
        "id": row["id"],
        "project": row["project"],
        "session_id": row["session_id"],
        "summary_text": row["summary_text"],
        "covers_through_turn_id": row["covers_through_turn_id"],
        "model": row["model"],
        "created_at": row["created_at"],
    }


async def turns_since_summary(project: str) -> int:
    """Count voice_turns rows newer than the project's latest summary.

    Returns the full count of rows for ``project`` when no summary exists.
    Drives the rollup-trigger decision in ``memory_rollup.maybe_rollup``.
    """
    db = await _connect()
    try:
        cur = await db.execute(
            """SELECT COALESCE(MAX(covers_through_turn_id), 0) AS watermark
               FROM voice_summaries
               WHERE project = ?""",
            (project,),
        )
        row = await cur.fetchone()
        watermark = int(row["watermark"]) if row else 0

        cur = await db.execute(
            """SELECT COUNT(*) AS n FROM voice_turns
               WHERE project = ? AND id > ?""",
            (project, watermark),
        )
        row = await cur.fetchone()
        return int(row["n"]) if row else 0
    finally:
        await db.close()


async def turns_to_rollup(
    project: str,
    since_turn_id: int,
    limit: int = 80,
) -> list[dict]:
    """Return role/content/id rows for ``project`` newer than ``since_turn_id``.

    Used by the rollup writer to feed Flash a chronological transcript of
    just the new work. Results are oldest-first (chronological). ``limit``
    is a safety cap so a runaway gap doesn't dump 10k rows into a single
    Flash call.

    Each entry includes ``id`` so the caller can record the highest id as
    the new ``covers_through_turn_id`` watermark.
    """
    if limit <= 0:
        return []
    db = await _connect()
    try:
        cur = await db.execute(
            """SELECT id, role, content FROM voice_turns
               WHERE project = ? AND id > ?
               ORDER BY id ASC
               LIMIT ?""",
            (project, since_turn_id, limit),
        )
        rows = await cur.fetchall()
    finally:
        await db.close()
    return [
        {"id": r["id"], "role": r["role"], "content": r["content"]}
        for r in rows
    ]


async def latest_turn_id(project: str) -> Optional[int]:
    """Return the highest ``voice_turns.id`` for ``project`` or ``None``.

    Used by the rollup writer to set ``covers_through_turn_id`` when it
    can't read individual ids back (e.g. a cap-truncated rollup window
    where we still want the watermark to advance to the actual newest
    row, not the cap row).
    """
    db = await _connect()
    try:
        cur = await db.execute(
            """SELECT MAX(id) AS max_id FROM voice_turns
               WHERE project = ?""",
            (project,),
        )
        row = await cur.fetchone()
    finally:
        await db.close()
    if row is None or row["max_id"] is None:
        return None
    return int(row["max_id"])


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

    Assistant turns matching ``_LEAK_PATTERNS`` are dropped at this
    boundary — they're persisted for audit but never replayed as
    context. See ``_is_leaky`` for the rationale.
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
    filtered: list[dict] = []
    leak_count = 0
    for r in reversed(rows):
        role = r["role"]
        content = r["content"]
        if _is_leaky(role, content):
            leak_count += 1
            continue
        filtered.append({"role": role, "content": content})
    if leak_count:
        logger.info(
            "history_store: filtered %d poisoned assistant turn(s) from "
            "rehydration project=%s",
            leak_count,
            project,
        )
    return filtered
