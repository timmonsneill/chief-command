"""Real code builders stop at a reviewed candidate, behind the task #9 safety wall.

Every repository and database here is disposable. Nothing invokes a real model,
touches the live record, or changes the real project's branches.
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dispatch  # noqa: E402
import executor  # noqa: E402
import gatekeeper  # noqa: E402
import gauntlet  # noqa: E402
import git_policy  # noqa: E402
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
    Seat("riggs", "claude-cli", "claude-opus-4-8", "claude", "subscription"),
    Seat("grinder_local", "ollama", "qwen2.5-coder:7b", "qwen", "local"),
    Seat("brain", "codex", "gpt-5.6-sol", "gpt", "subscription"),
    Seat("grok", "xai", "grok-4.5", "grok", "metered", daily_cap_cents=100),
]

CFG = {
    "seats": {},
    "gauntlet": {"reviewers": ["brain", "grok"], "min_model_families": 2},
}

GIT_ENV = {
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@chief.local",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@chief.local",
}


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, **GIT_ENV, "HOME": str(repo)}
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _new_repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    (path / "README.md").write_text("start\n")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "initial")
    return path


@pytest.fixture()
def repo(tmp_path):
    return _new_repo(tmp_path / "repo")


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    init_db(conn)
    for configured_seat in SEATS:
        upsert_seat(conn, configured_seat)
    yield conn
    conn.close()


def _diff(repo: Path, base: str, tip: str) -> str:
    proc = subprocess.run(
        ["git", "diff", f"{base}..{tip}"], cwd=repo,
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _new_diff_job(conn) -> int:
    cur = conn.execute(
        "INSERT INTO jobs "
        "(request, builder_seat, builder_tier, builder_family, bundle_kind) "
        "VALUES ('build it', 'riggs', 'subscription', 'claude', 'diff')"
    )
    return int(cur.lastrowid)


def _approve_diff_job(conn, job_id: int, version: str) -> None:
    conn.execute(
        "UPDATE jobs SET required_reviews=2, required_review_families=2 WHERE id=?",
        (job_id,),
    )
    set_head_version(conn, job_id, version)
    set_status(conn, job_id, "review")
    record_verdict(conn, job_id, "brain", verdict="pass", role="reviewer")
    record_verdict(conn, job_id, "grok", verdict="pass", role="reviewer")
    set_status(conn, job_id, "done")
    record_artifact(
        conn, job_id, "screenshot", path="/evidence/candidate.png",
        captured_by="playwright",
    )
    record_verdict(conn, job_id, "grok", verdict="pass", role="tester")


# ---------------------------------------------------------------------------
# Builder command lockdown
# ---------------------------------------------------------------------------
def test_builder_lockdown_accepts_the_real_argv():
    executor.assert_builders_locked_down()


@pytest.mark.parametrize("missing", ["--restricted", "--safe-mode", "--strict-mcp-config"])
def test_builder_lockdown_names_a_missing_flag(monkeypatch, missing):
    real = executor._claude_build_argv

    def without_one(model, clone, settings_path):
        return [arg for arg in real(model, clone, settings_path) if arg != missing]

    monkeypatch.setattr(executor, "_claude_build_argv", without_one)
    with pytest.raises(RuntimeError, match=missing):
        executor.assert_builders_locked_down()


# ---------------------------------------------------------------------------
# Commit-time refusal list
# ---------------------------------------------------------------------------
def _assert_commit_refused(repo: Path, reason: str) -> None:
    before = _git(repo, "rev-parse", "HEAD")
    result = executor._commit_diff_in_clone(repo, 7, "job/7", "test candidate")
    assert result.sha is None
    assert reason in result.reason
    assert _git(repo, "rev-parse", "HEAD") == before


def test_binary_file_is_refused_without_a_commit(repo):
    (repo / "payload.bin").write_bytes(b"plain prefix\x00binary body")
    _assert_commit_refused(repo, "binary file")


def test_symlink_is_refused_without_a_commit(repo):
    os.symlink("README.md", repo / "shortcut")
    _assert_commit_refused(repo, "symlink")


def test_executable_bit_change_is_refused_without_a_commit(repo):
    os.chmod(repo / "README.md", 0o755)
    _assert_commit_refused(repo, "executable bit")


def test_gitattributes_is_refused_without_a_commit(repo):
    (repo / ".gitattributes").write_text("*.secret -diff\n")
    _assert_commit_refused(repo, "future diffs")


def test_nothing_changed_is_refused_without_a_commit(repo):
    _assert_commit_refused(repo, "nothing changed")


def test_shared_policy_also_flags_submodules_and_git_components():
    raw = [
        ":000000 160000 0000000 1234567 A\tvendor/library",
        ":000000 100644 0000000 1234567 A\tnested/.git/hooks/check",
    ]
    assert git_policy.disallowed_paths(raw, []) == [
        "vendor/library (a submodule)",
        "nested/.git/hooks/check (inside .git)",
    ]


def test_plain_text_commit_binds_the_full_sha_to_the_job(repo, db):
    (repo / "candidate.py").write_text("answer = 42\n")
    committed = executor._commit_diff_in_clone(repo, 8, "job/8", "plain text")
    assert committed.sha is not None
    assert len(committed.sha) == 40
    assert set(committed.sha) <= set("0123456789abcdef")

    job_id = create_job(db, "plain text", builder_seat="grinder_local")
    set_head_version(db, job_id, committed.sha)
    row = db.execute("SELECT head_version FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["head_version"] == committed.sha


# ---------------------------------------------------------------------------
# Bundle shape is stamped at dispatch and then frozen
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("builder_seat", "expected"),
    [("riggs", "diff"), ("grinder_local", "text")],
)
def test_dispatch_stamps_the_builder_bundle_shape(db, monkeypatch, builder_seat, expected):
    monkeypatch.setattr(gauntlet, "has_runner", lambda provider: True)
    dispatched = dispatch.dispatch_local(
        db, "build a candidate", builder_seat, cfg=CFG, start=False,
    )
    row = db.execute(
        "SELECT bundle_kind FROM jobs WHERE id=?", (dispatched.job_id,),
    ).fetchone()
    assert row["bundle_kind"] == expected


def test_bundle_shape_cannot_be_rewritten(db):
    job_id = create_job(db, "write text", builder_seat="grinder_local")
    with pytest.raises(sqlite3.IntegrityError, match="guard:"):
        db.execute("UPDATE jobs SET bundle_kind='diff' WHERE id=?", (job_id,))


# ---------------------------------------------------------------------------
# Merge-time diff fidelity
# ---------------------------------------------------------------------------
def _candidate_branch(conn, repo: Path, job_id: int) -> tuple[str, str, str]:
    _git(repo, "checkout", "-q", "-b", f"job/{job_id}")
    (repo / "candidate.py").write_text("def ready():\n    return True\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "candidate")
    tip = _git(repo, "rev-parse", "HEAD")
    base = _git(repo, "merge-base", "main", tip)
    bundle = _diff(repo, base, tip)
    _git(repo, "checkout", "-q", "main")
    conn.execute(
        "UPDATE jobs SET branch=?, result=? WHERE id=?",
        (f"job/{job_id}", bundle, job_id),
    )
    return base, tip, bundle


def test_diff_bundle_that_exactly_matches_the_branch_can_merge(db, repo, monkeypatch):
    monkeypatch.setattr(gatekeeper, "REPO", repo)
    job_id = _new_diff_job(db)
    set_status(db, job_id, "in_progress")
    _base, tip, _bundle = _candidate_branch(db, repo, job_id)
    _approve_diff_job(db, job_id, tip)

    receipt = gatekeeper.merge(db, job_id, asked_by="test")

    assert receipt.verb == "merge"
    assert db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0] == "shipped"
    assert (repo / "candidate.py").read_text() == "def ready():\n    return True\n"


def test_diff_bundle_mismatch_is_refused_without_moving_main(db, repo, monkeypatch):
    monkeypatch.setattr(gatekeeper, "REPO", repo)
    job_id = _new_diff_job(db)
    set_status(db, job_id, "in_progress")
    _base, _first_tip, reviewed_bundle = _candidate_branch(db, repo, job_id)

    _git(repo, "checkout", "-q", f"job/{job_id}")
    (repo / "candidate.py").write_text("def ready():\n    return False\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "unreviewed follow-up")
    moved_tip = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    main_before = _git(repo, "rev-parse", "main")

    db.execute("UPDATE jobs SET result=? WHERE id=?", (reviewed_bundle, job_id))
    _approve_diff_job(db, job_id, moved_tip)

    with pytest.raises(gatekeeper.Refused, match="doesn't match what the reviewers"):
        gatekeeper.merge(db, job_id, asked_by="test")
    assert _git(repo, "rev-parse", "main") == main_before


# ---------------------------------------------------------------------------
# Certified diff jobs park for the owner; they never ask to merge
# ---------------------------------------------------------------------------
def test_certified_diff_job_stops_at_done_with_the_fixed_sentence(db, tmp_path, monkeypatch):
    job_id = _new_diff_job(db)
    set_status(db, job_id, "in_progress")
    db.execute(
        "UPDATE jobs SET required_reviews=1, required_review_families=1 WHERE id=?",
        (job_id,),
    )
    set_head_version(db, job_id, "a" * 40)
    set_status(db, job_id, "review", result="diff text")
    record_verdict(db, job_id, "brain", verdict="pass", role="reviewer")
    set_status(db, job_id, "done")

    def must_not_merge(*args, **kwargs):
        raise AssertionError("a diff candidate asked to merge")

    monkeypatch.setattr(gatekeeper, "handle", must_not_merge)
    gauntlet._ask_to_ship(db, job_id, CFG, tmp_path / "jobs.db")

    row = db.execute(
        "SELECT status, spoken_summary FROM jobs WHERE id=?", (job_id,),
    ).fetchone()
    assert row["status"] == "done"
    assert row["spoken_summary"] == (
        "Checked and passed by 1 different models. The changes are ready for you "
        "to read before anything goes in."
    )


# ---------------------------------------------------------------------------
# Process environment and standalone-clone isolation
# ---------------------------------------------------------------------------
def test_builder_process_receives_only_path_and_home(tmp_path, monkeypatch):
    clone = tmp_path / "clone"
    clone.mkdir()
    seen = {}

    monkeypatch.setenv("XAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")

    def fake_run(argv, **kwargs):
        seen["env"] = kwargs["env"]
        return SimpleNamespace(returncode=0, stdout='{"is_error": false, "result": "ok"}', stderr="")

    monkeypatch.setattr(executor.subprocess, "run", fake_run)
    executor._claude_cli_build(None, "make a small change", "probe-model", clone)

    assert set(seen["env"]) == {"PATH", "HOME"}
    assert "XAI_API_KEY" not in seen["env"]
    assert "OPENAI_API_KEY" not in seen["env"]


def test_fake_builder_writes_a_real_text_candidate_that_the_harness_commits(tmp_path,
                                                                            monkeypatch):
    repo = _new_repo(tmp_path / "repo")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake = fake_bin / "claude"
    fake.write_text(
        "#!/bin/sh\n"
        "printf 'answer = 42\\n' > candidate.py\n"
        "printf '{\"is_error\": false, \"result\": \"ok\"}\\n'\n"
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")

    output = executor._claude_cli_build(None, "write the answer", "probe-model", repo)
    committed = executor._commit_diff_in_clone(repo, 42, "job/42", "write the answer")

    assert '"is_error": false' in output
    assert (repo / "candidate.py").read_text() == "answer = 42\n"
    assert committed.sha is not None and len(committed.sha) == 40


def test_make_clone_has_its_own_git_directory_and_refuses_reuse(tmp_path, monkeypatch):
    source = _new_repo(tmp_path / "source")
    (source / "harness").mkdir()
    (source / "harness" / ".keep").write_text("test fixture\n")
    _git(source, "add", ".")
    _git(source, "commit", "-q", "-m", "add harness")

    clone_root = tmp_path / "clones"
    monkeypatch.setattr(executor, "HARNESS", source / "harness")
    monkeypatch.setattr(executor, "CLONES", clone_root)

    clone, note = executor._make_clone(41, "job/41")
    assert note == "clone"
    assert clone is not None
    assert (clone / ".git").is_dir()
    assert _git(clone, "rev-parse", "--git-common-dir") == ".git"

    again, reason = executor._make_clone(41, "job/41")
    assert again is None
    assert "already exists" in reason
