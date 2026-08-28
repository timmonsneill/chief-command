# Lane memory — Riggs (backend, Chief Command harness)

This is THIS repository's own conventions — Python harness code, not the Arch-EMR
memory files under ~/.claude/agents/memory/ (a different project entirely:
Express/Node/React, vitest, numbered SQL migrations for a medical-records app —
none of that applies here, and prepending it was flagged and rejected).

## What this project is
A voice-first multi-model agent orchestration harness (Python, `harness/`). Neill
talks to it; it routes work to a stack of models, reviews its own output through a
review panel ("the gauntlet"), and reports back. He cannot read or write code.

## Non-negotiable rules
- NO Google/Gemini anywhere (their ToS forbids third-party OAuth; consumer CLI
  access was banned in 2026).
- Providers are referenced by SEAT id (`riggs`, `reviewer`, `brain`, `grok`,
  `grinder_local`), never by vendor name, in any orchestration code.
- Local model output never ships without a higher-tier cross-family review — this
  is enforced by database triggers in `harness/db/schema.sql`, not by convention.
  Never write code that routes around a guard to make something pass.
- Numbered SQL migrations in `harness/db/migrations/` are immutable once applied;
  never edit an existing one, only add the next number.
- Comments explain constraints the code cannot show, not what the next line does.
- Tests live in `harness/tests/`, run with
  `.venv/bin/python -m pytest harness/tests/ -q`.
- Plain English only in anything a person reads at a glance (`spoken_summary`,
  status lines): no filenames, no tool names, no jargon ("429", "migration",
  "endpoint", "trigger"). Name the thing, say what happened to it.

## What you're building into
Match the existing style in `harness/`: dataclasses for structured results,
plain-English exception/refusal messages, fail-closed on ambiguity, and comments
that explain WHY a check exists (often citing what it was found to prevent) rather
than restating the code.
