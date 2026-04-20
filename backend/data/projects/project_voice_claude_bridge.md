---
name: Chief Command Center — v3 shipped, v4 dispatch-bridge architecture decided
description: v3 merged end-to-end (Team/Memory/Usage/ProjectContext/Playwright). Canonical future architecture: Chief Command = conversational UI + small changes via API; heavy agent work dispatches to terminal Claude Code under Max. Not yet built — requires dispatch bridge.
type: project
originSessionId: be67e0d9-9428-4101-bdd8-1f21f3e45b19
---

# Chief Command Center

Live at chiefcommand.app via Cloudflare tunnel (tunnel ID: 9cf2d650-08bf-4dc0-843e-ee6c1712b2de, tunnel name: `voice-claude`).
Repo: ~/Desktop/chief-command (github.com/timmonsneill/chief-command)

## Status: v3 merged, voice pending Neill's iOS verification

v2 shipped earlier (Anthropic streaming, VAD voice, projects/agents/sessions). v3 this session added Team tab with named roster + avatars + detail view, Memory tab with per-project/per-agent/audit-log, Usage tab promoted to main nav with hero cards + per-model breakdown + daily trend, ProjectContext reactive bridge (picker propagates to all consumers), Playwright browser-test harness for Forge. Full 5-reviewer sweep ran twice; CRITICALs (rate-limit per-IP, bcrypt, path traversal, JWT from env, upload try/except, audit fcntl.flock) all fixed. Forge browser harness: 7/7 routes PASS. Voice full chain still needs Neill's iOS verification.

## Canonical architecture (decided 2026-04-17)

**Goal: split the cost model between cheap conversational API and Max-covered heavy work.**

- **Chief Command (web, API-billed)**: voice conversations, text chat, observation (dashboards, memory, team roster, usage), and *small* build changes (rename a project, edit memory, toggle a flag).
- **Terminal Claude Code (Max-billed)**: anything heavy — multi-agent builds, full reviewer sweeps, Forge runs, anything that would trigger builders/reviewers.

Rough cost split under this model:
- Voice + chat + dashboards via API: $30-80/mo
- Max: $200/mo flat, covers all heavy agent work
- Total: $230-280/mo, well under the $300 cap

## Dispatch bridge (NOT YET BUILT — ~3-4 hours engineering)

The piece that makes the split actually work. **Not automatic — Chief Command needs a new backend endpoint + stream wrapper to hand work off to a terminal `claude-code` session running on Neill's Mac (or eventually the Mac Mini).**

Architecture:

```
Neill talks to Chief Command → VAD+STT → intent classified
  IF small change (edit text, toggle config, rename):
    handle directly via backend + API  (voice-scale API cost)
  IF heavy work (build feature, run sweep, dispatch agents):
    backend shell-execs `claude --print --output-format stream-json < spec.md`
    → Claude Code runs as Chief in terminal (Max covers)
    → spawns builders, reviewers, Forge
    → stdout streams JSON events back
    → backend pipes events through the same WS channel voice uses
    → TTS reads summary aloud
```

### Components to build

- `POST /api/dispatch` — accepts a spec string + returns a streaming WS channel
- `backend/services/terminal_dispatch.py` — `asyncio.create_subprocess_exec(['claude', '--print', '--output-format', 'stream-json'], stdin=spec)` with stdout parsing
- Stream event parser — Claude Code emits `{type: "tool_use"|"assistant"|"usage"|...}` JSON events; translate to `{type: "agent_status"|"token"|"message_done"}` that the frontend already handles
- Request queue — Max has session-concurrency limits, so queue dispatches and show "waiting for terminal Chief" state
- Intent classifier — simple prompt to decide "API-handled" vs "dispatch to terminal"
- Error handling — terminal session crash, Max quota hit, timeout

### Prerequisites + constraints

- **Neill's Mac must be running and logged in** for this to work. Eventually moves to Mac Mini as an always-on host per the Butler plan (`project_butler_orchestration.md`).
- **Latency**: `claude --print` adds ~1-3s before first token (vs ~500ms API streaming). Fine for async builds, wrong for conversational chat — that's why intent classification matters.
- **Max quota limits**: session-hour caps (5-hour windows). Heavy build days cap out, but user hits cap before API would have been $400+.
- **This aligns with existing Butler plan** — same direction, same intent, just puts Chief Command in the UI layer.

## Follow-up work (in priority order)

1. **Voice full-chain verification on iOS** (needs Neill on phone, not work to build)
2. **Haiku router tier + hard cap enforcement** (~25 min) — aggressive Haiku routing for short conversational replies; hard stop on voice when monthly critical cap hit. Biggest cost lever in the current architecture.
3. **Dispatch bridge** (above — 3-4 hours) — the real cost model fix
4. **Gemini 2.5 Pro as second-opinion reviewer / long-context researcher** — see chat logs for rationale
5. **"Always-listening" VAD** (Quill flagged this — current copy says always-on but it's tap-to-activate)
6. **Per-project dashboard = Arch dashboard style** — Neill wants other projects' pages to match the Arch dashboard at archdashboard.netlify.app

## v3 fixes in this session (shipped 2026-04-17)

- Rate limit per-IP via `CF-Connecting-IP` / `X-Forwarded-For` (was globally collapsing on tunnel IP)
- bcrypt password hashing (was bare SHA-256)
- `JWT_SECRET` required from env, no default (was ephemeral, invalidated every restart)
- Path traversal blocked in memory_service (`_safe_memory_path` with resolve+relative_to+suffix check)
- WS `context` frame validated against `AVAILABLE_PROJECTS` allowlist (prevents system-prompt injection)
- Upload 50MB cap with try/except cleanup (wraps the whole write loop — catches f.write failures too)
- Audit runner: `fcntl.flock` for concurrent-safe appends
- launchd plist `RunAtLoad=true` to catch missed Mondays
- `/api/usage/daily?days=` bounded 1-365 via FastAPI Query
- `memory_path` removed from `/api/team` response (was leaking host home dir)
- `/api/sessions/current` returns `null` (not `{}`) with full field shape (was causing Usage black screen)
- ProjectPicker + useProjectContext share state via React context provider (was dual independent state)
- ErrorBoundary wraps every route (prevents silent black screens — error + stack shown instead)
- chief-icon.svg created (was 404'ing on every page load)
- Audit log parser handles both inline and section-header formats

## Known gaps still

- `/api/status` `tunnel_url: null` cosmetic issue
- VAD WASM fallback path logs "unsupported MIME type" console error on every page load (non-fatal — VAD works via `baseAssetPath` override)
- `STATE_LABELS['speaking']` typo fix already landed; user should verify labels match observed states
- Dead endpoint `/api/agents/reviews` returns `[]` unconditionally (Sable flagged, not yet removed or implemented)

## Key files

- backend/services/llm.py — Anthropic SDK streaming with `project_scope` system prompt prepend
- backend/services/router.py — hybrid Sonnet/Opus routing on bridge phrase
- backend/services/project_parser.py — project detection
- backend/services/project_context.py — per-session project context store
- backend/services/team_service.py — named roster + per-agent memory IO
- backend/services/memory_service.py — global/per-project/per-agent/audit-log IO + parser
- backend/services/usage_tracker.py — spend tracking + by-model + daily series
- backend/scripts/audit_runner.py — weekly hygiene scanner
- backend/scripts/com.chief.audit-weekly.plist — launchd template (RunAtLoad=true, Monday 08:00)
- backend/tests/forge_browser.py — Playwright harness for Forge
- backend/tests/smoke_all_pages.py — all-routes smoke script
- backend/app/main.py — routes + rate limit + upload cap
- backend/app/websockets.py — voice + terminal channels + project scope propagation
- frontend/src/contexts/ProjectContextProvider.tsx — shared context provider
- frontend/src/components/ProjectPicker.tsx, Layout.tsx, ErrorBoundary.tsx
- frontend/src/pages/{Voice,Team,Memory,Usage,Agents,Projects,Terminal}Page.tsx
- frontend/src/hooks/{useVad,useProjectContext,useWebSocket,useAuth}.ts
