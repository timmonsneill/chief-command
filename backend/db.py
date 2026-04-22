"""SQLite database layer for session and turn persistence."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import aiosqlite

logger = logging.getLogger(__name__)

DB_PATH = Path(os.environ.get("CHIEF_DB_PATH", str(Path.home() / ".chief-command" / "usage.db")))

_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'owner',
    started_at TEXT NOT NULL,
    ended_at TEXT,
    total_cost_cents INTEGER NOT NULL DEFAULT 0,
    turn_count INTEGER NOT NULL DEFAULT 0,
    project TEXT
);

CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    created_at TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    cost_cents INTEGER NOT NULL DEFAULT 0,
    user_text TEXT NOT NULL DEFAULT '',
    assistant_text TEXT NOT NULL DEFAULT '',
    stt_seconds REAL NOT NULL DEFAULT 0,
    stt_provider TEXT,
    stt_cost_usd REAL NOT NULL DEFAULT 0,
    tts_chars INTEGER NOT NULL DEFAULT 0,
    tts_provider TEXT,
    tts_cost_usd REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# Columns to add to `turns` for existing DBs. Each entry = (column_name, DDL).
# PRAGMA table_info probe before ALTER so re-runs (or concurrent boots) are
# a no-op rather than an error.
_TURNS_VOICE_COLUMNS: list[tuple[str, str]] = [
    ("stt_seconds",    "ALTER TABLE turns ADD COLUMN stt_seconds REAL NOT NULL DEFAULT 0"),
    ("stt_provider",   "ALTER TABLE turns ADD COLUMN stt_provider TEXT"),
    ("stt_cost_usd",   "ALTER TABLE turns ADD COLUMN stt_cost_usd REAL NOT NULL DEFAULT 0"),
    ("tts_chars",      "ALTER TABLE turns ADD COLUMN tts_chars INTEGER NOT NULL DEFAULT 0"),
    ("tts_provider",   "ALTER TABLE turns ADD COLUMN tts_provider TEXT"),
    ("tts_cost_usd",   "ALTER TABLE turns ADD COLUMN tts_cost_usd REAL NOT NULL DEFAULT 0"),
]


async def _existing_columns(db: aiosqlite.Connection, table: str) -> set[str]:
    """Return the current set of column names on `table` via PRAGMA table_info.

    Needed so we don't try ALTER TABLE ADD COLUMN on a column that already
    exists — SQLite errors rather than no-ops, so we probe first.
    """
    cur = await db.execute(f"PRAGMA table_info({table})")
    rows = await cur.fetchall()
    return {row[1] for row in rows}


async def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_DDL)
        # Idempotent migration: add project column to existing sessions tables
        try:
            await db.execute("ALTER TABLE sessions ADD COLUMN project TEXT")
            await db.commit()
            logger.info("Migrated sessions table: added project column")
        except Exception:
            # Column already exists — ignore
            pass

        # Idempotent migration: add voice-usage columns to existing turns tables.
        # Fresh DBs already have them via _DDL, so we probe first and only add
        # what's missing. Safe to re-run on every boot.
        existing = await _existing_columns(db, "turns")
        added: list[str] = []
        for name, ddl in _TURNS_VOICE_COLUMNS:
            if name in existing:
                continue
            try:
                await db.execute(ddl)
                added.append(name)
            except Exception:
                # Most likely the column was added by a concurrent boot or a
                # prior partial migration. Real migration failures will surface
                # via subsequent queries that need the column — no need to dump
                # a full stack trace for an expected race condition.
                logger.info(
                    "Voice column %s already present — likely concurrent migration race",
                    name,
                )
        if added:
            await db.commit()
            logger.info("Migrated turns table: added voice columns %s", added)
    logger.info("DB initialised at %s", DB_PATH)


@asynccontextmanager
async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        yield db
