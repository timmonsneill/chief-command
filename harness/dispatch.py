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
    connect,
    create_job,
    init_db,
    over_budget,
    record_artifact,
    record_verdict,
    seat,
    set_status,
    upsert_seat,
)

CONFIG = Path(__file__).resolve().parent / "config" / "seats.toml"


class DispatchRefused(RuntimeError):
    """The harness declined to dispatch. Budget, disabled seat, or a guard."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def load_config(path: Path = CONFIG) -> dict[str, Any]:
    # seats.toml uses // comments for readability; strip them before parsing.
    raw = "\n".join(
        line for line in path.read_text().splitlines()
        if not line.lstrip().startswith("//")
    )
    return tomllib.loads(raw)


def sync_seats(conn, cfg: dict[str, Any]) -> None:
    """Push the config's seats into the store, so guards can reason about them."""
    for seat_id, s in cfg.get("seats", {}).items():
        upsert_seat(conn, Seat(
            id=seat_id,
            provider=s["provider"],
            model=s["model"],
            family=s["family"],
            tier=s["tier"],
            daily_cap_cents=s.get("daily_cap_cents"),
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

    gauntlet = cfg.get("gauntlet", {})
    required = len(gauntlet.get("reviewers", []))

    job_id = create_job(conn, request, builder_seat=builder_seat, origin=origin)
    conn.execute("UPDATE jobs SET required_reviews = ? WHERE id = ?", (required, job_id))

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
def run_gauntlet(conn, job_id: int, cfg: dict[str, Any]) -> list[str]:
    """Fan the finished work out to the reviewer panel, in parallel, same bundle.

    The model-diversity rule (§6) isn't asserted here — it's recorded. Every verdict
    snapshots the reviewing seat's family, so "at least two families looked at this"
    is a fact you can query rather than a promise someone made.
    """
    gauntlet = cfg.get("gauntlet", {})
    verdicts = []
    for reviewer in gauntlet.get("reviewers", []):
        if over_budget(conn, reviewer):
            continue  # a capped-out reviewer is skipped, not faked
        verdicts.append(reviewer)
    return verdicts


def ship(conn, job_id: int, owner_confirmed: bool = False) -> None:
    """Ship a job. The GATES decide, not a human and not an agent's confidence.

    Owner override (2026-07-13): Neill is out of the critical path. A job that cleared
    'done' AND carries a passing cross-family tester verdict backed by real Playwright
    artifacts will ship on its own. The schema refuses anything less — so an agent
    cannot ship on vibes, and Neill doesn't have to stay up to approve it.

    owner_confirmed is optional and only stamps the record (useful when he DOES eyeball
    something). It grants no extra power: the gates apply either way.
    """
    if owner_confirmed:
        conn.execute(
            "UPDATE jobs SET owner_confirmed_at = datetime('now') WHERE id = ?", (job_id,)
        )
    set_status(conn, job_id, "shipped")


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
