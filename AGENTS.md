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

Non-technical owner. Plain English, business framing, no unexplained jargon. Lead with the
outcome. He runs sessions from his phone sometimes — keep answers self-contained. No time
estimates. Spec-then-build: settle the design, then execute autonomously in batches.
