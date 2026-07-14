"""Which model does this work actually deserve?

═══════════════════════════════════════════════════════════════════════════════
THE PROBLEM (owner, 2026-07-14):

    "If we do highest model every time for every build, on autonomous work, Claude
     and ChatGPT are gonna bottom out. I need us to build in model tiering as well.
     Sol needs to be the brain, but prolly doesn't need to be ultra for most convos...
     And then builders need to be tiered. Reviewers prolly need to be opus and one
     model down for ChatGPT, unless we say to go high review or we have a good trigger
     to do higher reviews."

He's right, and it's the difference between a system that runs for a month and one
that dies on Thursday.

THE THING TO UNDERSTAND: the binding constraint on autonomous work is RATE LIMITS,
not money. Claude and OpenAI are flat-rate — you've already paid. But a hard-run
fleet burns weekly caps, and **you cannot buy your way out of a weekly cap.** You can
only spend it wisely. Every top-tier call on a piece of boilerplate is a top-tier call
you don't have left for the thing that actually needed it.

So: NOTHING gets the top tier by default. It has to be EARNED.

═══════════════════════════════════════════════════════════════════════════════
WHAT EARNS IT

  1. HE ASKS.            "think hard about this", "this is important", "go deep"
  2. IT'S RISKY.         auth, money, patient data, deletion, migrations, secrets.
                         The blast radius of being wrong is what matters, not the
                         difficulty. A three-line change to how we store passwords
                         deserves the best model we own.
  3. IT ALREADY FAILED.  Second attempt goes up a tier. Third goes to the top. If a
                         model has looped on something twice, more of the same model
                         is the definition of insanity.
  4. IT'S A DECISION.    Specs, architecture, "should we". These are the calls that
                         are expensive to reverse — exactly where an extra minute of
                         thinking is worth more than an hour of building.
  5. A REVIEWER SHOUTED. If any reviewer comes back p0/p1, the re-review goes heavy.
                         Somebody smelled smoke; send the better nose.

Everything else — scaffolding, boilerplate, tests, renames, docs, first drafts — runs
LIGHT or STANDARD. That's the vast majority of the work, and it's fine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

LIGHT, STANDARD, HEAVY = "light", "standard", "heavy"


@dataclass(frozen=True)
class TierCall:
    tier: str
    reason: str


# ── 1. He asked for it ────────────────────────────────────────────────────
_HE_ASKED = [
    r"\bthink (hard|deep|carefully|properly)", r"\bgo deep\b", r"\bbe thorough\b",
    r"\bthis (one )?(is|matters|really)\b.*\bimportant\b", r"\bdon'?t (rush|screw)",
    r"\bcareful\b", r"\btake your time\b", r"\bmost important\b", r"\bcritical\b",
    r"\bhigh (effort|review)\b", r"\bultra\b", r"\bbest model\b",
]

# ── 2. The blast radius is big ────────────────────────────────────────────
# Note this is about CONSEQUENCE, not difficulty. A three-line change to how we store
# passwords is trivial to write and catastrophic to get wrong.
# NOTE: no trailing \b — "password" must also catch "passwords", "migrate" must catch
# "migration". A missed plural here is a top-tier job silently demoted, which is the
# exact failure this whole file exists to prevent. (Caught in testing: "change how we
# store passwords" was scoring STANDARD.)
_RISKY = [
    r"\bauth", r"\blogin", r"\bpassword", r"\bsecret", r"\bpasswd",
    r"\btoken", r"\bcredential", r"\bpermission", r"\brole\b", r"\bsession",
    r"\bpay", r"\bbilling", r"\bcharge", r"\bstripe", r"\bmoney", r"\binvoice",
    r"\bpatient", r"\bphi\b", r"\bhipaa", r"\bmedical", r"\bhealth record",
    r"\bdelete", r"\bdrop\b", r"\bmigrat", r"\bschema", r"\bproduction",
    r"\bsecurity", r"\bencrypt", r"\bprivate key", r"\bwipe", r"\bpurge",
    r"\bpii\b", r"\bpersonal data", r"\bssn\b", r"\bcard number",
]

# ── 3. It's a decision, not a task ────────────────────────────────────────
_A_DECISION = [
    r"\bspec\b", r"\barchitect", r"\bdesign\b", r"\bshould we\b", r"\bwhich (way|one)\b",
    r"\btrade-?off", r"\bstrategy\b", r"\bplan\b", r"\bapproach\b", r"\bworth it\b",
    r"\bpros and cons\b", r"\brefactor\b.*\bwhole\b", r"\brewrite\b",
]

# ── The easy stuff. Most of the work lives here, and that's the point. ────
_ROUTINE = [
    r"\bboilerplate\b", r"\bscaffold", r"\bstub\b", r"\brename\b", r"\btypo\b",
    r"\bcomment", r"\bdocs?\b", r"\breadme\b", r"\bformat", r"\blint\b",
    r"\bfirst draft\b", r"\bsketch\b", r"\bplaceholder\b",
]


def tier_for_build(request: str, attempt: int = 1, worst_verdict: str | None = None) -> TierCall:
    """What does this piece of work deserve?

    Order matters. Failure and explicit asks beat everything, because they're the two
    signals we KNOW are real — the rest are guesses from keywords.
    """
    r = request.lower()

    # A model that has failed twice will fail a third time. Send someone better.
    if attempt >= 3:
        return TierCall(HEAVY, "it's failed twice — sending the best model we've got")
    if attempt == 2:
        return TierCall(STANDARD, "second attempt, moving up a tier")

    # Somebody smelled smoke on the last pass.
    if worst_verdict in ("p0", "p1"):
        return TierCall(HEAVY, f"a reviewer flagged this {worst_verdict} — sending the better nose")

    if any(re.search(p, r) for p in _HE_ASKED):
        return TierCall(HEAVY, "you asked for the best model")

    if any(re.search(p, r) for p in _RISKY):
        return TierCall(HEAVY, "this touches something dangerous to get wrong")

    if any(re.search(p, r) for p in _A_DECISION):
        return TierCall(HEAVY, "this is a decision, and decisions are expensive to reverse")

    if any(re.search(p, r) for p in _ROUTINE):
        return TierCall(LIGHT, "routine work — no need for the good stuff")

    return TierCall(STANDARD, "ordinary work")


def tier_for_review(builder_tier: str, request: str, is_tester: bool = False) -> TierCall:
    """What does REVIEWING this deserve?

    Owner: "Reviewers prolly need to be opus and one model down for ChatGPT, unless we
    say to go high review or we have a good trigger to do higher reviews."

    The principle: THE REVIEWER SHOULD NEVER BE WEAKER THAN THE BUILDER. A junior
    checking a senior's work is theatre — it produces a green tick and no safety. So
    the review tier floors at whatever built it.

    Reviews are also CHEAP (read-heavy, roughly a tenth the tokens of a build), so
    being generous here costs far less than being generous on the build side.
    """
    r = request.lower()

    if any(re.search(p, r) for p in _HE_ASKED):
        return TierCall(HEAVY, "you asked for a hard look")

    if any(re.search(p, r) for p in _RISKY):
        return TierCall(HEAVY, "the thing it touches is dangerous")

    # The tester DRIVES THE APP. It's the last line before something reaches Neill,
    # and it's the seat where being lazy is most expensive.
    if is_tester:
        return TierCall(STANDARD, "somebody has to actually use it")

    # Never send a weaker mind than the one that wrote it.
    if builder_tier == HEAVY:
        return TierCall(HEAVY, "the best model wrote it, so the best model checks it")

    return TierCall(STANDARD, "ordinary review")


def tier_for_talk(utterance: str, pushed_back: bool = False) -> TierCall:
    """What does a CONVERSATION deserve?

    Owner: "Sol needs to be the brain, but prolly doesn't need to be ultra for most
    convos. Maybe if it's managing a major spec and deciding builds and such, but
    generally lower effort in convo and idk, needs to tier up conversationally."

    Exactly right, and the measured numbers back him: Claude at ~8s gives a genuinely
    good conversational answer. Sol at full effort took over five minutes on the same
    question. In a conversation, five minutes is not "more thorough" — it's a hang.

    So the ladder is: standard for talk, heavy only when he pushes or it's a real
    decision. He named the trigger himself and it's the honest one — HIM saying it
    wasn't good enough.
    """
    if pushed_back:
        return TierCall(HEAVY, "you said that wasn't good enough")

    u = utterance.lower()
    if any(re.search(p, u) for p in _HE_ASKED):
        return TierCall(HEAVY, "you asked me to think hard")
    if any(re.search(p, u) for p in _A_DECISION):
        return TierCall(HEAVY, "this is a real decision")

    return TierCall(STANDARD, "normal conversation")


def resolve_model(seat_row, tier: str) -> tuple[str, str]:
    """Turn (seat, tier) into the actual model + effort to run.

    Falls back gracefully: a seat that only defines one model just uses it. That means
    adding tiers to a seat is opt-in and nothing breaks if you don't.
    """
    keys = {
        LIGHT:    ("model_light", "effort_light"),
        STANDARD: ("model_standard", "effort_standard"),
        HEAVY:    ("model_heavy", "effort_heavy"),
    }
    mk, ek = keys[tier]
    model = (seat_row[mk] if mk in seat_row.keys() and seat_row[mk] else None) or seat_row["model"]
    effort = (seat_row[ek] if ek in seat_row.keys() and seat_row[ek] else None) or "medium"
    return model, effort
