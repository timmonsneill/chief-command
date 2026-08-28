"""Certified work always gets asked about, from whichever door it came through
(queue #4, reworked after design + bug-hunting review found the first pass laundering
evidence and missing an entry point).

Two properties matter here, neither of which the earlier version had:

  1. NOTHING SUBSTITUTES FOR A REAL TESTER. There isn't one yet (nothing here opens
     the running app and drives it from a different model family), so the gatekeeper
     refuses every merge on `guard_ship_requires_a_passing_tester` — correctly — and
     that refusal is spoken as one honest, FIXED sentence, never invented text.
  2. BOTH WAYS A JOB CAN BE CERTIFIED (`executor.run_job`'s panel call, and
     `dispatch.run_gauntlet` -> `gauntlet.run_gauntlet_for_job`) ask the gatekeeper,
     because the ask lives inside `gauntlet.run_panel` itself — the one function both
     paths call — not bolted onto just one of them.

These tests use a disposable git repo (never the real project — `gatekeeper.REPO` is
always monkeypatched) and stubbed reviewer models.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import executor  # noqa: E402
import gatekeeper  # noqa: E402
import gauntlet  # noqa: E402
import tester  # noqa: E402
from db.jobs import (  # noqa: E402
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
    Seat("grinder_local", "ollama", "qwen2.5-coder:7b", "qwen", "local"),
    Seat("reviewer", "claude-cli", "claude-opus-4-8", "claude", "subscription"),
    Seat("brain", "codex", "gpt-5.6-sol", "gpt", "subscription"),
    # NOT on CFG's panel roster — used only to record a real tester verdict, kept
    # deliberately off a seat the panel itself will also write to. `_review_one`'s
    # "already reviewed this version?" dedup check matches on (job, seat, version)
    # only, not role — a tester verdict pre-seeded on a PANEL seat would be picked up
    # as if that seat had already reviewed, and it would never get a real
    # role='reviewer' row, silently failing the family floor. Unrelated to this
    # branch's own change; sidestepped here rather than touched.
    Seat("grok", "xai", "grok-4.5", "grok", "metered", daily_cap_cents=100),
]

CFG = {
    "seats": {},
    "gauntlet": {"reviewers": ["reviewer", "brain"], "min_model_families": 2},
}


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "t.db"
    c = connect(path)
    init_db(c)
    for configured_seat in SEATS:
        upsert_seat(c, configured_seat)
    return c, path


@pytest.fixture(autouse=True)
def _isolated_worktrees(tmp_path, monkeypatch):
    """A GRANTED merge calls `executor.cleanup_worktree(job_id)`. Job ids in these
    tests are small sequential ints and could collide with a REAL leftover directory
    under the real `harness/.worktrees/` — this makes that structurally impossible for
    every test in this file, not just the ones that think to check.
    """
    monkeypatch.setattr(executor, "WORKTREES", tmp_path / ".worktrees")


def _git(repo, *args):
    env = {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": os.environ["PATH"], "HOME": str(repo),
    }
    out = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / "README").write_text("v0\n")
    _git(r, "add", ".")
    _git(r, "commit", "-q", "-m", "root")
    monkeypatch.setattr(gatekeeper, "REPO", r)
    return r


def _job_on_branch(conn, repo, job_id, content):
    """Commit the worker's output where the worker actually commits it, and record it
    as what the panel will read — same shape as test_gatekeeper.py's helper."""
    _git(repo, "checkout", "-q", "-b", f"job/{job_id}")
    out = repo / "chief_output"
    out.mkdir(exist_ok=True)
    (out / f"job_{job_id}.txt").write_text(content)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "work")
    sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    conn.execute("UPDATE jobs SET branch = ?, result = ? WHERE id = ?",
                (f"job/{job_id}", content, job_id))
    return sha


def _prep_job(conn, repo, content="reviewed bytes\n", builder_seat="grinder_local"):
    """A job parked at 'review', with a real branch on the disposable repo and a
    frozen version — everything short of the panel's own decision."""
    job = create_job(conn, "write the login form", builder_seat=builder_seat)
    conn.execute(
        "UPDATE jobs SET required_reviews=2, required_review_families=2 WHERE id=?",
        (job,),
    )
    set_status(conn, job, "in_progress")
    sha = _job_on_branch(conn, repo, job, content)
    version = sha[:16]
    set_head_version(conn, job, version)
    set_status(conn, job, "review")
    return job, version


def _stub(verdicts):
    """Reviewer stubs keyed by MODEL — the panel's existing test convention."""
    def runner(request, code, model):
        return verdicts[model], f"{verdicts[model]} — stub said so"
    return runner


def _wire(monkeypatch, runner):
    monkeypatch.setattr(gauntlet, "REVIEWERS", {"claude-cli": runner, "codex": runner})


def _both_pass(monkeypatch):
    _wire(monkeypatch, _stub({"claude-opus-4-8": "pass", "gpt-5.6-sol": "pass"}))


def _certify(conn, db_path, job, content, version):
    return gauntlet.run_panel(conn, job, "write the login form", content, version,
                              CFG, db_path=db_path)


# ═══════════════════════════════════════════════════════════════════════════════
# NO REAL TESTER YET — the gatekeeper refuses, and says so in one fixed sentence
# ═══════════════════════════════════════════════════════════════════════════════
def test_certified_without_a_tester_is_refused_with_the_fixed_sentence(db, repo, monkeypatch):
    conn, db_path = db
    _both_pass(monkeypatch)
    job, version = _prep_job(conn, repo)

    panel = _certify(conn, db_path, job, "reviewed bytes\n", version)

    assert panel.certified is True
    row = conn.execute("SELECT status, spoken_summary FROM jobs WHERE id=?", (job,)).fetchone()
    assert row["status"] == "done"
    assert row["spoken_summary"] == (
        "Checked and passed by 2 different models. It's waiting for someone to "
        "actually open and use it before it goes in."
    )
    # never merged
    assert not (repo / "chief_output" / f"job_{job}.txt").exists()


def test_the_refusal_lands_in_the_gate_log(db, repo, monkeypatch):
    conn, db_path = db
    _both_pass(monkeypatch)
    job, version = _prep_job(conn, repo)

    _certify(conn, db_path, job, "reviewed bytes\n", version)

    rows = conn.execute(
        "SELECT * FROM gate_log WHERE job_id=? AND verb='merge' ORDER BY id", (job,)
    ).fetchall()
    assert rows and rows[-1]["granted"] == 0
    assert "cross-family tester" in rows[-1]["detail"]


# ═══════════════════════════════════════════════════════════════════════════════
# BOTH DOORS ASK THE SAME QUESTION
# ═══════════════════════════════════════════════════════════════════════════════
def test_run_gauntlet_for_job_also_asks_the_gatekeeper(db, repo, monkeypatch):
    """dispatch.run_gauntlet's own path — the second way a job gets certified — must
    reach the exact same ask as executor.run_job's, because both call `run_panel`."""
    conn, db_path = db
    _both_pass(monkeypatch)
    job, version = _prep_job(conn, repo)

    r = gauntlet.run_gauntlet_for_job(conn, job, CFG, db_path=db_path)

    assert r.certified is True
    rows = conn.execute(
        "SELECT * FROM gate_log WHERE job_id=? AND verb='merge' ORDER BY id", (job,)
    ).fetchall()
    assert rows and rows[-1]["granted"] == 0
    row = conn.execute("SELECT spoken_summary FROM jobs WHERE id=?", (job,)).fetchone()
    assert row["spoken_summary"] == (
        "Checked and passed by 2 different models. It's waiting for someone to "
        "actually open and use it before it goes in."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# A REAL TESTER VERDICT (recorded directly, the way test_gatekeeper.py's real-merge
# tests do — this module no longer produces one) → shipped
# ═══════════════════════════════════════════════════════════════════════════════
def test_certified_with_a_real_tester_verdict_is_shipped(db, repo, monkeypatch):
    conn, db_path = db
    _both_pass(monkeypatch)
    job, version = _prep_job(conn, repo)
    record_artifact(conn, job, "screenshot", path="/evidence/login.png", captured_by="playwright")
    record_verdict(conn, job, "grok", verdict="pass", role="tester", reviewed_version=version)

    panel = _certify(conn, db_path, job, "reviewed bytes\n", version)

    assert panel.certified is True
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0] == "shipped"
    assert (repo / "chief_output" / f"job_{job}.txt").read_text() == "reviewed bytes\n"


def test_a_granted_merge_cleans_up_the_worktree(db, repo, monkeypatch):
    conn, db_path = db
    _both_pass(monkeypatch)
    job, version = _prep_job(conn, repo)
    record_artifact(conn, job, "screenshot", path="/e.png", captured_by="playwright")
    record_verdict(conn, job, "grok", verdict="pass", role="tester", reviewed_version=version)
    fake_wt = executor.WORKTREES / f"job-{job}"
    fake_wt.mkdir(parents=True)
    (fake_wt / "marker.txt").write_text("x")

    _certify(conn, db_path, job, "reviewed bytes\n", version)

    assert conn.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0] == "shipped"
    assert not fake_wt.exists()


def test_a_refused_merge_leaves_the_worktree_alone(db, repo, monkeypatch):
    conn, db_path = db
    _both_pass(monkeypatch)
    job, version = _prep_job(conn, repo)          # no tester verdict -> refused
    fake_wt = executor.WORKTREES / f"job-{job}"
    fake_wt.mkdir(parents=True)
    (fake_wt / "marker.txt").write_text("x")

    _certify(conn, db_path, job, "reviewed bytes\n", version)

    assert conn.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0] == "done"
    assert fake_wt.exists()


# ═══════════════════════════════════════════════════════════════════════════════
# ANY OTHER REFUSAL — a fixed sentence too, never the gatekeeper's raw words (which
# may legitimately name a file — fine for the event log, wrong for a glance-read line)
# ═══════════════════════════════════════════════════════════════════════════════
def test_a_non_tester_refusal_gets_the_other_fixed_sentence(db, repo, monkeypatch):
    conn, db_path = db
    _both_pass(monkeypatch)
    job, version = _prep_job(conn, repo, content="v1\n")

    # the branch moves AFTER the version froze — the case gatekeeper.merge exists to
    # refuse, and it fires before the tester check is ever reached.
    _git(repo, "checkout", "-q", f"job/{job}")
    (repo / "chief_output" / f"job_{job}.txt").write_text("v2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "v2")
    _git(repo, "checkout", "-q", "main")

    panel = _certify(conn, db_path, job, "v1\n", version)

    assert panel.certified is True
    row = conn.execute("SELECT status, spoken_summary FROM jobs WHERE id=?", (job,)).fetchone()
    assert row["status"] == "done"
    assert row["spoken_summary"] == gauntlet._OTHER_REFUSAL_SENTENCE
    assert not (repo / "chief_output" / f"job_{job}.txt").exists()   # main untouched


# ═══════════════════════════════════════════════════════════════════════════════
# THE SMOKE CHECK — harmless, inert, never a verdict, never gates anything
# ═══════════════════════════════════════════════════════════════════════════════
def _green_command(monkeypatch):
    monkeypatch.setattr(tester, "TEST_CMD",
                        [sys.executable, "-c", "print('2 passed'); import sys; sys.exit(0)"])


def test_smoke_check_records_only_exit_code_and_stdout(db, repo, monkeypatch):
    conn, db_path = db
    job, version = _prep_job(conn, repo)
    _git(repo, "checkout", "-q", f"job/{job}")
    conn.execute("UPDATE jobs SET worktree=? WHERE id=?", (str(repo), job))
    _green_command(monkeypatch)

    tester.record_smoke_check(conn, job)

    rows = conn.execute(
        "SELECT kind, value, captured_by FROM artifacts WHERE job_id=? ORDER BY id", (job,)
    ).fetchall()
    assert {r["kind"] for r in rows} == {"exit_code", "stdout"}
    assert all(r["captured_by"] == "harness" for r in rows)
    assert [r["value"] for r in rows if r["kind"] == "exit_code"][0] == "0"
    # never a verdict, never written to disk (the exact bug that let a retry's
    # `git add -A chief_output` sweep the tester's own log into a reviewed commit)
    assert not conn.execute(
        "SELECT 1 FROM verdicts WHERE job_id=? AND role='tester'", (job,)
    ).fetchone()
    assert not (repo / "chief_output" / f"tester_job_{job}.log").exists()
    _git(repo, "checkout", "-q", "main")


def test_smoke_check_skips_when_the_branch_touches_anything_else(db, repo, monkeypatch):
    conn, db_path = db
    job, version = _prep_job(conn, repo)
    _git(repo, "checkout", "-q", f"job/{job}")
    (repo / "unexpected.py").write_text("import os\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "an extra file the panel never saw")
    conn.execute("UPDATE jobs SET worktree=? WHERE id=?", (str(repo), job))
    monkeypatch.setattr(
        tester, "TEST_CMD",
        [sys.executable, "-c", "print('should not run'); import sys; sys.exit(0)"])

    tester.record_smoke_check(conn, job)

    assert not conn.execute("SELECT 1 FROM artifacts WHERE job_id=?", (job,)).fetchone()
    _git(repo, "checkout", "-q", "main")


def test_smoke_check_skips_silently_with_no_worktree(db):
    conn, db_path = db
    job = create_job(conn, "write the login form", builder_seat="grinder_local")
    tester.record_smoke_check(conn, job)     # worktree column is NULL
    assert not conn.execute("SELECT 1 FROM artifacts WHERE job_id=?", (job,)).fetchone()


def test_smoke_check_never_gates_the_ask_to_ship(db, repo, monkeypatch):
    """A red smoke-check result still leads to the SAME ask (and the SAME refusal, for
    the SAME reason) as a green one — it has no say in what happens next."""
    conn, db_path = db
    job, version = _prep_job(conn, repo)
    _git(repo, "checkout", "-q", f"job/{job}")
    conn.execute("UPDATE jobs SET worktree=? WHERE id=?", (str(repo), job))
    monkeypatch.setattr(
        tester, "TEST_CMD",
        [sys.executable, "-c", "print('boom'); import sys; sys.exit(1)"])
    tester.record_smoke_check(conn, job)      # what executor.run_job would have done
    _git(repo, "checkout", "-q", "main")
    _both_pass(monkeypatch)

    panel = _certify(conn, db_path, job, "reviewed bytes\n", version)

    assert panel.certified is True
    row = conn.execute("SELECT status, spoken_summary FROM jobs WHERE id=?", (job,)).fetchone()
    assert row["status"] == "done"
    assert row["spoken_summary"] == (
        "Checked and passed by 2 different models. It's waiting for someone to "
        "actually open and use it before it goes in."
    )
    exit_row = conn.execute(
        "SELECT value FROM artifacts WHERE job_id=? AND kind='exit_code'", (job,)
    ).fetchone()
    assert exit_row["value"] == "1"          # recorded, and irrelevant to the outcome
