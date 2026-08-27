# Chief Command — Agent Conventions

Single source of truth for every agent working in this repo, regardless of harness
(Codex, Grok Build, Claude Code, Ollama). Codex reads this natively. Claude Code's
AGENTS.md support is **not** fully established, so `CLAUDE.md` imports this file rather
than relying on fallback behavior.

## What this project is

A voice-first multi-model agent orchestration harness. Neill talks to it; it routes work
to a stack of models, reviews its own output through a gauntlet, and reports back.

**Direction of record:** `docs/CC_REBOOT_SPEC_2026-07-11.md` (owner-authored).
**Corrections to that spec:** `docs/OSS_RESEARCH_2026-07-11.md` — read this too. Several
of the spec's load-bearing factual claims turned out to be wrong, and the research doc
supersedes them.

## Layout

| Path | Status |
|---|---|
| `harness/` | **v2. This is the live system.** |
| `backend/`, `frontend/` | **v1. Reference and parts salvage ONLY. Do not extend.** |
| `docs/` | Spec, research, handoffs. |

## The rules that are not negotiable

These come from the spec's standing constraints (§9) and from things that have already
bitten this project once.

1. **Text-first. Voice is a swappable skin.** Text chat is the permanent fallback. If a
   change makes the harness unusable without voice, it is wrong. *(v1 died on voice; the
   harness must never depend on it again.)*
2. **Version-pin everything. No auto-updates.** OpenClaw is pinned to `2026.6.11`. Three
   breaking releases landed in under 90 days in Q1 2026. Bumps are deliberate, tested
   events — never incidental to another change.
3. **Tailscale-only. Nothing public.** The gateway binds to the tailnet or loopback. No
   port forwarding, no exposed services.
4. **Hardware-agnostic.** One config change migrates MacBook Pro → Mac Studio. No
   machine-specific paths, ever.
5. **No Google/Gemini.** Their ToS prohibits third-party OAuth (it names OpenClaw
   explicitly) and consumer CLI access ended June 18, 2026. Enforced, not theoretical —
   accounts were banned.
6. **Local model output never ships without a higher-tier review.** This is enforced by
   a database trigger (`harness/db/schema.sql`, guard 1), not by convention. **Do not
   route around it.** If you find yourself wanting to disable the guard to make something
   pass, the something is wrong.
7. **Providers are referenced by SEAT, never by name.** `orchestrator`, `workhorse`,
   `grinder`, `reviewer`, `head`. Vendor policy drift is risk #1 on this project — any
   seat must be swappable in config without touching orchestration logic.
8. **Reviews before pushes.** Multi-reviewer pass on every commit before it goes up.
   Owner's standing rule across all his projects.
9. **Every spec carries a Feature Acceptance Checklist, and the gauntlet verifies the
   running app against it.** (Owner-locked 2026-07-23 — the #1 process gap he hit on the
   Arch project: features get spec'd, agreed, sometimes half-built, and then never land or
   land with no reachable button — and nothing catches it, because code review reads code
   and *can't see an absent button*, and Forge tests "does it work" not "is every spec'd
   feature present and clickable." The owner found ~12 UI defects in 5 minutes *after*
   multiple audits.) The fix is structural, not more manual work by Neill:
   - **The spec-writer (a model, never Neill) auto-generates a "Feature Acceptance
     Checklist" from the spec conversation.** Each item is a concrete, checkable statement:
     *what control exists, what it does, and where in the UI it's reachable from.* If a
     behaviour is in the spec, it's a checklist line. Neill talks features; the system
     captures them. He never hand-writes a checklist and never re-drives the app to find
     what's missing — that is the whole point of the harness (save his time).
   - **Forge verifies the running app against that checklist** before "done" — drives each
     item and reports PRESENT / MISSING / BROKEN / UNREACHABLE. **An ABSENCE is a failure**,
     same weight as a bug. This is a hard gate per surface, never skipped for machine load
     (serialize instead).
   - **The code gauntlet also checks the diff against the checklist for completeness** —
     not just "is this code correct" but "is every checklist item implemented AND wired to
     a control a user can reach." Green checks on an unreachable feature are the exact
     "believable green checks on code nobody can use" this project exists to prevent.
   - **"Done" = every checklist item verified present, reachable, and working.** Code with
     no button is not done.
   Why this is the right shape and not "give the model more common sense": product judgment
   is a genuine weak spot for current models, so don't put the "supposed-to-be" in the
   model's head — put it in an artifact (the checklist, born from the spec) and the tester's
   job becomes "does the app match this list," which a model + a browser does reliably.

## The architecture in one paragraph

A **fast head** (Talker) is what Neill talks to — non-reasoning, excellent tool-calling,
sub-second. It never thinks; it dispatches. Heavy work goes to **reasoner seats** via
OpenClaw's `sessions_spawn`, which is **non-blocking** — it returns a run id immediately,
so the head never waits and can keep talking. Deep reasoning is a tool the head reaches
for on demand, not a tax on every sentence. Long builds grind in the background on
flat-rate subscription seats. Every dispatched job is recorded in SQLite so
*"what did the overnight run do?"* is answerable.

**Do not put a reasoning model in the conversational path.** GPT-5.6-sol measures ~13s to
first token. That is the single mistake this architecture exists to avoid.

## Conventions

- Python for the harness (matches v1; the venv is `.venv/`).
- Tests live beside what they test, in `tests/`. Run: `.venv/bin/python -m pytest harness/tests/ -q`.
- Comments explain constraints the code cannot show. Not what the next line does.
- Every dispatched job gets a row. No silent work.

## Working with Neill

**Neill cannot read or write code.** This is the single most important thing on this page.

His own words: *"Think of it like I know Spanish 101, but that's it. The basic level of how
coding, databases, hosting environments, etc. work."* He knows general concepts — database,
hosting, frontend vs backend — but not code, not filenames, not tool names.

### The plain-English rule (applies to EVERY agent, not just the voice)

Anything that reaches Neill — a `spoken_summary`, a verdict summary, a status line, a
dispatch announcement — **must be readable by a smart person who has never programmed.**

**Never write, in anything he'll hear or read at a glance:**
- filenames or paths — `dispatch.py`, `backend/app/routes.py`
- tool names — bash, grep, read, edit
- code jargon — "429s", "middleware", "async", "regex", "the migration", "the endpoint"

**Instead, name THE THING and say what happened to it:**

| ❌ Don't | ✅ Do |
|---|---|
| "Editing dispatch.py, writing test_ratelimit.py" | "He's building the rate limiter, now he's testing it" |
| "It 429s the health check" | "It was accidentally blocking our own status checks" |
| "Migration failed on the users table" | "The change to how we store accounts didn't go through" |
| "Fixed the async race in the WS handler" | "Fixed a timing bug that was dropping messages" |

The detail is not lost — it lives in the **text channel**, which he can scroll past. This is
not dumbing down. It is putting each fact in the channel that can carry it.

**If you cannot explain what you did without jargon, you do not yet understand what you did.**

### Other conventions with Neill

Lead with the outcome. Business framing. He runs sessions from his phone sometimes — keep
answers self-contained. No time estimates. Spec-then-build: settle the design, then execute
autonomously in batches. Say "I was wrong" plainly when you were; he'd rather be corrected
than flattered.
