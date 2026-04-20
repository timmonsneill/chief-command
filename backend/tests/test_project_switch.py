"""Unit tests for detect_project_switch().

Covers the Hawke CRITICAL false-positives that the original regex matched:
  - "show me all the files"       -> must NOT match (no "all" project anymore)
  - "switch to all hands on deck" -> must NOT match
  - "show me arch of the design"  -> must NOT match (bare "of" after arch)

Plus the existing positives that must keep working.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.project_context import detect_project_switch  # noqa: E402


POSITIVE_CASES: list[tuple[str, str]] = [
    # Bare switch intents terminated at end-of-string.
    ("switch to Arch", "Arch"),
    ("switch to arch", "Arch"),
    # "archie" is now an alias that canonicalizes to Arch (same project,
    # Archie is the AI brain layer inside Arch).
    ("change to Archie", "Arch"),
    ("move to Archie", "Arch"),
    ("switch over to chief command", "Chief Command"),
    ("switch to chief-command", "Chief Command"),
    # Trailing punctuation terminators.
    ("Switch to Arch.", "Arch"),
    ("Switch to Archie!", "Arch"),
    ("Switch to archie, please.", "Arch"),
    # "Let's talk about X" forms.
    ("let's talk about archie", "Arch"),
    ("let's talk about Arch now", "Arch"),
    ("let's focus on archie today", "Arch"),
    # "Show me X" — short form.
    ("show me archie", "Arch"),
    ("show me Arch.", "Arch"),
    # Common-terminator trailing word allowed.
    ("switch to Arch instead", "Arch"),
    ("switch to Archie then", "Arch"),
    ("switch to Arch and let's go", "Arch"),
    # "Chief" short alias — no "command" needed.
    ("switch to Chief", "Chief Command"),
    ("switch to chief", "Chief Command"),
    ("switch to Chief, please.", "Chief Command"),
    ("switch to Chief now", "Chief Command"),
    ("show me Chief", "Chief Command"),
    ("let's talk about chief", "Chief Command"),
    # Personal Assist — canonical, hyphenated, and voice-alias "Jess".
    ("switch to Personal Assist", "Personal Assist"),
    ("switch to personal-assist", "Personal Assist"),
    ("switch to personal assist.", "Personal Assist"),
    ("switch to Jess", "Personal Assist"),
    ("show me Jess", "Personal Assist"),
    ("let's talk about Jess", "Personal Assist"),
    ("switch to Jess, please.", "Personal Assist"),
    ("switch to Jess now", "Personal Assist"),
    # --- Bare-name rule (2026-04-20) ---------------------------------------
    # Entire utterance is EXACTLY a recognized name (after trim + trailing
    # punctuation strip). Case-insensitive. Extra words disqualify.
    ("Jess", "Personal Assist"),
    ("jess", "Personal Assist"),
    ("Jess.", "Personal Assist"),
    ("jess!", "Personal Assist"),
    ("jess,", "Personal Assist"),
    ("  jess  ", "Personal Assist"),
    ("Arch", "Arch"),
    ("arch", "Arch"),
    ("Arch.", "Arch"),
    ("archie", "Arch"),
    ("Archie!", "Arch"),
    ("Chief", "Chief Command"),
    ("chief", "Chief Command"),
    ("chief!", "Chief Command"),
    ("Chief Command", "Chief Command"),
    ("chief command", "Chief Command"),
    ("chiefcommand", "Chief Command"),
    ("Personal Assist", "Personal Assist"),
    ("personal assist", "Personal Assist"),
    ("personalassist", "Personal Assist"),
]

NEGATIVE_CASES: list[str] = [
    # Hawke CRITICAL false-positives — must all return None.
    "show me all the files",
    "switch to all hands on deck",
    "show me arch of the design",
    # Legacy negatives that must continue to not match.
    "switch the arch to bcrypt",
    "the arch is looking good",
    "Archie will handle that",
    # Unrelated text.
    "",
    "   ",
    "tell me about architecture in general",
    "I want to archive this",  # "archive" shouldn't match "arch"
    "the approach here is solid",
    # Sounds-like-switch but wrong shape.
    "switching arch seems fine",  # no "to"
    "archive me the files",       # not a switch intent
    # Personal Assist negatives — "jess" / "personal" should not leak.
    "jessica called",                # "jess" inside other word, no switch verb
    "the personal assistant wrote",  # "personal assistant" not "personal assist"
    "tell me about personal finance",  # "personal" alone isn't a match
    # Bare-name rule negatives — one extra word must disqualify.
    "Jess is great",
    "jess is great",
    "arch is cool",
    "chief of staff",
    "let's go jess",
    "hey chief",
    "archie is on fire",
    "personal assist rocks",
]


def test_positives() -> None:
    failures: list[str] = []
    for text, expected in POSITIVE_CASES:
        got = detect_project_switch(text)
        if got != expected:
            failures.append(f"  {text!r} -> {got!r} (expected {expected!r})")
    if failures:
        raise AssertionError(
            "Positive cases that FAILED to match:\n" + "\n".join(failures)
        )


def test_negatives() -> None:
    failures: list[str] = []
    for text in NEGATIVE_CASES:
        got = detect_project_switch(text)
        if got is not None:
            failures.append(f"  {text!r} -> {got!r} (expected None)")
    if failures:
        raise AssertionError(
            "Negative cases that matched when they SHOULDN'T have:\n" + "\n".join(failures)
        )


if __name__ == "__main__":
    test_positives()
    test_negatives()
    print(f"OK — {len(POSITIVE_CASES)} positives + {len(NEGATIVE_CASES)} negatives all pass")
