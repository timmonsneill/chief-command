"""The live database must carry the same guards the tests test.

Every other test in this suite builds a FRESH database from schema.sql and proves
the guards hold there. None of that says anything about harness/db/chief.db — and
on 2026-07-20 the live DB turned out to be missing two guards (verdict deletion,
born-granted approvals) and running a stale panel guard, months of green tests
notwithstanding. Sol's build gate 1: the live DB must match the hardened rules.

These tests skip cleanly when no live DB exists (fresh checkout, CI).
"""

import sqlite3
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent.parent
SCHEMA = HARNESS / "db" / "schema.sql"
LIVE_DB = HARNESS / "db" / "chief.db"

pytestmark = pytest.mark.skipif(
    not LIVE_DB.exists(), reason="no live database on this machine"
)


def _object_names(conn: sqlite3.Connection, kind: str) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%'",
        (kind,),
    )
    return {r[0] for r in rows}


def _columns(conn: sqlite3.Connection, table: str) -> set[tuple]:
    # (name, type, notnull, default) — column ORDER is allowed to differ, because
    # ALTER TABLE ADD COLUMN appends and that is how live migrations happen.
    rows = conn.execute(f"PRAGMA table_info({table})")
    return {(r[1], r[2], r[3], r[4]) for r in rows}


def _normalized_trigger_sql(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'")
    # Whitespace and comments may drift between schema.sql and a migration; the
    # executable text must not. Strip comment lines, collapse whitespace.
    out = {}
    for name, sql in rows:
        lines = [
            line.split("--")[0] for line in sql.splitlines()
        ]
        out[name] = " ".join(" ".join(lines).split())
    return out


@pytest.fixture(scope="module")
def fresh():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA.read_text())
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def live():
    # Read-only: this test must never be able to touch the record.
    conn = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
    yield conn
    conn.close()


def test_live_db_has_every_table(fresh, live):
    assert _object_names(fresh, "table") <= _object_names(live, "table")


def test_live_db_has_every_view(fresh, live):
    assert _object_names(fresh, "view") <= _object_names(live, "view")


def test_live_db_has_every_guard_with_identical_teeth(fresh, live):
    fresh_triggers = _normalized_trigger_sql(fresh)
    live_triggers = _normalized_trigger_sql(live)
    missing = set(fresh_triggers) - set(live_triggers)
    assert not missing, f"live DB is missing guards: {sorted(missing)}"
    stale = {
        name
        for name, sql in fresh_triggers.items()
        if live_triggers[name] != sql
    }
    assert not stale, f"live DB guards differ from schema.sql: {sorted(stale)}"


def test_live_db_tables_have_every_column(fresh, live):
    for table in _object_names(fresh, "table"):
        missing = _columns(fresh, table) - _columns(live, table)
        assert not missing, f"live {table} is missing columns: {sorted(missing)}"
