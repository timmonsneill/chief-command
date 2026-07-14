"""Durable job store for the Chief Command v2 harness.

OpenClaw dispatches the work; this records it. Its subagent sessions auto-archive
after ~60 minutes, so anything we want to answer later ("what did the overnight run
do?", spec §7) has to live here.

The two rules that must never quietly stop holding are enforced in schema.sql as
triggers, not here — see guard_local_output_needs_review and
guard_unresolved_escalation. This module will simply raise if you try to violate them.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

DB_PATH = Path(__file__).resolve().parent / "chief.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# Raised by the schema triggers. We surface it as something catchable.
class GuardViolation(RuntimeError):
    """A structural rule was violated (e.g. shipping unreviewed local output)."""


@dataclass(frozen=True)
class Seat:
    id: str
    provider: str
    model: str
    family: str  # gpt | claude | grok | qwen — what determines shared blind spots
    tier: str  # local | subscription | metered
    daily_cap_cents: Optional[int] = None   # hard ceiling across everything
    build_cap_cents: Optional[int] = None   # ration the EXPENSIVE work
    review_cap_cents: Optional[int] = None  # reviewing is cheap — be generous
    # Three models per seat. NOTHING gets `heavy` by default — it has to be earned.
    # Rate limits, not money, are the binding constraint on autonomous work, and you
    # cannot buy your way out of a weekly cap.
    model_light: Optional[str] = None
    model_standard: Optional[str] = None
    model_heavy: Optional[str] = None
    enabled: bool = True
    notes: str = ""


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, isolation_level=None)  # autocommit; we manage txns
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text())


def _guarded(fn):
    """Translate the schema's RAISE(ABORT, 'guard: ...') into GuardViolation."""

    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except sqlite3.IntegrityError as exc:
            if "guard:" in str(exc):
                raise GuardViolation(str(exc)) from exc
            raise

    return wrapper


# --------------------------------------------------------------------------
# Seats
# --------------------------------------------------------------------------
def upsert_seat(conn: sqlite3.Connection, seat: Seat) -> None:
    conn.execute(
        """
        INSERT INTO seats (id, provider, model, family, tier, daily_cap_cents,
                           build_cap_cents, review_cap_cents,
                           model_light, model_standard, model_heavy, enabled, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            provider = excluded.provider,
            model = excluded.model,
            family = excluded.family,
            tier = excluded.tier,
            daily_cap_cents = excluded.daily_cap_cents,
            build_cap_cents = excluded.build_cap_cents,
            review_cap_cents = excluded.review_cap_cents,
            model_light = excluded.model_light,
            model_standard = excluded.model_standard,
            model_heavy = excluded.model_heavy,
            enabled = excluded.enabled,
            notes = excluded.notes
        """,
        (seat.id, seat.provider, seat.model, seat.family, seat.tier,
         seat.daily_cap_cents, seat.build_cap_cents, seat.review_cap_cents,
         seat.model_light, seat.model_standard, seat.model_heavy,
         int(seat.enabled), seat.notes),
    )


def seat(conn: sqlite3.Connection, seat_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM seats WHERE id = ?", (seat_id,)).fetchone()


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------
def create_job(
    conn: sqlite3.Connection,
    request: str,
    builder_seat: str,
    origin: str = "text",
    parent_job_id: Optional[int] = None,
    task_name: Optional[str] = None,
) -> int:
    """task_name is the 2-4 word handle the voice will use ("the rate limiter").

    The mouth coins it at dispatch. Without one the voice falls back to truncating
    the request, which reads back the owner's own paragraph at him — exactly what a
    colleague would never do.
    """
    row = seat(conn, builder_seat)
    if row is None:
        raise ValueError(f"unknown builder seat: {builder_seat}")

    # Snapshot who built this, AT BUILD TIME. Sol's cross-family review found that
    # reading the builder's tier live meant re-tiering a local seat retroactively
    # legitimized its old unreviewed work. What a seat is today cannot change what
    # it was. The schema then freezes these columns.
    cur = conn.execute(
        """
        INSERT INTO jobs (request, builder_seat, builder_tier, builder_family,
                          origin, parent_job_id, task_name)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (request, builder_seat, row["tier"], row["family"],
         origin, parent_job_id, task_name),
    )
    return int(cur.lastrowid)


def claim_next_job(conn: sqlite3.Connection, builder_seat: str) -> sqlite3.Row | None:
    """Atomically claim the oldest queued job for this seat.

    Uses UPDATE...RETURNING against a subquery so two workers racing for the same
    row cannot both win — SQLite serializes the write and only one claim lands.
    Cribbed from clu's approach; it removes the need for any external lock.
    """
    row = conn.execute(
        """
        UPDATE jobs
           SET status = 'in_progress',
               started_at = datetime('now'),
               attempts = attempts + 1
         WHERE id = (
             SELECT id FROM jobs
              WHERE status = 'todo' AND builder_seat = ?
              ORDER BY id
              LIMIT 1
         )
        RETURNING *
        """,
        (builder_seat,),
    ).fetchone()
    return row


def attach_run(
    conn: sqlite3.Connection,
    job_id: int,
    run_id: str,
    session_key: str | None = None,
    branch: str | None = None,
    worktree: str | None = None,
) -> None:
    """Record the OpenClaw spawn that is actually doing the work."""
    conn.execute(
        """
        UPDATE jobs SET run_id = ?, session_key = ?, branch = ?, worktree = ?
         WHERE id = ?
        """,
        (run_id, session_key, branch, worktree, job_id),
    )


@_guarded
def set_status(
    conn: sqlite3.Connection,
    job_id: int,
    status: str,
    result: str | None = None,
    error: str | None = None,
    spoken_summary: str | None = None,
) -> None:
    """Move a job's status.

    Moving to 'done' runs the schema guards. If the builder was a local seat and no
    higher-tier reviewer has passed it, this raises GuardViolation — by design.
    """
    finished = status in ("done", "failed", "cancelled")
    conn.execute(
        """
        UPDATE jobs
           SET status = ?,
               result = COALESCE(?, result),
               error = COALESCE(?, error),
               spoken_summary = COALESCE(?, spoken_summary),
               finished_at = CASE WHEN ? THEN datetime('now') ELSE finished_at END
         WHERE id = ?
        """,
        (status, result, error, spoken_summary, int(finished), job_id),
    )


def record_artifact(
    conn: sqlite3.Connection,
    job_id: int,
    kind: str,
    path: str | None = None,
    value: str | None = None,
    flow: str | None = None,
    captured_by: str = "playwright",
) -> int:
    """Record GROUND TRUTH.

    Two kinds, and they have different rules about who may write them:

    EVIDENCE OF A BUILD (screenshot, trace, logs, exit codes) — only the harness or
    Playwright. NEVER a model. A model cannot fabricate a screenshot, which is what
    makes "I ran it and it worked" unsayable.

    EVIDENCE OF A CLAIM (source, quote) — a model MAY write these, because a
    researcher's whole job is to hand you a URL you can go and read yourself. The
    check isn't "who wrote it down", it's "can someone else go and look?" — which is
    why a different family has to read the sources cold (guard 8).
    """
    _MODEL_MAY_CITE = {"source", "quote"}
    if captured_by == "model" and kind not in _MODEL_MAY_CITE:
        raise ValueError(
            f"a model may cite a source, but it may not produce a '{kind}' — "
            "build evidence is captured by the harness, never claimed by a model"
        )
    if captured_by not in ("harness", "playwright", "model"):
        raise ValueError(f"unknown artifact origin: {captured_by}")
    cur = conn.execute(
        """
        INSERT INTO artifacts (job_id, kind, path, value, flow, captured_by)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (job_id, kind, path, value, flow, captured_by),
    )
    return int(cur.lastrowid)


@_guarded
def record_verdict(
    conn: sqlite3.Connection,
    job_id: int,
    reviewer_seat: str,
    verdict: str,
    severity: str | None = None,
    summary: str | None = None,
    detail: str | None = None,
    role: str = "reviewer",
) -> int:
    """Record one seat's verdict.

    role='reviewer' reads the diff and judges it.
    role='tester'   drove the app (Playwright) and judges what it observed. Two guards
                    apply: it must cite captured artifacts, and its family must differ
                    from the builder's — Playwright stops a tester fabricating what
                    happened; only a different mind stops it rationalizing it.

    tier and family are snapshotted from the seat at write time, so re-tiering or
    re-pointing a seat later cannot retroactively legitimize a job.
    """
    row = seat(conn, reviewer_seat)
    if row is None:
        raise ValueError(f"unknown seat: {reviewer_seat}")

    cur = conn.execute(
        """
        INSERT INTO verdicts
            (job_id, reviewer_seat, reviewer_tier, role, model_family,
             verdict, severity, summary, detail)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (job_id, reviewer_seat, row["tier"], role, row["family"],
         verdict, severity, summary, detail),
    )
    return int(cur.lastrowid)


def resolve_escalation(conn: sqlite3.Connection, verdict_id: int, verdict: str) -> None:
    """Answer a needs_human verdict so the job can proceed (§6 human review queue)."""
    if verdict not in ("pass", "fail"):
        raise ValueError("an escalation resolves to 'pass' or 'fail'")
    conn.execute("UPDATE verdicts SET verdict = ? WHERE id = ?", (verdict, verdict_id))


# --------------------------------------------------------------------------
# Reporting — this is what the voice layer reads back
# --------------------------------------------------------------------------
def overnight_report(conn: sqlite3.Connection, since: str = "-1 day") -> list[sqlite3.Row]:
    """Answers spec §7's 'what did the overnight run do?'"""
    return conn.execute(
        """
        SELECT * FROM job_report
         WHERE created_at >= datetime('now', ?)
         ORDER BY id
        """,
        (since,),
    ).fetchall()


def spend_today(conn: sqlite3.Connection, seat_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_cents), 0) AS c FROM usage WHERE seat_id = ? AND day = date('now')",
        (seat_id,),
    ).fetchone()
    return int(row["c"])


def over_budget(conn: sqlite3.Connection, seat_id: str) -> bool:
    """True if this seat has hit its daily cap.

    OpenClaw has no per-agent budget of any kind, so the harness must check this
    before dispatching. Uncapped seats (daily_cap_cents IS NULL) never block.
    """
    row = seat(conn, seat_id)
    if row is None or row["daily_cap_cents"] is None:
        return False
    return spend_today(conn, seat_id) >= int(row["daily_cap_cents"])


@_guarded
def record_usage(
    conn: sqlite3.Connection,
    seat_id: str,
    cost_cents: int,
    job_id: int | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    role: str = "build",
) -> None:
    """Record spend. The DATABASE refuses an entry that would breach the daily cap.

    This used to be a Python check before dispatch — which Sol correctly called a
    race (two dispatches both pass the check before either records spend) and easily
    bypassed by any direct write. The ledger itself now says no.
    """
    conn.execute(
        """
        INSERT INTO usage (seat_id, job_id, role, input_tokens, output_tokens, cost_cents)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (seat_id, job_id, role, input_tokens, output_tokens, cost_cents),
    )


def can_build(conn: sqlite3.Connection, seat_id: str) -> bool:
    """Has this seat used up its BUILDING budget? (It may still be able to review.)"""
    row = seat(conn, seat_id)
    if row is None or row["build_cap_cents"] is None:
        return True
    spent = conn.execute(
        "SELECT COALESCE(SUM(cost_cents),0) c FROM usage "
        "WHERE seat_id=? AND day=date('now') AND role='build'", (seat_id,)
    ).fetchone()["c"]
    return int(spent) < int(row["build_cap_cents"])
