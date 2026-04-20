---
name: Personal Assist — Plan & Phases
description: Phased roadmap for Personal Assist (life-admin dashboard + Jess AI). Kickoff 2026-04-19. Currently in Phase 0 (foundation spine).
type: project
---

# Personal Assist — Plan

Personal life-admin system and voice-first AI assistant **Jess**. Sibling to Chief Command and Arch. Google-only runtime brain (`gemini-2.5-flash-native-audio-preview-12-2025` voice + `gemini-2.5-pro` reasoning via `deep_reasoning` tool). Stack: FastAPI + Postgres + Redis + Electron/React. Cost cap $150/mo with sub-budgets ($35 video, $10 image, $10 Workspace APIs).

Full detail lives in `personal-assist/docs/` (HANDOFF, PLAN, TIMELINE, TODO, DECISIONS). This memory is the dashboard summary.

## Phase 0: Foundation

Non-negotiable security + infra spine. Nothing downstream ships until this is done.

- [ ] FastAPI + pytest + ruff project scaffold in `backend/`.
- [ ] Postgres 16 + Redis via Docker Compose for dev.
- [ ] JWT auth + token rotation (single-user, owner only).
- [ ] Audit service — every write action logged.
- [ ] Confirmation-gate framework — generic wrapper around every write endpoint.
- [ ] Hard-limits service — daily spend caps, rate limits, per-transaction caps.
- [ ] 1Password Connect integration for runtime credentials.
- [ ] Google Cloud project + $150/mo billing cap + alerts 50/90/100%.
- [ ] Sub-budget enforcement ($35 video / $10 image / $10 Workspace / ~$95 voice+reasoning).
- [ ] Gemini Live session manager (bidi WS, streaming audio, VAD, barge-in).
- [ ] `deep_reasoning` tool wired to Gemini 2.5 Pro with `NON_BLOCKING` + `scheduling: INTERRUPT`.
- [ ] Flash↔Pro escalation triggers in Jess system prompt.
- [ ] Jess `echo` tool end-to-end proof.
- [ ] Electron + React dashboard shell.
- [ ] Cloud Run (if off Mac Mini) + Cloud Storage + Pub/Sub for Veo callbacks.
- [ ] OAuth installed-app flow for Workspace APIs.

## Phase 1: Core Dashboard

Ordered by dependency + risk, not handoff-doc order.

- [ ] Tasks API + Today view widget + Jess tasks tool.
- [ ] Plaid integration + expenses categorization + budget buckets + alerts.
- [ ] Amazon purchase history integration.
- [ ] Gmail read + draft-and-confirm email send.
- [ ] Whoop health sync + dashboard widget.
- [ ] News aggregation + Pro summarization + preference learning.
- [ ] Weather widget + Google Calendar widget.
- [ ] File upload → Gemini 2.5 Pro analysis pipeline.
- [ ] Creative surface: Imagen 4 + `gemini-2.5-flash-image` + Docs/Slides/Drive APIs.

## Phase 2: Action Layer

Write actions. Gated by Phase 0 security spine.

- [ ] 1Password runtime credential lookup (unlocks the rest).
- [ ] DoorDash + Uber Eats ordering.
- [ ] Whole Foods / Amazon Fresh / Publix grocery.
- [ ] Amazon purchasing.
- [ ] Travel booking (flights + Airbnb + tickets).
- [ ] Computer-use form filling + portal navigation.
- [ ] Veo 3.1 video generation (per-clip confirm + $35/mo cap).

## Phase 3: Golf Caddy + Mobile

- [ ] TheGrint API integration (read scores + courses + GPS).
- [ ] TheGrint score sync write access.
- [ ] PGA Handicap sync.
- [ ] iOS app scaffold (React Native).
- [ ] TestFlight distribution setup.
- [ ] GPS caddy mode.
- [ ] Club selection + distance recommendation.
- [ ] Hands-free on-course voice mode.

## Milestones

- 2026-04-19 — Project kicked off. Repo scaffolded. Handoff doc received. Decisions locked (Google stack, $150 cap, Flash↔Pro router, sibling repo). Chief Command wired.
