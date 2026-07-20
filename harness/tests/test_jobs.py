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
    set_head_version,
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
    set_head_version(conn, job, "abc123")
    record_verdict(conn, job, "orchestrator", verdict="pass")

    set_status(conn, job, "done", result="merged to main")

    row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job,)).fetchone()
    assert row["status"] == "done"


def test_subscription_built_job_needs_no_such_rescue(conn):
    """The guard targets local output specifically; it must not block everything else."""
    job = create_job(conn, "big refactor", builder_seat="workhorse")
    set_head_version(conn, job, "abc123")
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
    set_head_version(conn, job, "abc123")
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
    set_head_version(conn, job, "abc123")
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
    set_head_version(conn, job, "abc123")
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
    set_head_version(conn, job, "abc123")
    record_artifact(conn, job, kind="screenshot", path="/tmp/login.png")
    record_verdict(conn, job, "gpt_tester", verdict="pass", role="tester")  # different family
    set_status(conn, job, "done")

    set_status(conn, job, "shipped")

    assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job,)).fetchone()["status"] == "shipped"


def test_nothing_ships_without_a_tester_actually_driving_it(conn):
    """A code review is not a test. Somebody has to have RUN the thing."""
    job = create_job(conn, "add the login form", builder_seat="orchestrator")
    set_head_version(conn, job, "abc123")
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


def test_a_failing_tester_stops_the_job_dead(conn):
    """A failing review isn't a vote — it's a smoke detector going off.

    Sol's round-2 review caught that my panel counted PASSES and ignored FAILS, so
    three passes could outvote one reviewer screaming that the thing was broken. That's
    the opposite of what a gauntlet is for. Now a single fail stops it, and it stops it
    at 'done' — it never even reaches the ship gate.
    """
    upsert_seat(conn, Seat("gpt_tester", "codex", "gpt-5.6-sol", "gpt", "subscription"))
    job = create_job(conn, "add the login form", builder_seat="reviewer")
    record_artifact(conn, job, kind="screenshot", path="/tmp/broken.png")
    record_verdict(conn, job, "gpt_tester", verdict="fail", role="tester",
                   severity="p1", summary="500 on submit")

    with pytest.raises(GuardViolation, match="does not get outvoted"):
        set_status(conn, job, "done")


def test_a_failing_review_cannot_be_outvoted_by_passes(conn):
    upsert_seat(conn, Seat("gpt2", "codex", "gpt-5.6-sol", "gpt", "subscription"))
    job = create_job(conn, "the migration", builder_seat="workhorse")
    record_verdict(conn, job, "orchestrator", verdict="pass")
    record_verdict(conn, job, "gpt2", verdict="pass")
    record_verdict(conn, job, "reviewer", verdict="fail", severity="p1",
                   summary="this drops rows")

    with pytest.raises(GuardViolation, match="does not get outvoted"):
        set_status(conn, job, "done")


# ---------------------------------------------------------------------------
# Guards 7 & 8 — Atlas. A researcher must show you where it got that.
#
# These exist because of a real failure IN THIS PROJECT. Researching the seat
# hierarchy, Atlas confidently reported Grok Build scored 70.8% on a coding
# benchmark — wrong model entirely. It also said a web-view app can't get
# background mic access — also wrong. Both fluent, both sourced-sounding, both
# false. Neill caught them by pushing back; nothing in the system would have.
#
# A confident wrong answer is worse than no answer, because you ACT on it. Neill
# nearly made a purchasing decision on the first one.
# ---------------------------------------------------------------------------
def _research_job(conn, seat="orchestrator"):
    job = create_job(conn, "Is Grok Build worth buying?", builder_seat=seat,
                     task_name="the Grok question")
    conn.execute("UPDATE jobs SET kind='research' WHERE id=?", (job,))
    return job


def test_a_research_answer_without_sources_cannot_land(conn):
    job = _research_job(conn)
    set_status(conn, job, "review", result="Grok scores 70.8%. Not worth it.")

    with pytest.raises(GuardViolation, match="no source, no answer"):
        set_status(conn, job, "done")


def test_a_research_answer_with_sources_can_land(conn):
    job = _research_job(conn)
    record_artifact(conn, job, kind="source", path="https://docs.x.ai/build/overview",
                    captured_by="model")
    set_status(conn, job, "done")
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()["status"] == "done"


def test_a_model_may_cite_a_source_but_not_forge_a_screenshot(conn):
    """The asymmetry that makes this work.

    A researcher's job IS to hand you a URL — it may write one down. But no model
    may claim it took a screenshot, because then 'I ran it and it worked' becomes
    sayable again.
    """
    job = _research_job(conn)
    record_artifact(conn, job, kind="source", path="https://x.ai", captured_by="model")

    with pytest.raises(ValueError, match="may not produce a 'screenshot'"):
        record_artifact(conn, job, kind="screenshot", path="/tmp/fake.png", captured_by="model")


def test_a_family_cannot_fact_check_its_own_research(conn):
    """The exact hole that let the Grok number through.

    A family that got a fact wrong will re-read its own sources and find them
    convincing — it made the same inference the first time. Only a different mind
    reads them cold.
    """
    job = _research_job(conn, seat="orchestrator")  # gpt
    record_artifact(conn, job, kind="source", path="https://x.ai", captured_by="model")

    with pytest.raises(GuardViolation, match="may not fact-check its own research"):
        record_verdict(conn, job, "orchestrator", verdict="pass", role="verifier")


def test_a_different_family_can_fact_check_it(conn):
    job = _research_job(conn, seat="orchestrator")  # gpt built it
    record_artifact(conn, job, kind="source", path="https://x.ai", captured_by="model")

    record_verdict(conn, job, "reviewer", verdict="fail", role="verifier", severity="p1",
                   summary="That benchmark is the old model. The one you'd get is far better.")

    row = conn.execute(
        "SELECT model_family, verdict FROM verdicts WHERE job_id=? AND role='verifier'", (job,)
    ).fetchone()
    assert row["model_family"] == "claude" and row["verdict"] == "fail"


def test_a_build_job_needs_no_sources(conn):
    """The citation guard is for research. It must not block ordinary code."""
    job = create_job(conn, "add the login form", builder_seat="orchestrator")
    set_head_version(conn, job, "abc123")
    set_status(conn, job, "done")
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()["status"] == "done"


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
def test_a_seat_with_no_daily_cap_never_blocks_on_its_own_cap(conn):
    """A seat with no daily cap has no daily cap. But the MONTHLY budget still binds —
    it binds on everything, which is the point of a ceiling.

    (The old version of this spent $1,000 to prove the seat was uncapped, and the new
    monthly-budget guard rightly refused. That test was asserting we had no brakes.)
    """
    record_usage(conn, "orchestrator", cost_cents=900)   # $9, well under the $100 month
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
    set_head_version(conn, job, "abc123")
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
