---
name: Chief Command Phase 9 — Mac Mini Convergence (Archive)
description: Historical reference for the "Butler" plan from 2026-04-15. Dissolved into Chief Command as Phase 9 on 2026-04-18. Most OpenClaw/flat-rate OAuth detail is stale — CC v1 shipped as web + Claude API brain + subprocess dispatch. Keep hardware sizing, Tailscale, and audit-log research only.
type: project
originSessionId: 8ae8b068-aaa3-4d0c-b899-a097fabc5303
---
# Chief Command Phase 9 — Archive (formerly "Butler")

> **2026-04-18 UPDATE — READ THIS FIRST:** Butler is no longer a separate project. Dissolved into Chief Command as Phase 9 ("Mac Mini convergence"). The OpenClaw / multi-model / flat-rate OAuth plan below is **HISTORICAL** — Chief Command v1 shipped as a web app with Claude API brain and subprocess dispatch to Claude Code. Keep the hardware sizing + Tailscale + audit-log research for when Phase 9 is actually built; discard the OpenClaw/OAuth grey-area plan and the multi-model routing.

Owner decided 2026-04-15 to build an always-on personal AI butler using OpenClaw on a dedicated Mac Mini. This memory captures everything decided, researched, and still open so any future session can pick up cold.

## What the butler is for

- **Build software for Arch** (primary) — orchestrate planner → builder → reviewer loop
- **Build clinical expansion software** (later, gated on more info)
- **Rebuild/integrate the Recovery app** (owner has the repo)
- **Build the marketing dashboard** (staff-facing tool for WordPress + Meta + GBP)
- **Email triage** — flag urgent, summarize, Gmail cleanup
- **Research** — market scans, competitive intel, "look into X"
- **Personal dashboard** (v2 goal)
- **NOT for operating Arch with staff** — that's the AI Brain (Vertex AI), separate system

## Non-negotiables (owner-specified)

- **Telegram access** — text the chief-of-staff from phone, always reachable
- **Persistent memory** — always updating, always improving, shared across agents
- **Always-on** — runs 24/7 on Mac Mini at home, not dependent on laptop being open
- **Shared workspace** — all agents read/write the same memory and project files
- **Audit logging** — every action logged to a reviewable file
- **Kill switch** — hard-halt via text or file touch
- **Backup system** — Claude Code subagents path as fallback if OpenClaw breaks

## Decided architecture — FINAL (confirmed 2026-04-15)

### Primary mode: Max OAuth via OpenClaw
```
CHIEF OF STAFF — Claude Opus via Max OAuth ($200/mo flat, already paying)
    │   strategy, planning, memory curation
    │   ONLY builds code on escalation (complex arch, security, or Sonnet failure)
    │   Lives in OpenClaw, textable via Telegram
    │
SONNET BUILDER — Claude Sonnet via same Max OAuth ($0 extra)
    │   PRIMARY code builder — handles most tasks
    │   Runs as a separate OpenClaw agent on the same subscription
    │
GEMINI REVIEWER — Gemini 2.5 Pro per-token (~$35/mo)
    │   cross-model code review (different biases than Claude)
    │   Fires per-commit or on schedule
    │
LOCAL BUILDER — Qwen 32B on Mac Mini ($0)
    │   continuous coding from task queue, grunt work
    │   ~22GB RAM, runs 24/7
    │
LOCAL RESEARCH — Qwen 14B on Mac Mini ($0)
    │   email triage, web research, summaries
    │   ~10GB RAM, runs alongside builder
```

**Monthly cost (OAuth mode): ~$235/mo** ($200 Max already paying + $35 Gemini per-token)
**Net new vs current spend: ~$15/mo more** (currently paying $220 for Max + OpenAI)

### Fallback mode: Per-token API (if OAuth gets flagged)
```
OPUS — chief-of-staff + escalation builder ONLY (expensive, minimize usage)
SONNET — primary API builder (~1/15th Opus cost per token, handles volume)
GEMINI — reviewer (~$35/mo, unchanged)
QWEN 32B — local builder, grunt work queue (free, unchanged)
QWEN 14B — local research/triage (free, unchanged)
```

**Monthly ceiling (per-token mode): $400/mo** — owner confirmed 2026-04-15
**Strategy:** Opus stays as brain but rarely touches code. Sonnet does the heavy building. Two local Qwens absorb grunt work. At owner's recent build pace (heavy daily output, all Opus), spreading across Sonnet + two locals should land well under $400.
**Possible future optimization:** Drop Sonnet for something even cheaper (Haiku, Gemini Flash) if $400 feels tight. TBD.

## Hardware

- **Mac Mini M4 Pro 64GB RAM, 512GB+ SSD** — needed for two local models (32B + 14B) simultaneously
- **~$2,000 new** or **~$1,600 refurb** (check Apple Refurb, Swappa, Best Buy Open Box)
- **Dedicated Apple ID** — clean separation from owner's personal iCloud
- Stable high-speed home internet confirmed
- Headless after initial setup — remote access via **Tailscale** (free personal tier, WireGuard mesh, no port forwarding)
- Power draw: ~$5-8/mo electricity

## The OAuth gray area — owner's informed decision

Claude Max OAuth with OpenClaw is NOT officially documented by Anthropic. API key (per-token) IS documented. The OpenClaw creator Peter Steinberger was temp-banned by Anthropic for Claude usage through OpenClaw (TechCrunch April 10 2026), then access was restored.

**Owner decided to proceed with OAuth anyway.** Reasoning: the ban was temporary, Steinberger's access was restored, and the cost difference is significant ($120-150/mo savings vs per-token).

**Fallback plan if Anthropic flags the account:**
1. Same-day switch chief-of-staff to Claude API key (per-token, ~$150-200/mo extra)
2. OR fail over to the pre-built Claude Code subagents backup path
3. Claude Code backup uses `.claude/agents/`, `.claude/skills/`, `.claude/commands/` and is pre-tested before OpenClaw goes live

**TO VERIFY ON DAY 1:** Can OpenClaw run two Claude models (Opus + Sonnet) on the same Max OAuth simultaneously? This is the assumption that makes $235/mo work. If not, Sonnet builder shifts to per-token or local-only.

## OpenClaw — what it is and isn't

**IS:** A local-first personal AI gateway that runs 24/7 on your Mac. Routes messages from Telegram/WhatsApp/Slack/Discord/iMessage to isolated agent workspaces. Per-agent memory, cron jobs, webhooks, voice wake words. MIT licensed, 358K GitHub stars.

**IS NOT:** An orchestration framework. OpenClaw routes channels to agents but has NO built-in planner→builder→reviewer handoffs, no workflow DAGs, no agent-to-agent messaging. The orchestration loop (chief delegates to builder, builder hands off to reviewer) must be built on top using OpenClaw's primitives (sessions, cron, shared workspace files) or a separate orchestration layer.

**Created by:** Peter Steinberger (ex-PSPDFKit founder). Joined OpenAI Feb 2026. Project moving to foundation governance. Install: `brew install --cask openclaw`.

## Key research findings

### Flat-rate landscape (confirmed April 2026)
- **Claude Max $200/mo** — flat rate, Claude Code officially blessed. OAuth with third-party tools = gray area.
- **Google Gemini** — NO flat-rate agent tier. Consumer plan uses credits (200-1000/mo), not unlimited. API is metered. "Google banned people for OAuth with OpenClaw, then rolled it back" — per Alex Finn.
- **OpenAI ChatGPT Pro $250/mo** — allows OAuth with OpenClaw. Alex Finn uses this for his engineering manager agent (Ralph). Only confirmed flat-rate OAuth for agents.
- **Mistral Le Chat Pro $15/mo** — lists "Agents" as a feature. Cheapest option, limits unclear.

### Alex Finn's confirmed stack (from owner watching video)
- **Henry** — Opus, chief of staff (top of hierarchy)
- **Ralph** — ChatGPT Pro OAuth $250/mo, engineering manager, checks on builders every 10 min
- **Charlie** — Qwen local on Mac Studio, primary builder, codes non-stop
- **Scout** — sub-agent, research
- **Quil** — sub-agent, content creation (video scripts from research)
- **4 OpenClaw instances** running on his computer
- **Multiple Mac Minis/Studios** for parallel local agents
- **Dashboard** showing build stages, memory, hierarchy, approval gates
- **Per-agent memory** (.md files) + shared workspace
- **Approval cycle:** local agent researches → content agent creates → Alex approves
- **Smart glasses** — putting Henry (chief of staff) in hackable smart glasses

### Security (confirmed from OpenClaw SECURITY.md)
- Trust model: single-user, trusted-operator
- Loopback-only gateway by default (127.0.0.1)
- Workspace-only file access enforced
- DM pairing codes for unknown senders
- Docker sandbox available for non-main sessions (`agents.defaults.sandbox.mode: "non-main"`)
- "If you install a bad skill, that's your fault" — explicitly out-of-scope in their threat model

### Sandboxing recommendation for owner (trust level 5-6)
- **Use Docker Desktop** + OpenClaw's built-in non-main sandbox mode
- Primary channel (Telegram DM) runs on host for speed
- Non-primary sessions (group chats, webhooks, research agents) run in Docker
- Install Docker Desktop (free, GUI installer, ~15 min setup)
- Full VM (UTM/Parallels) is overkill for trust 5-6

### Tool/skill sourcing
- **Tier 1 (safe):** OpenClaw core built-in tools — browser, cron, sessions, canvas, Discord/Slack, voice, Gmail
- **Tier 2 (caution):** ClawHub marketplace — no ratings/download counts, automated security analysis but light vetting
- **Tier 3 (avoid):** Random GitHub repos, Discord drops, DMs
- **Custom skills:** just a markdown file with YAML frontmatter (`SKILL.md`). Easy to create. Agents can theoretically write their own.
- **Rule for first 30 days:** only core built-in tools. Build custom for anything Arch-specific.

### Mac Mini vs alternatives (confirmed)
- Mac Mini wins on: unified memory (GPU sees all 64GB), power efficiency (5-7W idle), silence, macOS ML tooling support
- M4 Pro 64GB runs Qwen 32B at ~18-22 tok/s, Llama 70B at ~8-12 tok/s
- M4 base 32GB runs Qwen 14B at ~20+ tok/s but can't fit 32B well
- Linux mini PCs without GPUs: 3-5 tok/s on CPU, unusable for local models
- Used RTX 3090/4090 builds: faster for models under 24GB VRAM but loud, power-hungry, hard ceiling
- Clustering multiple Mac Minis: possible via `exo` (43K stars) but rabbit hole for non-dev. Better to buy one bigger machine.

### Messaging
- **Start with Telegram** — 10-min setup, reliable, OpenClaw has native support
- **iMessage via BlueBubbles** — later (fragile, breaks on macOS updates, ~1hr setup)
- **Skip WhatsApp** (Meta breaks unofficial bridges) and **SMS** (don't mix with Arch Twilio)

## Claude Code Orchestration (BUILD THIS WEEK — 2026-04-16 start)

**This is the priority build.** A working orchestration system using Claude Code's native subagent capabilities. Serves as both:
1. The immediate usable orchestration system (this week)
2. The permanent fallback if OpenClaw ever breaks

### What it actually is

All Opus. All in one terminal window. The main Claude Code session IS the chief-of-staff. It spawns Opus subagents as builder and reviewer. No model switching, no external services, no second repo.

**The workflow:**
1. Owner talks to chief-of-staff (the main session)
2. Chief plans the work, breaks it into tasks
3. Chief spawns a **builder agent** (Opus subagent) with role instructions + task spec
4. Builder does the work, reports back to chief
5. Chief spawns a **reviewer agent** (Opus subagent) to check builder's output
6. Reviewer reports issues/approval back to chief
7. Chief summarizes to owner: here's what got built, here's what the reviewer found, here's what needs your decision

Multiple builders can run in parallel on independent tasks. Reviewer checks each one.

### Where it lives

**User-level Claude Code config at `~/.claude/`** — available in every session, every repo.
- `~/.claude/agents/` — agent role definitions (builder, reviewer, researcher)
- `~/.claude/commands/` — slash commands (`/build`, `/review`, `/research`)
- No separate repo needed. Owner types `arch` to open Arch session and butler commands are available.

### What we're building

1. **Agent definitions** — Markdown files that give each spawned subagent its role:
   - `builder.md` — stay in scope, follow existing patterns, test your work, report back
   - `reviewer.md` — check correctness, security, patterns, edge cases, report issues
   - `researcher.md` — web research, summaries, competitive intel
2. **Slash commands** — Shortcuts to trigger the workflow:
   - `/build` — describe what to build, chief plans + delegates to builder + reviewer
   - `/review` — kick off review of recent changes
   - `/research` — delegate a research question
3. **Simple audit log** — track what agents did (not for security, just for visibility)

### What this is NOT
- NOT always-on (runs when you have a session open)
- NOT multi-model (all Opus, all Claude Code)
- NOT autonomous overnight
- No kill switch needed (you're in the terminal, just close it)
- No Docker/sandbox needed (Claude Code has its own permission model)
- No Telegram (that's OpenClaw's job later)

### Key realization (2026-04-15)
Claude Code subagents have been available this whole time. We were already using them for research/file searches but should have been using them for the full build→review cycle instead of copy-pasting between windows. The orchestration layer just formalizes this with role definitions and slash commands.

## v1 build plan (revised 2026-04-15)

### Phase 1: Claude Code orchestration (THIS WEEK — start 2026-04-16)
- **Day 1:** Build agent definitions + slash commands in `~/.claude/`. Open Arch session, test the full loop — `/build` a real task, verify chief→builder→reviewer chain works.
- **Day 2-3:** Iterate based on what breaks. Tune agent prompts, adjust delegation logic.
- **Day 4-5:** Run real Arch work through the system. Refine.
- **End of week:** Working orchestration-in-terminal. No new repo needed, no setup from owner.

### Phase 2: Mac Mini + OpenClaw (after Phase 1)
- **Week 1:** Order Mac Mini, create dedicated Apple ID, install Tailscale + Docker Desktop + Homebrew + Node
- **Week 2:** Install OpenClaw, configure Opus OAuth + Sonnet agent, audit logging, kill switch, Telegram pairing
- **Week 3:** Install local models (Qwen 32B + 14B via Ollama), wire Gemini reviewer, set up shared workspace + memory files, first test tasks
- **Week 4:** Symlink memory between OpenClaw and Claude Code backup, run first real Arch task through full OpenClaw loop, iterate

## Open questions

### Resolve TOMORROW (2026-04-16) — needed for Phase 1 build
1. **Repo name** — owner needs to create the GitHub repo and clone it. Suggested names: `butler`, `orchestrator`, `chief-of-staff`

### Resolve before Phase 2 (Mac Mini + OpenClaw)
2. **Can Max OAuth run two Claude models (Opus + Sonnet) simultaneously in OpenClaw?** — test on day 1
3. **Orchestration layer** — OpenClaw doesn't have built-in agent-to-agent handoffs. How do we wire chief→builder→reviewer? Options: custom scripts, CrewAI, or just shared task files that agents poll.
4. **Dashboard** — owner will customize and build this themselves
5. **Docker sandbox config** — detailed walkthrough needed with owner before setup
6. **Gmail integration** — OpenClaw has Gmail Pub/Sub built in. Wire for email triage in Phase 2 week 3.
7. **PHI firewall** — regex-based outbound filter blocking resident names/DOBs from butler traffic. Decide yes/no.
8. **Smart glasses** — Park for v2+.
9. **Mac Mini order** — timeline TBD. Not blocking Phase 1.

## Relationship to other Arch plans

- **AI Brain Plan** (`docs/AI_BRAIN_PLAN.md`) — the Brain is for STAFF using Arch operationally (Vertex AI / Gemini). The butler is for OWNER building Arch. Separate systems, separate purposes, some shared infrastructure patterns (runaway protection, memory files).
- **Locked order of operations** (CLAUDE.md) — walkthroughs → core missing features → resident portal → AI Brain. The butler ACCELERATES this order by making builds faster, not by changing the sequence.
- **Marketing dashboard** — the butler builds it, the dashboard is a staff-facing tool.
- **Clinical expansion** — the butler builds it when the time comes, gated on owner providing more info.

---

*Created 2026-04-15. Updated 2026-04-15 evening — locked stack as final, added per-token fallback model ($400/mo ceiling), reordered build plan (Claude Code backup FIRST this week, OpenClaw Phase 2 after).*
