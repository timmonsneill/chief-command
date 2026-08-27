"""The bridge: OpenClaw dispatches the work, the job store decides whether it may run.

This is the piece that turns three separate things (a pinned OpenClaw, a job store,
a seat config) into a system.

Design decision worth stating, because it drives everything else:

    THE JOB STORE IS THE SOURCE OF TRUTH, NOT A LISTENER.

We record the job BEFORE we spawn it, and we refuse to spawn if the store says no
(over budget, seat disabled). If we merely *observed* OpenClaw's events, work could
run that was never recorded — and OpenClaw's subagent sessions auto-archive after
~60 minutes and get soft-deleted, so anything we didn't write down is simply gone.
Spec §7 wants "what did the overnight run do?" to be answerable. That only works if
nothing can run without a row.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db.jobs import (  # noqa: E402
    Seat,
    attach_run,
    create_job,
    over_budget,
    seat,
    set_status,
    upsert_seat,
)

CONFIG = Path(__file__).resolve().parent / "config" / "seats.toml"


class DispatchRefused(RuntimeError):
    """The harness declined to dispatch. Budget, disabled seat, or a guard."""


# ---------------------------------------------------------------------------
# The REAL, in-process dispatch path (task #9).
#
# dispatch()/_spawn() below hand work to OpenClaw — the production path, for the
# paid coding seats. This one runs the free LOCAL model directly, in a background
# worker, so the whole spine can be exercised end-to-end today with no money and no
# exposed keys. Same recording, same guards, same worktree isolation.
# ---------------------------------------------------------------------------
@dataclass
class LocalDispatch:
    job_id: int
    seat_id: str
    reused: bool          # True = a duplicate key matched an existing job
    tier: str
    tier_reason: str


def dispatch_local(
    conn,
    request: str,
    builder_seat: str,
    *,
    cfg: dict[str, Any] | None = None,
    origin: str = "text",
    dispatch_key: str | None = None,
    start: bool = True,
) -> LocalDispatch:
    """Record a job and start a real local worker on it. Non-blocking.

    Order is load-bearing: we check the seat and the budget, dedupe, THEN write the
    row, THEN start the worker — the store is the source of truth, nothing runs
    without a row. Duplicate protection: a repeated dispatch_key returns the job that
    already exists instead of starting the work twice.

    The review requirements come from the GAUNTLET CONFIG, never from the caller
    (task #10). A caller that could say "one reviewer is enough" is the single-reviewer
    door this task exists to close.
    """
    # create_job / over_budget / seat are already imported at module level — re-importing
    # them here rebound them as LOCALS, which silently shadowed the module's own names.
    # Anything that swapped them (a test, a future wrapper) was ignored inside this
    # function while appearing to work everywhere else.
    from tiering import tier_for_build
    import executor

    cfg = load_config() if cfg is None else cfg
    roster, excluded = panel_roster(conn, cfg)
    families = int(cfg.get("gauntlet", {}).get("min_model_families", 0))
    builder_row = seat(conn, builder_seat)
    _refuse_a_panel_that_cannot_hold(conn, roster, families,
                                     builder_row["family"] if builder_row else None)

    # Duplicate protection — a retry must not start the same work twice.
    if dispatch_key:
        existing = conn.execute(
            "SELECT id, builder_seat, tier, tier_reason FROM jobs WHERE dispatch_key = ?",
            (dispatch_key,),
        ).fetchone()
        if existing is not None:
            return LocalDispatch(
                job_id=existing["id"], seat_id=existing["builder_seat"],
                reused=True, tier=existing["tier"] or "standard",
                tier_reason=existing["tier_reason"] or "",
            )

    row = seat(conn, builder_seat)
    if row is None:
        raise DispatchRefused(f"unknown seat: {builder_seat}")
    if not row["enabled"]:
        raise DispatchRefused(f"seat '{builder_seat}' is turned off")
    if over_budget(conn, builder_seat):
        raise DispatchRefused(f"seat '{builder_seat}' is over its budget for today")

    call = tier_for_build(request)

    # ONE transaction for the whole record of this dispatch. A job must never exist,
    # even for an instant, without its review requirements stamped on — in autocommit
    # that gap is a real row with a zero floor. The excluded-reviewer notes go in here
    # too: if writing them fails, the right outcome is no job at all, not a committed
    # job whose panel quietly shrank without saying so.
    #
    # BEGIN IMMEDIATE, not BEGIN: this reads before it writes, and a deferred
    # transaction that upgrades to a write after another connection has committed fails
    # instantly with a snapshot error that the busy-timeout does NOT retry.
    conn.execute("BEGIN IMMEDIATE")
    try:
        job_id = create_job(conn, request, builder_seat=builder_seat, origin=origin)
        conn.execute(
            "UPDATE jobs SET required_reviews = ?, required_review_families = ?, "
            "tier = ?, tier_reason = ?, dispatch_key = ?, branch = ? WHERE id = ?",
            (len(roster), families, call.tier, call.reason, dispatch_key,
             f"job/{job_id}", job_id),
        )
        # No silent caps: a reviewer left out of the panel is written down on the job.
        # (A roster name that isn't a seat at all has no row to hang an event on —
        # sync_seats refuses that config outright, the earlier and louder place.)
        for seat_id, why in excluded.items():
            row = seat(conn, seat_id)
            if row is None:
                continue
            conn.execute(
                "INSERT INTO events (job_id, seat_id, lane, model, family, kind, detail) "
                "VALUES (?,?,?,?,?,?,?)",
                (job_id, seat_id, seat_id, row["model"], row["family"], "skipped",
                 f"not on the panel: {why}"),
            )
        conn.execute("COMMIT")
    except Exception as exc:
        conn.execute("ROLLBACK")
        # Anything that goes wrong here is a refusal to dispatch, not a mystery 500 —
        # and above all not a committed job with no worker coming for it.
        raise DispatchRefused(f"could not record the job: {exc}") from exc
    set_status(conn, job_id, "in_progress")

    if start:
        executor.start_in_background(job_id, cfg=cfg)

    return LocalDispatch(
        job_id=job_id, seat_id=builder_seat, reused=False,
        tier=call.tier, tier_reason=call.reason,
    )


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def load_config(path: Path = CONFIG) -> dict[str, Any]:
    # seats.toml uses // comments for readability; TOML has no //, so strip them before
    # parsing. Must be string-aware: value strings hold `https://…` and `base_url` lines
    # end in `"…"  // note` — cutting at the first // blindly would eat the URL. So we
    # only strip a // that sits OUTSIDE a quoted string.
    raw = "\n".join(_strip_slash_comment(line) for line in path.read_text().splitlines())
    return tomllib.loads(raw)


def _strip_slash_comment(line: str) -> str:
    """Drop an inline/full-line `//` comment, ignoring `//` inside quotes."""
    quote = ""
    for i in range(len(line) - 1):
        ch = line[i]
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "/" and line[i + 1] == "/":
            return line[:i].rstrip()
    return line


def unresolved_reviewers(cfg: dict[str, Any]) -> list[str]:
    """Gauntlet reviewers that don't name a real seat. Empty == all resolve.

    A roster entry pointing at a seat that doesn't exist (the old `grinder_paid`) is a
    silent trap: `over_budget` treats the unknown seat as uncapped, the pre-check says
    "fine," then the verdict write rejects it. Catch it at startup instead.
    """
    seats = cfg.get("seats", {})
    return [r for r in cfg.get("gauntlet", {}).get("reviewers", []) if r not in seats]


def panel_roster(conn, cfg: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    """Who can actually sit on the panel right now — and who can't, and why.

    A roster entry is only real if the seat exists, is switched on, and we have a way to
    RUN it on this machine. A seat whose provider has no runner, or whose key is absent, is excluded; the
    codex seat needs its login. Counting those as panel members would produce a required
    panel that can never report, which fails closed but useless — every job parked
    forever. Counting them silently would be worse: a panel that shrank without saying
    so reads exactly like a full one.

    So: exclude them, name them, and let the FAMILY FLOOR (which cannot be lowered) be
    the thing that decides whether what's left is enough.
    """
    import gauntlet

    roster: list[str] = []
    excluded: dict[str, str] = {}
    for seat_id in cfg.get("gauntlet", {}).get("reviewers", []):
        row = seat(conn, seat_id)
        if row is None:
            excluded[seat_id] = "that reviewer isn't set up on this machine"
        elif not row["enabled"]:
            excluded[seat_id] = "that reviewer is turned off"
        elif not gauntlet.has_runner(row["provider"]):
            excluded[seat_id] = "we have no way to run that reviewer yet"
        else:
            roster.append(seat_id)
    return roster, excluded


def _refuse_a_panel_that_cannot_hold(conn, roster: list[str], floor: int,
                                     builder_family: str | None = None) -> None:
    """Refuse to dispatch work the panel could never certify.

    Fail CLOSED, but say so at DISPATCH — when it is one legible refusal — rather than
    at review time, where it becomes a job that parks forever and looks like a bug.
    """
    if floor < 1:
        raise DispatchRefused(
            "the review panel isn't configured (it needs at least one model family) "
            "— refusing to start work that nothing would check"
        )
    # Counted the way the database counts (migration 007): the author's own family is
    # not a second opinion. Counting it here admitted jobs the record then parked.
    families = {seat(conn, s)["family"] for s in roster} - {builder_family}
    if len(families) < floor:
        raise DispatchRefused(
            f"only {len(families)} model family/families can review right now and "
            f"{floor} are required — refusing to start work that could never be checked"
        )


def sync_seats(conn, cfg: dict[str, Any]) -> None:
    """Push the config's seats into the store, so guards can reason about them.

    Refuses a config whose gauntlet names a seat that doesn't exist, or a seat missing a
    family — both would blow up later, mid-panel, where it's far harder to see.
    """
    missing = unresolved_reviewers(cfg)
    if missing:
        raise DispatchRefused(f"gauntlet names seats that don't exist: {missing}")
    for seat_id, s in cfg.get("seats", {}).items():
        if "family" not in s:
            raise DispatchRefused(f"seat '{seat_id}' has no family — the gauntlet can't count it")
    for seat_id, s in cfg.get("seats", {}).items():
        upsert_seat(conn, Seat(
            id=seat_id,
            provider=s["provider"],
            model=s["model"],
            family=s["family"],
            tier=s["tier"],
            daily_cap_cents=s.get("daily_cap_cents"),
            build_cap_cents=s.get("build_cap_cents"),
            review_cap_cents=s.get("review_cap_cents"),
            enabled=not s.get("disabled", False),
            notes=s.get("notes", ""),
        ))


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
@dataclass
class Dispatch:
    job_id: int
    run_id: str | None
    seat_id: str


def dispatch(
    conn,
    request: str,
    builder_seat: str,
    cfg: dict[str, Any],
    origin: str = "text",
    blocking: bool = False,
) -> Dispatch:
    """Record the job, then spawn it on OpenClaw. In that order, always.

    Non-blocking by default — this is what lets the mouth stay responsive. OpenClaw's
    sessions_spawn returns a run id immediately, so the voice head can say "on it" and
    keep talking while the work grinds. A blocking dispatch here would reintroduce
    exactly the latency wound that killed v1.
    """
    row = seat(conn, builder_seat)
    if row is None:
        raise DispatchRefused(f"unknown seat: {builder_seat}")
    if not row["enabled"]:
        raise DispatchRefused(f"seat '{builder_seat}' is disabled")

    # OpenClaw core has NO per-agent budget of any kind. If we don't check here,
    # nothing does.
    if over_budget(conn, builder_seat):
        raise DispatchRefused(
            f"seat '{builder_seat}' is over its daily cap — refusing to dispatch"
        )

    # SAME roster question dispatch_local asks. This path used to count the raw config
    # list, so a job here was stamped as needing 3 reviewers when only 2 could ever run —
    # and then parked forever with nothing to explain it, instead of one legible refusal
    # at the door. Both entrances, one rule.
    gauntlet = cfg.get("gauntlet", {})
    roster, excluded = panel_roster(conn, cfg)
    required = len(roster)
    families = int(gauntlet.get("min_model_families", 0))
    _refuse_a_panel_that_cannot_hold(conn, roster, families, row["family"])

    # One transaction: the job must never exist for even a moment without its review
    # requirements stamped on (autocommit would otherwise leave a floor-0 gap).
    # IMMEDIATE because this reads before it writes — see dispatch_local.
    conn.execute("BEGIN IMMEDIATE")
    try:
        job_id = create_job(conn, request, builder_seat=builder_seat, origin=origin)
        conn.execute(
            "UPDATE jobs SET required_reviews = ?, required_review_families = ? WHERE id = ?",
            (required, families, job_id),
        )
        # No silent caps here either — same note dispatch_local writes.
        for seat_id, why in excluded.items():
            ex = seat(conn, seat_id)
            if ex is None:
                continue
            conn.execute(
                "INSERT INTO events (job_id, seat_id, lane, model, family, kind, detail) "
                "VALUES (?,?,?,?,?,?,?)",
                (job_id, seat_id, seat_id, ex["model"], ex["family"], "skipped",
                 f"not on the panel: {why}"),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    run_id = _spawn(row, request, blocking=blocking)
    attach_run(conn, job_id, run_id=run_id or "", branch=f"job/{job_id}")
    set_status(conn, job_id, "in_progress")

    return Dispatch(job_id=job_id, run_id=run_id, seat_id=builder_seat)


def _spawn(seat_row, request: str, blocking: bool) -> str | None:
    """Hand the work to OpenClaw.

    `openclaw agent` runs one turn via the Gateway. The model is chosen per-spawn,
    which is what lets a single orchestrator turn fan out across several families.
    """
    cmd = [
        "openclaw", "agent",
        "--model", f"{seat_row['provider']}/{seat_row['model']}",
        "--message", request,
    ]
    if not blocking:
        cmd.append("--detach")

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        raise DispatchRefused(f"openclaw refused the spawn: {proc.stderr.strip()[:300]}")

    # The gateway echoes a run id; fall back to the raw stdout if the shape changes.
    try:
        return json.loads(proc.stdout).get("runId")
    except (json.JSONDecodeError, AttributeError):
        return proc.stdout.strip()[:80] or None


# ---------------------------------------------------------------------------
# The gauntlet (spec §6)
# ---------------------------------------------------------------------------
def run_gauntlet(conn, job_id: int, cfg: dict[str, Any]) -> dict[str, Any]:
    """Fan the finished work out to the reviewer panel, in parallel, same bundle.

    This used to return a list of reviewer NAMES and launch nobody — the gauntlet was a
    plan, not a thing that ran (task #10). It now runs the panel and returns what
    actually happened: who reviewed, which families, what they said, and — if the job
    stayed parked — why, in plain words.

    The model-diversity rule (§6) still isn't asserted here; it's RECORDED. Every
    verdict snapshots the reviewing seat's family, and the schema refuses to complete a
    job without enough distinct families passing THIS version. So "two different minds
    looked at this" is a fact you can query, not a promise this function made.
    """
    import gauntlet as gauntlet_mod

    return gauntlet_mod.run_gauntlet_for_job(conn, job_id, cfg).as_dict()


def ship(conn, job_id: int, owner_confirmed: bool = False) -> None:
    """DO NOT CALL. Shipping belongs to the gatekeeper now (task #11).

    This function is the reason #11 exists: it was one import away from any agent, and
    it did the thing because it was called. `gatekeeper.merge()` does the same job and
    re-reads the record instead of trusting the caller.

    Kept as a refusal rather than deleted, so that anything still reaching for the old
    power gets a legible answer instead of an ImportError somebody "fixes" by
    reimplementing it locally.
    """
    raise DispatchRefused(
        "shipping is the gatekeeper's job now — ask it to merge (gatekeeper.merge), "
        "which checks the panel, the version and the families itself"
    )


# The original ship() implementation used to sit here, renamed. That was the mistake the
# comment above warns about, made in the same file: the new ship() refused politely while
# the old power stayed callable eight lines below it. Deleted. gatekeeper.merge() is the
# only route to 'shipped', and it re-reads the record instead of trusting the caller.


def morning_report(conn) -> list[dict[str, Any]]:
    """What shipped while you slept, and what didn't.

    Neill is out of the critical path, not out of the loop. This is the difference.
    """
    rows = conn.execute(
        """
        SELECT id, request, status, builder_seat, spoken_summary, finished_at
          FROM jobs
         WHERE created_at >= datetime('now', '-1 day')
         ORDER BY
           CASE status WHEN 'shipped' THEN 0 WHEN 'review' THEN 1
                       WHEN 'failed' THEN 2 ELSE 3 END,
           id
        """
    ).fetchall()
    return [dict(r) for r in rows]
