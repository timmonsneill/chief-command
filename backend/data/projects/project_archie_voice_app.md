---
name: Archie — Voice-First Staff App (Native)
description: Native iOS/Android app for staff. Voice interface to Vertex AI Brain. Wake word "Hey Archie", proactive alerts to earbuds, conversational note entry, tiered usage caps.
type: project
---

# Archie — Voice-First Native Staff App

## What it is
The primary way staff interact with the AI Brain. Native app (Expo wrapper around web core) with voice as the main interface. Staff puts in earbuds at start of shift, Archie is their AI colleague all day.

## When to build
Part of the AI Brain build (Phase 3). NOT part of Chief Command (that's owner-only).

## Core Features

### Staff-initiated voice
- "Hey Archie" wake word — always listening, no Siri needed
- Conversational note entry: staff talks, Archie cleans up, reads back summary, staff approves, note written
- Voice queries: "What's the med round look like?", "Who's overdue on UAs?", "What's Mike's phase?"
- Voice commands: "Write a note for...", "Log a UA for...", "Schedule a tour for..."

### Archie-initiated (proactive alerts to earbuds)
- Push notification + spoken alert via AirPods/earbuds
- Overdue UA reminders
- Med administration reminders
- Curfew override expirations
- Family visit reminders with ROI details
- Low med stock warnings
- Incoming admissions pipeline updates
- Any alert rule from the automation system

### Native app requirements (why not PWA)
- Reliable push notifications (APNs/FCM)
- Background audio session for incoming voice alerts
- Wake word detection running locally (iOS Speech framework)
- Badge counts on app icon
- Always-on Bluetooth audio connection for earbuds

## Architecture
- **Native shell:** Expo/React Native wrapping the web app
- **AI backend:** Vertex AI (Gemini) — HIPAA compliant within Google Cloud
- **STT:** Google Cloud Speech-to-Text
- **TTS:** Google Cloud Text-to-Speech (or Kokoro if self-hosting)
- **Model routing:** Flash for notes/commands/alerts/scanning, Pro for complex conversations

## Usage Caps — Tiered by Role

Caps are on CONVERSATION minutes (the expensive part), not on notes/commands/alerts.

| Tier | Roles | Monthly voice conversation cap |
|------|-------|-------------------------------|
| Part-time staff | recovery_advocate (PT), facilitator | 120 min/month (~4 min/day) |
| Full-time staff | recovery_advocate (FT), family_coach, client_care_coordinator, admissions_outreach, logistics_coordinator | 300 min/month (~10 min/day) |
| Leadership | case_manager, director_cm_family, administrator | 600 min/month (~20 min/day) |
| Owner | leadership (Neill) | 1200 min/month (~40 min/day) |

Notes, commands, and proactive alerts do NOT count against the cap — only open-ended conversations.

Soft cap: warning at 80%. Hard cap: voice conversations blocked, text still works.

## Cost Projections (Vertex AI)

### Per-interaction costs (Flash)
- Conversational note (full flow): ~$0.05
- Voice command (quick action): ~$0.01
- Proactive alert (push + TTS): ~$0.002
- Background scan/automation: ~$0.001

### Monthly estimate (25 staff)
| Category | Volume | Monthly cost |
|----------|--------|-------------|
| Voice notes (40/day) | 1,200/month | ~$60 |
| Voice commands | 500/month | ~$5 |
| Proactive alerts (50/day) | 1,500/month | ~$3 |
| Background automation/scans | ~5,000/month | ~$5 |
| Conversations (Pro model) | ~200 hrs total | ~$200-400 |
| **Total** | | **~$275-475/month** |

### Hard ceiling: $600/month
Usage caps exist to prevent runaway costs. If approaching ceiling, reduce conversation caps or shift more conversations to Flash.

## Relationship to Other Systems
- **Chief Command** = owner-only dev tool (separate app, separate purpose)
- **Archie app** = staff-facing, connects to Vertex AI Brain
- **AI Brain** = Vertex AI backend that powers Archie (lives in Google Cloud, HIPAA compliant)
- Same app will eventually serve all 5 portals: staff, resident, family, alumni, outside collaborator
