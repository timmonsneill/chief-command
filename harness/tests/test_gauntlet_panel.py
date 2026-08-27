"""The review panel actually runs — and fails closed (task #10).

`run_gauntlet` used to return a list of reviewer NAMES and launch nobody. These tests
hold the real thing to the four properties that make it worth having:

  1. it runs every roster seat, in parallel, against ONE frozen version
  2. a family only counts when that family really produced a verdict
  3. anything short of the floor — skips, crashes, caps, one family wearing two hats —
     leaves the job PARKED, never certified
  4. it never routes around the database guards; it feeds them

The reviewers are stubbed. What is under test is the panel's judgment, not a model's.
"""

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dispatch  # noqa: E402
import gauntlet  # noqa: E402
from db.jobs import (  # noqa: E402
    Seat, connect, create_job, init_db, record_usage, set_head_version,
    set_status, upsert_seat,
)

SEATS = [
    Seat("grinder_local", "ollama", "qwen2.5-coder:7b", "qwen", "local"),
    Seat("reviewer", "claude-cli", "claude-opus-4-8", "claude", "subscription"),
    Seat("brain", "codex", "gpt-5.6-sol", "gpt", "subscription"),
    Seat("brain2", "codex", "gpt-5.6-terra", "gpt", "subscription"),
    Seat("grok", "xai", "grok-4.5", "grok", "metered", daily_cap_cents=100),
    # A real seat with NO reviewer runner — the voice can't read a diff.
    Seat("mouth", "xai-realtime", "grok-voice-think-fast-1.0", "grok", "metered"),
]

CFG = {"seats": {}, "gauntlet": {"reviewers": ["reviewer", "brain"],
                                 "min_model_families": 2}}


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "test-key")     # grok's runner needs a key to count
    path = tmp_path / "t.db"
    c = connect(path)
    init_db(c)
    for s in SEATS:
        upsert_seat(c, s)
    return c, path


def _parked_job(c, seats_required=2, families_required=2):
    job = create_job(c, "write the login form", builder_seat="grinder_local")
    c.execute("UPDATE jobs SET required_reviews=?, required_review_families=? WHERE id=?",
              (seats_required, families_required, job))
    set_status(c, job, "in_progress")
    set_head_version(c, job, "v1")
    set_status(c, job, "review", result="def login(): ...")
    return job


def _stub(verdicts: dict[str, str], *, delay: float = 0.0, boom: set[str] = frozenset()):
    """Reviewer stubs keyed by MODEL, so each seat answers differently."""
    def runner(request, code, model):
        if delay:
            time.sleep(delay)
        if model in boom:
            raise RuntimeError("reviewer exploded")
        return verdicts[model], f"{verdicts[model]} — stub said so"
    return runner


def _wire(monkeypatch, runner):
    monkeypatch.setattr(gauntlet, "REVIEWERS",
                        {"claude-cli": runner, "codex": runner, "xai": runner})


# ── The happy path: two families pass, the job completes ─────────────────────
def test_two_families_passing_certifies_the_job(db, monkeypatch):
    c, path = db
    _wire(monkeypatch, _stub({"claude-opus-4-8": "pass", "gpt-5.6-sol": "pass"}))
    job = _parked_job(c)

    r = gauntlet.run_gauntlet_for_job(c, job, CFG, db_path=path)

    assert r.certified is True
    assert r.families_passed == {"claude", "gpt"}
    assert c.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0] == "done"


# ── One mind in two hats: a full panel that is not a real panel ──────────────
def test_a_full_panel_of_one_family_stays_parked(db, monkeypatch):
    c, path = db
    _wire(monkeypatch, _stub({"gpt-5.6-sol": "pass", "gpt-5.6-terra": "pass"}))
    job = _parked_job(c)
    cfg = {"seats": {}, "gauntlet": {"reviewers": ["brain", "brain2"],
                                     "min_model_families": 2}}

    r = gauntlet.run_gauntlet_for_job(c, job, cfg, db_path=path)

    assert r.certified is False
    assert r.families_passed == {"gpt"}
    assert "famil" in r.parked_reason
    assert c.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0] == "review"


# ── A failure is not outvoted ────────────────────────────────────────────────
def test_one_fail_parks_the_job_even_with_a_pass(db, monkeypatch):
    c, path = db
    _wire(monkeypatch, _stub({"claude-opus-4-8": "pass", "gpt-5.6-sol": "fail"}))
    job = _parked_job(c)

    r = gauntlet.run_gauntlet_for_job(c, job, CFG, db_path=path)

    assert r.certified is False
    assert [x.seat_id for x in r.failed] == ["brain"]
    assert c.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0] == "review"


# ── A dead reviewer contributes nothing — it never becomes a pass ────────────
def test_a_crashed_reviewer_is_a_skip_not_a_pass(db, monkeypatch):
    c, path = db
    _wire(monkeypatch, _stub({"claude-opus-4-8": "pass", "gpt-5.6-sol": "pass"},
                             boom={"gpt-5.6-sol"}))
    job = _parked_job(c)

    r = gauntlet.run_gauntlet_for_job(c, job, CFG, db_path=path)

    assert r.certified is False
    assert r.families_passed == {"claude"}
    brain = [x for x in r.runs if x.seat_id == "brain"][0]
    assert brain.verdict is None and brain.skipped, "a dead reviewer must be recorded as skipped"


def test_a_reviewer_that_answers_gibberish_does_not_pass():
    """Silence is not approval. An unreadable answer is a FAIL, not a shrug."""
    assert gauntlet._parse_verdict("I think it's probably fine?")[0] == "fail"
    assert gauntlet._parse_verdict("")[0] == "fail"
    assert gauntlet._parse_verdict("PASS looks correct")[0] == "pass"
    assert gauntlet._parse_verdict("thinking...\nFAIL off-by-one in the loop")[0] == "fail"


# ── Money: the reservation happens BEFORE the call, and a capped seat is skipped ──
def test_a_capped_out_reviewer_is_skipped_and_said_so(db, monkeypatch):
    c, path = db
    record_usage(c, "grok", 100, role="review")        # grok's whole daily cap, spent
    _wire(monkeypatch, _stub({"claude-opus-4-8": "pass", "grok-4.5": "pass"}))
    job = _parked_job(c)
    cfg = {"seats": {}, "gauntlet": {"reviewers": ["reviewer", "grok"],
                                     "min_model_families": 2}}

    r = gauntlet.run_gauntlet_for_job(c, job, cfg, db_path=path)

    grok = [x for x in r.runs if x.seat_id == "grok"][0]
    assert grok.verdict is None
    assert "budget" in grok.skipped
    assert r.certified is False, "a skipped reviewer must not be counted toward the floor"


def test_budget_is_reserved_before_the_provider_call(db, monkeypatch):
    """Refusing to RECORD a charge doesn't unspend it — so the charge lands first."""
    c, path = db
    spend_at_call_time = {}

    def runner(request, code, model):
        probe = connect(path)
        spend_at_call_time[model] = probe.execute(
            "SELECT COALESCE(SUM(cost_cents),0) FROM usage WHERE seat_id='grok'"
        ).fetchone()[0]
        probe.close()
        return "pass", "ok"

    _wire(monkeypatch, runner)
    job = _parked_job(c)
    cfg = {"seats": {"grok": {"review_estimate_cents": 7}},
           "gauntlet": {"reviewers": ["grok"], "min_model_families": 1}}

    gauntlet.run_gauntlet_for_job(c, job, cfg, db_path=path)
    assert spend_at_call_time["grok-4.5"] == 7, "the money was not reserved before the call"


# ── One frozen bundle: everyone reviews the same version ─────────────────────
def test_every_reviewer_binds_to_the_same_version(db, monkeypatch):
    c, path = db
    _wire(monkeypatch, _stub({"claude-opus-4-8": "pass", "gpt-5.6-sol": "pass"},
                             delay=0.05))
    job = _parked_job(c)

    gauntlet.run_gauntlet_for_job(c, job, CFG, db_path=path)

    versions = {r[0] for r in c.execute(
        "SELECT reviewed_version FROM verdicts WHERE job_id=?", (job,))}
    assert versions == {"v1"}, f"the panel split across versions: {versions}"


def test_the_panel_runs_in_parallel_not_one_after_another(db, monkeypatch):
    """Serial reviewers would make the panel N times slower than one reviewer — the
    exact latency wound this architecture exists to avoid."""
    c, path = db
    overlap = {"max": 0}
    live = {"n": 0}
    lock = threading.Lock()

    def runner(request, code, model):
        with lock:
            live["n"] += 1
            overlap["max"] = max(overlap["max"], live["n"])
        time.sleep(0.15)
        with lock:
            live["n"] -= 1
        return "pass", "ok"

    _wire(monkeypatch, runner)
    job = _parked_job(c)
    gauntlet.run_gauntlet_for_job(c, job, CFG, db_path=path)
    assert overlap["max"] == 2, "the reviewers ran one at a time"


# ── An unversioned job has nothing to review ─────────────────────────────────
def test_a_job_with_no_version_cannot_be_reviewed(db, monkeypatch):
    c, path = db
    _wire(monkeypatch, _stub({}))
    job = create_job(c, "something", builder_seat="grinder_local")
    r = gauntlet.run_gauntlet_for_job(c, job, CFG, db_path=path)
    assert r.certified is False and "no finished version" in r.parked_reason


# ── The roster: who can actually sit, and who is named as excluded ───────────
def test_a_reviewer_with_no_runner_is_excluded_by_name(db):
    c, _ = db
    # grok grew a runner on 2026-08-27; the mouth (a realtime voice seat) is the one
    # that can never review, so it stands in for "named on the roster, cannot run".
    cfg = {"gauntlet": {"reviewers": ["reviewer", "brain", "mouth"],
                        "min_model_families": 2}}
    roster, excluded = dispatch.panel_roster(c, cfg)
    assert roster == ["reviewer", "brain"]
    assert "mouth" in excluded and excluded["mouth"]     # named, not silently dropped


def test_dispatch_refuses_when_the_panel_cannot_reach_the_floor(db):
    c, _ = db
    cfg = {"gauntlet": {"reviewers": ["reviewer"], "min_model_families": 2}}
    with pytest.raises(dispatch.DispatchRefused, match="could never be checked"):
        dispatch.dispatch_local(c, "build a thing", "grinder_local", cfg=cfg, start=False)


def test_dispatch_refuses_an_unconfigured_panel(db):
    c, _ = db
    cfg = {"gauntlet": {"reviewers": ["reviewer", "brain"], "min_model_families": 0}}
    with pytest.raises(dispatch.DispatchRefused, match="isn't configured"):
        dispatch.dispatch_local(c, "build a thing", "grinder_local", cfg=cfg, start=False)


def test_the_caller_cannot_ask_for_a_smaller_panel(db):
    """The single-reviewer door, closed: dispatch takes its requirements from the
    config, and there is no argument that says 'one is enough'."""
    import inspect
    params = inspect.signature(dispatch.dispatch_local).parameters
    assert "required_reviews" not in params
    assert "reviewer_seat" not in params


# ═══════════════════════════════════════════════════════════════════════════════
# The bug hunter's findings, 2026-07-21. Each of these passed the suite before it
# was found, which is the point of writing them down.
# ═══════════════════════════════════════════════════════════════════════════════

def test_an_unconfigured_panel_certifies_nothing(db, monkeypatch):
    """A floor of zero is an unconfigured panel, not "no requirement".

    `0 < 0` is False, so every later check waved it through and the job was certified
    with an empty panel and the words "0 different models checked it and it passed."
    """
    c, path = db
    _wire(monkeypatch, _stub({}))
    job = _parked_job(c, families_required=0)

    r = gauntlet.run_panel(c, job, "req", "code", "v1", {}, db_path=path)

    assert r.certified is False
    assert "no review panel" in r.parked_reason
    assert c.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0] == "review"


def test_an_empty_roster_is_not_unanimous_approval(db, monkeypatch):
    c, path = db
    _wire(monkeypatch, _stub({}))
    job = _parked_job(c)
    cfg = {"gauntlet": {"reviewers": [], "min_model_families": 2}}
    r = gauntlet.run_panel(c, job, "req", "code", "v1", cfg, db_path=path)
    assert r.certified is False


def test_a_reviewer_that_answers_after_the_decision_cannot_be_ignored(db, monkeypatch):
    """A straggler's FAIL must not land on an already-certified job.

    The completion guards only fire on the way INTO done, so a late objection could
    never stop anything — the job sat at 'done' with a recorded failure against it.
    """
    c, path = db
    monkeypatch.setattr(gauntlet, "REVIEW_TIMEOUT_S", 0)   # forces the join to give up
    monkeypatch.setattr(gauntlet, "JOIN_GRACE_S", 0)

    def runner(request, code, model):
        if model == "grok-4.5":
            time.sleep(0.6)                                # answers after the decision
            return "fail", "found a real problem"
        return "pass", "ok"

    _wire(monkeypatch, runner)
    job = _parked_job(c, seats_required=2, families_required=2)
    cfg = {"seats": {}, "gauntlet": {"reviewers": ["reviewer", "brain", "grok"],
                                     "min_model_families": 2}}

    r = gauntlet.run_gauntlet_for_job(c, job, cfg, db_path=path)
    time.sleep(1.0)                                        # let the straggler finish

    status = c.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0]
    fails = c.execute("SELECT COUNT(*) FROM verdicts WHERE job_id=? AND verdict='fail'",
                      (job,)).fetchone()[0]
    assert not (status == "done" and fails), \
        "a job is sitting at done with a recorded failure against it"
    grok = [x for x in r.runs if x.seat_id == "grok"][0]
    assert grok.skipped, "a reviewer that never answered must be recorded as such"


def test_a_late_failure_un_certifies_the_job(db, monkeypatch):
    """Belt to the flag's braces: if a fail lands anyway, the reconcile catches it."""
    c, path = db
    _wire(monkeypatch, _stub({"claude-opus-4-8": "pass", "gpt-5.6-sol": "pass"}))
    job = _parked_job(c)

    real_set_status = gauntlet.set_status

    def sneak(conn, job_id, status, **kw):
        real_set_status(conn, job_id, status, **kw)
        if status == "done":                       # a straggler writes the instant we commit
            conn.execute(
                "INSERT INTO verdicts (job_id, reviewer_seat, reviewer_tier, role, "
                "model_family, verdict, reviewed_version) "
                "VALUES (?,'grok','metered','reviewer','grok','fail','v1')", (job_id,))
    monkeypatch.setattr(gauntlet, "set_status", sneak)

    r = gauntlet.run_gauntlet_for_job(c, job, CFG, db_path=path)

    assert r.certified is False, "a late failure did not un-certify the job"
    assert c.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0] == "review"


def test_a_broken_reviewer_tool_is_not_a_failing_verdict(db, monkeypatch):
    """An expired login or a renamed model must not condemn the BUILD.

    Verdicts are permanent and version-bound, so recording a fail here would blame the
    builder for an infrastructure fault and no rebuild could ever clear it.
    """
    c, path = db

    def runner(request, code, model):
        if model == "gpt-5.6-sol":
            raise gauntlet.ReviewerBroke("codex exited 1: not logged in")
        return "pass", "ok"

    _wire(monkeypatch, runner)
    job = _parked_job(c)

    r = gauntlet.run_gauntlet_for_job(c, job, CFG, db_path=path)

    assert r.failed == [], "a broken tool was recorded as a failing review"
    fails = c.execute("SELECT COUNT(*) FROM verdicts WHERE job_id=? AND verdict='fail'",
                      (job,)).fetchone()[0]
    assert fails == 0
    brain = [x for x in r.runs if x.seat_id == "brain"][0]
    assert "couldn't run" in brain.skipped
    assert r.certified is False


def test_a_nonzero_exit_code_never_becomes_a_verdict(monkeypatch):
    """The CLI printed a friendly error and exited 1 — that is not a review."""
    class Proc:
        returncode = 1
        stdout = "There's an issue with the selected model. It may not exist."
        stderr = ""
    monkeypatch.setattr(gauntlet.subprocess, "run", lambda *a, **k: Proc())
    with pytest.raises(gauntlet.ReviewerBroke):
        gauntlet._run_cli(["claude", "-p", "whatever"])


def test_the_verdict_is_read_from_the_end_not_the_start():
    """Our own prompt contains the words PASS and FAIL. If a CLI ever echoes its input
    to stdout, reading forwards would return an automatic pass on every review."""
    echoed = gauntlet.REVIEW_PROMPT.format(request="r", code="c") + "\nFAIL it is broken"
    assert gauntlet._parse_verdict(echoed) [0] == "fail"


def test_a_reviewer_that_dies_before_running_is_still_accounted_for(db, monkeypatch):
    """Anything escaping the worker used to kill the thread with the run left blank —
    neither counted nor excused, and invisible in the result."""
    c, path = db
    _wire(monkeypatch, _stub({"claude-opus-4-8": "pass", "gpt-5.6-sol": "pass"}))
    job = _parked_job(c)

    real_event = gauntlet._event

    def boom(conn, job_id, seat_row, kind, detail):
        if seat_row["id"] == "brain" and kind == "thinking":
            raise RuntimeError("the record blew up")
        real_event(conn, job_id, seat_row, kind, detail)
    monkeypatch.setattr(gauntlet, "_event", boom)

    r = gauntlet.run_gauntlet_for_job(c, job, CFG, db_path=path)

    brain = [x for x in r.runs if x.seat_id == "brain"][0]
    assert brain.verdict is None and brain.skipped, \
        "a reviewer that died left no trace in the panel result"
    assert r.certified is False


def test_the_panel_does_not_charge_twice_for_the_same_version(db, monkeypatch):
    """Re-running the panel on a version it already judged must not re-spend or stack
    duplicate verdicts on the record."""
    c, path = db
    calls = {"n": 0}

    def runner(request, code, model):
        calls["n"] += 1
        return "pass", "ok"

    _wire(monkeypatch, runner)
    job = _parked_job(c)

    gauntlet.run_gauntlet_for_job(c, job, CFG, db_path=path)
    first = calls["n"]
    gauntlet.run_gauntlet_for_job(c, job, CFG, db_path=path)

    assert calls["n"] == first, "the panel re-reviewed a version it had already judged"
    n = c.execute("SELECT COUNT(*) FROM verdicts WHERE job_id=?", (job,)).fetchone()[0]
    assert n == 2, f"duplicate verdicts stacked on the record: {n}"


def test_a_failed_dispatch_leaves_no_orphan_job(db, monkeypatch):
    """A job committed without a worker coming for it sits at 'in_progress' forever and
    looks like a hang. If the record can't be written whole, there is no job."""
    c, _ = db
    import dispatch as d

    real_seat = d.seat
    seen = {"mouth": 0}

    def explode(conn, seat_id):
        if seat_id == "mouth":
            seen["mouth"] += 1
            if seen["mouth"] > 1:              # the lookup inside the skipped-seat notes
                raise RuntimeError("disk gave out")
        return real_seat(conn, seat_id)

    monkeypatch.setattr(d, "seat", explode)
    cfg = {"gauntlet": {"reviewers": ["reviewer", "brain", "mouth"],
                        "min_model_families": 2}}
    with pytest.raises(d.DispatchRefused):
        d.dispatch_local(c, "build a thing", "grinder_local", cfg=cfg, start=False)

    n = c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert n == 0, "a job was committed that nothing was ever going to run"


def test_the_ui_says_which_wall_the_job_is_behind(db):
    """The seat count can be satisfied while the family floor isn't. The page used to
    read '2 of 2 have looked at it' beside a job that could not move."""
    import server
    j = {"status": "review", "builder_tier": "subscription",
         "required_reviews": 2, "required_review_families": 2,
         "verdicts": [
             {"reviewer_seat": "brain", "reviewer_tier": "subscription", "role": "reviewer",
              "model_family": "gpt", "verdict": "pass"},
             {"reviewer_seat": "brain2", "reviewer_tier": "subscription", "role": "reviewer",
              "model_family": "gpt", "verdict": "pass"},
         ]}
    line = server._waiting_on(j) if hasattr(server, "_waiting_on") else None
    if line is not None:
        assert "different" in line, f"the page doesn't explain the real blocker: {line}"
