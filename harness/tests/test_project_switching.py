"""Jobs build, review, and merge against the project named on their record."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
    path.mkdir()
    _git(path, "init", "-q", "-b", branch)
    (path / "PROJECT_MARKER.txt").write_text(f"belongs to {path.name}\n")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "initial")
    return path


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "jobs.db")
    init_db(c)
    for configured in SEATS:
        upsert_seat(c, configured)
    yield c
    c.close()


def _point_project(conn, project_id: str, repo: Path) -> None:
    conn.execute(
        "UPDATE projects SET repo_path = ? WHERE id = ?", (str(repo), project_id),
    )


def test_resolve_repo_returns_each_projects_path_and_default_branch(conn, tmp_path):
    chief_repo = _repo(tmp_path / "chief-repo", "main")
    jess_repo = _repo(tmp_path / "jess-repo", "trunk")
    _point_project(conn, "chief", chief_repo)
    _point_project(conn, "jess", jess_repo)

    assert resolve_repo(conn, "chief") == (chief_repo, "main")
    assert resolve_repo(conn, "jess") == (jess_repo, "trunk")


def test_arch_code_is_kept_at_arms_length(conn):
    message = (
        "That project is kept at arm's length — the team can read its notes but "
        "not touch its code."
    )
    with pytest.raises(ProjectRepoUnavailable, match=f"^{re.escape(message)}$"):
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
    jess_repo = _repo(tmp_path / "jess-code", "trunk")
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


def test_lane_memory_prefers_project_file_then_shared_fallback(tmp_path, monkeypatch):
    harness = tmp_path / "harness"
    lanes = harness / "config" / "lanes"
    (lanes / "jess").mkdir(parents=True)
    (lanes / "riggs.md").write_text("shared conventions")
    (lanes / "jess" / "riggs.md").write_text("jess conventions")
    monkeypatch.setattr(executor, "HARNESS", harness)

    assert executor._lane_memory_text("jess") == "jess conventions"
    assert executor._lane_memory_text("chief") == "shared conventions"


def test_builder_prompt_orders_and_caps_project_background():
    memory = "§" * (executor.MEMORY_INDEX_MAX_CHARS + 100)
    prompt = executor._compose_builder_prompt("original request", "lane rules", memory)

    assert prompt.index("lane rules") < prompt.index("§" * 100) < prompt.index("original request")
    assert prompt.count("§") == executor.MEMORY_INDEX_MAX_CHARS


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


def test_dispatch_endpoint_returns_friendly_error_and_stamps_valid_project(
        conn, tmp_path, monkeypatch):
    db_path = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    monkeypatch.setattr(server, "DB", db_path)
    monkeypatch.setattr(server, "_SEATS_SYNCED", True)
    monkeypatch.setattr(dispatch, "load_config", lambda: CFG)
    monkeypatch.setattr(gauntlet, "has_runner", lambda _provider: True)
    monkeypatch.setattr(executor, "start_in_background", lambda *_a, **_kw: None)
    client = TestClient(server.app)

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


def test_gatekeeper_merges_into_the_projects_own_branch(conn, tmp_path, monkeypatch):
    project_repo = _repo(tmp_path / "jess-merge", "trunk")
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
