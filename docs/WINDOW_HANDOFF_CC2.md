# Chief Command v2 — Fresh-Window Handoff (2026-07-11)

You are a fresh Claude Code session opened in `~/code-projects/chief-command`. This doc is your complete orientation. Read it, then read the spec, then talk to Neill before building anything.

## The one-line state

**Neill has decided to START OVER on Chief Command.** He delivered a complete self-authored spec on 2026-07-11. Nothing from that spec has been built yet. Your first job is a design conversation + research, not code.

## Read these, in order

1. **`docs/CC_REBOOT_SPEC_2026-07-11.md`** — THE spec, owner-authored, verbatim. This is the direction of record. Voice-first multi-model agent harness: OpenClaw backbone, four model seats (Codex orchestrator / Grok Build workhorse / Ollama local grinder / Claude escalation reviewer), local Whisper+Kokoro+Pipecat voice as a swappable skin, text-first build order, Tailscale-only, phases 1–5 ending in a Mac Studio migration.
2. `CHIEF_COMMAND_V3_SPEC.md` (repo root) + the running v1 backend (`backend/`) — the OLD system. It runs (or ran) as a FastAPI service with dispatch/audit/repo-map machinery. **Kept for reference and parts salvage only.** Do not extend it. Open question for Neill: archive it or mine it (its audit-trail and job-record patterns are decent prior art for spec §7).
3. `docs/dispatch-bridge-glue-spec.md`, `docs/chief-access-model.md`, `docs/phase-1.1-google-voice.md` — v1-era docs. Historical context only. Note the Google-voice doc is now explicitly DEAD (spec §4.5 excludes Google entirely).

## What Neill said he wants first (verbatim intent, 2026-07-11)

- "I will likely do some research to see if we can pull any open source stuff that uses OpenClaw for this very specific thing."
- So: **research lane before build lane.** Survey the OpenClaw ecosystem for existing open-source projects that already do multi-provider model routing / voice front-ends / agent gauntlets on top of OpenClaw. Assess maturity, license, and fit against the spec's four-seat + Pipecat design. Deliver a compare-and-recommend, not a scaffold.
- Verify the spec's factual load-bearing claims while researching (they were written by Neill from his own research; trust but verify): Codex CLI subagent system status, Grok Build headless/OAuth sanction, OpenClaw provider-abstraction maturity, Kokoro/Pipecat state of the art, current Claude Code credit-pool rules.

## Standing constraints (from the spec — do not violate)

- Text-first; voice is a swappable skin. Text chat is the permanent fallback.
- Version-PIN everything; no auto-updates.
- Tailscale-only networking; nothing public.
- Hardware-agnostic: one config change migrates MacBook Pro → Mac Studio.
- No Google/Gemini integration, period.
- Claude headless = credit pool with a config budget cap; never gray-area workarounds.
- Local model output never ships without a subscription-tier review.
- Jess is a SEPARATE project (see §10): she connects later via a tailnet relay endpoint. Do not merge scopes. Her window handoff is `~/code-projects/personal-assist/docs/WINDOW_HANDOFF_JESS.md`.

## Working conventions with Neill

- Non-technical owner; plain English; business framing; no unexplained jargon.
- Spec-then-build: get the design conversation done, then execute autonomously in batches.
- No time estimates. Reviews before pushes (his standing rule across all projects: multi-reviewer pass on every commit before push — port the Arch review discipline here when the repo goes active).
- He runs sessions from his phone sometimes ("remote control") — keep answers self-contained, lead with the outcome.

## Suggested first-session agenda

1. Read the spec end-to-end.
2. Run the open-source research sweep (OpenClaw ecosystem + prior art for voice-front-end harnesses + Codex TOML agent-porting tools). Owner explicitly wants this before any scaffolding.
3. Bring Neill: (a) research findings + recommendation, (b) the v1-backend archive-or-salvage question, (c) Phase-0 RAM check on his MacBook (sizes the Ollama model), (d) naming — he said "rename at will."
4. Only then scaffold Phase 1 (text harness).
