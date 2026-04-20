---
name: Archie AI Brain — Cost Model & Usage Caps
description: Detailed cost projections for Vertex AI + infrastructure. $315-585/mo estimated, $1000 hard ceiling. Tiered voice caps by role.
type: project
---

# Archie AI Brain — Cost Model (April 2026)

## Hard ceiling: $1,000/month — owner approved 2026-04-16

## Tiered Voice Conversation Caps

Notes, commands, and alerts do NOT count against caps — only open-ended conversations.

| Tier | Roles | Monthly cap |
|------|-------|-------------|
| Overnight | overnight staff | 60 min/month |
| Case managers | case_manager, director_cm_family | 150 min/month |
| Frontline/ops | recovery_advocate, client_care_coordinator, logistics_coordinator | 200 min/month |
| Support | family_coach, admissions_outreach, facilitator | 150 min/month |
| Leadership | leadership, administrator | 600 min/month |
| Owner | Neill | 1200 min/month |

Soft warning at 80%. Hard cap blocks voice conversations, text still works.

## Daily Usage Model (based on actual staffing)

Shift 1 & 2: 2 frontline + 1-2 leadership each. Overnight: 1 staff. CMs: 3-4 doing 15-20 hrs CM work/day total.

### Notes
- Ops/frontline: 20-30 notes/day (~$0.05 each) = $1.00-1.50/day
- Case managers: 6-8 notes/day (~$0.06 each) = $0.36-0.48/day
- Leadership: 3-5 notes/day = $0.15-0.25/day
- **Total notes: ~$1.50-2.25/day = ~$50/month**

### Voice Commands (quick actions)
- 60-80/day across all staff at ~$0.01 each = ~$18/month

### CM Pre-Session Summaries
- CMs ask "catch me up on this resident" before sessions
- Flash pulls recent notes/UAs/concerns, reads back
- 6-8/day at ~$0.02 each = ~$4/month

### Conversations (Pro model — the expensive part)
- Frontline: 2-3/day, 3-5 min
- Leadership: 2-4/day, 5-10 min
- CMs: 1-2/day, 3-5 min (NOT long consultations)
- Owner: 1-3/day, variable
- **Total: ~$80-250/month**

### Proactive Alerts (to earbuds)
- UA reminders, med rounds, curfews, shift briefings, concern flags
- 40-55/day at ~$0.002 each = ~$5/month

### Background Automation
- Post-data scans, alert rule evaluation, daily summaries, anomaly detection
- ~$12/month

### Infrastructure (Google Cloud)
- Cloud STT: $50-80
- Cloud TTS: $5-10
- Cloud Run: $30-60
- Cloud SQL: $50-80
- Cloud Storage: $5-10
- **Total: $140-240/month**

## Monthly Summary

| Category | Cost |
|----------|------|
| Voice notes (all staff) | ~$50 |
| Voice commands | ~$18 |
| CM summaries | ~$4 |
| Conversations (Pro) | $80-250 |
| Proactive alerts | ~$5 |
| Background automation | ~$12 |
| Infrastructure | $140-240 |
| **TOTAL** | **$315-585/month** |

## Cost Levers (if approaching ceiling)
1. Route conversations through Flash first, escalate to Pro only when needed (~40% savings)
2. Cache common queries (house schedules, med lists, resident summaries)
3. Tighten overnight and PT caps
4. Batch background scans instead of per-event

## Model Routing Strategy
- **Flash:** Notes, commands, alerts, scans, summaries, simple queries
- **Pro:** Open-ended conversations, complex clinical reasoning, report generation
