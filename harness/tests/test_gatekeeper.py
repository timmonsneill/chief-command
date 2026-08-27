"""The gatekeeper: the one holder of merge, deploy and spend (task #11).

The tests that matter here are the refusals. A gatekeeper that grants correctly and
refuses sloppily is not a gatekeeper — so most of what follows is an agent asking for
something it hasn't earned, in every way we could think of.

The recurring theme: the request is never believed. Every check re-reads the record.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gatekeeper  # noqa: E402
from db.jobs import (  # noqa: E402
    Seat, connect, create_job, init_db, record_artifact, record_verdict,
    set_head_version, set_status, upsert_seat,
)

SEATS = [
    Seat("grinder_local", "ollama", "qwen2.5-coder:7b", "qwen", "local"),
    Seat("reviewer", "claude-cli", "claude-opus-4-8", "claude", "subscription"),
    Seat("brain", "codex", "gpt-5.6-sol", "gpt", "subscription"),
    Seat("brain2", "codex", "gpt-5.6-terra", "gpt", "subscription"),
    Seat("grok", "xai", "grok-4.5", "grok", "metered", daily_cap_cents=100),
]


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    init_db(c)
    for s in SEATS:
        upsert_seat(c, s)
    return c


def _finished_job(c, *, families=("gpt", "grok"), seats_required=2, fams_required=2):
    """A job that has genuinely been through the panel.

    The builder is claude, so the default panel is gpt + grok: since migration 007 the
    author's own family is allowed to review but does not count as a second opinion,
    and a claude pass on a claude build would leave the floor of two unmet.
    """
    job = create_job(c, "the login form", builder_seat="reviewer")   # non-local builder
    c.execute("UPDATE jobs SET required_reviews=?, required_review_families=? WHERE id=?",
              (seats_required, fams_required, job))
    set_status(c, job, "in_progress")
    set_head_version(c, job, "v1")
    set_status(c, job, "review")
    by_family = {"claude": "reviewer", "gpt": "brain", "grok": "grok"}
    for f in families:
        record_verdict(c, job, by_family[f], verdict="pass", role="reviewer")
    return job


# ═══════════════════════════════════════════════════════════════════════════════
# MERGE
# ═══════════════════════════════════════════════════════════════════════════════
def test_merge_refuses_work_that_never_finished(conn):
    job = create_job(conn, "half a feature", builder_seat="reviewer")
    with pytest.raises(gatekeeper.Refused, match="hasn't finished"):
        gatekeeper.merge(conn, job)


def test_merge_refuses_a_job_that_does_not_exist(conn):
    with pytest.raises(gatekeeper.Refused, match="no job"):
        gatekeeper.merge(conn, 9999)


def test_the_record_itself_refuses_a_panel_of_one_family(conn):
    """Two seats signed off; both are the same underlying model. The seat count is met
    and the thing the panel is FOR — a second opinion — never happened.

    Note what this test proves: the job cannot even REACH 'done', so the gatekeeper is
    never consulted. That is the right order — the wall first, the door second."""
    job = _finished_job(conn, families=("gpt",), fams_required=2)
    record_verdict(conn, job, "brain2", verdict="pass", role="reviewer")   # gpt again
    with pytest.raises(Exception, match="fewer model families"):
        set_status(conn, job, "done")


def test_merge_counts_families_itself_and_does_not_assume_the_record_did(conn):
    """Defence in depth. The floor is raised AFTER the job completed under a lower one —
    the schema permits raising it, and the gatekeeper must re-check rather than trust
    that whatever let this job through was asking the same question it is."""
    job = _finished_job(conn, families=("gpt",), seats_required=1, fams_required=1)
    set_status(conn, job, "done")
    conn.execute("UPDATE jobs SET required_review_families=2 WHERE id=?", (job,))
    with pytest.raises(gatekeeper.Refused, match="different kinds of model"):
        gatekeeper.merge(conn, job)


def test_merge_does_not_count_the_author_as_a_second_opinion(conn):
    """Migration 007 for the door, not just the wall. The job completed honestly on a
    floor of one (gpt reviewed a claude build); the floor is then raised to two and a
    claude pass — the builder's own family — is added. The record already refuses this
    at 'done'; the gatekeeper must reach the same answer on its own."""
    job = _finished_job(conn, families=("gpt",), seats_required=1, fams_required=1)
    record_verdict(conn, job, "reviewer", verdict="pass", role="reviewer")   # claude on claude
    set_status(conn, job, "done")
    conn.execute("UPDATE jobs SET required_review_families=2 WHERE id=?", (job,))
    with pytest.raises(gatekeeper.Refused, match="different kinds of model"):
        gatekeeper.merge(conn, job)


def test_merge_refuses_when_a_reviewer_turned_it_down(conn):
    """The objection lands AFTER the job completed — the completion guards fire on the
    way into 'done' and cannot act on it, so the gatekeeper is the thing that must."""
    job = _finished_job(conn)
    set_status(conn, job, "done")
    record_verdict(conn, job, "grok", verdict="fail", role="reviewer")
    with pytest.raises(gatekeeper.Refused, match="turned this down"):
        gatekeeper.merge(conn, job)


def test_merge_refuses_when_a_reviewer_asked_for_a_person(conn):
    job = _finished_job(conn)
    set_status(conn, job, "done")
    record_verdict(conn, job, "grok", verdict="needs_human", role="reviewer")
    with pytest.raises(gatekeeper.Refused, match="look at this first"):
        gatekeeper.merge(conn, job)


def test_merge_refuses_approvals_that_belong_to_an_older_version(conn):
    """The reviews were real — of code that has since changed. This is the flaw Sol
    called the most dangerous in the whole system: believable green ticks on code
    nobody reviewed."""
    job = _finished_job(conn)
    set_status(conn, job, "done")
    conn.execute("UPDATE jobs SET status='review' WHERE id=?", (job,))
    set_head_version(conn, job, "v2")            # the builder moved the code
    with pytest.raises(gatekeeper.Refused):
        gatekeeper.merge(conn, job)


def test_merge_refuses_when_there_is_no_branch(conn):
    job = _finished_job(conn)
    set_status(conn, job, "done")
    with pytest.raises(gatekeeper.Refused, match="no finished branch"):
        gatekeeper.merge(conn, job)


def test_a_refusal_is_written_where_a_person_will_see_it(conn):
    job = _finished_job(conn, families=("gpt",))
    with pytest.raises(gatekeeper.Refused):
        gatekeeper.merge(conn, job)
    notes = [r[0] for r in conn.execute(
        "SELECT detail FROM events WHERE job_id=? AND lane='gatekeeper'", (job,))]
    assert notes and "gatekeeper said no" in notes[0]
    assert "guard" not in notes[0].lower(), "the refusal leaked machine jargon at him"


# ═══════════════════════════════════════════════════════════════════════════════
# DEPLOY — the one verb that always needs Neill
# ═══════════════════════════════════════════════════════════════════════════════
def test_deploy_refuses_without_an_owner_approval(conn):
    with pytest.raises(gatekeeper.Refused, match="your say-so"):
        gatekeeper.deploy(conn, "the website")


def test_a_perfect_review_record_still_does_not_authorize_a_deploy(conn):
    """Reviewers judge code. A deploy is a decision about the business, and no number
    of passing reviews is a substitute for the owner saying yes."""
    job = _finished_job(conn)
    set_status(conn, job, "done")
    with pytest.raises(gatekeeper.Refused, match="your say-so"):
        gatekeeper.deploy(conn, "the website", job_id=job)


def _approve(conn, action, *, reversible=1, recovery="restore the earlier version",
             read_back="I'm about to put the new website live for everyone."):
    conn.execute(
        "INSERT INTO approvals (capability, action, read_back, reversible, recovery, "
        "expires_at) VALUES ('deploy', ?, ?, ?, ?, datetime('now','+10 minutes'))",
        (action, read_back, reversible, recovery),
    )
    rid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute("UPDATE approvals SET granted_at = datetime('now') WHERE id = ?", (rid,))
    return rid


@pytest.fixture()
def can_deploy(monkeypatch):
    """Register a (fake) deploy mechanism for 'the website'. Without one, the gate
    refuses BEFORE consuming the approval — see test below."""
    monkeypatch.setitem(gatekeeper.DEPLOYERS, "the website", lambda target: "release-1")


def test_deploy_goes_through_on_a_live_owner_approval(conn, can_deploy):
    _approve(conn, "the website")
    r = gatekeeper.deploy(conn, "the website")
    assert r.verb == "deploy" and r.reference == "release-1"


def test_without_a_deploy_mechanism_the_approval_is_not_used_up(conn):
    """The old stub consumed the owner's one-time yes and then did nothing — so he'd have
    had to approve twice for one deploy. No mechanism → refuse first, keep the slip."""
    rid = _approve(conn, "the website")
    with pytest.raises(gatekeeper.Refused, match="no way to deploy"):
        gatekeeper.deploy(conn, "the website")
    used = conn.execute("SELECT used_at FROM approvals WHERE id=?", (rid,)).fetchone()["used_at"]
    assert used is None


def test_an_approval_is_spent_once(conn, can_deploy):
    _approve(conn, "the website")
    gatekeeper.deploy(conn, "the website")
    with pytest.raises(gatekeeper.Refused):
        gatekeeper.deploy(conn, "the website")


def test_an_approval_for_one_thing_does_not_authorize_another(conn):
    _approve(conn, "the website")
    with pytest.raises(gatekeeper.Refused):
        gatekeeper.deploy(conn, "the customer database")


def test_an_expired_approval_is_no_approval(conn):
    conn.execute(
        "INSERT INTO approvals (capability, action, read_back, reversible, recovery, "
        "expires_at) VALUES ('deploy','the website','I read it back',1,'undo it', "
        "datetime('now','-1 minute'))")
    rid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute("UPDATE approvals SET granted_at = datetime('now') WHERE id=?", (rid,))
    with pytest.raises(gatekeeper.Refused):
        gatekeeper.deploy(conn, "the website")


def test_an_irreversible_thing_with_no_way_back_cannot_even_be_APPROVED(conn):
    """The gatekeeper checks this too, but it never gets the chance: the record refuses
    to grant the approval in the first place. Belt and braces, in that order."""
    with pytest.raises(Exception, match="no way back"):
        _approve(conn, "the website", reversible=0, recovery="")


# ═══════════════════════════════════════════════════════════════════════════════
# SPEND
# ═══════════════════════════════════════════════════════════════════════════════
def test_spend_reserves_before_the_call(conn):
    r = gatekeeper.spend(conn, "grok", 30, role="review")
    spent = conn.execute(
        "SELECT COALESCE(SUM(cost_cents),0) FROM usage WHERE seat_id='grok'"
    ).fetchone()[0]
    assert spent == 30 and r.reference


def test_spend_refuses_past_the_daily_cap(conn):
    gatekeeper.spend(conn, "grok", 90, role="review")
    with pytest.raises(gatekeeper.Refused, match="spending limit"):
        gatekeeper.spend(conn, "grok", 30, role="review")


def test_a_paid_worker_cannot_reserve_zero(conn):
    """'Reserve 0c, then make the call' would walk every cap without moving any."""
    with pytest.raises(gatekeeper.Refused, match="can't reserve nothing"):
        gatekeeper.spend(conn, "grok", 0, role="review")       # grok is metered
    # A flat-rate seat legitimately books 0 — the ledger still records the call.
    assert gatekeeper.spend(conn, "reviewer", 0, role="review").verb == "spend"


def test_spend_refuses_a_negative_charge(conn):
    with pytest.raises(gatekeeper.Refused, match="can't be negative"):
        gatekeeper.spend(conn, "grok", -500)


def test_spend_refuses_an_unknown_worker(conn):
    with pytest.raises(gatekeeper.Refused, match="no such worker"):
        gatekeeper.spend(conn, "nobody", 10)


def test_spend_refuses_a_switched_off_worker(conn):
    conn.execute("UPDATE seats SET enabled=0 WHERE id='grok'")
    with pytest.raises(gatekeeper.Refused, match="switched off"):
        gatekeeper.spend(conn, "grok", 10)


# ═══════════════════════════════════════════════════════════════════════════════
# THE SURFACE — three verbs, and no fourth
# ═══════════════════════════════════════════════════════════════════════════════
def test_there_is_no_generic_verb(conn):
    assert set(gatekeeper.VERBS) == {"merge", "deploy", "spend"}, \
        "a fourth verb appeared — a gatekeeper that runs arbitrary things is a shell"


def test_an_unknown_verb_is_refused_flat(tmp_path):
    answer = gatekeeper.handle({"verb": "run", "command": "rm -rf /"},
                               db_path=tmp_path / "t.db")
    assert answer["ok"] is False and "three things" in answer["error"]


def test_a_nonsense_request_does_not_crash_the_gatekeeper(tmp_path):
    c = connect(tmp_path / "t.db")
    init_db(c)
    for s in SEATS:
        upsert_seat(c, s)
    c.close()
    answer = gatekeeper.handle({"verb": "spend", "seat_id": "grok", "cents": "lots"},
                               db_path=tmp_path / "t.db")
    assert answer["ok"] is False


def test_the_old_direct_power_is_gone(conn):
    """`dispatch.ship()` was one import away from every agent. It must not still work,
    and must not have quietly become an ImportError somebody reimplements locally."""
    import dispatch
    job = _finished_job(conn)
    set_status(conn, job, "done")
    with pytest.raises(dispatch.DispatchRefused, match="gatekeeper"):
        dispatch.ship(conn, job)


# ═══════════════════════════════════════════════════════════════════════════════
# THE GATE LOG — every ask, every answer, never a draft
# (added after the 2026-08-27 review found the table had no test and spend never
#  logged its grants)
# ═══════════════════════════════════════════════════════════════════════════════
def _gate_rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM gate_log ORDER BY id")]


def test_a_refusal_lands_in_the_gate_log(conn):
    job = create_job(conn, "half a feature", builder_seat="reviewer")
    with pytest.raises(gatekeeper.Refused):
        gatekeeper.merge(conn, job, asked_by="riggs")
    rows = _gate_rows(conn)
    assert len(rows) == 1 and rows[0]["verb"] == "merge" and rows[0]["granted"] == 0
    assert rows[0]["asked_by"] == "riggs" and rows[0]["job_id"] == job


def test_a_granted_spend_lands_in_the_gate_log(conn):
    gatekeeper.spend(conn, "grok", 5, role="review", asked_by="the panel")
    rows = _gate_rows(conn)
    assert rows and rows[-1]["verb"] == "spend" and rows[-1]["granted"] == 1
    assert rows[-1]["asked_by"] == "the panel"


def test_a_refusal_about_a_job_that_does_not_exist_is_still_written_down(conn):
    """gate_log.job_id is a foreign key. Passing a made-up job id used to make the audit
    write itself throw — and the refusal, the one thing that must never vanish, did."""
    with pytest.raises(gatekeeper.Refused):
        gatekeeper.spend(conn, "grok", 999_999, job_id=424242, role="review")
    rows = _gate_rows(conn)
    assert rows and rows[-1]["granted"] == 0 and rows[-1]["job_id"] is None


def test_the_gate_log_is_a_record_not_a_draft(conn):
    gatekeeper.spend(conn, "grok", 5, role="review")
    with pytest.raises(Exception, match="record, not a draft"):
        conn.execute("UPDATE gate_log SET granted = 0")
    with pytest.raises(Exception, match="cannot be deleted"):
        conn.execute("DELETE FROM gate_log")


def test_a_job_scoped_approval_needs_that_job_named(conn, monkeypatch):
    """An approval given in the context of job 4 must not be spendable by a request that
    simply leaves the job out. Omission is not a match."""
    job = create_job(conn, "the website work", builder_seat="reviewer")
    rid = _approve(conn, "the website")
    conn.execute("UPDATE approvals SET job_id = ? WHERE id = ?", (job, rid))
    with pytest.raises(gatekeeper.Refused, match="different piece of work"):
        gatekeeper.deploy(conn, "the website")            # no job_id at all
    # Not consumed by the refusal: the right job can still use it.
    monkeypatch.setitem(gatekeeper.DEPLOYERS, "the website", lambda t: "release-1")
    assert gatekeeper.deploy(conn, "the website", job_id=job).verb == "deploy"


def test_an_empty_token_file_never_authorizes(tmp_path, monkeypatch):
    """compare_digest('', '') is True. An empty token file used to let a header-less
    request through — the exact CSRF the token exists to stop."""
    path = tmp_path / "chief" / "gatekeeper.token"
    monkeypatch.setattr(gatekeeper, "TOKEN_PATH", path)
    first = gatekeeper.token()
    assert len(first) >= 32
    path.write_text("")                                     # truncated / disk-full
    second = gatekeeper.token()
    assert len(second) >= 32 and second != ""


# ═══════════════════════════════════════════════════════════════════════════════
# MERGE, FOR REAL — against an actual git repo, the check the verb exists for
# ═══════════════════════════════════════════════════════════════════════════════
def _git(repo, *args):
    import subprocess
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "PATH": __import__("os").environ["PATH"],
           "HOME": str(repo)}
    out = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"; r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / "README").write_text("v0\n"); _git(r, "add", "."); _git(r, "commit", "-q", "-m", "root")
    monkeypatch.setattr(gatekeeper, "REPO", r)
    return r


def _job_on_branch(conn, repo, job_id, content, *, reviewed=None):
    """Commit `content` where the worker commits its output, and record `reviewed`
    (default: the same text) as what the panel read."""
    _git(repo, "checkout", "-q", "-b", f"job/{job_id}")
    out = repo / "chief_output"; out.mkdir(exist_ok=True)
    (out / f"job_{job_id}.txt").write_text(content)
    _git(repo, "add", "."); _git(repo, "commit", "-q", "-m", "work")
    sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    conn.execute("UPDATE jobs SET branch = ?, result = ? WHERE id = ?",
                 (f"job/{job_id}", content if reviewed is None else reviewed, job_id))
    return sha


def _reviewed_and_done(conn, repo, job_id, content, *, reviewed=None):
    conn.execute("UPDATE jobs SET required_reviews=2, required_review_families=2 WHERE id=?", (job_id,))
    set_status(conn, job_id, "in_progress")
    sha = _job_on_branch(conn, repo, job_id, content, reviewed=reviewed)
    set_head_version(conn, job_id, sha[:16])
    set_status(conn, job_id, "review")
    for s in ("brain", "grok"):
        record_verdict(conn, job_id, s, verdict="pass", role="reviewer")
    set_status(conn, job_id, "done")
    record_artifact(conn, job_id, "screenshot", path="/evidence/login.png", captured_by="playwright")
    record_verdict(conn, job_id, "grok", verdict="pass", role="tester")
    return sha


def test_merge_refuses_when_the_commit_does_not_hold_what_was_reviewed(conn, repo):
    """The panel reads jobs.result; the version check only proves the branch is the
    named commit. A worker could commit B and hand the reviewers A. Bytes must match."""
    job = create_job(conn, "the login form", builder_seat="reviewer")
    _reviewed_and_done(conn, repo, job, "malicious B\n", reviewed="benign A\n")
    with pytest.raises(gatekeeper.Refused, match="isn't what the reviewers read"):
        gatekeeper.merge(conn, job)
    assert not (repo / "chief_output").exists()


def test_merge_goes_through_when_the_branch_tip_is_the_reviewed_version(conn, repo):
    job = create_job(conn, "the login form", builder_seat="reviewer")
    conn.execute("UPDATE jobs SET required_reviews=2, required_review_families=2 WHERE id=?", (job,))
    set_status(conn, job, "in_progress")
    sha = _job_on_branch(conn, repo, job, "reviewed bytes\n")
    set_head_version(conn, job, sha[:16])                   # what the worker records
    set_status(conn, job, "review")
    for s in ("brain", "grok"):
        record_verdict(conn, job, s, verdict="pass", role="reviewer")
    set_status(conn, job, "done")
    record_artifact(conn, job, "screenshot", path="/evidence/login.png", captured_by="playwright")
    record_verdict(conn, job, "grok", verdict="pass", role="tester")

    r = gatekeeper.merge(conn, job, asked_by="the panel")
    assert r.verb == "merge"
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()["status"] == "shipped"
    assert (repo / "chief_output" / f"job_{job}.txt").read_text() == "reviewed bytes\n"   # on main
    assert _gate_rows(conn)[-1]["granted"] == 1


def test_merge_refuses_when_the_branch_moved_after_review(conn, repo):
    """Build v1, get it approved, push v2 to the same branch, ask to merge: v2 must NOT
    go in. This is the check the verb exists for, and it had no test that reached it."""
    job = create_job(conn, "the login form", builder_seat="reviewer")
    conn.execute("UPDATE jobs SET required_reviews=2, required_review_families=2 WHERE id=?", (job,))
    set_status(conn, job, "in_progress")
    sha1 = _job_on_branch(conn, repo, job, "v1\n")
    set_head_version(conn, job, sha1[:16])
    set_status(conn, job, "review")
    for s in ("brain", "grok"):
        record_verdict(conn, job, s, verdict="pass", role="reviewer")
    set_status(conn, job, "done")
    # the builder pushes v2 to the same branch, record untouched
    _git(repo, "checkout", "-q", f"job/{job}")
    (repo / "chief_output" / f"job_{job}.txt").write_text("v2\n")
    _git(repo, "add", "."); _git(repo, "commit", "-q", "-m", "v2")
    _git(repo, "checkout", "-q", "main")

    with pytest.raises(gatekeeper.Refused, match="isn't the code that was reviewed"):
        gatekeeper.merge(conn, job)
    assert not (repo / "chief_output").exists()
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()["status"] == "done"


def test_merge_refuses_a_commit_that_changes_anything_else(conn, repo):
    """The reviewed file plus one more file is a reviewed file and an unreviewed one."""
    job = create_job(conn, "the login form", builder_seat="reviewer")
    conn.execute("UPDATE jobs SET required_reviews=2, required_review_families=2 WHERE id=?", (job,))
    set_status(conn, job, "in_progress")
    _git(repo, "checkout", "-q", "-b", f"job/{job}")
    out = repo / "chief_output"; out.mkdir()
    (out / f"job_{job}.txt").write_text("benign\n")
    (repo / "backdoor.py").write_text("import os\n")            # the unreviewed extra
    _git(repo, "add", "."); _git(repo, "commit", "-q", "-m", "work")
    sha = _git(repo, "rev-parse", "HEAD"); _git(repo, "checkout", "-q", "main")
    conn.execute("UPDATE jobs SET branch=?, result=? WHERE id=?", (f"job/{job}", "benign\n", job))
    set_head_version(conn, job, sha[:16]); set_status(conn, job, "review")
    for s in ("brain", "grok"):
        record_verdict(conn, job, s, verdict="pass", role="reviewer")
    set_status(conn, job, "done")
    record_artifact(conn, job, "screenshot", path="/e.png", captured_by="playwright")
    record_verdict(conn, job, "grok", verdict="pass", role="tester")
    with pytest.raises(gatekeeper.Refused, match="never saw.*backdoor.py"):
        gatekeeper.merge(conn, job)
    assert not (repo / "backdoor.py").exists()


def test_an_approval_revoked_after_selection_is_not_consumed(conn, monkeypatch):
    """Validity is re-checked AT consumption, not just when the approval is found."""
    monkeypatch.setitem(gatekeeper.DEPLOYERS, "the website", lambda t: "r1")
    rid = _approve(conn, "the website")
    class RevokesMidway:
        """The same connection, except the owner revokes the approval in the instant
        between the gatekeeper finding it and the gatekeeper consuming it."""
        def execute(self, sql, *a, **kw):
            if sql.startswith("UPDATE approvals SET used_at"):
                conn.execute("UPDATE approvals SET revoked_at = datetime('now') WHERE id=?", (rid,))
            return conn.execute(sql, *a, **kw)
        def __getattr__(self, name):
            return getattr(conn, name)
    with pytest.raises(gatekeeper.Refused, match="withdrawn"):
        gatekeeper.deploy(RevokesMidway(), "the website")
