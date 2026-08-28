"""The smallest running-work check needed before certified work can ship.

The review panel judges the work it was handed, but shipping has a separate rule: a
different model family must judge evidence captured by the harness while the work is
actually exercised. This module creates that evidence, asks one eligible review seat
to judge only whether the run completed successfully, and records the answer against
the exact version already on the job. If either the run or the judge is unavailable,
it leaves the certified job alone and says so instead of inventing a result.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

import gauntlet
import gatekeeper
from db.jobs import (
    GuardViolation,
    over_budget,
    record_artifact,
    record_verdict,
    seat,
)

HARNESS = Path(__file__).resolve().parent
REPO = HARNESS.parent
VENV_PYTHON = REPO / ".venv" / "bin" / "python"
TEST_TIMEOUT_S = 600
TAIL_LINES = 40
DEFAULT_TEST_ESTIMATE_CENTS = 5

TEST_CMD = [str(VENV_PYTHON), "-m", "pytest", "harness/tests/", "-q"]


def _plain_summary(text: str, passed: bool) -> str:
    """Keep model-written status text suitable for the voice and activity view."""
    summary = re.sub(r"\s+", " ", str(text or "")).strip()
    replacements = (
        (r"\bpytest\b", "the automated tests"),
        (r"\bexit code\b", "run result"),
        (r"\bsubprocess\b", "test runner"),
        (r"\bguard violation\b", "record refusal"),
        (r"\bguard\b", "safety rule"),
    )
    for pattern, replacement in replacements:
        summary = re.sub(pattern, replacement, summary, flags=re.IGNORECASE)
    if summary:
        return summary[:280]
    return ("The automated tests completed successfully." if passed else
            "The automated tests did not show a successful completed run.")


def _event(conn, job, kind: str, detail: str, tester_row=None) -> None:
    """Add one plain-English tester update without allowing reporting to crash work."""
    if job is None:
        return
    try:
        row = tester_row or seat(conn, job["builder_seat"])
        if row is None:
            return
        conn.execute(
            "INSERT INTO events (job_id, seat_id, lane, model, family, kind, detail) "
            "VALUES (?,?,?,?,?,?,?)",
            (job["id"], row["id"], "tester", row["model"], row["family"], kind,
             detail),
        )
    except Exception:  # noqa: BLE001 - a status note must never crash the tester
        return


def _skip(conn, job, detail: str, reason: str, tester_row=None) -> dict[str, Any]:
    _event(conn, job, "skipped", detail, tester_row)
    return {"ran": False, "passed": False, "reason": reason}


def run_tester_for_job(conn, job_id: int, cfg: dict[str, Any]) -> dict[str, Any]:
    """Exercise a certified job and record one cross-family judgement of the run.

    Every failure mode is a stated skip or a real failing judgement. Nothing escapes
    to crash the worker, and a broken judge is never confused with broken work.
    """
    job = None
    tester_row = None
    try:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            return _skip(
                conn, job,
                "The automated tests had no recorded work to check.",
                "no recorded job to test",
            )

        worktree_text = str(job["worktree"] or "").strip()
        worktree = Path(worktree_text) if worktree_text else None
        if worktree is None or not worktree.is_dir():
            return _skip(
                conn, job,
                "The automated tests had no working copy to check.",
                "no working copy to test",
            )

        roster = cfg.get("gauntlet", {}).get("reviewers", [])
        if not isinstance(roster, list):
            roster = []
        for seat_id in roster:
            row = seat(conn, str(seat_id))
            if (row is not None and row["enabled"]
                    and row["family"] != job["builder_family"]
                    and gauntlet.has_runner(row["provider"])):
                tester_row = row
                break

        no_tester_detail = (
            "No available model from a different family could check whether the "
            "tests actually ran."
        )
        if tester_row is None:
            return _skip(conn, job, no_tester_detail, "no eligible tester seat")

        seat_id = tester_row["id"]
        if over_budget(conn, seat_id):
            return _skip(conn, job, no_tester_detail, "no eligible tester seat",
                         tester_row)
        estimate = DEFAULT_TEST_ESTIMATE_CENTS if tester_row["tier"] == "metered" else 0
        try:
            gatekeeper.spend(
                conn, seat_id, estimate, job_id=job_id, role="test",
                asked_by="the tester",
            )
        except gatekeeper.Refused:
            return _skip(conn, job, no_tester_detail, "no eligible tester seat",
                         tester_row)

        try:
            completed = subprocess.run(
                TEST_CMD,
                cwd=worktree,
                capture_output=True,
                text=True,
                timeout=TEST_TIMEOUT_S,
            )
            run_result = completed.returncode
            output = "\n".join(
                part for part in (completed.stdout, completed.stderr) if part
            )
        except subprocess.TimeoutExpired:
            run_result = -1
            output = "The automated tests took too long and were stopped."
        except OSError:
            run_result = -1
            output = "The automated tests could not start."

        command = shlex.join(str(part) for part in TEST_CMD)
        tail = "\n".join(output.splitlines()[-TAIL_LINES:])
        evidence_text = f"command: {command}\nexit code: {run_result}\n{tail}".rstrip()

        output_dir = worktree / "chief_output"
        output_dir.mkdir(parents=True, exist_ok=True)
        log_file = output_dir / f"tester_job_{job_id}.log"
        log_file.write_text(
            f"command: {command}\nexit code: {run_result}\n\n{output}",
            encoding="utf-8",
        )
        record_artifact(
            conn,
            job_id,
            "trace",
            path=str(log_file),
            value=f"exit code {run_result}\n{tail}",
            flow="tester",
            captured_by="harness",
        )

        request_text = (
            f"The job was: {job['request']}\n\n"
            "Judge ONLY whether this test run actually finished and shows the tests "
            "passing. A non-zero exit code, or output that does not show the suite "
            "completing, is a FAIL. Explain the answer in plain English without naming "
            "commands or tools and without using the phrase 'exit code'."
        )
        try:
            verdict, summary = gauntlet.REVIEWERS[tester_row["provider"]](
                request_text, evidence_text, tester_row["model"]
            )
        except (gauntlet.ReviewerBroke, subprocess.TimeoutExpired):
            return _skip(
                conn, job,
                "The model checking the automated tests could not finish.",
                "the model checking the tests could not finish",
                tester_row,
            )
        except Exception:  # noqa: BLE001 - a broken judge is a skip, never a verdict
            return _skip(
                conn, job,
                "The model checking the automated tests could not finish.",
                "the model checking the tests could not finish",
                tester_row,
            )

        verdict = "pass" if verdict == "pass" else "fail"
        summary = _plain_summary(summary, verdict == "pass")
        try:
            record_verdict(
                conn,
                job_id,
                tester_row["id"],
                verdict=verdict,
                summary=summary,
                role="tester",
                reviewed_version=job["head_version"],
            )
        except GuardViolation as exc:
            reason = str(exc).replace("guard: ", "").strip()
            reason = _plain_summary(reason, False)
            return _skip(conn, job, reason, reason, tester_row)

        _event(
            conn,
            job,
            "verdict" if verdict == "pass" else "error",
            ("The automated tests were checked and confirmed." if verdict == "pass" else
             "The automated tests were checked and something looked wrong."),
            tester_row,
        )
        return {
            "ran": True,
            "passed": verdict == "pass",
            "reason": summary,
            "seat": tester_row["id"],
        }
    except Exception:  # noqa: BLE001 - this function must never crash the worker
        return _skip(
            conn,
            job,
            "The automated tests could not be checked.",
            "the automated tests could not be checked",
            tester_row,
        )
