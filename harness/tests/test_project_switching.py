"""Jobs build, review, and merge against the project named on their record.

Covers both the original build and the fixes from the wiring review that followed
a real run against a Jess clone (docs/sol/build_project_switching_prompt.txt and the
coordinator's follow-up list): the shared lane-file fallback, `cleanup_worktree`
touching the right repo, a default branch that's never "whatever happens to be
checked out", the projects-root containment guard, the give-form picker, the
OpenClaw dispatch path, `_make_worktree` checking out an explicit branch, and a
project's own CLAUDE.md riding into the builder prompt.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db.projects as projects_mod  # noqa: E402
import dispatch  # noqa: E402
import executor  # noqa: E402
import gatekeeper  # noqa: E402
import gauntlet  # noqa: E402
import server  # noqa: E402
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
from db.projects import ProjectRepoUnavailable, resolve_repo  # noqa: E402


SEATS = [
    Seat("riggs", "claude-cli", "builder-model", "claude", "subscription"),
    Seat("grinder_local", "ollama", "local-model", "qwen", "local"),
    Seat("brain", "codex", "review-model", "gpt", "subscription"),
    Seat("reviewer_metered", "xai", "second-review-model", "grok", "metered"),
]

CFG = {
    "seats": {},
    "gauntlet": {"reviewers": ["brain"], "min_model_families": 1},
}

GIT_ENV = {
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@chief.local",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@chief.local",
}


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True,
        env={**os.environ, **GIT_ENV, "HOME": str(repo)},
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _repo(path: Path, branch: str) -> Path:
    """A plain, ORIGIN-LESS repo. Only good for a branch literally named 'main' or
    'master' — `_default_branch` no longer trusts anything else without a real
    origin (see `_origin_repo` below)."""
    path.mkdir(exist_ok=True)
    _git(path, "init", "-q", "-b", branch)
    (path / "PROJECT_MARKER.txt").write_text(f"belongs to {path.name}\n")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "initial")
    return path


def _origin_repo(tmp_path: Path, name: str, branch: str) -> Path:
    """A repo with a REAL origin whose HEAD follows `branch` — the shape a real
    project checkout has, and the only shape `_default_branch` trusts for a
    default branch that isn't literally 'main' or 'master'. A live wiring review
    found the earlier version would happily return whatever the SOURCE repo's
    working tree happened to be sitting on, which is a different (and wrong) fact.
    """
    bare = tmp_path / f"{name}.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", branch, str(bare))
    seed = tmp_path / f"{name}-seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", branch)
    (seed / "PROJECT_MARKER.txt").write_text(f"belongs to {name}\n")
    _git(seed, "add", ".")
    _git(seed, "commit", "-q", "-m", "initial")
    _git(seed, "remote", "add", "origin", str(bare))
    _git(seed, "push", "-q", "origin", branch)
    clone = tmp_path / name
    _git(tmp_path, "clone", "-q", str(bare), str(clone))
    return clone


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "jobs.db")
    init_db(c)
    for configured in SEATS:
        upsert_seat(c, configured)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _projects_root_is_tmp(tmp_path, monkeypatch):
    """Every project resolved anywhere in this file must live under THIS test's
    tmp_path — the exact guard `resolve_repo` enforces in production (item 4 of the
    wiring review). This is what turns "a test forgot to repoint a project row"
    into a loud, immediate refusal instead of that test silently driving whatever
    the seeded 'chief'/'jess' rows point at on the real machine.
    """
    monkeypatch.setattr(projects_mod, "_projects_root", lambda: tmp_path)


def _point_project(conn, project_id: str, repo: Path) -> None:
    conn.execute(
        "UPDATE projects SET repo_path = ? WHERE id = ?", (str(repo), project_id),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# resolve_repo — path, branch, and every refusal
# ═══════════════════════════════════════════════════════════════════════════════
def test_resolve_repo_returns_each_projects_path_and_default_branch(conn, tmp_path):
    chief_repo = _repo(tmp_path / "chief-repo", "main")       # no origin needed for 'main'
    jess_repo = _origin_repo(tmp_path, "jess-repo", "trunk")  # a real non-main default
    _point_project(conn, "chief", chief_repo)
    _point_project(conn, "jess", jess_repo)

    assert resolve_repo(conn, "chief") == (chief_repo, "main")
    assert resolve_repo(conn, "jess") == (jess_repo, "trunk")


def test_arch_code_is_kept_at_arms_length(conn):
    message = (
        "That project is kept at arm's length — the team can read its notes but "
        "not touch its code."
    )
    with pytest.raises(ProjectRepoUnavailable, match=f"^{message}$"):
        resolve_repo(conn, "arch")


def test_missing_project_folder_is_refused(conn, tmp_path):
    _point_project(conn, "jess", tmp_path / "not-there")
    with pytest.raises(ProjectRepoUnavailable):
        resolve_repo(conn, "jess")


def test_non_repo_project_folder_is_refused(conn, tmp_path):
    folder = tmp_path / "ordinary-folder"
    folder.mkdir()
    _point_project(conn, "jess", folder)
    with pytest.raises(ProjectRepoUnavailable):
        resolve_repo(conn, "jess")


def test_resolve_repo_refuses_a_path_outside_the_projects_root(conn):
    """A real, valid git repo — refused ONLY because it sits outside the
    configured projects root (item 4), not because anything else about it is
    wrong. `tempfile.mkdtemp()` deliberately lands outside this test's own
    tmp_path (the root the autouse fixture pins), which is the whole point.
    """
    outside = Path(tempfile.mkdtemp())
    try:
        _repo(outside, "main")
        _point_project(conn, "jess", outside)
        with pytest.raises(ProjectRepoUnavailable, match="supposed to live"):
            resolve_repo(conn, "jess")
    finally:
        subprocess.run(["rm", "-rf", str(outside)], capture_output=True)


def test_resolve_repo_refuses_when_the_default_branch_cant_be_pinned_down(conn, tmp_path):
    """No origin, and the only branch that exists isn't 'main' or 'master' — a
    live wiring review found the earlier version would return this branch anyway
    (whatever happened to be checked out). Now it refuses instead of guessing.
    """
    repo = tmp_path / "no-origin-hotfix"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "hotfix")
    (repo / "f.txt").write_text("x\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "only commit")
    _point_project(conn, "jess", repo)

    with pytest.raises(
        ProjectRepoUnavailable,
        match="couldn't tell which branch is the main line of that project",
    ):
        resolve_repo(conn, "jess")


# ═══════════════════════════════════════════════════════════════════════════════
# run_job end to end — explicit project, Arch refused before isolation, legacy path
# ═══════════════════════════════════════════════════════════════════════════════
def _run_local_job(conn, monkeypatch, tmp_path, *, project_id):
    db_path = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    monkeypatch.setattr(executor, "DB_PATH", db_path)
    monkeypatch.setattr(executor, "WORKTREES", tmp_path / "worktrees")
    monkeypatch.setattr(executor, "CLONES", tmp_path / "clones")
    prompts = []

    def builder(_seat, prompt, _model):
        prompts.append(prompt)
        return "finished work\n"

    monkeypatch.setitem(executor._BUILDERS, "ollama", builder)
    job_id = create_job(
        conn, "build the requested thing", builder_seat="grinder_local",
        project_id=project_id,
    )
    set_status(conn, job_id, "in_progress")
    result = executor.run_job(job_id, cfg=None)
    return job_id, result, prompts


def test_explicit_project_job_uses_that_projects_code_and_names_it(
        conn, tmp_path, monkeypatch):
    jess_repo = _origin_repo(tmp_path, "jess-code", "trunk")
    (jess_repo / "CLAUDE.md").write_text(
        "# Jess conventions\n\nAlways write tests. Never touch billing directly.\n"
    )
    _point_project(conn, "jess", jess_repo)

    job_id, result, prompts = _run_local_job(
        conn, monkeypatch, tmp_path, project_id="jess",
    )

    assert result["status"] == "review"
    row = conn.execute("SELECT worktree FROM jobs WHERE id = ?", (job_id,)).fetchone()
    worktree = Path(row["worktree"])
    assert (worktree / "PROJECT_MARKER.txt").read_text() == "belongs to jess-code\n"
    detail = conn.execute(
        "SELECT detail FROM events WHERE job_id = ? AND kind = 'dispatched'", (job_id,),
    ).fetchone()["detail"]
    assert "Working in Jess's code" in detail
    assert "The task:\n\nbuild the requested thing" in prompts[0]
    # item 8: the project's OWN CLAUDE.md rides into the prompt even though jess
    # has no memory_dir configured.
    assert "Always write tests. Never touch billing directly." in prompts[0]


def test_arch_job_fails_before_any_isolated_copy_is_created(conn, tmp_path, monkeypatch):
    job_id, result, prompts = _run_local_job(
        conn, monkeypatch, tmp_path, project_id="arch",
    )
    message = (
        "That project is kept at arm's length — the team can read its notes but "
        "not touch its code."
    )

    assert result["status"] == "failed"
    row = conn.execute(
        "SELECT status, error FROM jobs WHERE id = ?", (job_id,),
    ).fetchone()
    assert (row["status"], row["error"]) == ("failed", message)
    assert not (tmp_path / "worktrees" / f"job-{job_id}").exists()
    assert not (tmp_path / "clones" / f"job-{job_id}").exists()
    assert prompts == []


def test_legacy_job_still_uses_the_harness_repo(conn, tmp_path, monkeypatch):
    source = _repo(tmp_path / "legacy-source", "main")
    (source / "harness").mkdir()
    monkeypatch.setattr(executor, "HARNESS", source / "harness")

    job_id, result, _prompts = _run_local_job(
        conn, monkeypatch, tmp_path, project_id=None,
    )

    assert result["status"] == "review"
    row = conn.execute("SELECT worktree FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert (Path(row["worktree"]) / "PROJECT_MARKER.txt").read_text() == (
        "belongs to legacy-source\n"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Lane memory — the REAL tree, seat-aware, path-shape validated
# ═══════════════════════════════════════════════════════════════════════════════
def test_lane_memory_prefers_project_file_then_shared_fallback():
    """Real files under harness/config/lanes/ — no tmp-created fallback. Proves the
    real project override wins over the real restored shared fallback (item 1),
    and both are seat-aware (looked up by 'riggs', never hardcoded).
    """
    jess_text = executor._lane_memory_text("jess", "riggs")
    assert "no conventions recorded yet" in jess_text.lower()

    shared_text = executor._lane_memory_text("a-project-with-no-lane-file", "riggs")
    assert "shared fallback" in shared_text.lower()
    assert shared_text != jess_text

    chief_text = executor._lane_memory_text("chief", "riggs")
    assert "chief command harness" in chief_text.lower()
    assert chief_text not in (jess_text, shared_text)


def test_lane_memory_refuses_a_path_shaped_project_or_seat_id():
    """project_id (and seat_id) become path segments — a value that doesn't look
    like a real id must never reach a path at all, not even to fail closed inside
    Path.read_text()'s own error handling."""
    assert executor._lane_memory_text("../../etc", "riggs") == ""
    assert executor._lane_memory_text("chief", "../../etc") == ""
    assert executor._lane_memory_text("", "riggs") == ""


def test_builder_prompt_orders_and_caps_project_background():
    memory = "§" * (executor.MEMORY_INDEX_MAX_CHARS + 100)
    conventions = "¶" * (executor.REPO_CONVENTIONS_MAX_CHARS + 50)
    prompt = executor._compose_builder_prompt(
        "original request", "lane rules", memory, conventions,
    )

    assert (prompt.index("lane rules") < prompt.index("¶" * 50)
            < prompt.index("§" * 100) < prompt.index("original request"))
    assert prompt.count("¶") == executor.REPO_CONVENTIONS_MAX_CHARS
    assert prompt.count("§") == executor.MEMORY_INDEX_MAX_CHARS


# ═══════════════════════════════════════════════════════════════════════════════
# cleanup_worktree — the PROJECT's own repo, and no stale entry left behind
# ═══════════════════════════════════════════════════════════════════════════════
def test_cleanup_worktree_leaves_no_stale_entry_in_the_projects_own_repo(
        conn, tmp_path, monkeypatch):
    project_repo = _repo(tmp_path / "jess-wt", "main")
    _point_project(conn, "jess", project_repo)
    monkeypatch.setattr(executor, "WORKTREES", tmp_path / "worktrees")

    job_id = create_job(conn, "work", builder_seat="grinder_local", project_id="jess")
    dest, note = executor._make_worktree(job_id, f"job/{job_id}", project_repo, "main")
    assert note == "worktree"
    assert dest.exists()
    assert str(dest) in _git(project_repo, "worktree", "list", "--porcelain")

    executor.cleanup_worktree(conn, job_id)

    assert not dest.exists()
    listing_after = _git(project_repo, "worktree", "list", "--porcelain")
    assert str(dest) not in listing_after, (
        "a stale worktree entry was left in the project's own repo"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# _make_worktree — checks out the RESOLVED branch, not the source's current HEAD
# ═══════════════════════════════════════════════════════════════════════════════
def test_make_worktree_checks_out_the_given_branch_not_whatever_is_checked_out(
        tmp_path, monkeypatch):
    monkeypatch.setattr(executor, "WORKTREES", tmp_path / "wt")
    repo = tmp_path / "two-branches"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "common.txt").write_text("common\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "root")
    # 'release' forks HERE, before 'on-main.txt' exists — so the two branches each
    # have a file the other one genuinely doesn't, and "checked out the wrong
    # branch" is actually distinguishable from "checked out the right one".
    _git(repo, "checkout", "-q", "-b", "release")
    (repo / "on-release.txt").write_text("release content\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "release commit")
    _git(repo, "checkout", "-q", "main")
    (repo / "on-main.txt").write_text("main content\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "main commit")
    # the SOURCE repo's working tree sits on 'main' right now — the bug this test
    # guards against is `_make_worktree` following THAT instead of the branch it
    # was actually told to use.

    dest, note = executor._make_worktree(9911, "job/9911", repo, "release")

    assert note == "worktree"
    assert (dest / "on-release.txt").exists()
    assert not (dest / "on-main.txt").exists()


# ═══════════════════════════════════════════════════════════════════════════════
# dispatch_local / dispatch() — project validation and stamping
# ═══════════════════════════════════════════════════════════════════════════════
def test_dispatch_validates_and_stamps_the_project(conn, monkeypatch):
    monkeypatch.setattr(gauntlet, "has_runner", lambda _provider: True)
    with pytest.raises(dispatch.DispatchRefused, match="unknown project"):
        dispatch.dispatch_local(
            conn, "build it", "grinder_local", cfg=CFG, start=False,
            project_id="unknown",
        )

    dispatched = dispatch.dispatch_local(
        conn, "build it", "grinder_local", cfg=CFG, start=False,
        project_id="jess",
    )
    row = conn.execute(
        "SELECT project_id FROM jobs WHERE id = ?", (dispatched.job_id,),
    ).fetchone()
    assert row["project_id"] == "jess"

    defaulted = dispatch.dispatch_local(
        conn, "build the default", "grinder_local", cfg=CFG, start=False,
    )
    row = conn.execute(
        "SELECT project_id FROM jobs WHERE id = ?", (defaulted.job_id,),
    ).fetchone()
    assert row["project_id"] == "chief"


def test_openclaw_dispatch_gets_the_same_project_validation_and_stamp(conn, monkeypatch):
    """Item 6: the OpenClaw path (`dispatch()`) has no in-process builder and no
    callers today, but a job's project must not depend on which entrance recorded
    it — so it gets the exact same door."""
    monkeypatch.setattr(dispatch, "_spawn", lambda row, request, blocking: "run-1")

    with pytest.raises(dispatch.DispatchRefused, match="unknown project"):
        dispatch.dispatch(conn, "build it", "riggs", CFG, project_id="unknown")

    d = dispatch.dispatch(conn, "build it", "riggs", CFG, project_id="jess")
    row = conn.execute(
        "SELECT project_id FROM jobs WHERE id = ?", (d.job_id,),
    ).fetchone()
    assert row["project_id"] == "jess"

    d2 = dispatch.dispatch(conn, "build the default", "riggs", CFG)
    row2 = conn.execute(
        "SELECT project_id FROM jobs WHERE id = ?", (d2.job_id,),
    ).fetchone()
    assert row2["project_id"] == "chief"


# ═══════════════════════════════════════════════════════════════════════════════
# /api/dispatch — friendly refusals BEFORE a job exists, project stamped on success
# ═══════════════════════════════════════════════════════════════════════════════
def _dispatch_client(conn, monkeypatch):
    db_path = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    monkeypatch.setattr(server, "DB", db_path)
    monkeypatch.setattr(server, "_SEATS_SYNCED", True)
    monkeypatch.setattr(dispatch, "load_config", lambda: CFG)
    monkeypatch.setattr(gauntlet, "has_runner", lambda _provider: True)
    monkeypatch.setattr(executor, "start_in_background", lambda *_a, **_kw: None)
    return TestClient(server.app)


def test_dispatch_endpoint_returns_friendly_error_and_stamps_valid_project(
        conn, monkeypatch):
    client = _dispatch_client(conn, monkeypatch)

    refused = client.post(
        "/api/dispatch",
        json={"text": "build it", "builder": "grinder_local", "project": "unknown"},
    )
    assert refused.status_code == 400
    assert refused.json()["error"] == "That project doesn't exist."

    accepted = client.post(
        "/api/dispatch",
        json={"text": "build it", "builder": "grinder_local", "project": "jess"},
    )
    assert accepted.status_code == 200
    job_id = accepted.json()["job_id"]
    row = conn.execute("SELECT project_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["project_id"] == "jess"
    assert "working in Jess" in accepted.json()["reply"]


def test_dispatch_endpoint_refuses_arch_with_the_arms_length_sentence_before_any_job(
        conn, monkeypatch):
    """Item 5: refused at the door (never a job that would only fail at build
    time)."""
    client = _dispatch_client(conn, monkeypatch)
    before = conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]

    refused = client.post(
        "/api/dispatch",
        json={"text": "build it", "builder": "grinder_local", "project": "arch"},
    )

    assert refused.status_code == 400
    assert refused.json()["error"] == (
        "That project is kept at arm's length — the team can read its notes "
        "but not touch its code."
    )
    after = conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
    assert after == before, "Arch must be refused before any job is ever recorded"


# ═══════════════════════════════════════════════════════════════════════════════
# Voice fallback — carries a named project; the live session does not (yet)
# ═══════════════════════════════════════════════════════════════════════════════
def test_fallback_voice_path_passes_a_named_project(conn, monkeypatch):
    db_path = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    monkeypatch.setattr(server, "DB", db_path)
    monkeypatch.setattr(server, "_SEATS_SYNCED", True)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    seen = {}

    def fake_ask(said, context="", pushed_back=False, project=""):
        seen.update(said=said, context=context, project=project)
        return {
            "spoken": "Got it.", "full": "Got it.", "model": "test-model",
            "failed": False,
        }

    monkeypatch.setattr(server, "ask_chief", fake_ask)
    response = TestClient(server.app).post(
        "/api/voice/ask", json={"said": "work on Jess", "project": "jess"},
    )

    assert response.status_code == 200
    assert seen["project"] == "jess"


# ═══════════════════════════════════════════════════════════════════════════════
# gatekeeper.merge — the PROJECT's own repo and branch, protected by the same
# projects-root guard every other test in this file relies on
# ═══════════════════════════════════════════════════════════════════════════════
def test_gatekeeper_merges_into_the_projects_own_branch(conn, tmp_path, monkeypatch):
    # `unrelated_repo` stands in for `gatekeeper.REPO` (what the legacy/no-project
    # path would use) — proving the merge happened in project_repo INSTEAD of here
    # is what shows resolution is actually per-job, not a fallback that happened to
    # work. Isolation for this test comes from TWO guards, not one: the explicit
    # `unrelated_repo` check below, AND the autouse `_projects_root_is_tmp` fixture
    # (item 4) — if `_point_project` below were ever forgotten, resolve_repo would
    # refuse rather than silently falling through to `gatekeeper.REPO` or the real
    # chief-command checkout.
    project_repo = _origin_repo(tmp_path, "jess-merge", "trunk")
    unrelated_repo = _repo(tmp_path / "unrelated", "main")
    _point_project(conn, "jess", project_repo)
    monkeypatch.setattr(gatekeeper, "REPO", unrelated_repo)

    job_id = create_job(
        conn, "the reviewed work", builder_seat="riggs", project_id="jess",
    )
    conn.execute(
        "UPDATE jobs SET required_reviews=2, required_review_families=2 WHERE id=?",
        (job_id,),
    )
    set_status(conn, job_id, "in_progress")
    _git(project_repo, "checkout", "-q", "-b", f"job/{job_id}")
    out = project_repo / "chief_output"
    out.mkdir()
    reviewed = "reviewed bytes\n"
    (out / f"job_{job_id}.txt").write_text(reviewed)
    _git(project_repo, "add", ".")
    _git(project_repo, "commit", "-q", "-m", "reviewed work")
    tip = _git(project_repo, "rev-parse", "HEAD")
    _git(project_repo, "checkout", "-q", "trunk")
    conn.execute(
        "UPDATE jobs SET branch = ?, result = ? WHERE id = ?",
        (f"job/{job_id}", reviewed, job_id),
    )
    set_head_version(conn, job_id, tip[:16])
    set_status(conn, job_id, "review")
    record_verdict(conn, job_id, "brain", verdict="pass", role="reviewer")
    record_verdict(
        conn, job_id, "reviewer_metered", verdict="pass", role="reviewer",
    )
    set_status(conn, job_id, "done")
    record_artifact(
        conn, job_id, "screenshot", path="/evidence/jess.png", captured_by="playwright",
    )
    record_verdict(
        conn, job_id, "reviewer_metered", verdict="pass", role="tester",
    )

    receipt = gatekeeper.merge(conn, job_id, asked_by="the panel")

    assert receipt.verb == "merge"
    assert _git(project_repo, "rev-parse", "--abbrev-ref", "HEAD") == "trunk"
    assert (project_repo / "chief_output" / f"job_{job_id}.txt").read_text() == reviewed
    assert not (unrelated_repo / "chief_output").exists()
