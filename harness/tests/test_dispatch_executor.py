"""Dispatch + executor: the spine that makes assigned work actually start (task #9).

The recording/dedup/refusal tests run anywhere. The end-to-end test needs the local
Ollama model actually serving, so it skips cleanly when that isn't available.
"""

import sys
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dispatch  # noqa: E402
import executor  # noqa: E402
from db.jobs import (  # noqa: E402
    GuardViolation,
    Seat,
    connect,
    init_db,
    set_status,
    upsert_seat,
)

SEATS = [
    Seat("grinder_local", "ollama", "qwen2.5-coder:7b", "qwen", "local"),
    Seat("reviewer", "claude-cli", "claude-opus-4-8", "claude", "subscription",
         daily_cap_cents=500),
    Seat("brain", "codex", "gpt-5.6-sol", "gpt", "subscription"),
    Seat("off_seat", "ollama", "qwen2.5-coder:7b", "qwen", "local"),
]

# Two families that both have real runners — the minimum panel that can certify
# anything. Dispatch now reads its review requirements from here, never from the caller.
CFG = {
    "seats": {},
    "gauntlet": {"reviewers": ["reviewer", "brain"], "min_model_families": 2},
}


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    # Point the executor at the throwaway DB, and never spawn a background thread by
    # default — these tests drive run_job() synchronously where they want it.
    db = tmp_path / "test.db"
    monkeypatch.setattr(executor, "DB_PATH", db)
    monkeypatch.setattr(executor, "WORKTREES", tmp_path / ".worktrees")
    c = connect(db)
    init_db(c)
    for s in SEATS:
        upsert_seat(c, s)
    c.execute("UPDATE seats SET enabled = 0 WHERE id = 'off_seat'")
    return c


def _ollama_up() -> bool:
    try:
        urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3)
        return True
    except Exception:
        return False


# ── Recording: nothing runs without a row, and the row says the right things ──
def test_dispatch_records_the_job_before_starting(conn):
    d = dispatch.dispatch_local(conn, "add a helper function", "grinder_local",
                                cfg=CFG, start=False)
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (d.job_id,)).fetchone()
    assert row["status"] == "in_progress"
    assert row["required_reviews"] == 2, "the panel size must come from the config"
    assert row["required_review_families"] == 2, "the family floor must be stamped on"
    assert row["tier"] and row["tier_reason"]          # tiering actually ran
    assert row["branch"] == f"job/{d.job_id}"
    assert d.reused is False


# ── Duplicate protection: a retry must not start the same job twice ───────────
def test_a_repeated_dispatch_key_returns_the_same_job(conn):
    first = dispatch.dispatch_local(conn, "build X", "grinder_local",
                                    cfg=CFG, dispatch_key="abc-123", start=False)
    second = dispatch.dispatch_local(conn, "build X", "grinder_local",
                                     cfg=CFG, dispatch_key="abc-123", start=False)
    assert second.reused is True
    assert second.job_id == first.job_id
    n = conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
    assert n == 1, "the retry started a second job"


def test_different_keys_are_different_jobs(conn):
    a = dispatch.dispatch_local(conn, "build X", "grinder_local", cfg=CFG, dispatch_key="k1", start=False)
    b = dispatch.dispatch_local(conn, "build Y", "grinder_local", cfg=CFG, dispatch_key="k2", start=False)
    assert a.job_id != b.job_id


# ── Refusals: the store says no before any work is done ───────────────────────
def test_unknown_seat_is_refused(conn):
    with pytest.raises(dispatch.DispatchRefused, match="unknown seat"):
        dispatch.dispatch_local(conn, "build X", "nobody", cfg=CFG, start=False)


def test_disabled_seat_is_refused(conn):
    with pytest.raises(dispatch.DispatchRefused, match="turned off"):
        dispatch.dispatch_local(conn, "build X", "off_seat", cfg=CFG, start=False)


# ── End to end: the local model actually runs and lands real work ─────────────
@pytest.mark.skipif(not _ollama_up(), reason="local Ollama model not serving")
def test_the_local_worker_actually_produces_and_parks_for_review(conn):
    task = ("Write a Python function `add(a, b)` that returns their sum. "
            "Return ONLY the code.")
    d = dispatch.dispatch_local(conn, task, "grinder_local", cfg=CFG, start=False)

    result = executor.run_job(d.job_id)          # synchronous; no panel wired
    assert result["status"] == "review", "local output should park for review"

    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (d.job_id,)).fetchone()
    assert row["head_version"], "the produced version was not recorded"
    assert row["result"] and len(row["result"]) > 0, "no real output landed"
    assert row["worktree"], "no isolated worktree was recorded"
    assert Path(row["worktree"]).exists(), "the worktree does not exist on disk"

    events = conn.execute(
        "SELECT kind FROM events WHERE job_id = ? ORDER BY id", (d.job_id,)
    ).fetchall()
    kinds = [e["kind"] for e in events]
    assert "dispatched" in kinds and "write" in kinds, f"missing event trail: {kinds}"

    # The safety spine: local output cannot be forced to done without a real review.
    with pytest.raises(GuardViolation):
        set_status(conn, d.job_id, "done")

    executor.cleanup_worktree(conn, d.job_id)


def test_a_metered_builder_reserves_money_before_it_is_called(conn, monkeypatch):
    """Checking the ledger without writing to it let a paid seat build all day at zero."""
    import dispatch as d
    from db.jobs import Seat, upsert_seat
    c = conn if not isinstance(conn, tuple) else conn[0]
    upsert_seat(c, Seat("grok", "xai", "grok-4.5", "grok", "metered", daily_cap_cents=100))
    monkeypatch.setattr(d, "_spawn", lambda row, request, blocking: "run-1")
    cfg = {"seats": {}, "gauntlet": {"reviewers": ["reviewer", "brain"], "min_model_families": 2}}
    r = d.dispatch(c, "build a thing", "grok", cfg)
    spent = c.execute("SELECT COALESCE(SUM(cost_cents),0) FROM usage WHERE seat_id='grok'").fetchone()[0]
    assert spent == d.BUILD_ESTIMATE_CENTS and r.job_id
