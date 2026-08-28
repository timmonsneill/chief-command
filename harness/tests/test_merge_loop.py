"""Certified work is exercised, judged from captured evidence, and then shipped.

These tests use a disposable project and stubbed models. They prove the whole handoff
without allowing a test run or merge to touch the real checkout.
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
    set_head_version,
    set_status,
    upsert_seat,
)

SEATS = [
    Seat("grinder_local", "ollama", "qwen2.5-coder:7b", "qwen", "local"),
    Seat("reviewer", "claude-cli", "claude-opus-4-8", "claude", "subscription"),
    Seat("brain", "codex", "gpt-5.6-sol", "gpt", "subscription"),
]

CFG = {
    "seats": {},
    "gauntlet": {
        "reviewers": ["reviewer", "brain"],
        "min_model_families": 2,
    },
}


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "t.db"
    c = connect(path)
    init_db(c)
    for configured_seat in SEATS:
        upsert_seat(c, configured_seat)
    return c, path


def _git(repo, *args):
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": os.environ["PATH"],
        "HOME": str(repo),
    }
    out = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, env=env
    )
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


def _job_on_branch(conn, repo, job_id, content, *, reviewed=None):
    """Commit the worker's output and record what the panel will read."""
    _git(repo, "checkout", "-q", "-b", f"job/{job_id}")
    out = repo / "chief_output"
    out.mkdir(exist_ok=True)
    (out / f"job_{job_id}.txt").write_text(content)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "work")
    sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    conn.execute(
        "UPDATE jobs SET branch = ?, result = ? WHERE id = ?",
        (f"job/{job_id}", content if reviewed is None else reviewed, job_id),
    )
    return sha


def _stub(verdicts):
    """Reviewer stubs keyed by model, matching the panel's existing test convention."""
    def runner(request, code, model):
        verdict = verdicts[model]
        reason = ("the completed run showed every test passing" if verdict == "pass" else
                  "the completed run did not show the tests passing")
        return verdict, reason
    return runner


def _wire(monkeypatch, runner):
    monkeypatch.setattr(
        gauntlet,
        "REVIEWERS",
        {"claude-cli": runner, "codex": runner},
    )


def _green_command(monkeypatch):
    monkeypatch.setattr(
        tester,
        "TEST_CMD",
        [sys.executable, "-c", "print('2 passed'); import sys; sys.exit(0)"],
    )


def _red_command(monkeypatch):
    monkeypatch.setattr(
        tester,
        "TEST_CMD",
        [sys.executable, "-c", "print('boom'); import sys; sys.exit(1)"],
    )


def _certified_job(conn, db_path, repo, monkeypatch, content="reviewed bytes\n"):
    passing = _stub({"claude-opus-4-8": "pass", "gpt-5.6-sol": "pass"})
    _wire(monkeypatch, passing)
    job = create_job(conn, "write the login form", builder_seat="grinder_local")
    conn.execute(
        "UPDATE jobs SET required_reviews=2, required_review_families=2 WHERE id=?",
        (job,),
    )
    set_status(conn, job, "in_progress")
    sha = _job_on_branch(conn, repo, job, content)
    version = sha[:16]
    set_head_version(conn, job, version)
    set_status(conn, job, "review")
    panel = gauntlet.run_panel(
        conn,
        job,
        "write the login form",
        content,
        version,
        CFG,
        db_path=db_path,
    )
    assert panel.certified is True
    conn.execute("UPDATE jobs SET worktree=? WHERE id=?", (str(repo), job))
    monkeypatch.setattr(executor, "DB_PATH", db_path)

    # The production run writes into the job's separate working copy. This fixture uses
    # one disposable project for both sides, so hide only its generated run record from
    # the clean-project check without changing the committed project.
    exclude = repo / ".git" / "info" / "exclude"
    exclude.write_text(exclude.read_text() + "\nchief_output/tester_job_*.log\n")
    return job, version


def _tester_verdicts(conn, job):
    return conn.execute(
        "SELECT * FROM verdicts WHERE job_id=? AND role='tester' ORDER BY id", (job,)
    ).fetchall()


def test_certified_green_work_is_shipped_and_merged(db, repo, monkeypatch):
    conn, db_path = db
    job, _ = _certified_job(conn, db_path, repo, monkeypatch)
    _green_command(monkeypatch)

    executor._after_certification(conn, job, CFG)

    row = conn.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()
    assert row["status"] == "shipped"
    assert (repo / "chief_output" / f"job_{job}.txt").read_text() == "reviewed bytes\n"


def test_a_real_failing_tester_judgement_leaves_plain_reason(db, repo, monkeypatch):
    conn, db_path = db
    job, _ = _certified_job(conn, db_path, repo, monkeypatch)
    _red_command(monkeypatch)
    _wire(monkeypatch, _stub({"claude-opus-4-8": "fail", "gpt-5.6-sol": "fail"}))

    executor._after_certification(conn, job, CFG)

    row = conn.execute(
        "SELECT status, spoken_summary FROM jobs WHERE id=?", (job,)
    ).fetchone()
    assert row["status"] == "done"
    assert "didn't confirm it runs" in row["spoken_summary"]
    for jargon in ("pytest", "exit code", "guard"):
        assert jargon not in row["spoken_summary"].lower()


def test_no_cross_family_runner_records_a_skip_and_no_tester_verdict(
        db, repo, monkeypatch):
    conn, db_path = db
    job, _ = _certified_job(conn, db_path, repo, monkeypatch)
    same_family_cfg = {
        "seats": {},
        "gauntlet": {"reviewers": ["grinder_local"], "min_model_families": 1},
    }

    executor._after_certification(conn, job, same_family_cfg)

    assert not _tester_verdicts(conn, job)
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0] == "done"
    skipped = conn.execute(
        "SELECT detail FROM events WHERE job_id=? AND kind='skipped' ORDER BY id DESC",
        (job,),
    ).fetchone()
    assert skipped is not None and "different family" in skipped["detail"]


def test_gatekeeper_refusal_becomes_the_current_plain_status(db, repo, monkeypatch):
    conn, db_path = db
    job, _ = _certified_job(conn, db_path, repo, monkeypatch, content="v1\n")
    _green_command(monkeypatch)

    _git(repo, "checkout", "-q", f"job/{job}")
    (repo / "chief_output" / f"job_{job}.txt").write_text("v2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "v2")
    _git(repo, "checkout", "-q", "main")

    executor._after_certification(conn, job, CFG)

    row = conn.execute(
        "SELECT status, spoken_summary FROM jobs WHERE id=?", (job,)
    ).fetchone()
    assert row["status"] == "done"
    assert "isn't the code that was reviewed" in row["spoken_summary"]
    assert not (repo / "chief_output" / f"job_{job}.txt").exists()


def test_tester_records_trusted_trace_and_versioned_verdict(db, repo, monkeypatch):
    conn, db_path = db
    job, version = _certified_job(conn, db_path, repo, monkeypatch)
    _green_command(monkeypatch)

    outcome = tester.run_tester_for_job(conn, job, CFG)

    assert outcome["passed"] is True
    artifact = conn.execute(
        "SELECT * FROM artifacts WHERE job_id=? ORDER BY id DESC", (job,)
    ).fetchone()
    assert artifact["kind"] == "trace"
    assert artifact["captured_by"] == "harness"
    assert artifact["path"] and Path(artifact["path"]).is_file()
    assert artifact["value"]
    verdict = _tester_verdicts(conn, job)[-1]
    assert verdict["role"] == "tester"
    assert verdict["reviewed_version"] == version


@pytest.mark.parametrize("missing_worktree", ["", "missing-directory"])
def test_missing_worktree_skips_without_a_verdict(
        db, repo, monkeypatch, missing_worktree):
    conn, db_path = db
    job, _ = _certified_job(conn, db_path, repo, monkeypatch)
    value = "" if not missing_worktree else str(repo.parent / missing_worktree)
    conn.execute("UPDATE jobs SET worktree=? WHERE id=?", (value, job))

    outcome = tester.run_tester_for_job(conn, job, CFG)

    assert outcome["ran"] is False
    assert not _tester_verdicts(conn, job)


def test_same_family_tester_is_rejected_before_any_verdict_write(
        db, repo, monkeypatch):
    conn, db_path = db
    job, _ = _certified_job(conn, db_path, repo, monkeypatch)
    attempted = {"value": False}

    def verdict_was_attempted(*args, **kwargs):
        attempted["value"] = True
        raise AssertionError("a same-family verdict write was attempted")

    monkeypatch.setattr(tester, "record_verdict", verdict_was_attempted)
    same_family_cfg = {
        "seats": {},
        "gauntlet": {"reviewers": ["grinder_local"], "min_model_families": 1},
    }

    outcome = tester.run_tester_for_job(conn, job, same_family_cfg)

    assert outcome["ran"] is False
    assert attempted["value"] is False
    assert not _tester_verdicts(conn, job)
