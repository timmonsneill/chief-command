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
    record_artifact,
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
    Seat("orchestrator", "codex", "gpt-5.6-sol", "gpt", "subscription"),
    Seat("workhorse", "xai", "grok-4.5", "grok", "subscription"),
    Seat("grinder", "ollama", "qwen2.5-coder:7b", "qwen", "local"),
    Seat("reviewer", "claude-cli", "claude-opus-4-8", "claude", "metered", daily_cap_cents=500),
    Seat("tester", "claude-cli", "claude-opus-4-8", "claude", "subscription"),
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
    upsert_seat(conn, Seat("grinder2", "ollama", "qwen2.5-coder:7b", "qwen", "local"))
    job = create_job(conn, "write the tests", builder_seat="grinder")
    record_verdict(conn, job, "grinder2", verdict="pass")

    with pytest.raises(GuardViolation):
        set_status(conn, job, "done")


def test_local_job_cannot_ship_on_a_failing_review(conn):
    job = create_job(conn, "write the tests", builder_seat="grinder")
    record_verdict(conn, job, "orchestrator", verdict="fail")

    with pytest.raises(GuardViolation):
        set_status(conn, job, "done")


def test_local_job_ships_once_a_higher_tier_seat_passes_it(conn):
    job = create_job(conn, "scaffold the auth module", builder_seat="grinder")
    record_verdict(conn, job, "orchestrator", verdict="pass")

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
    upsert_seat(conn, Seat("grinder2", "ollama", "qwen2.5-coder:7b", "qwen", "local"))
    job = create_job(conn, "write the tests", builder_seat="grinder")
    record_verdict(conn, job, "grinder2", verdict="pass")

    # Someone "promotes" the local seat after the fact.
    upsert_seat(conn, Seat("grinder2", "ollama", "qwen2.5-coder:7b", "qwen", "subscription"))

    with pytest.raises(GuardViolation):
        set_status(conn, job, "done")


# ---------------------------------------------------------------------------
# Guard 2 — escalations must be answered, not outrun
# ---------------------------------------------------------------------------
def test_unresolved_escalation_blocks_completion(conn):
    job = create_job(conn, "tricky migration", builder_seat="workhorse")
    record_verdict(conn, job, "orchestrator", verdict="needs_human",
                   summary="contested: might drop rows")

    with pytest.raises(GuardViolation, match="needs_human"):
        set_status(conn, job, "done")


def test_resolved_escalation_unblocks_completion(conn):
    job = create_job(conn, "tricky migration", builder_seat="workhorse")
    vid = record_verdict(conn, job, "orchestrator", verdict="needs_human")

    resolve_escalation(conn, vid, "pass")
    set_status(conn, job, "done")

    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job,)).fetchone()["status"] == "done"


# ---------------------------------------------------------------------------
# Guard 3 — a tester must cite ground truth (Playwright artifacts)
#
# This is the anti-fabrication layer. GPT-5.6-sol's own system card admits it
# fabricates results; METR measured a cheating rate higher than any public model.
# A tester that lies disables the fleet's only quality gate, because nothing checks
# the tester. So: no screenshot, no verdict. "I ran it and it worked" is unsayable.
# ---------------------------------------------------------------------------
def test_tester_cannot_pass_a_job_it_captured_nothing_for(conn):
    job = create_job(conn, "add the login form", builder_seat="orchestrator")

    with pytest.raises(GuardViolation, match="no screenshot, no verdict"):
        record_verdict(conn, job, "tester", verdict="pass", role="tester",
                       summary="I ran it and it worked")


def test_tester_can_pass_once_playwright_has_captured_the_flow(conn):
    job = create_job(conn, "add the login form", builder_seat="orchestrator")
    record_artifact(conn, job, kind="screenshot", path="/tmp/login-ok.png",
                    flow="login -> dashboard")
    record_artifact(conn, job, kind="console_log", path="/tmp/console.log",
                    flow="login -> dashboard")

    record_verdict(conn, job, "tester", verdict="pass", role="tester",
                   summary="Login renders, redirects to dashboard, console clean.")

    row = conn.execute(
        "SELECT COUNT(*) c FROM verdicts WHERE job_id = ? AND role = 'tester'", (job,)
    ).fetchone()
    assert row["c"] == 1


def test_a_model_may_never_claim_to_have_captured_an_artifact(conn):
    """Artifacts are ground truth. Only the harness or Playwright writes them."""
    job = create_job(conn, "add the login form", builder_seat="orchestrator")
    with pytest.raises(ValueError, match="never claimed by a model"):
        record_artifact(conn, job, kind="screenshot", path="/tmp/fake.png",
                        captured_by="model")


def test_a_plain_reviewer_needs_no_artifacts(conn):
    """The artifact guard is for testers, who drive the app. Reviewers read diffs."""
    job = create_job(conn, "add the login form", builder_seat="orchestrator")
    record_verdict(conn, job, "reviewer", verdict="pass", role="reviewer")
    set_status(conn, job, "done")
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job,)).fetchone()["status"] == "done"


# ---------------------------------------------------------------------------
# Guard 4 — a model family may not test its own work
#
# Playwright stops a tester FABRICATING what happened. It cannot stop it
# RATIONALIZING. If Claude builds a form and decides validation fires on blur,
# Claude will look at a screenshot of validation firing on blur and call it correct
# — truthfully, and wrongly. Same artifact, same blind spot, rubber stamp.
# ---------------------------------------------------------------------------
def test_a_family_cannot_test_its_own_build(conn):
    # 'reviewer' and 'tester' are both claude; so is this builder.
    upsert_seat(conn, Seat("claude_builder", "claude-cli", "claude-opus-4-8", "claude", "subscription"))
    job = create_job(conn, "add the login form", builder_seat="claude_builder")
    record_artifact(conn, job, kind="screenshot", path="/tmp/x.png")

    with pytest.raises(GuardViolation, match="may not test its own build"):
        record_verdict(conn, job, "tester", verdict="pass", role="tester")


def test_a_different_family_may_test_it(conn):
    upsert_seat(conn, Seat("claude_builder", "claude-cli", "claude-opus-4-8", "claude", "subscription"))
    upsert_seat(conn, Seat("grok_tester", "xai", "grok-4.5", "grok", "metered"))
    job = create_job(conn, "add the login form", builder_seat="claude_builder")
    record_artifact(conn, job, kind="screenshot", path="/tmp/x.png")

    record_verdict(conn, job, "grok_tester", verdict="fail", role="tester",
                   severity="p2", summary="Validation fires on blur; users expect it on submit.")

    row = conn.execute(
        "SELECT verdict, model_family FROM verdicts WHERE job_id = ? AND role='tester'", (job,)
    ).fetchone()
    assert row["verdict"] == "fail" and row["model_family"] == "grok"


def test_the_same_family_may_still_REVIEW_its_own_build(conn):
    """The no-self-testing rule is about DRIVING THE APP, not reading a diff.

    A same-family reviewer is a weaker signal, but it isn't forbidden — the gauntlet's
    min_model_families rule handles diversity there. Only the tester seat is hard-gated.
    """
    upsert_seat(conn, Seat("claude_builder", "claude-cli", "claude-opus-4-8", "claude", "subscription"))
    job = create_job(conn, "add the login form", builder_seat="claude_builder")
    record_verdict(conn, job, "reviewer", verdict="pass", role="reviewer")  # claude reviewing claude
    set_status(conn, job, "done")
    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job,)).fetchone()["status"] == "done"


# ---------------------------------------------------------------------------
# Guard 6 — a job ships on the GATES, not on a human and not on agent confidence.
#
# Owner override: "I don't want shipped depending on me. If it is reviewed and
# tested, then it ships." Neill is out of the critical path — so the schema now
# carries the backstop he used to carry personally.
# ---------------------------------------------------------------------------
def test_a_fully_gauntleted_job_ships_itself(conn):
    """The happy path. Nobody had to wake Neill up."""
    upsert_seat(conn, Seat("gpt_tester", "codex", "gpt-5.6-sol", "gpt", "subscription"))
    job = create_job(conn, "add the login form", builder_seat="reviewer")  # claude built it
    record_artifact(conn, job, kind="screenshot", path="/tmp/login.png")
    record_verdict(conn, job, "gpt_tester", verdict="pass", role="tester")  # different family
    set_status(conn, job, "done")

    set_status(conn, job, "shipped")

    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job,)).fetchone()["status"] == "shipped"


def test_nothing_ships_without_a_tester_actually_driving_it(conn):
    """A code review is not a test. Somebody has to have RUN the thing."""
    job = create_job(conn, "add the login form", builder_seat="orchestrator")
    record_verdict(conn, job, "reviewer", verdict="pass", role="reviewer")  # read the diff only
    set_status(conn, job, "done")

    with pytest.raises(GuardViolation, match="cross-family tester"):
        set_status(conn, job, "shipped")


def test_nothing_ships_straight_out_of_review(conn):
    """You cannot skip 'done' — that's where the full panel and escalation gates live."""
    upsert_seat(conn, Seat("gpt_tester", "codex", "gpt-5.6-sol", "gpt", "subscription"))
    job = create_job(conn, "add the login form", builder_seat="reviewer")
    record_artifact(conn, job, kind="screenshot", path="/tmp/x.png")
    record_verdict(conn, job, "gpt_tester", verdict="pass", role="tester")
    set_status(conn, job, "review")

    with pytest.raises(GuardViolation):
        set_status(conn, job, "shipped")


def test_a_failing_tester_stops_the_ship(conn):
    upsert_seat(conn, Seat("gpt_tester", "codex", "gpt-5.6-sol", "gpt", "subscription"))
    job = create_job(conn, "add the login form", builder_seat="reviewer")
    record_artifact(conn, job, kind="screenshot", path="/tmp/broken.png")
    record_verdict(conn, job, "gpt_tester", verdict="fail", role="tester",
                   severity="p1", summary="500 on submit")
    set_status(conn, job, "done")

    with pytest.raises(GuardViolation):
        set_status(conn, job, "shipped")


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
    record_verdict(conn, job, "orchestrator", verdict="pass")
    record_verdict(conn, job, "workhorse", verdict="pass")
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
