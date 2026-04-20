---
name: Agent Roster — Named, With Personalities + Memory
description: Full named roster of Claude Code subagents. Each has distinct personality, scoped tools, own memory file. Chief orchestrates with 5-7min check-in loop.
type: project
originSessionId: be67e0d9-9428-4101-bdd8-1f21f3e45b19
---
# Agent Roster

Recovered 2026-04-17 from session `d70c0147` (original naming session, Apr 17 ~1am). This is the canonical design. Names were decided with rationale; do not rename without owner sign-off.

## The Roster

| Name | Role | Model | Maps to | Lean / rationale |
|---|---|---|---|---|
| **Chief** | Orchestrator | Opus | Top-level Claude Code session (this one, always) | Watches all agents, appends lessons to their memory, catches drift |
| **Atlas** | Researcher | Opus | `researcher.md` | Knowledge / maps |
| **Forge** | Integration tester | Opus | `integration-tester.md` | Proves-it-works, fire-tested |
| **Riggs** | Builder — backend | Sonnet | `builder.md` (variant) | Systems, FastAPI, SQL, async, infra. Bash for migrations/pytest |
| **Finn** | Builder — frontend | Sonnet | `builder.md` (variant) | React, Tailwind, iOS quirks, animations. Scoped npm tools only |
| **Nova** | Builder — glue/data | Sonnet | `builder.md` (variant) | Parsers, dashboards, LLM wiring, metrics. Cross-cutting work |
| **Vera** | Security review | Sonnet | `security-reviewer.md` | Verify, vigilance |
| **Hawke** | Bug hunter | Sonnet | `bug-hunter.md` | Eagle eye |
| **Sable** | Hygiene review | Sonnet | `hygiene-reviewer.md` | Dark/tidy, clean sweeper |
| **Pax** | Practical review (wiring) | Sonnet | `practical-reviewer.md` | Pragmatic, wiring checker |
| **Quill** | QA verifier | Sonnet | `qa-verifier.md` | Precise, requirements scribe |
| **Hip** | HIPAA review (Arch only) | Sonnet | `hipaa-reviewer.md` | Only fired on Arch project |

**Specialties are LEANING, not locked.** Nova handles cross-cutting. Any builder can cross lines if the task calls for it. Chief picks primary lean and routes.

## Per-agent memory

Each named agent has its own memory file at `~/.claude/agents/memory/<name>.md`, read at the start of every invocation. Chief appends lessons after each build. Over months, each accrues specialty-specific wisdom (e.g. Riggs: "pricing tables were duplicated across `llm.py` and `usage_tracker.py` — assign one source-of-truth file").

## Chief's check-in loop

When Chief spawns a background builder, Chief also schedules a `ScheduleWakeup` at T+300–420s to check that builder.

**Red flags Chief watches for:**
- Output file hasn't grown in 5+ min → stuck/hung
- Output growing fast with no file changes → looping in planning
- Writes outside scope (Riggs touching frontend) → drift
- Repeated identical tool calls → stuck loop
- Runtime exceeds 2× estimated duration

**Chief's response:**
- Minor drift → `SendMessage` to the agent with course-correction
- Serious drift → `TaskStop` + respawn with tighter prompt

**Context-safe monitoring:** Chief uses `TaskOutput` (summary), `TaskList` / `TaskGet` (metadata), `Bash ls -la` on output file (size/mtime). Never reads the full JSONL output file directly — it overflows context.

## Memory discipline rhythm

- **After every build:** Chief appends lessons to each participating agent's memory
- **After every review sweep:** Chief appends spec-gap lessons to Chief's own memory so the orchestrator improves too
- **Weekly Monday:** existing `feedback_memory_hygiene.md` audit extended to cover per-agent files
- **Per-project memory:** same rhythm, skips projects flagged `status: done` in PROJECTS.json

## Status (2026-04-17)

- ✅ Roster decided and saved (this file)
- ❌ `~/.claude/CLAUDE.md` — Chief persona not yet written
- ❌ Named agent files (riggs.md, finn.md, nova.md, atlas.md, forge.md, vera.md, hawke.md, sable.md, pax.md, quill.md, hip.md) — not yet created. Generic files still in place (builder.md, integration-tester.md, etc.)
- ❌ Per-agent memory files at `~/.claude/agents/memory/` — dir doesn't exist
- ❌ Check-in loop mechanism — documented, not yet a hook/rule
- ❌ Team tab + Memory tab in Chief Command dashboard — not built

## Why this got lost once before

Previous session (d70c0147) decided everything above but hit a permission wall mid-scaffold — Write/Edit/Bash got blocked session-wide before any of the named files could be created. The owner ended up manually loosening settings; by the time a fresh session started, no memory had captured the design decisions. This file exists so that can't happen again.
