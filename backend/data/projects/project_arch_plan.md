---
name: Arch — Comprehensive Build Plan
description: Full phase-by-phase roadmap for Arch to Freedom EMR. Phases, todos, and milestones parsed by project_parser.py for the Projects dashboard.
type: project
---

# Arch to Freedom — Build Plan

Single source of truth for Arch's phase structure and build progress. Phase headers, checkbox todos, and dated milestones feed the Plan / Todo / Timeline tabs in the Chief Command Projects dashboard.

**Repo:** `/Users/user/Documents/GitHub/arch-to-freedom-emr`  
**Stack:** React 18 + TypeScript + Vite + Tailwind + shadcn/ui (frontend) · Express + PostgreSQL on Cloud Run + Cloud SQL (backend) · GCP prod stack.

---

## Milestones

2026-03-16 - Project scaffolded from Lovable template — initial EMR UI scaffolding
2026-03-16 - Dashboard, bed board, activity log, alerts, and concerns tab shipped
2026-04-13 - Automation framework shipped — 8 live automations, event bus, cron scheduler, settings UI
2026-04-14 - AI Brain plan locked — vision, architecture, cost model, runaway protection design
2026-04-14 - Clinical Expansion plan locked — outpatient program model, Dr. Rafael as Clinical Director
2026-04-14 - Integrations plan locked — 15 integrations scoped across Phases 3–5
2026-04-14 - CRM Communications plan locked — lean SendGrid replacement for HubSpot
2026-04-15 - GCP migration complete — Railway decommissioned, full Cloud Run + Cloud SQL prod stack
2026-04-15 - CLAUDE.md locked order of operations — walkthroughs → core missing → resident portal → AI Brain
2026-04-15 - Chore checks system shipped — DB, API, frontend
2026-04-15 - Staff page wired to real API — replaced mock data
2026-04-20 - Lovable scaffolding removed — native config, Cloud Build auto-deploy pipeline added
2026-04-20 - Rate changes port — pending_rate_changes migration + route from Desktop
2026-04-21 - Tasks system — undo toast, completed-today view, dashboard expand, project selector, priority labels, dedup
2026-04-21 - Dashboard UX — house bullet icons, tasks widget, foundation gray circle fix
2026-04-21 - Gmail integration MVP shipped — OAuth, label-gated ingestion, email-backed chat, Pub/Sub OIDC verification
2026-04-21 - Notes — toggle Unread/All, Mark all as read
2026-04-21 - Chat — compact rows, collapsible email sections
2026-04-21 - Settings — Design tab moved out of sidebar
2026-04-22 - Forge seed user added — idempotent Playwright MCP integration-test user

---

## Phase 1: Foundation — Make It Real ✅

**Goal:** App saves real data with real logins. Persistent backend, auth, core clinical modules.

- [x] Initial EMR UI scaffolding (Lovable → native config migration)
- [x] JWT auth — login, token, role-based access
- [x] 6 houses + 47 beds seeded — room/bed layouts in migrations
- [x] Residents + stays — real persistence, foreign keys
- [x] Notes module wired to real API
- [x] Concerns module wired to real API
- [x] Tasks module wired to real API
- [x] Medications module — admin history, refusal workflow, inventory tracking
- [x] Billing module — program fees, CM level add-ons, intake fee, ad-hoc charges, rate change approval
- [x] UA/BA module — logging, refusal, 12-day overdue detection
- [x] Calendar module — events, house calendar
- [x] Chat module — house chats, DMs, WebSocket real-time
- [x] Activity log — all writes logged, role-gated reads
- [x] Admissions pipeline — application form, approval cascade
- [x] Provider sessions module
- [x] Phase + CM level + house transfer workflows
- [x] SI keyword detection — safety screening flow (protected automation)
- [x] GCP migration — Cloud Run + Cloud SQL + GCS production stack (Railway decommissioned 2026-04-15)
- [x] Cloud Build auto-deploy pipeline wired (2026-04-20)

**Shipped:** 2026-04-15 (full GCP stack live).

---

## Phase 2: Daily Workflows Work (in progress)

**Goal:** Every core staff workflow is solid — no mocks, no dead buttons, no confusing UX. Automation foundation. No AI yet.

### Core module completions
- [x] Bed board — open-bed highlighting, house occupancy colors
- [x] Clients list — sortable columns, new column set (2026-04-15)
- [x] Dashboard — tasks today widget, house bullet icons, foundation gray circle, timeline overhaul (2026-04-15/21)
- [x] Tasks — undo toast, completed-today view, project selector, priority labels, dedup (2026-04-21)
- [x] Notes — Unread/All toggle, Mark all as read (2026-04-21)
- [x] Chat — compact rows, collapsible email sections, drop House suffix (2026-04-21)
- [x] Settings — Design tab relocated to Settings (2026-04-21)
- [x] Staff page wired to real API (2026-04-15)
- [x] Chore checks system — DB, API, frontend (2026-04-16)
- [x] Rate changes — pending_rate_changes migration + route (2026-04-20)
- [x] Quick Log button wired to QuickLog dialog (2026-04-21)

### Automation framework (8 live automations)
- [x] `automation_definitions` + `automation_runs` tables with `source_rule`/`source_run_id` tagging
- [x] Generic runner — enabled-check, error capture, stats, failure alerting
- [x] Event bus (`emitAutomationEvent`)
- [x] Cron scheduler (node-cron, reads from DB)
- [x] Hardcoded rules registry (`server/src/automations/registry.ts`)
- [x] `/api/automations` REST endpoints (list, get, runs, toggle, run-now, cleanup)
- [x] Settings → Automations UI (leadership only) with health stats + controls
- [x] Owner-override dialog for protected rules (Neill only, reason required)
- [x] `si-monitor` — SI keyword detection, critical alert + urgent SI Assessment task (protected)
- [x] `ua-ba-weekly-schedule` — Sunday 00:00 cron, deterministic per-resident seeding
- [x] `ua-ba-overdue-monitor` — daily 08:00, residents >12 days without test → critical alert
- [x] `curfew-override-expiration` — daily midnight, expired consequence directives reset to phase default
- [x] `milestone-celebration` — 30/60/90/180/365/730 days clean → alerts + house calendar event
- [x] `resident-birthday-reminder` — daily 09:00, alerts + calendar event, leap-year safe
- [x] `discharge-planning-reminders` — daily 08:00, 30/14/7/0 day cascade → tasks + alerts
- [x] `cm-assessment-intake-task` — event-driven on admission, "Complete CM Assessment" task due 3 days after intake

### Walkthrough-driven fixes (in progress)
- [ ] Notes walkthrough — flag broken, confusing, or wrong UX; ship inline fixes
- [ ] Concerns + behavioral contracts walkthrough
- [ ] Medications walkthrough — declare stable before building automation #5/#20
- [ ] Billing walkthrough — declare stable before automations #8-12
- [ ] UA/BA walkthrough — declare stable before automations #2/#3
- [ ] Phases walkthrough
- [ ] House transfer + CM level change workflows walkthrough
- [ ] Provider sessions + appointments walkthrough
- [ ] Discharge walkthrough
- [ ] Goals system walkthrough

### Core missing features (blockers for Phase 3)
- [ ] File storage section — GCS backend + frontend (unblocks insurance card, DL photos, signed PDFs, note attachments)
- [ ] Tag system — full tag catalog decision + implementation
- [ ] Bike + car tracking — inventory, lock combos, history log, profile header integration, monthly audit
- [ ] Form builder — owner-configurable forms for intake, assessments, surveys
- [ ] Recurring monthly invoice generation automation (`recurring-monthly-invoice-generation`)

**"Done" = no module has mock data; every walkthrough finding is addressed; core missing features shipped.**

---

## Phase 3: The Smart Stuff

**Goal:** All 48 automations, assessments, analytics, CRM, alumni. No AI brain yet — deterministic and data-driven rules.

### Remaining automations (40 not yet built)
- [ ] `positive-ua-admitted-use` (#2) — concern + alert + task template on admitted use
- [ ] `refused-ua-ba-escalation` (#3) — tier 1 critical alert on refusal
- [ ] `missed-therapy-appointment-alert` (#4) — 15-min cron, 1hr window after appointment
- [ ] `low-medication-stock-alert` (#5) — ≤7 doses warning, 0 doses critical
- [ ] `medical-alert` (#6) — medical emergency language detection (regex fallback; Brain primary later)
- [ ] `behavioral-contract-violation` (#7) — new concern vs. contract corrective actions
- [ ] `recurring-monthly-invoice-generation` (#8) — 1st of month, `INV-{YEAR}-{SEQ4}` format
- [ ] `invoice-overdue-detection` (#9) — daily 9AM, Day 1/5+/15+ escalation tiers
- [ ] `payment-received-reconciliation` (#10) — clears payment flag, updates balance
- [ ] `adhoc-charge-creation` (#11) — one-time charge auto-appears on next invoice
- [ ] `billing-rate-change-approval` (#12) — any billing field change requires leadership approval
- [ ] `weekly-search-assignments` (#15) — Monday 6AM, flagged + 1-2 random residents
- [ ] `chore-check-scheduling` (#16) — Monday 6AM, round-robin resident assignment
- [ ] `chore-check-failure-concern` (#17) — 3 failures in 30 days → behavioral concern
- [ ] `missed-curfew-auto-concern` (#18) — concern + persistent alert + tasks for CPO/CCC
- [ ] `community-event-attendance-rule` (#19) — monthly, first-30-day check-in rule
- [ ] `daily-medication-round-tasks` (#20) — daily AM/PM/Midday/PRN tasks per house
- [ ] `recovery-engagement-tracking` (#21) — recovery entries → momentum score contribution
- [ ] `phase-progression-validation` (#22) — validates minimum time, prompts for sponsor/employment
- [ ] `phase-demotion-logic` (#24) — documented reason, curfew reverts
- [ ] `house-transfer-approval-workflow` (#25) — two-step approval, bed update, billing recalc
- [ ] `cm-level-change-approval-workflow` (#26) — two-step approval, billing recalc
- [ ] `growth-plan-goals-auto-creation` (#28) — checked items → trackable goals
- [ ] `behavioral-contract-goals-auto-creation` (#29) — corrective actions → profile goals
- [ ] `goal-lock-staff-override` (#30) — lock resident from self-completing goals
- [ ] `admission-approval-cascade` (#31) — creates resident + stay + billing + agreements + intake checklist + CM task
- [ ] `intake-checklist-auto-creation` (#32) — template-driven from `resident_created` event
- [ ] `application-discrepancy-detection` (#33) — AI fuzzy-match call log vs. application
- [ ] `roi-agreement-auto-creation` (#34) — ROI + house rules + financial + confidentiality agreements on approval
- [ ] `prorated-first-invoice` (#35) — prorated intake-to-month-end invoice with intake fee
- [ ] `discharge-checklist-auto-creation` (#37) — 11-step discharge checklist from template
- [ ] `alumni-outreach-auto-scheduling` (#38) — 1/3/6/12 month SMS via Twilio on discharge
- [ ] `alumni-check-in-tracking` (#39) — alumni SMS replies → Chat "Alumni Outreach" folder
- [ ] `personalized-alert-rules-engine` (#40) — user-configurable rules from UI builder
- [ ] `persistent-concern-alerts` (#41) — positive UA stays until resolved, SI minimum 24hrs
- [ ] `curfew-violation-task-escalation` (#42) — tasks for CPO + CCC + on-shift staff
- [ ] `concern-severity-tier-alerts` (#43) — Tier 1/2/3 escalation logic
- [ ] `cm-hours-auto-tracking` (#46) — CM Level → contact hour requirements tracking
- [ ] `event-check-in-resident-prompt` (#47) — pre-event prompt + follow-up on no check-in
- [ ] `note-followup-task-creation` (#48) — staff-initiated button → auto-populate follow-up task

### Assessments + analytics
- [ ] Assessments dashboard page — wire from mock to real API
- [ ] Reports module — wire from mock to real API
- [ ] Recovery engagement module — backend + frontend (required for #21)
- [ ] Recovery Momentum scoring — owner redesigning, blocked until locked

### CRM + communications
- [ ] SendGrid integration — ESP adapter, DKIM/SPF/DMARC
- [ ] Marketing contacts in CRM module — real data (currently mock)
- [ ] Campaign sender — pick template + segment + schedule/send
- [ ] Open + unsubscribe tracking from ESP
- [ ] Email activity on contact records (click-to-email button path only)
- [ ] Bedlist auto-generation from live occupancy data
- [ ] Manual activity logging (Log Call / Log Text / Log Meeting buttons on contact cards)
- [ ] Unsubscribe handling + flag on contact records

### Alumni
- [ ] Twilio account + A2P 10DLC registration (2-week lead time)
- [ ] Alumni SMS outreach automation wired (#38/#39)
- [ ] Alumni check-in timeline in Chat

**"Done" = all 48 automations running, assessments real, CRM replaces HubSpot, alumni SMS live.**

---

## Phase 4: Portals and Integrations

**Goal:** Non-staff audiences. Resident/Family/Alumni/Outside Collaborator portals. Stripe, Quo, Gmail/Chat, Twilio general, clock-in, geolocation, CRM email.

### Resident portal
- [ ] Agreements view — ROI, house rules, financial agreement
- [ ] Goals view — resident self-completion (with staff lock option)
- [ ] Calendar view — events visible to resident
- [ ] Pass requests — submit + track status
- [ ] Messages — portal-native DMs (not email-backed)
- [ ] Consumes form builder + file storage from Phase 2

### Family portal
- [ ] Family chain renders in family portal Chat view, bi-directional with email
- [ ] Family-initiated DMs stay portal-only (no email)
- [ ] No Archie AI summaries shown to family (staff-only rendering)

### Gmail integration
- [ ] Gmail OAuth per staff (narrow scopes: `gmail.labels` + `gmail.send` + `gmail.readonly`)
- [ ] `Arch-chain` label auto-created in staff Gmail via Labels API
- [ ] Pub/Sub push with OIDC JWT verification, JWKS rotation, replay protection
- [x] Gmail MVP scaffolded — OAuth, label-gated ingestion, Pub/Sub JWT verification (2026-04-21)
- [x] Email-backed chat threads in Chat tab (2026-04-21)
- [x] `Arch-chain` label ingestion with SPF/DKIM/DMARC suspicious-flag guard (2026-04-21)
- [x] Reply via Gmail API, correct `In-Reply-To` threading (2026-04-21)
- [x] AES-256-GCM encryption for OAuth refresh/access tokens (2026-04-21)
- [x] Activity log on every PHI touch — 9 event types (2026-04-21)
- [ ] Auto-start family chain on admission with readiness-check alert
- [ ] Canonical participant list UI on resident profile (`include_in_chain` per contact)
- [ ] Per-contact prompts (CM change, new therapist, ROI signed, contact removed)
- [ ] DM creation for partial replies (family Reply instead of Reply-All)
- [ ] Auto-reply inline rendering (1-line Archie summary, no bubble)
- [ ] Multi-subject consolidation per (staff, contact) pair
- [ ] Archie Gmail → CRM auto-ingestion (`arch-crm` label, contact + company auto-create)

### Stripe payments
- [ ] Stripe account setup (no BAA needed)
- [ ] Payment links generated per invoice, emailed to payer
- [ ] Webhook updates invoice status (`payment_intent.succeeded/failed`, `charge.refunded`)
- [ ] Resident/family portal payment page
- [ ] ACH fee pass-through + "save $X by paying with ACH" badge

### Staff clock-in + geolocation
- [ ] Clock In / Clock Out button — house + shift selection
- [ ] `time_entries` table + payroll period view
- [ ] Optional geolocation during shift (consent required, Florida law review first)

### Other integrations (Phase 4)
- [ ] Adobe Sign fallback for e-signature (only if built-in signatures prove insufficient)
- [ ] Twilio general SMS — click-to-SMS, two-way threads in Chat, group SMS, automation-driven SMS

**"Done" = resident portal live, family email chain working end-to-end, Stripe payments processing.**

---

## Phase 5: Advanced Tools + AI Brain

**Goal:** Form builder, mobile, GPS curfew, QuickBooks, Gusto, lab APIs, Vertex AI Brain (all tiers).

### Form builder
- [ ] Owner-configurable form definition (field types, validation, required)
- [ ] Forms embedded in resident portal intake, assessments, surveys
- [ ] Form submissions stored + viewable in resident profile

### Mobile
- [ ] Native shell (Expo/React Native wrapping web core)
- [ ] APNs/FCM push notifications
- [ ] Background audio session for Archie voice alerts
- [ ] Badge counts on app icon
- [ ] "Hey Siri, talk to Arch" shortcut for voice mode on iOS

### GPS curfew enforcement
- [ ] Resident location tracking during active curfew override
- [ ] Geofence alerts on curfew breach

### Financial integrations
- [ ] QuickBooks sync — push invoices, payments, refunds, contractor payouts
- [ ] ACH payouts to contractors — Stripe Connect or Modern Treasury
- [ ] Gusto schedule pull — who's working, which house, what shift

### Lab APIs
- [ ] Lab result API adapter (vendor TBD — depends on which lab Arch uses)
- [ ] Results land as pending records, staff review + confirm before finalizing

### Vertex AI Brain — Phase-by-phase build
- [ ] **Prerequisite:** GCP project + billing + BAA signed (owner action — blocks all AI work)
- [ ] **Brain Phase 1 (Foundation):** `ai_interactions` + `ai_drafts` + `ai_feedback` tables; Policy + Facility Context doc structure; Vertex AI client wrapper; read tools layer; `<AIDraftPrompt>` reusable inline approval component; thumbs up/down feedback component
- [ ] **Brain Phase 2 (Tier 1 read-only):** "Ask the Brain" chat (leadership first); on-demand resident assessments; scheduled summary reports + email delivery; retrieval-augmented memory (embeddings)
- [ ] **Brain Phase 3 (Tier 2 monitoring):** reactive mode on event stream; reflective mode on cron (30–60 min) + nightly; policy compliance watchdog; natural-language alert rule creation
- [ ] **Brain Phase 4 (Tier 3 drafting):** draft tools (notes, concerns, contracts, directives, tasks, emails, rules, policy edits); inline "AI draft" suggestions in dialogs; Copilot Mode side panel + toast patterns
- [ ] **Brain Phase 5 (Tier 4 commits):** `commit_approved_draft` tool; full audit stamping; staff notifications on approval/rejection; voice on PWA (Google STT + TTS)
- [ ] **Brain Phase 6 (learning loop):** nightly tuning job; feedback aggregation dashboard; retrieval-augmented memory wired into Tier 2
- [ ] Runaway protection (build BEFORE any capability): per-call rate limit, daily token budget per feature, circuit breaker (auto-disable on N calls/60s), GCP quota cap
- [ ] Chat + note summaries (Archie) — every chat and resident Notes tab gets a living 2-3 line summary, staff-only rendering
- [ ] Write-path confirmation UX — full literal draft shown before any commit, voice summary + screen draft simultaneously

### Integrations (Phase 5)
- [ ] Lab result APIs wired after Brain trust is established
- [ ] Insurance billing APIs (clearinghouse — OfficeAlly free tier recommended)
- [ ] e-Prescribing (DrFirst/Rcopia with EPCS for controlled substances, ~$200-350/mo)

**"Done" = AI Brain live in production with runaway guards; staff using Ask the Brain and AI drafts daily; mobile app in TestFlight.**

---

## Phase 6: Post-Launch

**Goal:** Operational tuning, staff training, owner manual, policy rewrite based on real use.

- [ ] Owner Operator's Manual — GCP cheat sheet, database overview, "oh shit" runbook, cost guide
- [ ] Staff training program — curriculum, materials, sign-off checklist
- [ ] Policy & procedure document rewrite to match the new system (aligned with `docs/ai_policy.md`)
- [ ] Operational tuning from first 30/60/90 days of real use data
- [ ] Backlog of UX wins discovered during real use
- [ ] Deferred items from launch hardening pass

**Future tracks (parallel, post-go-live):**
- Recovery App integration (~30–60 days post-launch)
- Clinical Expansion (end of 2026) — Dr. Rafael, outpatient + concierge, 3-lane model, $95k buildout, 4-year ramp to +$360k/yr net. Full plan: `docs/CLINICAL_EXPANSION.md`.
