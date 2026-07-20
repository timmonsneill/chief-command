"""Sol's SECOND cross-family review — eighteen findings, four critical.

Neill went to bed and told me to keep working. So I ran the gauntlet on the night's
code, and Sol took it apart again. Round one found nine holes in the guards. Round two
found eighteen more — in the guards, the escalation logic, and the web app.

Two of them broke promises I'd made to Neill personally:

    #10: "The allow-list is not safe. It accepts any short message beginning with
          words such as 'do', 'fix', 'run', 'go', or 'make'. Therefore 'Do you think
          this is safe?' ... stays with the shallow path."

    #14: "The web app explicitly leaks filenames, commands, model names, and jargon.
          The plain-English requirement is contradicted by the interface itself."

And the worst one for correctness:

    #5:  "Bad reviews do not stop completion. Completion counts passing entries but
          ignores failing ones. Three passes and one serious failure can still satisfy
          the panel."

Each test below is one of his findings, turned into a lock.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.jobs import (GuardViolation, Seat, connect, create_job, init_db,
                     record_artifact, record_verdict, set_status, upsert_seat)
from mouth import is_pushback, needs_the_brain

BLOCKED = (GuardViolation, sqlite3.IntegrityError)

SEATS = [
    Seat("sol", "codex", "gpt-5.6-sol", "gpt", "subscription"),
    Seat("riggs", "claude-cli", "claude-opus-4-8", "claude", "subscription"),
    Seat("grok", "xai", "grok-4.5", "grok", "metered"),
    Seat("coal", "ollama", "qwen2.5-coder:7b", "qwen", "local"),
]


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "t.db"); init_db(c)
    for s in SEATS:
        upsert_seat(c, s)
    return c


# ── #1 CRITICAL — a job could lie about who built it, at birth ───────────
def test_a_job_cannot_lie_about_who_built_it(conn):
    """I froze the builder's identity AFTER creation so it couldn't be rewritten — but
    never checked it AT creation. Sol: 'The identity is frozen only after creation; the
    initial lie is never caught.'

    Freezing a lie just makes it a permanent lie. Coal could be born claiming to be a
    paid seat, and then complete with no higher-tier review at all.
    """
    with pytest.raises(BLOCKED, match="cannot misrepresent who built it"):
        conn.execute(
            "INSERT INTO jobs (request, builder_seat, builder_tier, builder_family) "
            "VALUES ('sneaky', 'coal', 'subscription', 'claude')"   # coal is local/qwen
        )


# ── #5 CRITICAL — a failing review could be outvoted ─────────────────────
def test_one_reviewer_finding_a_bug_stops_the_job(conn):
    """THE WORST ONE. My panel counted PASSES and ignored FAILS.

    A gauntlet is not a democracy. It's a set of independent smoke detectors, and ANY
    ONE of them going off stops the job. Three colleagues who didn't notice cannot
    outvote the one who did.
    """
    job = create_job(conn, "the migration", builder_seat="riggs")
    record_verdict(conn, job, "sol", verdict="pass")
    record_verdict(conn, job, "grok", verdict="pass")
    record_verdict(conn, job, "coal", verdict="fail", severity="p1", summary="this drops rows")

    with pytest.raises(BLOCKED, match="does not get outvoted"):
        set_status(conn, job, "done")


def test_a_tester_and_a_fact_checker_do_not_count_as_the_review_panel(conn):
    """Sol: 'It also counts testers and research fact-checkers as ordinary reviewers,
    so the required review panel can be filled by the wrong kinds of check.'"""
    job = create_job(conn, "the login form", builder_seat="riggs")
    conn.execute("UPDATE jobs SET required_reviews=2 WHERE id=?", (job,))
    record_artifact(conn, job, kind="screenshot", path="/tmp/x.png", captured_by="playwright")
    record_verdict(conn, job, "sol", verdict="pass", role="tester")   # a tester, not a reviewer
    record_verdict(conn, job, "grok", verdict="pass", role="reviewer")

    with pytest.raises(BLOCKED, match="full review panel"):
        set_status(conn, job, "done")


# ── #6 — a late objection could be outrun ────────────────────────────────
def test_an_objection_raised_after_done_still_blocks_the_ship(conn):
    """The escalation guard only fired on the way INTO 'done'. So a reviewer who spoke
    up a moment too late could be walked straight past on done -> shipped."""
    job = create_job(conn, "the migration", builder_seat="riggs")
    conn.execute("UPDATE jobs SET head_version='abc123' WHERE id=?", (job,))
    record_artifact(conn, job, kind="screenshot", path="/tmp/x.png", captured_by="playwright")
    record_verdict(conn, job, "sol", verdict="pass", role="tester")
    set_status(conn, job, "done")

    record_verdict(conn, job, "grok", verdict="needs_human", summary="wait, what about rollback?")

    with pytest.raises(BLOCKED, match="objection"):
        set_status(conn, job, "shipped")


# ── #4 — empty evidence was evidence ─────────────────────────────────────
def test_an_empty_screenshot_path_is_not_evidence(conn):
    job = create_job(conn, "the login form", builder_seat="riggs")
    for empty in ("", "   "):
        with pytest.raises(BLOCKED, match="nothing in it is not evidence"):
            conn.execute("INSERT INTO artifacts (job_id, kind, path, captured_by) "
                         "VALUES (?, 'screenshot', ?, 'playwright')", (job, empty))


# ── #10 — the allow-list let command-shaped QUESTIONS through ────────────
def test_a_question_wearing_a_command_costume_still_gets_thought_about(conn):
    """Sol's example, and it's perfect: 'Do you think this is safe?' starts with 'do',
    so my verb allow-list waved it through as a routine dispatch.

    A dispatch is an IMPERATIVE. The moment there's a question mark, a 'you', a hedge or
    an opinion word, it stopped being an order and became a conversation.
    """
    for u in ("Do you think this is safe?",
              "Fix it however you think best",
              "make sense?",
              "go deep on the auth stuff"):
        assert needs_the_brain(u), u


def test_a_dangerous_order_gets_a_moment_of_thought_before_it_is_obeyed(conn):
    """'Run production without a backup' sailed through as a routine dispatch. It's
    command-shaped, so every grammar check passed — but a fast model that cheerfully
    obeys that is not a feature, it's a loaded gun."""
    for u in ("Run production without a backup",
              "delete the old accounts",
              "change the billing logic",
              "drop the patient table"):
        assert needs_the_brain(u), u


def test_real_commands_are_still_instant(conn):
    """Don't overcorrect. The whole point of the allow-list is that dispatch stays fast."""
    for u in ("kick off the rate limiter", "how's it going", "yeah go ahead",
              "what ran last night", "stop", "thanks"):
        assert not needs_the_brain(u), u


# ── #11 — quiet disagreement was being missed ────────────────────────────
def test_quiet_disagreement_counts_as_pushback(conn):
    """Sol: I'd only caught the LOUD pushback. Most disagreement is quieter than that —
    and a quiet correction that gets a shallow answer is worse than a loud one, because
    Neill won't push twice."""
    for u in ("I disagree", "you misunderstood me", "that misses the point",
              "I'm not convinced", "that worries me", "why would we do that",
              "no, because it breaks the other thing", "that feels thin"):
        assert is_pushback(u), u
