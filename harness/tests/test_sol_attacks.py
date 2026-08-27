"""Sol's nine attacks. Each one must now fail.

On 2026-07-13, Sol (GPT, cross-family) reviewed the harness Claude had written and
found NINE ways through the guards. Neill's question — "have you been running any
reviewers on your own work today?" — was the reason we ran it at all. The answer was
no, and it showed.

The headline finding, verbatim:

    "All completion guards can be bypassed by creating the job already marked 'done'.
     The protections run only when an existing job's status changes. They do not run
     when a job is first created."

And the closing line, which was the one that mattered:

    "Those are application conventions, however — not the claimed structurally
     impossible guarantees."

He was right. Every test below is one of his attacks, turned into a lock. These tests
exist so nobody — including a future me — can quietly reopen a door Sol found.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.jobs import (  # noqa: E402
    GuardViolation, Seat, connect, create_job, init_db, record_artifact,
    record_usage, record_verdict, set_status, upsert_seat,
)

# A guard trips the same way whether you go through the helpers or straight at the
# database with raw SQL — the helpers just translate it into a friendlier error.
# THAT is the whole point: Sol's attacks were all direct writes, and the DB must
# refuse them without any Python in the loop.
BLOCKED = (GuardViolation, sqlite3.IntegrityError)

SEATS = [
    Seat("sol",   "codex",      "gpt-5.6-sol",     "gpt",    "subscription"),
    Seat("riggs", "claude-cli", "claude-opus-4-8", "claude", "subscription"),
    Seat("grok",  "xai",        "grok-4.5",        "grok",   "metered", daily_cap_cents=100),
    Seat("coal",  "ollama",     "qwen2.5-coder:7b","qwen",   "local"),
]


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    init_db(c)
    for s in SEATS:
        upsert_seat(c, s)
    return c


# ── Sol #1 — the worst one ────────────────────────────────────────────────
def test_a_job_cannot_be_born_finished(conn):
    """Every guard fired on UPDATE. None fired on INSERT. So you could insert a job
    that was already 'done' — no reviews, no tests, no evidence — and walk right past
    the entire gauntlet."""
    for status in ("done", "shipped"):
        with pytest.raises(BLOCKED, match="cannot be created already finished"):
            conn.execute(
                "INSERT INTO jobs (request, builder_seat, builder_tier, builder_family, status) "
                "VALUES ('sneak it in', 'coal', 'local', 'qwen', ?)", (status,)
            )


# ── Sol #2 — verdicts were editable ───────────────────────────────────────
def test_a_verdict_cannot_be_rewritten_into_a_pass(conn):
    """The family and evidence checks ran on INSERT only. So you wrote an honest
    'fail', then quietly UPDATE'd it to 'pass' and no guard ever looked again."""
    job = create_job(conn, "the login form", builder_seat="riggs")
    vid = record_verdict(conn, job, "sol", verdict="fail", summary="it's broken")

    with pytest.raises(BLOCKED, match="a verdict cannot be rewritten"):
        conn.execute("UPDATE verdicts SET verdict='pass' WHERE id=?", (vid,))


def test_an_escalation_may_still_be_answered(conn):
    """The one legitimate exception: needs_human -> pass/fail. Don't over-lock."""
    job = create_job(conn, "the migration", builder_seat="riggs")
    vid = record_verdict(conn, job, "sol", verdict="needs_human")
    conn.execute("UPDATE verdicts SET verdict='pass' WHERE id=?", (vid,))
    assert conn.execute("SELECT verdict FROM verdicts WHERE id=?", (vid,)).fetchone()[0] == "pass"


def test_a_reviewer_cannot_rewrite_who_it_was(conn):
    job = create_job(conn, "the login form", builder_seat="riggs")
    vid = record_verdict(conn, job, "coal", verdict="pass")  # local seat
    with pytest.raises(BLOCKED, match="cannot be rewritten after the fact"):
        conn.execute("UPDATE verdicts SET reviewer_tier='subscription' WHERE id=?", (vid,))


# ── Sol #3 — "no screenshot, no verdict" was FALSE ────────────────────────
def test_a_tester_cannot_pass_on_a_url_somebody_pasted(conn):
    """My guard accepted ANY artifact. So a tester could 'pass' a running app on the
    strength of a research source — a link. It never drove anything."""
    job = create_job(conn, "the login form", builder_seat="riggs")
    record_artifact(conn, job, kind="source", path="https://example.com", captured_by="model")

    with pytest.raises(BLOCKED, match="no screenshot, no verdict"):
        record_verdict(conn, job, "sol", verdict="pass", role="tester")


def test_evidence_with_nothing_in_it_is_not_evidence(conn):
    """Sol, round 2: 'A direct writer can insert a made-up screenshot path — even an
    empty string.' An empty path satisfied "IS NOT NULL" perfectly well. Now the
    artifact itself is refused, so the tester never even gets a foothold."""
    job = create_job(conn, "the login form", builder_seat="riggs")
    for bad in ("", "   ", None):
        with pytest.raises(BLOCKED, match="nothing in it is not evidence"):
            conn.execute(
                "INSERT INTO artifacts (job_id, kind, path, captured_by) "
                "VALUES (?, 'screenshot', ?, 'harness')", (job, bad)
            )


def test_a_tester_passes_on_real_captured_evidence(conn):
    job = create_job(conn, "the login form", builder_seat="riggs")
    record_artifact(conn, job, kind="screenshot", path="/tmp/login.png", captured_by="playwright")
    record_verdict(conn, job, "sol", verdict="pass", role="tester")  # gpt tests claude
    assert conn.execute("SELECT COUNT(*) FROM verdicts WHERE job_id=? AND role='tester'",
                        (job,)).fetchone()[0] == 1


# ── Sol #4 — identity was self-declared ───────────────────────────────────
def test_a_reviewer_cannot_lie_about_its_own_tier_or_family(conn):
    """The DB took the row's word for who was reviewing. So Coal — the free local
    model — could sign a verdict claiming to be a paid seat, and satisfy the very
    guard designed to stop Coal shipping unreviewed."""
    job = create_job(conn, "the login form", builder_seat="coal")
    with pytest.raises(BLOCKED, match="cannot misrepresent its own tier or family"):
        conn.execute(
            "INSERT INTO verdicts (job_id, reviewer_seat, reviewer_tier, model_family, verdict) "
            "VALUES (?, 'coal', 'subscription', 'gpt', 'pass')", (job,)
        )


# ── Sol #5/#6 — re-tiering rewrote history ────────────────────────────────
def test_promoting_coal_cannot_legitimize_its_old_work(conn):
    """The guard read the builder's tier LIVE. So you re-tiered Coal to 'subscription'
    and every unreviewed job it had ever built suddenly became shippable."""
    job = create_job(conn, "the overnight scaffold", builder_seat="coal")
    upsert_seat(conn, Seat("coal", "ollama", "qwen2.5-coder:7b", "qwen", "subscription"))

    # Since migration 007 the unconditional "another mind passed it" floor refuses this
    # too, and SQLite does not promise which guard speaks first. Either is the same no.
    with pytest.raises(BLOCKED, match="subscription-tier review|fewer model families"):
        set_status(conn, job, "done")


def test_who_built_it_cannot_be_rewritten(conn):
    job = create_job(conn, "the overnight scaffold", builder_seat="coal")
    for col, val in (("builder_seat", "riggs"), ("builder_tier", "subscription"),
                     ("builder_family", "claude")):
        with pytest.raises(BLOCKED, match="cannot be rewritten after the fact"):
            conn.execute(f"UPDATE jobs SET {col}=? WHERE id=?", (val, job))


def test_the_family_check_reads_history_not_the_current_seat(conn):
    """The snapshot must protect the PAST.

    Riggs built this as Claude. If we later repoint the Riggs seat to a GPT model,
    the job is still a CLAUDE build — so a Claude tester must still be refused, and
    a GPT tester must still be allowed. What a seat is today cannot change who
    actually wrote the code yesterday.
    """
    job = create_job(conn, "the login form", builder_seat="riggs")   # claude built it
    record_artifact(conn, job, kind="screenshot", path="/tmp/x.png", captured_by="playwright")

    # Someone repoints the Riggs lane to GPT tomorrow.
    upsert_seat(conn, Seat("riggs", "codex", "gpt-5.6-sol", "gpt", "subscription"))

    # A CLAUDE seat still cannot test it — the code is Claude's, whatever Riggs is now.
    upsert_seat(conn, Seat("finn", "claude-cli", "claude-opus-4-8", "claude", "subscription"))
    with pytest.raises(BLOCKED, match="may not test its own build"):
        record_verdict(conn, job, "finn", verdict="pass", role="tester")

    # And a GPT seat still can. The history is intact.
    record_verdict(conn, job, "sol", verdict="pass", role="tester")
    assert conn.execute(
        "SELECT model_family FROM verdicts WHERE job_id=? AND role='tester'", (job,)
    ).fetchone()[0] == "gpt"


# ── Sol #7 — the panel could be faked ─────────────────────────────────────
def test_one_reviewer_cannot_pass_the_same_job_six_times(conn):
    """It counted verdict ROWS, not distinct reviewers. So a single seat could sign
    the same job over and over until the panel looked full."""
    job = create_job(conn, "the login form", builder_seat="riggs")
    conn.execute("UPDATE jobs SET required_reviews=3 WHERE id=?", (job,))
    for _ in range(3):
        record_verdict(conn, job, "sol", verdict="pass")

    with pytest.raises(BLOCKED, match="full review panel has not reported"):
        set_status(conn, job, "done")


def test_three_distinct_reviewers_do_satisfy_the_panel(conn):
    job = create_job(conn, "the login form", builder_seat="riggs")
    conn.execute("UPDATE jobs SET required_reviews=3, head_version='abc123' WHERE id=?", (job,))
    for s in ("sol", "grok", "coal"):
        record_verdict(conn, job, s, verdict="pass")
    set_status(conn, job, "done")
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0] == "done"


def test_the_panel_cannot_be_shrunk_after_dispatch(conn):
    job = create_job(conn, "the login form", builder_seat="riggs")
    conn.execute("UPDATE jobs SET required_reviews=3 WHERE id=?", (job,))
    with pytest.raises(BLOCKED, match="panel cannot be shrunk"):
        conn.execute("UPDATE jobs SET required_reviews=0 WHERE id=?", (job,))


# ── Sol #8 — spend caps were a Python suggestion ──────────────────────────
def test_the_daily_cap_is_refused_by_the_ledger_itself(conn):
    """The cap was checked in Python before dispatch — a race (two dispatches both
    pass before either records spend) and trivially bypassed by a direct write."""
    record_usage(conn, "grok", cost_cents=95)
    with pytest.raises(BLOCKED, match="over its daily cap"):
        record_usage(conn, "grok", cost_cents=10)   # 95 + 10 > 100


def test_spend_cannot_be_unwound_to_make_room(conn):
    record_usage(conn, "grok", cost_cents=100)
    with pytest.raises(BLOCKED, match="cannot be negative"):
        record_usage(conn, "grok", cost_cents=-50)


def test_a_direct_write_cannot_dodge_the_cap(conn):
    record_usage(conn, "grok", cost_cents=100)
    with pytest.raises(BLOCKED, match="over its daily cap"):
        conn.execute("INSERT INTO usage (seat_id, cost_cents) VALUES ('grok', 50)")


# ── Sol #9 — models could forge evidence via a direct write ───────────────
def test_a_model_cannot_forge_a_screenshot_even_by_direct_write(conn):
    """The 'models may not capture build evidence' rule lived only in the Python
    helper. The database itself accepted it."""
    job = create_job(conn, "the login form", builder_seat="riggs")
    with pytest.raises(BLOCKED, match="may not produce build evidence"):
        conn.execute(
            "INSERT INTO artifacts (job_id, kind, path, captured_by) "
            "VALUES (?, 'screenshot', '/tmp/fake.png', 'model')", (job,)
        )


def test_a_model_may_still_cite_a_source(conn):
    """The asymmetry must survive: a researcher's job IS to hand you a URL."""
    job = create_job(conn, "is grok worth it", builder_seat="sol")
    conn.execute("UPDATE jobs SET kind='research' WHERE id=?", (job,))
    record_artifact(conn, job, kind="source", path="https://docs.x.ai", captured_by="model")
    assert conn.execute("SELECT COUNT(*) FROM artifacts WHERE job_id=?", (job,)).fetchone()[0] == 1


# ── Per-role rationing (owner, 2026-07-13) ───────────────────────────────
# "it can review as much as it wants more or less, but shouldn't build as much
#  as claude and chat"
#
# The economics agree: Claude and OpenAI are flat-rate (a build costs nothing
# marginal — already bought), Grok is metered (every build is real money). And
# builds are token-heavy while reviews are read-heavy. So build on what you own,
# review on what you meter.
def test_grok_runs_out_of_building_budget_but_can_still_review(conn):
    upsert_seat(conn, Seat("grok", "xai", "grok-4.5", "grok", "metered",
                           daily_cap_cents=100, build_cap_cents=25, review_cap_cents=75))

    record_usage(conn, "grok", cost_cents=25, role="build")   # ration spent

    with pytest.raises(BLOCKED, match="used up its building budget"):
        record_usage(conn, "grok", cost_cents=5, role="build")

    # ...but the thing it's actually best at is still wide open.
    record_usage(conn, "grok", cost_cents=40, role="review")
    record_usage(conn, "grok", cost_cents=20, role="test")
    assert conn.execute(
        "SELECT SUM(cost_cents) FROM usage WHERE seat_id='grok' AND role IN ('review','test')"
    ).fetchone()[0] == 60


def test_the_seats_we_already_pay_for_cost_nothing_marginal(conn):
    """Claude and Sol are FLAT-RATE. A build on those seats costs zero MARGINAL money —
    you already paid for it this month.

    The old version of this test recorded real dollars against them, which was simply
    wrong, and the new monthly-budget guard caught it: it tried to bill $500 to seats
    that cost nothing extra to use, and blew through the $100 cap doing it.

    What flat-rate seats actually consume is RATE LIMIT, not money — and you cannot buy
    your way out of a weekly cap. That's tracked as tokens, not cents.
    """
    for _ in range(50):
        record_usage(conn, "riggs", cost_cents=0, output_tokens=5000, role="build")
        record_usage(conn, "sol", cost_cents=0, output_tokens=5000, role="build")

    money = conn.execute("SELECT SUM(cost_cents) FROM usage").fetchone()[0]
    tokens = conn.execute("SELECT SUM(output_tokens) FROM usage").fetchone()[0]
    assert money == 0, "flat-rate seats must not bill money"
    assert tokens == 500_000, "but they DO burn rate limit, and that's what we watch"


def test_the_hard_ceiling_still_wins_over_the_role_budgets(conn):
    """Role caps ration WITHIN the total. They can never add up to more than it."""
    upsert_seat(conn, Seat("grok", "xai", "grok-4.5", "grok", "metered",
                           daily_cap_cents=100, build_cap_cents=25, review_cap_cents=90))
    record_usage(conn, "grok", cost_cents=25, role="build")
    record_usage(conn, "grok", cost_cents=70, role="review")   # 95 of 100 spent

    # The review budget still has room (70 of 90) — but the DAY does not.
    with pytest.raises(BLOCKED, match="over its daily cap"):
        record_usage(conn, "grok", cost_cents=10, role="review")


# ── Sol round 3 (2026-07-14) — two holes of the same old shape ────────────
# Verified with live exploits before fixing. Both were the exact bugs Sol had
# already found ELSEWHERE — "append-only" and "born done" — that had simply never
# been applied to the verdicts and approvals tables.
def test_a_failing_verdict_cannot_be_deleted_to_unblock_a_job(conn):
    """"Append-only" was enforced against UPDATE only. A failing review that couldn't be
    EDITED into a pass could just be DELETED — after which the failing-review guard, which
    asks 'does a fail exist?', saw nothing and let the job complete. Proven, then locked."""
    job = create_job(conn, "the billing change", builder_seat="riggs")
    vid = record_verdict(conn, job, "sol", verdict="fail", summary="it's broken")

    with pytest.raises(BLOCKED, match="cannot be deleted"):
        conn.execute("DELETE FROM verdicts WHERE id=?", (vid,))

    # And the fail still does its job: the objection blocks completion.
    with pytest.raises(BLOCKED, match="does not get outvoted"):
        set_status(conn, job, "done")


def test_an_approval_cannot_be_born_granted(conn):
    """Every approval guard fired on UPDATE OF granted_at — the same 'born done' hole Sol
    found on jobs in round 1, never applied here. So a row inserted with granted_at already
    set, reversible=0 and no recovery plan sailed straight into live_approvals. Now an
    approval must be born ungranted and be granted through a read-back, where the recovery
    and read-back guards actually fire."""
    job = create_job(conn, "delete the old accounts", builder_seat="riggs")
    with pytest.raises(BLOCKED, match="cannot be created already granted"):
        conn.execute(
            "INSERT INTO approvals "
            "(job_id, capability, action, read_back, reversible, recovery, granted_at, expires_at) "
            "VALUES (?, 'delete_data', 'delete 4200 accounts', 'I will delete 4200 accounts', "
            "0, '', datetime('now'), datetime('now','+10 minutes'))", (job,)
        )


def test_an_approval_granted_the_right_way_still_needs_a_recovery_plan(conn):
    """Don't over-lock: the legitimate path still works, and still enforces recovery.
    Born ungranted, then an UPDATE to grant an irreversible act with no way back is
    refused by the existing guard."""
    job = create_job(conn, "delete the old accounts", builder_seat="riggs")
    conn.execute(
        "INSERT INTO approvals (job_id, capability, action, read_back, reversible, expires_at) "
        "VALUES (?, 'delete_data', 'delete 4200 accounts', 'I will delete 4200 accounts', 0, "
        "datetime('now','+10 minutes'))", (job,)
    )
    with pytest.raises(BLOCKED, match="no way back"):
        conn.execute("UPDATE approvals SET granted_at=datetime('now') WHERE job_id=?", (job,))
