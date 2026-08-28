"""The worker. This is the thing that was missing — the reason nothing ran.

Dispatch (dispatch.py) decides a job MAY run and records it. This module is what
actually DOES it: it takes a queued job, runs the work on the assigned seat's real
model, writes down every step as it goes, and lands a real result. Before this file
existed, Chief said "putting Riggs on it" and no worker ever started.

Two hard rules, both structural rather than polite:

  1. THE WORK HAPPENS IN AN ISOLATED WORKTREE, never the live project. A builder gets
     its own private copy of the repo (a git worktree). It physically cannot scribble
     on main while it works — main is untouched until something is reviewed and merged
     by the one service allowed to merge. "One worktree per agent" (owner + Sol).

  2. LOCAL OUTPUT CANNOT COMPLETE ON ITS OWN. When the free local model finishes, the
     job parks at 'review'. The database guards (schema.sql) refuse to let it reach
     'done' without a higher-tier reviewer passing THIS version. We do not route
     around that here; we feed it.

Runs in a background thread so the app never blocks — the whole point of the
architecture. sessions come and go; the job row is the durable truth.
"""

from __future__ import annotations

import hashlib
import os
import json
import subprocess
import threading
import urllib.request
from pathlib import Path
from typing import Any

from db.jobs import (
    connect,
    seat,
    set_head_version,
    set_status,
)

HARNESS = Path(__file__).resolve().parent
DB_PATH = HARNESS / "db" / "chief.db"
WORKTREES = HARNESS / ".worktrees"       # gitignored; one subdir per job
OUTPUT_DIRNAME = "chief_output"          # where a builder drops standalone work

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"


# ---------------------------------------------------------------------------
# Event trail — this is what the app's activity view reads. Every step, live.
# ---------------------------------------------------------------------------
def _event(conn, job_id: int, seat_row, kind: str, detail: str = "", target: str = "") -> None:
    conn.execute(
        "INSERT INTO events (job_id, seat_id, lane, model, family, kind, target, detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, seat_row["id"], seat_row["id"], seat_row["model"],
         seat_row["family"], kind, target or None, detail or None),
    )


def _commit_in_worktree(wt: Path, job_id: int, branch: str, summary: str) -> str | None:
    """Commit the job's output on its own branch and return the commit id.

    This is what makes the gatekeeper's merge possible at all: it refuses unless the
    branch tip IS the reviewed version, and a version has to be a commit for that to be
    provable. If anything here fails, the caller falls back to a content hash — the job
    is still reviewable, it just can never be merged by the gatekeeper, which is the
    honest outcome for work that isn't in git.
    """
    env = {**os.environ,
           "GIT_AUTHOR_NAME": "chief-worker", "GIT_AUTHOR_EMAIL": "worker@chief.local",
           "GIT_COMMITTER_NAME": "chief-worker", "GIT_COMMITTER_EMAIL": "worker@chief.local"}
    def git(*args):
        return subprocess.run(["git", *args], cwd=wt, capture_output=True, text=True,
                              timeout=60, env=env)
    try:
        if git("rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
            return None
        if git("checkout", "-B", branch).returncode != 0:
            return None
        if git("add", "-A", OUTPUT_DIRNAME).returncode != 0:
            return None
        if git("commit", "-q", "-m", f"job {job_id}: {summary[:60]}").returncode != 0:
            return None
        sha = git("rev-parse", "HEAD").stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return None          # a hung git is "not in git", not a dead worker thread
    return sha[:16] if len(sha) >= 16 else None


def _version_of(text: str) -> str:
    """The exact content being put forward, as a short hash. This is what the whole
    review-to-version chain binds to: change the output, change the version, and every
    earlier approval silently stops counting (schema.sql, gate 3)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Worktree isolation — a builder never touches the live project.
# ---------------------------------------------------------------------------
def _make_worktree(job_id: int, branch: str) -> tuple[Path | None, str]:
    """Give this job its own private copy of the repo. Returns (path, note).

    If git worktrees aren't usable for any reason, fall back to a plain isolated
    directory and SAY SO — a silent fallback that quietly drops isolation would be
    exactly the kind of unspoken gap this project keeps getting bitten by.
    """
    WORKTREES.mkdir(exist_ok=True)
    dest = WORKTREES / f"job-{job_id}"
    if dest.exists():
        return dest, "reused"
    try:
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(dest)],
            cwd=HARNESS.parent, capture_output=True, text=True, timeout=60, check=True,
        )
        return dest, "worktree"
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        # Isolation still holds (separate dir, outside the repo tree) — it just isn't a
        # git worktree, so we record that honestly.
        dest.mkdir(parents=True, exist_ok=True)
        detail = getattr(exc, "stderr", "") or str(exc)
        return dest, f"plain-dir ({str(detail)[:60].strip()})"


def cleanup_worktree(job_id: int) -> None:
    """Remove a job's worktree once its work is merged or abandoned."""
    dest = WORKTREES / f"job-{job_id}"
    if not dest.exists():
        return
    subprocess.run(["git", "worktree", "remove", "--force", str(dest)],
                   cwd=HARNESS.parent, capture_output=True, text=True)
    if dest.exists():
        subprocess.run(["rm", "-rf", str(dest)], capture_output=True, text=True)


# ---------------------------------------------------------------------------
# The actual builders, one per provider. Add a provider = add a function.
# ---------------------------------------------------------------------------
def _ollama_build(seat_row, request: str, model: str) -> str:
    """The free local coder actually writes the code."""
    payload = json.dumps({"model": model, "prompt": request, "stream": False}).encode()
    req = urllib.request.Request(
        OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())["response"].strip()


_BUILDERS = {
    "ollama": _ollama_build,
}


# Reviewing lives in gauntlet.py (task #10). This module builds; that one judges.
# It used to hold a single hard-wired Claude reviewer, which was enough to close the
# loop and was never the design — one reviewer cannot show that a DIFFERENT MIND looked.


# ---------------------------------------------------------------------------
# The run loop — everything above, in order, for one job.
# ---------------------------------------------------------------------------
def _after_certification(conn, job_id: int, cfg: dict[str, Any]) -> None:
    """Once the panel certifies (result.certified), prove the work actually runs and
    ask the one component allowed to ship it. Pulled out of run_job so it can be
    exercised directly against a job already parked at 'done'.
    """
    import tester
    outcome = tester.run_tester_for_job(conn, job_id, cfg)
    if outcome.get("passed"):
        import gatekeeper
        try:
            answer = gatekeeper.handle(
                {"verb": "merge", "job_id": job_id, "asked_by": "the panel"},
                db_path=DB_PATH,
            )
        except Exception:  # noqa: BLE001 - asking must never crash the worker
            answer = {"ok": False, "error": "something went wrong asking to merge this"}
        if not answer.get("ok"):
            # The gatekeeper already wrote its own refusal onto the event trail. Put
            # the same plain-English reason where the voice reads current status.
            set_status(
                conn,
                job_id,
                "done",
                spoken_summary=str(answer.get("error") or "the gatekeeper said no")[:200],
            )
    elif outcome.get("ran"):
        set_status(
            conn,
            job_id,
            "done",
            spoken_summary=("Checked and certified, but the automated tests didn't "
                            "confirm it runs: "
                            f"{str(outcome.get('reason') or '')[:160]}"),
        )


def run_job(job_id: int, *, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Take one recorded job and actually do it. Safe to call in a thread.

    Opens its own database connection (sqlite + threads). WAL mode lets this write
    while the web server reads. Never raises to the caller — a worker that dies must
    leave a legible 'failed' row, not a stack trace nobody sees.
    """
    conn = connect(DB_PATH)
    try:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            return {"job_id": job_id, "status": "missing"}
        seat_row = seat(conn, job["builder_seat"])
        if seat_row is None:
            set_status(conn, job_id, "failed", error=f"unknown seat {job['builder_seat']}")
            return {"job_id": job_id, "status": "failed"}

        builder = _BUILDERS.get(seat_row["provider"])
        if builder is None:
            # A provider we don't have a local runner for (codex/xai/claude-cli builds
            # go through OpenClaw, not this in-process worker). Say so plainly.
            set_status(conn, job_id, "failed",
                       error=f"no in-process builder for provider '{seat_row['provider']}' "
                             "— this seat dispatches through OpenClaw")
            _event(conn, job_id, seat_row, "error",
                   "This worker only runs the local model directly.")
            return {"job_id": job_id, "status": "failed"}

        # 1. Isolate.
        wt, wt_note = _make_worktree(job_id, job["branch"] or f"job/{job_id}")
        conn.execute("UPDATE jobs SET worktree = ? WHERE id = ?", (str(wt), job_id))
        _event(conn, job_id, seat_row, "dispatched", f"Working in its own copy ({wt_note}).")

        # 2. Build.
        model = job["tier"] and _model_for(seat_row, job["tier"]) or seat_row["model"]
        _event(conn, job_id, seat_row, "thinking", "Working out how to do it.")
        try:
            result = builder(seat_row, job["request"], model)
        except Exception as exc:  # network, model, timeout — all land here
            set_status(conn, job_id, "failed", error=f"builder error: {exc}")
            _event(conn, job_id, seat_row, "error", "The worker hit a problem.")
            return {"job_id": job_id, "status": "failed"}

        # 3. Land the output in the isolated copy, and version it.
        out_dir = wt / OUTPUT_DIRNAME
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"job_{job_id}.txt").write_text(result)
        branch = job["branch"] or f"job/{job_id}"
        version = _commit_in_worktree(wt, job_id, branch, job["request"]) or _version_of(result)
        set_head_version(conn, job_id, version)
        _event(conn, job_id, seat_row, "write",
               f"Wrote {len(result)} characters of work.", target=str(out_dir))

        # 4. Park for review. The guards will hold it here until a real reviewer passes
        #    THIS version — we never force it past them.
        set_status(conn, job_id, "review", result=result)

        # 5. Hand the frozen bundle to the review panel — several families at once,
        #    all bound to THIS version. The panel decides nothing the database wouldn't;
        #    it produces the verdicts the guards ask for. Without a config there is no
        #    panel, and the job simply stays parked (the safe outcome, not a shortcut).
        if cfg is not None:
            import gauntlet
            panel = gauntlet.run_panel(conn, job_id, job["request"], result, version,
                                       cfg, db_path=DB_PATH)
            if panel.certified:
                _after_certification(conn, job_id, cfg)

        final = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()["status"]
        return {"job_id": job_id, "status": final, "version": version}
    finally:
        conn.close()


def _model_for(seat_row, tier: str) -> str:
    try:
        from tiering import resolve_model
        return resolve_model(seat_row, tier)[0]
    except Exception:
        return seat_row["model"]


# ---------------------------------------------------------------------------
# Fire-and-forget: start a worker in the background so the app stays responsive.
# ---------------------------------------------------------------------------
def start_in_background(job_id: int, *, cfg: dict[str, Any] | None = None) -> threading.Thread:
    t = threading.Thread(
        target=run_job, args=(job_id,), kwargs={"cfg": cfg},
        name=f"job-{job_id}", daemon=True,
    )
    t.start()
    return t
