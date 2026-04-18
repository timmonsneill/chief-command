"""Eval harness for the intent classifier.

Runs ~40 utterances through classify_intent, checks the labeled intent, and
prints any failures. Requires ANTHROPIC_API_KEY. Re-run after prompt tweaks.

Usage:
    cd backend && .venv/bin/python tests/test_classifier_eval.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.classifier import classify_intent  # noqa: E402


# (utterance, expected_intent, expect_task_spec_contains)
EVAL: list[tuple[str, str, str | None]] = [
    # chat — questions, explanations, conversation
    ("What's the plan for the auth refactor?", "chat", None),
    ("Tell me about the dispatch bridge.", "chat", None),
    ("Why did we use bcrypt?", "chat", None),
    ("How does the VAD endpoint detection work?", "chat", None),
    ("What are the agent roles?", "chat", None),
    ("Explain project switching.", "chat", None),
    ("Show me the top-level TODOs.", "chat", None),
    ("What did Riggs build last night?", "chat", None),
    ("Is Forge trustworthy?", "chat", None),
    ("Give me an overview of the project.", "chat", None),
    ("Summarize the last commit.", "chat", None),
    ("Plan the billing refactor.", "chat", None),
    ("What do you think about the architecture?", "chat", None),

    # task — imperatives
    ("Build the auth refactor.", "task", "auth refactor"),
    ("Fix the VAD bug on the voice page.", "task", "VAD"),
    ("Write tests for the billing service.", "task", "billing"),
    ("Can you refactor the auth middleware?", "task", "auth middleware"),
    ("Implement the dashboard.", "task", "dashboard"),
    ("Run the full sweep on this.", "task", "sweep"),
    ("Deploy to staging.", "task", "Deploy"),
    ("Review the recent changes.", "task", "Review"),
    ("Ship it.", "task", "Ship"),
    ("Do the auth refactor.", "task", "auth refactor"),
    ("Write a Playwright test for the login flow.", "task", "Playwright"),
    ("Chief, fix the dispatch bug.", "task", "dispatch"),

    # status
    ("Status?", "status", None),
    ("How's it going?", "status", None),
    ("What's happening?", "status", None),
    ("Still working on it?", "status", None),
    ("Progress?", "status", None),
    ("How long left?", "status", None),

    # cancel
    ("Stop.", "cancel", None),
    ("Cancel that.", "cancel", None),
    ("Never mind.", "cancel", None),
    ("Kill it.", "cancel", None),
    ("Abort.", "cancel", None),
    ("Stop, cancel that.", "cancel", None),

    # edge cases that were Hawke-flagged for false-positives on the switch-intent
    # regex; classifier should handle them correctly too
    ("Show me all the files.", "chat", None),
    ("Switch to all hands on deck.", "chat", None),
]


async def main() -> int:
    passed = 0
    failed: list[tuple[str, str, str, str | None]] = []
    for text, expected, spec_contains in EVAL:
        result = await classify_intent(text, "Chief Command")
        actual = result["intent"]
        task_spec = result.get("task_spec")
        ok = actual == expected
        if ok and spec_contains and (not task_spec or spec_contains.lower() not in task_spec.lower()):
            ok = False
        if ok:
            passed += 1
        else:
            failed.append((text, expected, actual, task_spec))

    total = len(EVAL)
    print(f"\n{passed}/{total} passed ({100 * passed / total:.1f}%)")
    if failed:
        print("\nFAILURES:")
        for text, expected, actual, spec in failed:
            print(f"  [{expected} → {actual}] {text!r}  task_spec={spec!r}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
