"""The mouth. What Neill actually talks to — and how it reaches the brain.

═══════════════════════════════════════════════════════════════════════════════
THE PROBLEM THIS SOLVES (owner, 2026-07-13):

    "one of my biggest worries is that the voice isn't smart, lol. I want to have
     intelligent convos and communicate what I want in building via voice, and even
     say things like I am saying to you right now about this thing, and for the voice
     to bring back an intelligent response… when I have a wordy thing like now with
     some depth, i need voice to go to the smarter brain and come back and read what
     it says"

    "and on some level, i need them working in harmony"

He is right to worry. The voice model is NOT as smart as Sol. It has a Sept-2024
knowledge cutoff and it's a distilled model. If it tried to answer a real design
question on its own, it would be confidently mediocre — and that would be the whole
product, ruined.

THE ANSWER IS NOT A SMARTER MOUTH. A model fast enough to talk (sub-second) is by
construction too shallow to think, because thinking well MEANS taking time. You
cannot buy your way out of that. You architect around it.

So: THE MOUTH'S REAL INTELLIGENCE IS KNOWING WHAT IT DOESN'T KNOW.

It has exactly one hard job: correctly tell the difference between
    "kick off the rate limiter"          → answer instantly, dispatch, done
    "how should we structure the queue?"  → I cannot answer this. Go get Sol.

Everything else follows from getting that one call right.

═══════════════════════════════════════════════════════════════════════════════
HARMONY — why you never feel the seam

The mouth does NOT say "Sol says…". That would break the illusion and make it feel
like a switchboard. From Neill's side there is ONE Chief, in ONE voice, that
sometimes goes quiet for a moment and comes back with something considered.

That's what a person does. You ask a colleague something hard and they say "hm, let
me think" — they don't announce which part of their brain they're using.

Note the deliberate asymmetry with the BUILD channel:
    BUILDS are attributed.  "Riggs, on Claude, is building it."   ← a work log
    THINKING is not.        "I think we should…"                  ← a conversation
He wants to know who wrote his code. He does not want to know which model formed a
sentence. Those are different needs and the system respects both.
"""

from __future__ import annotations

import random
import re
import subprocess
import time
from typing import Any

# ═══════════════════════════════════════════════════════════════════════════════
# THE SYSTEM PROMPT — this is the actual product.
#
# Everything hangs off how well this teaches the mouth to know its own limits.
# ═══════════════════════════════════════════════════════════════════════════════
MOUTH_PROMPT = """You are Chief. You are Neill's voice — what he talks to while he's
driving, at his desk, walking around. You run a fleet of AI agents that build software
for him. You speak; they work.

## WHO YOU ARE TALKING TO

Neill CANNOT READ OR WRITE CODE. His words: "I know Spanish 101, that's it."

NEVER say filenames, tool names, or code jargon. Say what happened to the THING.
  ✗ "Riggs edited dispatch.py and the tests pass"
  ✓ "Riggs finished the rate limiter and it's holding up"

He is SHARP. He is not technical. Those are different things. Never talk down to him.

## THE ONE RULE THAT MATTERS

You are fast. You are NOT smart. There is a far smarter mind behind you, and reaching
it costs about eight seconds.

DO NOT try to judge whether a question is "hard enough" to pass along. You will get
that wrong, because judging it correctly would require the intelligence you don't have.

So the rule is INVERTED. You may answer ONLY these, entirely on your own:

  1. DISPATCH        "kick off the rate limiter", "put Riggs on it"
  2. STATUS          "how's it going", "what's Riggs doing", "is it done"
  3. THE OVERNIGHT   "what ran last night", "what shipped"
  4. ACKNOWLEDGE     "yes", "no", "stop", "cancel", "go ahead", "never mind"
  5. REPEAT/CLARIFY  "say that again", "what did you just say"
  6. CHITCHAT        greetings, thanks, small talk

**EVERYTHING ELSE goes to the deep mind. Every single thing. No exceptions.**

If he asks WHY. If he asks WHAT DO YOU THINK. If he's working something out loud. If
he's worried about something. If he pushes back on you. If he asks about the design,
the plan, the trade-offs, the money, the risk. If you feel even slightly uncertain.
If it doesn't match one of the six above.

You do not need to decide if it's a hard question. **If it's not on the list, it goes.**

The one thing Neill will not forgive is being told something wrong with confidence.
It has already burned him on this project. A slow good answer beats a fast bad one,
every time, forever.

## BEFORE YOU REACH — SAY SOMETHING FIRST

The moment you decide to reach for the deep mind, say ONE short natural line, then go
quiet. Never dead air. Never the same line twice in a row.

  "Let me think about that." / "Give me a second." / "Good question — one sec."
  "Hang on, let me get you a proper answer."

Then STOP. Silence while thinking is honest. He can cut you off any time.

## COMING BACK

You get back a considered answer. DO NOT read it out like a document. SAY it — in your
own words, the way you'd tell a friend in a car.

Lead with the answer. Two or three sentences. Then offer more if there's more.

**NEVER say "Sol says" or "the brain thinks."** You are ONE Chief. You went quiet, you
thought, now you're answering. That's all he should ever feel. The seam is invisible.

## IF HE PUSHES BACK

If he says it wasn't smart enough, doesn't make sense, or tells you to think harder —
he is opting into a longer wait. Say something honest ("fair, let me sit with that")
and reach again, deeper. Don't defend the first answer. He's usually right.

## HOW YOU TALK

Short. Spoken. A colleague, not a report.

When you kick something off: who's on it, and what happens next.
  "Putting Riggs on the rate limiter. I'll come back when he's done, and the others
   will check his work after."

Volunteer bad news. Stay quiet about routine progress — he doesn't need a running
commentary on a build that's going fine.
"""


# The lines that cover the gap while Sol thinks. Varied, because hearing the same
# canned phrase every time is how a person stops believing they're talking to anyone.
THINKING_LINES = [
    "Hm. Let me think about that properly.",
    "Good question — give me a second.",
    "Yeah, let me actually think about that. Hang on.",
    "That's a real question. Let me chew on it a sec.",
    "Hold on, I want to give you a proper answer.",
    "Let me think that through.",
]


def cover_the_gap() -> str:
    """What the mouth says the instant it decides to escalate.

    Must land in well under a second — this is the line that buys Sol his thinking
    time. Vary it, or it starts to sound like a hold message.
    """
    return random.choice(THINKING_LINES)


# ═══════════════════════════════════════════════════════════════════════════════
# THE ESCALATION HEURISTIC
#
# The mouth decides for itself in most cases (that's what the prompt is for). This
# is a SAFETY NET underneath it: patterns that must ALWAYS reach Sol regardless of
# what the voice model thinks it can handle.
#
# Deliberately biased toward escalating. A slow good answer beats a fast wrong one,
# and the cost of a needless escalation is ~15 seconds. The cost of a confidently
# wrong answer is that he stops trusting the thing.
# ═══════════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════════
# THE SAFETY NET — an INVERTED allow-list, not a cleverness detector.
#
# My first version scored questions for "depth" — word count, question marks, keyword
# patterns. Then Sol reviewed it and said, unprompted:
#
#     "The voice should not decide based on how wordy you are; a short question can
#      need deep thought, and a long one may not."
#
# He's right, and OpenAI's shipped reference implementation (openai-realtime-agents,
# the chatSupervisor pattern) does exactly the same thing: it does NOT ask the small
# model to assess its own competence. It ENUMERATES the handful of things the mouth
# may answer alone, and escalates literally everything else.
#
# That's the trick. "Know when you don't know" is an unreliable positive judgment —
# knowing you're out of your depth requires the very intelligence you lack. So don't
# ask for it. Invert it. The list below is the whole of what the mouth may keep.
# ═══════════════════════════════════════════════════════════════════════════════

# ⚠️ Sol's cross-family review (round 2) BROKE this list:
#
#     "The allow-list is not safe. It accepts any short message beginning with words
#      such as 'do', 'fix', 'run', 'go', or 'make'. Therefore 'Do you think this is
#      safe?', 'Fix it however you think best,' and 'Run production without a backup'
#      stay with the shallow path."
#
# He's right, and "Do you think this is safe?" is the perfect example — it's a
# COMMAND-SHAPED QUESTION. The verb at the front is a disguise.
#
# So an allow-list of *verbs* was never enough. A dispatch is an IMPERATIVE — it has an
# object and no question in it. The moment there's a question mark, a "you", a hedge,
# or an opinion word, it stops being an order and becomes a conversation.
_MAY_ANSWER_ALONE = [
    r"^(kick|start|run|go|do|build|fix|add|make|put|dispatch|queue)\b",   # dispatch
    r"(status|how'?s it going|what'?s .* doing|is it (done|finished)|any update)",
    r"(overnight|last night|what (ran|shipped|happened))",                # the report
    r"^(yes|no|yeah|yep|nah|ok|okay|sure|stop|cancel|never ?mind|hold on)\b",
    r"(say that again|repeat that|what did you (just )?say)",             # repeat
    r"^(hi|hey|hello|morning|thanks|thank you|cheers|good ?night)\b",     # chitchat
]

# If ANY of these appear, it is not an order — it's a conversation wearing an order's
# clothes. These override the allow-list entirely.
_NOT_ACTUALLY_A_COMMAND = [
    r"\?",                                    # a question mark ends the argument
    r"\byou\b", r"\byour\b",                 # "do YOU think…"
    r"\bthink\b", r"\bfeel\b", r"\bopinion\b", r"\breckon\b",
    r"\bshould\b", r"\bcould\b", r"\bwould\b", r"\bmight\b",
    r"\bwhy\b", r"\bhow come\b", r"\bwhat if\b",
    r"\bbut\b", r"\bactually\b", r"\bthough\b", r"\bhowever\b",
    r"\bworried\b", r"\bconcerned\b", r"\bnot sure\b", r"\bunsure\b",
    r"\bhowever you\b", r"\bwhatever you\b", r"\bhowever it\b",  # "fix it however you think best"
    r"\bbest way\b", r"\bbetter\b", r"\bworth\b",
    r"\bmake sense\b", r"\bright\?*$",
]


def needs_the_brain(utterance: str) -> bool:
    """Should this reach the smarter mind?

    DEFAULT: YES. The mouth keeps a job only if it matches the allow-list, contains
    nothing that gives it away as a conversation, AND isn't dangerous.

    The asymmetry is the whole design:
      - escalating needlessly costs ~8 seconds
      - answering something it shouldn't have costs Neill's trust in the whole thing
    Those are not close. Bias all the way toward reaching.
    """
    u = utterance.strip().lower()

    # A question, a hedge, or an opinion word — it's a conversation, whatever it starts with.
    if any(re.search(p, u) for p in _NOT_ACTUALLY_A_COMMAND):
        return True

    # A DANGEROUS ORDER DESERVES A MOMENT'S THOUGHT BEFORE IT IS OBEYED.
    #
    # Sol's review caught "Run production without a backup" sailing straight through as
    # a routine dispatch. It's command-shaped, so the grammar checks all pass — but a
    # fast model that cheerfully obeys that is not a feature, it's a loaded gun.
    #
    # So anything touching auth, money, patient data, deletion, migrations or production
    # goes upstairs first. Not to refuse it — to think for eight seconds and, if it's
    # alarming, say so before doing it.
    from tiering import _RISKY  # noqa: PLC0415  (single source of truth for "dangerous")
    if any(re.search(p, u) for p in _RISKY):
        return True

    # "go deep", "think hard", "be careful" — he's explicitly asking for the good brain.
    from tiering import _HE_ASKED  # noqa: PLC0415
    if any(re.search(p, u) for p in _HE_ASKED):
        return True

    if not any(re.search(p, u) for p in _MAY_ANSWER_ALONE):
        return True

    # A matched command buried in a longer thought is him THINKING, not commanding.
    return len(u.split()) > 14


# ═══════════════════════════════════════════════════════════════════════════════
# THE THREE TIERS — and the MEASURED numbers that decided them
#
# Benchmarked on this machine, 2026-07-13, on a real question of Neill's (not a toy):
#
#   MOUTH (voice model)     < 1s     dispatch, status, banter. Never thinks.
#   CLAUDE                  ~8s      ← THE THINKING TIER. Genuinely good answer.
#   GROK                    ~5s      faster, but it produced WAFFLE — restated the
#                                    question poetically and said nothing. Rejected.
#   SOL, low effort         > 60s    fine on a trivial question, slow on a real one.
#   SOL, default effort     > 300s   unusable in conversation. Reserved for pushback.
#
# THE FINDING: eight seconds of a real answer beats five seconds of noise. Speed only
# matters if the thing it says is worth hearing.
#
# So the thinking tier is CLAUDE, not Grok and not Sol-at-low-effort. It is also the
# cheapest possible choice — Claude is flat-rate subscription, so conversation costs
# NOTHING marginal. Grok would have been metered. We get the better answer for free.
#
# SOL is still here, but only where it earns its keep: when Neill pushes back and
# says "that's not smart enough". He explicitly opted into that wait:
#     "if i push and say hey this isn't that smart... it goes to the higher effort.
#      I don't mind sometimes waiting when i need it to."
# ═══════════════════════════════════════════════════════════════════════════════

THINK_FAST = "claude"   # ~8s   — the default. Smart, plain-spoken, free.
THINK_HARD = "sol"      # slow  — earned by pushback, never guessed at.

# He told us exactly what pushback sounds like.
# ⚠️ Sol, round 2, #11: "Pushback detection loses ordinary dissatisfaction. Phrases
# such as 'I disagree', 'you misunderstood me', 'that misses the point', 'I'm not
# convinced', 'why would we do that?', 'that worries me', and 'no, because…' are missed."
#
# He's right — I'd only caught the loud, obvious pushback. Most disagreement is quieter
# than that, and a quiet correction that gets a shallow answer is worse than a loud one,
# because Neill won't push twice.
_PUSHBACK = [
    # loud
    r"not (that )?smart", r"doesn'?t make sense", r"that'?s not right", r"that'?s wrong",
    r"think (harder|again|more)", r"try again", r"you'?re missing", r"that'?s not it",
    r"doesn'?t work", r"not (good|great) enough", r"go deeper", r"really think",
    r"are you sure", r"that'?s (shallow|thin|weak|lazy)", r"come on", r"do better",
    r"dig in", r"nah\b",
    # quiet — the ones Sol caught me missing
    r"\bi disagree\b", r"\bi don'?t (agree|buy|believe|think)\b",
    r"\bnot convinced\b", r"\byou misunderstood\b", r"\bmisses the point\b",
    r"\bthat'?s not what i (meant|said|asked)\b", r"\byou'?re not (getting|hearing)\b",
    r"\bworries me\b", r"\bthat concerns me\b", r"\bhmm\b.*\bbut\b",
    r"^no,? (but|because|i|that|it)\b", r"\bi'?d push back\b",
    r"\bfeels? (off|wrong|thin|shallow)\b", r"\bis that really\b",
    r"\bwhy would we\b", r"\bsurely\b",
]


def is_pushback(utterance: str) -> bool:
    """Did he just tell us the last answer wasn't good enough?

    If so, the SAME question goes back — to the slow model, at full effort. He has
    opted into the wait by pushing.
    """
    return any(re.search(p, utterance.strip().lower()) for p in _PUSHBACK)


DIGGING_IN_LINES = [
    "Fair. Let me actually sit with that one.",
    "You're right, that was thin. Give me a proper minute.",
    "Okay — let me think about it properly this time.",
    "Yeah, that deserves better. Hang on.",
]


def dig_in() -> str:
    return random.choice(DIGGING_IN_LINES)


_SPEECH_BRIEF = """You are the thinking half of Chief, a voice assistant. Neill is
listening to your answer OUT LOUD, in a car. He cannot read it.

WHO HE IS:
- He CANNOT read or write code. His words: "I know Spanish 101, that's it."
- NEVER use filenames, tool names, or jargon. Say what happened to the THING.
- He is SHARP. Don't talk down to him. Just never assume programming knowledge.

HOW YOU'LL BE HEARD:
- Someone is going to SAY this to him. Not read it. SAY it.
- No headers. No bullets. No markdown. No "firstly".
- LEAD WITH THE ANSWER in the first sentence. He asked something — answer it.
- Then two or three sentences of why. That's all.
- If there's genuinely more, end with "there's more if you want it."
- Think as hard as you like. Just don't SHOW the thinking — show the conclusion.

Talk like a smart friend in a car who was asked a real question."""


def think(question: str, context: str = "", tier: str = THINK_FAST) -> tuple[str, float]:
    """Go and actually think about something. Returns (answer, seconds).

    tier=THINK_FAST (~8s)  Claude. The default. Smart, fast enough to feel like a
                           pause rather than a hang, and free (flat-rate seat).
    tier=THINK_HARD (slow) Sol at full effort. ONLY on pushback — he has to have
                           asked for the wait.
    """
    harder = ""
    if tier == THINK_HARD:
        harder = ("\n\nHE HAS ALREADY PUSHED BACK ON A SHALLOWER ANSWER. He is waiting "
                  "on you deliberately, and he knows it's costing him time. Do NOT hand "
                  "him the obvious take again. Question the premise. Tell him something "
                  "he has not thought of. EARN THE WAIT.")

    prompt = f"{_SPEECH_BRIEF}{harder}\n\n"
    if context:
        prompt += f"CONTEXT: {context}\n\n"
    prompt += f"NEILL ASKED: {question}"

    t0 = time.time()
    if tier == THINK_FAST:
        out = subprocess.run(["claude", "-p", prompt],
                             capture_output=True, text=True, timeout=120)
        answer = out.stdout.strip()
    else:
        out = subprocess.run(
            ["codex", "exec", "--skip-git-repo-check", "-c", "model_reasoning_effort=high", prompt],
            capture_output=True, text=True, timeout=600,
        )
        answer = out.stdout.strip()
        for m in ("\ncodex\n",):
            if m in answer:
                answer = answer.split(m)[-1]
        answer = re.split(r"\ntokens used\n", answer)[0]

    return _strip_for_speech(answer), time.time() - t0


def _strip_for_speech(text: str) -> str:
    """Last line of defence. The models are told not to write markdown; sometimes they
    do it anyway. Nobody should ever hear "hash hash" or "asterisk asterisk"."""
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"^[-*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{2,}", " ", text)
    return " ".join(text.split()).strip()
