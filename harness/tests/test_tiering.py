"""Model tiering. Owner (2026-07-14):

    "If we do highest model every time for every build, on autonomous work, Claude and
     ChatGPT are gonna bottom out."

He's right. The binding constraint on autonomous work is RATE LIMITS, not money — both
big seats are flat-rate, so you've already paid. But you CANNOT BUY YOUR WAY OUT OF A
WEEKLY CAP. Every top-tier call spent on boilerplate is one you don't have left for the
thing that actually needed it.

So nothing gets the top tier by default. It has to be earned.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tiering import HEAVY, LIGHT, STANDARD, tier_for_build, tier_for_review, tier_for_talk


# ── The top tier is EARNED, never assumed ────────────────────────────────
def test_routine_work_does_not_get_the_good_model():
    for r in ("scaffold the boilerplate", "write a first draft of the tests",
              "fix the typo in the readme", "rename the variable"):
        assert tier_for_build(r).tier == LIGHT, r


def test_ordinary_work_gets_the_middle_model():
    assert tier_for_build("add rate limiting to the dispatch endpoint").tier == STANDARD


# ── What earns it ─────────────────────────────────────────────────────────
def test_asking_for_it_earns_it():
    assert tier_for_build("add caching — think hard, this one matters").tier == HEAVY


def test_dangerous_things_earn_it_even_when_they_look_trivial():
    """The blast radius is what matters, not the difficulty.

    'Change how we store passwords' is three lines to write and catastrophic to get
    wrong. Difficulty and consequence are different axes and only one of them counts.

    (This test exists because the first version of the regex missed the PLURAL —
    'passwords' scored STANDARD. A missed plural here silently demotes a job that
    should have had our best model on it, which is the exact failure this file exists
    to prevent.)
    """
    for r in ("change how we store passwords", "update the login flow",
              "run the migration", "delete the old accounts", "update the invoices",
              "touch the patient records", "rotate the secrets"):
        assert tier_for_build(r).tier == HEAVY, r


def test_failing_twice_earns_it():
    """A model that has looped on something twice will loop a third time. Send someone
    better — more of the same model is the definition of insanity."""
    assert tier_for_build("build the login form", attempt=1).tier == HEAVY  # login = risky
    assert tier_for_build("add a tooltip", attempt=1).tier == STANDARD
    assert tier_for_build("add a tooltip", attempt=2).tier == STANDARD
    assert tier_for_build("add a tooltip", attempt=3).tier == HEAVY


def test_a_reviewer_smelling_smoke_earns_it():
    assert tier_for_build("add caching", worst_verdict="p1").tier == HEAVY
    assert tier_for_build("add caching", worst_verdict="p3").tier == STANDARD


def test_decisions_earn_it_because_they_are_expensive_to_reverse():
    for r in ("should we rewrite the queue or patch it", "write the spec for voice",
              "what's the right architecture here"):
        assert tier_for_build(r).tier == HEAVY, r


# ── Reviews: never send a weaker mind than the one that wrote it ─────────
def test_the_reviewer_is_never_weaker_than_the_builder():
    """A junior checking a senior's work is theatre. It produces a green tick and no
    safety at all."""
    assert tier_for_review(HEAVY, "add caching").tier == HEAVY


def test_a_dangerous_area_gets_a_hard_look_even_if_it_was_cheap_to_build():
    assert tier_for_review(LIGHT, "update the billing logic").tier == HEAVY


def test_the_tester_is_never_the_cheap_model():
    """The tester is the last thing between a bug and Neill. Being lazy here is the
    most expensive place to be lazy."""
    assert tier_for_review(LIGHT, "add a tooltip", is_tester=True).tier != LIGHT


# ── Conversation: cheap by default, deep when earned ──────────────────────
def test_ordinary_conversation_is_cheap():
    """Owner: 'Sol needs to be the brain, but prolly doesn't need to be ultra for most
    convos.' Measured: Claude at ~8s answers well. Sol at full effort took over five
    minutes on the same question — in conversation that's not thorough, it's a hang."""
    assert tier_for_talk("how's it going").tier == STANDARD


def test_pushing_back_earns_the_deep_model():
    """The honest trigger, and he named it himself: HIM saying it wasn't good enough."""
    assert tier_for_talk("that's not smart enough", pushed_back=True).tier == HEAVY


def test_a_real_decision_earns_it_in_conversation_too():
    assert tier_for_talk("should we do voice before the phone app").tier == HEAVY
