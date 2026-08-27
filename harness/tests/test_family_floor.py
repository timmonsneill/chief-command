"""The family floor — the gauntlet's core property, now a DB guard (task #10).

Sol's #10 gate: "at least two model FAMILIES reviewed this" was enforced nowhere in the
database — it lived in a config value and a comment, so it rested on Python being right.
These tests prove the schema now refuses to complete a job unless enough DISTINCT
families actually passed it, on the current version, and that the floor can't be lowered
after the fact.

A guard trips the same way through the helpers or raw SQL — that's the point.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.jobs import (  # noqa: E402
    GuardViolation, Seat, connect, create_job, init_db, record_verdict,
    set_status, upsert_seat,
)

BLOCKED = (GuardViolation, sqlite3.IntegrityError)

# Two seats share the GPT family on purpose — a "full panel" of distinct seats can still
# be a single mind wearing several hats, which is exactly what the family floor catches.
SEATS = [
    Seat("riggs", "claude-cli", "claude-opus-4-8", "claude", "subscription"),
    Seat("sol",   "codex",      "gpt-5.6-sol",     "gpt",    "subscription"),
    Seat("sol2",  "codex",      "gpt-5.6-terra",   "gpt",    "subscription"),
    Seat("grok",  "xai",        "grok-4.5",        "grok",   "metered", daily_cap_cents=100),
]


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    init_db(c)
    for s in SEATS:
        upsert_seat(c, s)
    return c


def _job(conn, families):
    # Builder is subscription-tier (not local) so the higher-tier guard doesn't also
    # fire — we're isolating the FAMILY floor here.
    job = create_job(conn, "the login form", builder_seat="riggs")
    conn.execute(
        "UPDATE jobs SET required_reviews=2, required_review_families=?, head_version='v1' "
        "WHERE id=?", (families, job),
    )
    return job


def test_a_full_panel_of_one_family_does_not_complete(conn):
    """Two DISTINCT seats pass — but both are GPT. Seat count is satisfied; the family
    floor is not. The job must NOT reach done."""
    job = _job(conn, families=2)
    record_verdict(conn, job, "sol",  verdict="pass")   # gpt
    record_verdict(conn, job, "sol2", verdict="pass")   # gpt again — different seat, same mind
    with pytest.raises(BLOCKED, match="fewer model families"):
        set_status(conn, job, "done")


def test_two_families_satisfy_the_floor(conn):
    job = _job(conn, families=2)
    record_verdict(conn, job, "sol",  verdict="pass")   # gpt
    record_verdict(conn, job, "grok", verdict="pass")   # grok — a second family
    set_status(conn, job, "done")
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0] == "done"


def test_only_current_version_families_count(conn):
    """A pass on an earlier version is history, not a vote on what's on the table now."""
    job = _job(conn, families=2)
    record_verdict(conn, job, "grok", verdict="pass", reviewed_version="OLD")  # stale
    record_verdict(conn, job, "sol",  verdict="pass")                          # current (v1)
    # Only one CURRENT-version family (gpt) — the stale grok pass doesn't count.
    with pytest.raises(BLOCKED, match="fewer model families"):
        set_status(conn, job, "done")


def test_the_family_floor_cannot_be_lowered_after_dispatch(conn):
    job = _job(conn, families=2)
    with pytest.raises(BLOCKED, match="family floor cannot be lowered"):
        conn.execute("UPDATE jobs SET required_review_families=1 WHERE id=?", (job,))


def test_a_zero_floor_is_NOT_inert_it_means_one(conn):
    """Migration 007 replaced 'zero is inert' with 'unstamped is not unrequired': a job
    whose floor was never set still needs one mind other than the author's. This test
    used to assert the old behaviour and would have stayed green if the floor were
    accidentally removed — because its two passes came from one qualifying family."""
    job = _job(conn, families=0)
    # With no passes at all, BOTH unconditional floors refuse (SQLite doesn't promise
    # which speaks first; either is the same no).
    with pytest.raises(BLOCKED, match="panel has not reported|fewer model families"):
        set_status(conn, job, "done")
    record_verdict(conn, job, "sol",  verdict="pass")
    record_verdict(conn, job, "sol2", verdict="pass")
    set_status(conn, job, "done")   # required_reviews=2 met, floor of MAX(0,1)=1 met by gpt
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0] == "done"
