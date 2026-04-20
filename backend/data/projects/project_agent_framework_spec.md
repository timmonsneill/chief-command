---
name: Agent Framework — Generic files live, naming/memory pending
description: Generic agent .md files ship in ~/.claude/agents/ and work. Named roster (Chief/Riggs/Finn/...) + per-agent memory not yet wired — see project_agent_roster.md for the target design.
type: project
originSessionId: be67e0d9-9428-4101-bdd8-1f21f3e45b19
---
# Agent Framework

## Status (2026-04-17)

**What works:** 9 generic agent files in `~/.claude/agents/` are live and fire correctly via the `Agent` tool. 6-reviewer parallel sweep tested Apr 16.

**What's incomplete:** the named roster (Chief, Riggs, Finn, Nova, Atlas, Forge, Vera, Hawke, Sable, Pax, Quill, Hip) and per-agent memory system are not yet scaffolded. See **project_agent_roster.md** for the canonical roster design, evolution loop, and Chief check-in mechanism.

## What's Built

### 8 Agents (~/.claude/agents/)
- `builder.md` — worktree-isolated code builder (Opus)
- `security-reviewer.md` — OWASP top 10 audit
- `hipaa-reviewer.md` — PHI exposure + audit trail check
- `bug-hunter.md` — runtime bugs (async, null, React, SQL, API contracts)
- `hygiene-reviewer.md` — dead code, hardcoded values, TODOs
- `practical-reviewer.md` — wiring, completeness, orphans, regression
- `qa-verifier.md` — requirements match, redundancy, sanity
- `researcher.md` — web research + investigation

### 2 Skills (~/.claude/skills/)
- `arch-conventions/SKILL.md` — 10KB of real Arch codebase patterns (tech stack, file structure, API patterns, DB conventions, 13 roles, business rules)
- `hipaa-review/SKILL.md` — PHI field definitions, logging rules, audit requirements, encryption standards

### 3 Slash Commands (~/.claude/commands/)
- `/build [description]` — plan → builder(s) → 6 reviewers → merged report
- `/review [scope]` — 6 reviewers in parallel on changes/files/branch
- `/qa [feature]` — practical reviewer 4-check protocol

## First Test Results (2026-04-16)
6 reviewers ran in parallel on last 3 commits. Found:
- 2 CRITICAL: hardcoded JWT secret fallback, inverted occupancy status logic
- 9 HIGH: missing role checks (billing, meds), missing transactions, missing audit trails, duplicated pricing logic
- 8 MEDIUM: CORS config, magic numbers, unused imports, orphaned stage
- Cross-validation worked: security + HIPAA both caught role check gaps; bug hunter + practical both caught transfers/CM level issues
