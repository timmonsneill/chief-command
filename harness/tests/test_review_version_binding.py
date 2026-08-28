"""Sol build gate 3: the review-to-version chain.

Sol's most dangerous flaw, in his words: approve version A, builder changes it to B,
"the old approval still counts" — producing "believable green checks on code nobody
reviewed." These tests attack the chain the way he would: reuse a stale approval,
outrun a fail by re-completing, rewrite what shipped after the fact.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.jobs import (  # noqa: E402
    GuardViolation,
    Seat,
    connect,
    create_job,
    init_db,
    record_artifact,
    record_verdict,
    set_head_version,
    set_status,
    upsert_seat,
)

SEATS = [
    Seat("riggs", "claude-cli", "claude-opus-4-8", "claude", "subscription"),
    Seat("sol", "codex", "gpt-5.6-sol", "gpt", "subscription"),
    Seat("grok", "xai", "grok-4.5", "grok", "metered"),
    Seat("grinder", "ollama", "qwen2.5-coder:7b", "qwen", "local"),
]


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    init_db(c)
    for s in SEATS:
        upsert_seat(c, s)
    return c


# ── The headline attack: a stale approval must not count ────────────────────
def test_an_approval_of_version_a_does_not_approve_version_b(conn):
    """Sol: 'approve version A, builder changes it to B, the old approval still
    counts.' It must not. The moment the code moves, the green checks go stale."""
    job = create_job(conn, "the auth module", builder_seat="grinder")
    set_head_version(conn, job, "version-A")
    record_verdict(conn, job, "sol", verdict="pass")  # reviews version-A

    set_head_version(conn, job, "version-B")  # builder quietly changes the code

    with pytest.raises(GuardViolation, match="review"):
        set_status(conn, job, "done")


def test_a_fresh_review_of_the_new_version_completes_it(conn):
    job = create_job(conn, "the auth module", builder_seat="grinder")
    set_head_version(conn, job, "version-A")
    record_verdict(conn, job, "sol", verdict="pass")
    set_head_version(conn, job, "version-B")
    record_verdict(conn, job, "sol", verdict="pass")  # someone actually looked at B

    set_status(conn, job, "done")
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0] == "done"


def test_the_full_panel_must_have_reviewed_the_current_version(conn):
    """Three real passes on version A are zero passes on version B."""
    job = create_job(conn, "the login form", builder_seat="riggs")
    conn.execute("UPDATE jobs SET required_reviews=2 WHERE id=?", (job,))
    set_head_version(conn, job, "version-A")
    record_verdict(conn, job, "sol", verdict="pass")
    record_verdict(conn, job, "grok", verdict="pass")

    set_head_version(conn, job, "version-B")

    # One cross-family pass on B satisfies the family floor, so the guard left standing
    # is the one this test is about: the PANEL of two never reported on B.
    record_verdict(conn, job, "grok", verdict="pass")
    with pytest.raises(GuardViolation, match="panel has not reported"):
        set_status(conn, job, "done")


# ── A fail condemns a VERSION, not the job forever ──────────────────────────
def test_a_fail_condemns_the_version_it_reviewed_not_the_job(conn):
    """Before gate 3, one fail meant the job could never complete — a fixed build
    could never ship. Now: fail version A, fix it, pass version B, done."""
    job = create_job(conn, "the rate limiter", builder_seat="riggs")
    set_head_version(conn, job, "version-A")
    record_verdict(conn, job, "sol", verdict="fail", severity="p1",
                   summary="blocks our own status checks")

    set_head_version(conn, job, "version-B")  # the fix
    record_verdict(conn, job, "sol", verdict="pass")

    set_status(conn, job, "done")
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0] == "done"


def test_a_fail_on_the_current_version_still_stops_it_dead(conn):
    job = create_job(conn, "the rate limiter", builder_seat="riggs")
    set_head_version(conn, job, "version-A")
    record_verdict(conn, job, "sol", verdict="pass")
    record_verdict(conn, job, "grok", verdict="fail", severity="p1", summary="drops rows")

    with pytest.raises(GuardViolation, match="does not get outvoted"):
        set_status(conn, job, "done")


def test_a_fail_with_no_recorded_version_blocks_everything(conn):
    """Fail-closed: a fail that condemned we-don't-know-what condemns everything.
    (Written straight to the DB to simulate a legacy row — the Python layer always
    records a version on a versioned job.)"""
    job = create_job(conn, "the rate limiter", builder_seat="riggs")
    set_head_version(conn, job, "version-A")
    conn.execute(
        "INSERT INTO verdicts (job_id, reviewer_seat, reviewer_tier, role, "
        "model_family, verdict, reviewed_version) "
        "VALUES (?, 'sol', 'subscription', 'reviewer', 'gpt', 'fail', 'legacy')",
        (job,),
    )
    # Even after "fixing" to a new version, a versionless fail would block; here the
    # fail cites a version, so the fix clears it — but now plant a true NULL one.
    set_head_version(conn, job, "version-B")
    conn.execute("UPDATE jobs SET head_version=NULL WHERE id=?", (job,))
    with pytest.raises(GuardViolation):
        set_status(conn, job, "done")


# ── An unanswered question blocks regardless of version ─────────────────────
def test_needs_human_cannot_be_outrun_by_a_new_version(conn):
    """A fail condemns a version. A QUESTION demands an answer — pushing new code
    is not an answer."""
    job = create_job(conn, "the migration", builder_seat="riggs")
    set_head_version(conn, job, "version-A")
    record_verdict(conn, job, "sol", verdict="needs_human",
                   summary="does this drop rows?")

    set_head_version(conn, job, "version-B")
    record_verdict(conn, job, "sol", verdict="pass")

    with pytest.raises(GuardViolation, match="needs_human"):
        set_status(conn, job, "done")


# ── The record itself resists tampering ─────────────────────────────────────
def test_a_versioned_job_rejects_verdicts_that_name_no_version(conn):
    job = create_job(conn, "the auth module", builder_seat="riggs")
    set_head_version(conn, job, "version-A")
    with pytest.raises(Exception, match="which version"):
        conn.execute(
            "INSERT INTO verdicts (job_id, reviewer_seat, reviewer_tier, role, "
            "model_family, verdict) "
            "VALUES (?, 'sol', 'subscription', 'reviewer', 'gpt', 'pass')",
            (job,),
        )


def test_a_build_cannot_finish_without_naming_its_version(conn):
    job = create_job(conn, "the auth module", builder_seat="riggs")
    # A genuine cross-family pass, so the only thing missing is the version itself.
    record_verdict(conn, job, "sol", verdict="pass")
    with pytest.raises(GuardViolation, match="naming the exact version"):
        set_status(conn, job, "done")


def test_what_finished_cannot_be_rewritten_afterward(conn):
    """The complement of the stale-approval attack: complete honestly as version A,
    then flip head_version to B so the record claims B was the approved thing."""
    job = create_job(conn, "the auth module", builder_seat="riggs")
    set_head_version(conn, job, "version-A")
    record_verdict(conn, job, "sol", verdict="pass")
    set_status(conn, job, "done")

    with pytest.raises(GuardViolation, match="cannot be rewritten"):
        set_head_version(conn, job, "version-B")


def test_shipping_requires_the_tester_to_have_driven_the_current_version(conn):
    job = create_job(conn, "the login form", builder_seat="riggs")
    set_head_version(conn, job, "version-A")
    record_verdict(conn, job, "sol", verdict="pass")   # the current version was reviewed...
    record_artifact(conn, job, kind="screenshot", path="/tmp/a.png",
                    captured_by="playwright")
    record_verdict(conn, job, "sol", verdict="pass", role="tester",
                   reviewed_version="version-OLD")  # ...but the tester drove an earlier build
    set_status(conn, job, "done")

    with pytest.raises(GuardViolation, match="tester"):
        set_status(conn, job, "shipped")


def test_a_verdict_cannot_be_repointed_at_another_version(conn):
    """Sol's home-and-workers gate (2026-08-28) proved this one-line bypass of the whole
    version chain: approve OLD, then UPDATE the verdict to say it was about the head."""
    job = create_job(conn, "the login form", builder_seat="grinder")
    set_status(conn, job, "in_progress")
    set_head_version(conn, job, "version-A")
    record_verdict(conn, job, "sol", verdict="pass", reviewed_version="version-OLD")
    with pytest.raises((GuardViolation, sqlite3.IntegrityError), match="cannot be moved"):
        conn.execute("UPDATE verdicts SET reviewed_version='version-A' WHERE job_id=?", (job,))
    with pytest.raises((GuardViolation, sqlite3.IntegrityError), match="cannot be moved"):
        conn.execute("UPDATE verdicts SET job_id=? WHERE job_id=?", (job + 1000, job))
