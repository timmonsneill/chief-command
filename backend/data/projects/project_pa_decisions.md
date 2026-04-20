---
name: Personal Assist — locked decisions (ADR summary)
description: Summary of the locked architectural decisions for Personal Assist. Full ADR log lives in personal-assist/docs/DECISIONS.md in-repo.
type: project
---

# Personal Assist — decisions summary

Eight ADRs locked at kickoff 2026-04-19. Full text in `personal-assist/docs/DECISIONS.md` in-repo. Summary here for quick recall:

- **ADR-001 Name + alias.** Canonical `Personal Assist`. Voice aliases `Jess`, `Personal Assist`, `PA`. Jess is also the in-app AI assistant's name. Avoids "Command" collision with Chief Command.
- **ADR-002 Sibling repo.** Not a subscope of CC. Own repo, own DB, own auth. Dispatchable from CC voice layer but otherwise isolated.
- **ADR-003 Stack.** FastAPI + Postgres 16 + Redis + React/Electron. React Native deferred to Phase 3.
- **ADR-004 Jess brain: Google-only.** `gemini-2.5-flash-native-audio-preview-12-2025` voice; `gemini-2.5-pro` reasoning; `gemini-2.5-flash-image` + Imagen 4 image; Veo 3.1 video; Docs/Slides/Drive APIs. Claude stays CC's brain with zero crossover.
- **ADR-005 Flash↔Pro router.** Three-tier escalation: auto (triggers in Flash), explicit ("thinking cap on"), session ("thinking mode"). Prevents "Flash feels elementary" worry.
- **ADR-006 $150/mo cap + sub-budgets.** Google Cloud hard cap + app-level daily turn caps + per-action financial caps.
- **ADR-007 Three memory layers.** CC orchestrator memory / PA build memory / Jess runtime memory. Disjoint, do not conflate.
- **ADR-008 Atlas verification (2026-04-19).** Model IDs corrected. Stay on Gemini 2.5 native-audio (3.1 Flash Live drops NON_BLOCKING). Veo 3.1 Fast is $0.10/s. Auth = OAuth installed-app, not service account. Realistic monthly spend ~$30–65; lots of headroom under $150 cap. Re-verify 2026-07-19.

## Why this is in the dashboard-memory folder

The in-repo `DECISIONS.md` is the authoritative source of truth. This memory is a summary so Chief Command's dashboard and any orchestrator session can see the decision-state at a glance without cloning the repo.
