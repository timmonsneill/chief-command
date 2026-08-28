"""A harmless diagnostic run — NOT a tester (queue #4, reworked after review found
the earlier version laundering evidence).

What this file used to do: run the harness's OWN test suite and have a model judge
whether it passed, then record that judgement as a role='tester' verdict. Two
reviews (design + a bug-hunting pass) found this wrong on several counts, and all of
them are fixed by simply not doing it:

  1. THE SUITE BEING TESTED WAS NEVER THE JOB'S OWN WORK. It is the harness's fixed,
     unrelated test suite — green for every job, forever, regardless of what the job
     actually built. Recording that as guard 6's "someone drove the app" evidence is
     exactly the believable-green-check-on-code-nobody-reviewed failure mode this
     project exists to prevent.
  2. IT WOULD RUN BUILDER-WRITTEN CODE AS THE OWNER. Once a real code-writing builder
     lands, its branch could contain anything, and this module ran an arbitrary
     command inside that branch's own checkout with no sandbox.
  3. The exit code was never actually checked before treating a run as evidence of
     anything, the judge prompt was buildable out of job text AND the run's own
     stdout (both attacker/builder-controlled), and the run's log was written INSIDE
     `chief_output/`, the exact directory a retry's `git add -A` stages — so the
     tester's own diagnostic file could ride along into a future reviewed commit.

So: no verdict, ever, from this file, and nothing it does gates or feeds anything
downstream — `gauntlet._ask_to_ship` asks the gatekeeper to merge regardless of what
this module finds. What's left is an inert diagnostic: it runs ONLY when a job's
branch changes nothing relative to `main` but its own placeholder text output
(`chief_output/*.txt`, which is all any builder produces today — the moment a real
code builder lands, this stops running for it, on purpose, until it is rebuilt with an
actual sandbox), it never writes anything to disk (nothing to accidentally commit on a
retry), and what it records lands in artifact `kind`s the schema DELIBERATELY excludes
from guard 6 (`guard_tester_must_cite_artifacts` only accepts
screenshot/trace/video/dom_snapshot — 'exit_code' and 'stdout' can never satisfy it,
by construction, not by convention). A real tester — something that opens the running
app and drives it, from a different model family than the builder — is still unbuilt.
Until it exists, `guard_ship_requires_a_passing_tester` refuses every merge, and it is
refusing correctly; `gauntlet._ask_to_ship` says so in plain English rather than
hiding the wait behind a fake pass.
"""

from __future__ import annotations

import subprocess
from fnmatch import fnmatch
from pathlib import Path

from db.jobs import record_artifact

HARNESS = Path(__file__).resolve().parent
REPO = HARNESS.parent
VENV_PYTHON = REPO / ".venv" / "bin" / "python"
CHECK_TIMEOUT_S = 600
TAIL_LINES = 40                 # how much of the run's output actually lands in the
                                 # record — read via the module global, not a default
                                 # argument, so tests can monkeypatch TEST_CMD.

TEST_CMD = [str(VENV_PYTHON), "-m", "pytest", "harness/tests/", "-q"]


def _git(worktree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=worktree, capture_output=True,
                          text=True, timeout=30)


def _only_touches_text_output(worktree: Path) -> bool:
    """True only if this worktree's branch changes nothing, relative to `main`,
    except its own `chief_output/*.txt`.

    This is the one thing standing between "run a command in this directory" and
    running whatever a builder's branch happens to contain. Fail closed on anything
    unexpected — no git repo, no common history with main, a diff that can't be
    read — rather than guess that it's probably fine.
    """
    try:
        inside = _git(worktree, "rev-parse", "--is-inside-work-tree")
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return False
        base = _git(worktree, "merge-base", "main", "HEAD")
        if base.returncode != 0 or not base.stdout.strip():
            return False
        diff = _git(worktree, "diff", "--name-only", f"{base.stdout.strip()}..HEAD")
        if diff.returncode != 0:
            return False
    except (subprocess.TimeoutExpired, OSError):
        return False
    changed = [p.strip() for p in diff.stdout.splitlines() if p.strip()]
    return all(fnmatch(p, "chief_output/*.txt") for p in changed)


def record_smoke_check(conn, job_id: int) -> None:
    """Run the harness's own suite in the job's worktree and record what happened —
    ONLY as raw diagnostic artifacts, never as evidence of anything about the JOB.

    This never gates anything and never raises: whoever certified the job
    (`gauntlet.run_panel`) asks the gatekeeper to merge regardless of what this finds,
    because this has nothing to say about whether the job's own work runs — only
    whether the harness's own suite still imports in that checkout. A job with no
    worktree, a worktree whose branch touches anything beyond its own placeholder
    text output, or a check that can't run at all is silently skipped — there was
    never a verdict to withhold either way.
    """
    try:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            return
        worktree_text = str(job["worktree"] or "").strip()
        if not worktree_text:
            return
        worktree = Path(worktree_text)
        if not worktree.is_dir():
            return
        if not _only_touches_text_output(worktree):
            return

        try:
            completed = subprocess.run(
                TEST_CMD, cwd=worktree, capture_output=True, text=True,
                timeout=CHECK_TIMEOUT_S,
            )
            code = completed.returncode
            output = "\n".join(p for p in (completed.stdout, completed.stderr) if p)
        except (subprocess.TimeoutExpired, OSError):
            return          # the check itself failing to run says nothing about the job

        tail = "\n".join(output.splitlines()[-TAIL_LINES:]) or "(no output)"

        # Kinds the schema DELIBERATELY excludes from guard 6 (see the module
        # docstring). Never 'trace', never a verdict, never written to disk — nothing
        # here can be mistaken for someone having actually driven the app, and
        # nothing here can ride along into a future commit.
        record_artifact(conn, job_id, "exit_code", value=str(code),
                        flow="smoke_check", captured_by="harness")
        record_artifact(conn, job_id, "stdout", value=tail,
                        flow="smoke_check", captured_by="harness")
    except Exception:                          # noqa: BLE001 — purely diagnostic;
        return                                  # never worth losing the caller over
