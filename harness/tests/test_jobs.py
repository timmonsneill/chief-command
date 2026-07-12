"""Tests for the harness job store.

The load-bearing ones are the guard tests: they prove that shipping unreviewed local
output is *structurally impossible*, not merely discouraged. Spec §9 asks for this to
be enforced "in the pipeline itself, not by convention" — these tests are what makes
that claim true.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.jobs import (  # noqa: E402
    GuardViolation,
    Seat,
    claim_next_job,
    connect,
    create_job,
    init_db,
    over_budget,
    overnight_report,
    record_usage,
    record_verdict,
    resolve_escalation,
    set_status,
    upsert_seat,
)

SEATS = [
    Seat("orchestrator", "codex", "gpt-5.6-sol", "subscription"),
    Seat("workhorse", "xai", "grok-build", "subscription"),
    Seat("grinder", "ollama", "qwen2.5-coder:7b", "local"),
    Seat("reviewer", "claude-cli", "claude-fable-5", "metered", daily_cap_cents=500),
]


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    init_db(c)
    for s in SEATS:
        upsert_seat(c, s)
    return c


# ---------------------------------------------------------------------------
# Guard 1 — local output never ships unreviewed
# ---------------------------------------------------------------------------
def test_local_job_cannot_reach_done_without_review(conn):
    job = create_job(conn, "scaffold the auth module", builder_seat="grinder")
    set_status(conn, job, "review")

    with pytest.raises(GuardViolation, match="subscription-tier review"):
        set_status(conn, job, "done")


def test_local_job_cannot_be_rescued_by_another_local_review(conn):
    """Ollama reviewing Ollama is not a review. Two juniors are not a senior."""
    upsert_seat(conn, Seat("grinder2", "ollama", "qwen2.5-coder:7b", "local"))
    job = create_job(conn, "write the tests", builder_seat="grinder")
    record_verdict(conn, job, "grinder2", verdict="pass", model_family="qwen")

    with pytest.raises(GuardViolation):
        set_status(conn, job, "done")


def test_local_job_cannot_ship_on_a_failing_review(conn):
    job = create_job(conn, "write the tests", builder_seat="grinder")
    record_verdict(conn, job, "orchestrator", verdict="fail", model_family="gpt")

    with pytest.raises(GuardViolation):
        set_status(conn, job, "done")


def test_local_job_ships_once_a_higher_tier_seat_passes_it(conn):
    job = create_job(conn, "scaffold the auth module", builder_seat="grinder")
    record_verdict(conn, job, "orchestrator", verdict="pass", model_family="gpt")

    set_status(conn, job, "done", result="merged to main")

    row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job,)).fetchone()
    assert row["status"] == "done"


def test_subscription_built_job_needs_no_such_rescue(conn):
    """The guard targets local output specifically; it must not block everything else."""
    job = create_job(conn, "big refactor", builder_seat="workhorse")
    set_status(conn, job, "done")
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job,)).fetchone()["status"] == "done"


def test_retiering_a_seat_cannot_retroactively_legitimize_a_job(conn):
    """reviewer_tier is snapshotted at write time.

    If a local seat reviews a job and someone later re-tiers that seat to
    'subscription', the old verdict must NOT suddenly count.
    """
    upsert_seat(conn, Seat("grinder2", "ollama", "qwen2.5-coder:7b", "local"))
    job = create_job(conn, "write the tests", builder_seat="grinder")
    record_verdict(conn, job, "grinder2", verdict="pass", model_family="qwen")

    # Someone "promotes" the local seat after the fact.
    upsert_seat(conn, Seat("grinder2", "ollama", "qwen2.5-coder:7b", "subscription"))

    with pytest.raises(GuardViolation):
        set_status(conn, job, "done")


# ---------------------------------------------------------------------------
# Guard 2 — escalations must be answered, not outrun
# ---------------------------------------------------------------------------
def test_unresolved_escalation_blocks_completion(conn):
    job = create_job(conn, "tricky migration", builder_seat="workhorse")
    record_verdict(conn, job, "orchestrator", verdict="needs_human", model_family="gpt",
                   summary="contested: might drop rows")

    with pytest.raises(GuardViolation, match="needs_human"):
        set_status(conn, job, "done")


def test_resolved_escalation_unblocks_completion(conn):
    job = create_job(conn, "tricky migration", builder_seat="workhorse")
    vid = record_verdict(conn, job, "orchestrator", verdict="needs_human", model_family="gpt")

    resolve_escalation(conn, vid, "pass")
    set_status(conn, job, "done")

    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job,)).fetchone()["status"] == "done"


# ---------------------------------------------------------------------------
# Atomic claiming — parallel workers must not double-claim
# ---------------------------------------------------------------------------
def test_a_job_is_claimed_exactly_once(conn):
    create_job(conn, "job A", builder_seat="grinder")

    first = claim_next_job(conn, "grinder")
    second = claim_next_job(conn, "grinder")

    assert first is not None and first["status"] == "in_progress"
    assert second is None, "the same job was handed out twice"


def test_claim_is_scoped_to_the_seat(conn):
    create_job(conn, "for the grinder", builder_seat="grinder")
    assert claim_next_job(conn, "workhorse") is None
    assert claim_next_job(conn, "grinder") is not None


def test_claims_are_fifo(conn):
    a = create_job(conn, "first", builder_seat="grinder")
    b = create_job(conn, "second", builder_seat="grinder")
    assert claim_next_job(conn, "grinder")["id"] == a
    assert claim_next_job(conn, "grinder")["id"] == b


# ---------------------------------------------------------------------------
# Budget caps — OpenClaw core has none, so these must hold here
# ---------------------------------------------------------------------------
def test_uncapped_seat_never_blocks(conn):
    record_usage(conn, "orchestrator", cost_cents=100_000)
    assert over_budget(conn, "orchestrator") is False


def test_capped_seat_blocks_once_the_cap_is_hit(conn):
    assert over_budget(conn, "reviewer") is False
    record_usage(conn, "reviewer", cost_cents=499)
    assert over_budget(conn, "reviewer") is False
    record_usage(conn, "reviewer", cost_cents=1)  # now at 500 of 500
    assert over_budget(conn, "reviewer") is True


# ---------------------------------------------------------------------------
# §7 — "what did the overnight run do?"
# ---------------------------------------------------------------------------
def test_overnight_report_answers_the_voice_query(conn):
    job = create_job(conn, "backfill the tests", builder_seat="grinder", origin="voice")
    record_verdict(conn, job, "orchestrator", verdict="pass", model_family="gpt")
    record_verdict(conn, job, "workhorse", verdict="pass", model_family="grok")
    set_status(conn, job, "done", spoken_summary="Backfilled 14 tests. All green.")

    report = overnight_report(conn)
    assert len(report) == 1

    row = report[0]
    assert row["request"] == "backfill the tests"
    assert row["status"] == "done"
    assert row["builder_tier"] == "local"
    assert row["review_count"] == 2
    # §6's model-diversity rule is auditable from the record, not just asserted
    assert row["families_reviewed"] == 2
    assert row["spoken_summary"] == "Backfilled 14 tests. All green."
