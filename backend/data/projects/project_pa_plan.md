---
name: Personal Assist — Plan & Phases
description: Phased roadmap for Personal Assist (life-admin dashboard + Jess AI). Kickoff 2026-04-19. Currently Phase 0 (foundation spine). Phase 0 chunk 1 partly shipped.
type: project
---

# Personal Assist — Plan

Personal life-admin system and voice-first AI assistant **Jess**. Sibling to Chief Command and Arch. Google-only runtime brain (`gemini-2.5-flash-native-audio-preview-12-2025` voice + `gemini-2.5-pro` reasoning via `deep_reasoning` tool). Stack: FastAPI + Postgres + Redis + Electron/React. Cost cap $150/mo with sub-budgets ($35 video, $10 image, $10 Workspace APIs).

Full detail lives in `personal-assist/docs/` (HANDOFF, PLAN, TIMELINE, TODO, DECISIONS). This memory is the dashboard summary.

---

## Milestones

- 2026-04-19 - Project kicked off. Repo scaffolded. Handoff doc received. 8 ADRs locked. Chief Command wired.
- 2026-04-19 - Phase 0 chunk 1 shipped — FastAPI scaffold + Electron + React dashboard shell
- 2026-04-19 - Phase 0 chunk 1 review sweeps — CORS guard, DB URL scheme, compose loopback, offline fonts, macOS dock fix
- 2026-04-19 - Dashboard pivoted to bright/alive consumer aesthetic — Jess visual style locked
- 2026-04-19 - QuickActions row + Shopping + Calendar widgets added
- 2026-04-19 - Frontend expansion sweep — focus-ring, color collision, responsive pills, AccentChip extraction, action tokens

---

## Phase 0: Foundation (chunk 1 shipped, chunks 2-3 pending)

Non-negotiable security + infra spine. Nothing downstream ships until this is done.

### Chunk 1 — scaffold (SHIPPED 2026-04-19)
- [x] FastAPI + pytest + ruff project scaffold in `backend/`
- [x] Postgres 16 + Redis via Docker Compose for dev
- [x] CORS guard, DB URL scheme fixed, compose loopback bindings
- [x] Test deps and secrets isolation sweep
- [x] Electron + React dashboard shell — main process, renderer, preload IPC bridge
- [x] macOS dock behavior, offline fonts, version drift fixed
- [x] Jess dashboard pivoted to bright/alive consumer aesthetic (light mode, color, life — Arch EMR home-screen reference)
- [x] QuickActions row — quick-launch actions for common Jess commands
- [x] Shopping widget placeholder
- [x] Calendar widget placeholder
- [x] Design tokens — AccentChip extraction, action tokens, shared color system

### Chunk 2 — security spine (NOT YET SHIPPED)
- [ ] JWT auth + token rotation (single-user, owner only)
- [ ] Audit service — every write action logged
- [ ] Confirmation-gate framework — generic wrapper around every write endpoint
- [ ] Hard-limits service — daily spend caps, rate limits, per-transaction caps
- [ ] 1Password Connect integration for runtime credentials

### Chunk 3 — Google infra (NOT YET SHIPPED)
- [ ] Google Cloud project + $150/mo billing cap + alerts 50/90/100%
- [ ] Sub-budget enforcement ($35 video / $10 image / $10 Workspace / ~$95 voice+reasoning)
- [ ] Gemini Live session manager (bidi WS, streaming audio, VAD, barge-in)
- [ ] `deep_reasoning` tool wired to Gemini 2.5 Pro with `NON_BLOCKING` + `scheduling: INTERRUPT`
- [ ] Flash↔Pro escalation triggers in Jess system prompt
- [ ] Jess `echo` tool end-to-end proof
- [ ] Cloud Run (if off Mac Mini) + Cloud Storage + Pub/Sub for Veo callbacks
- [ ] OAuth installed-app flow for Workspace APIs

**"Done" = Jess responds via voice, every write has a confirmation gate, billing cap enforced, daily spend reported.**

---

## Phase 1: Core Dashboard

Ordered by dependency + risk, not handoff-doc order. Gated by Phase 0 security spine.

- [ ] Tasks API + Today view widget + Jess tasks tool
- [ ] Plaid integration + expenses categorization + budget buckets + alerts
- [ ] Amazon purchase history integration
- [ ] Gmail read + draft-and-confirm email send
- [ ] Whoop health sync + dashboard widget
- [ ] News aggregation + Pro summarization + preference learning
- [ ] Weather widget + Google Calendar widget (live data — currently placeholder)
- [ ] File upload → Gemini 2.5 Pro analysis pipeline
- [ ] Creative surface: Imagen 4 + `gemini-2.5-flash-image` + Docs/Slides/Drive APIs

---

## Phase 2: Action Layer

Write actions. Gated by Phase 0 security spine (confirmation gates + hard caps are non-negotiable before any of these ship).

- [ ] 1Password runtime credential lookup (unlocks the rest)
- [ ] DoorDash + Uber Eats ordering
- [ ] Whole Foods / Amazon Fresh / Publix grocery
- [ ] Amazon purchasing
- [ ] Travel booking (flights + Airbnb + tickets)
- [ ] Computer-use form filling + portal navigation
- [ ] Veo 3.1 video generation (per-clip confirm + $35/mo cap)

---

## Phase 3: Golf Caddy + Mobile

- [ ] TheGrint API integration (read scores + courses + GPS)
- [ ] TheGrint score sync write access
- [ ] PGA Handicap sync
- [ ] iOS app scaffold (React Native)
- [ ] TestFlight distribution setup
- [ ] GPS caddy mode
- [ ] Club selection + distance recommendation
- [ ] Hands-free on-course voice mode
