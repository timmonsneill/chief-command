---
name: Chief Command — Roadmap & Plan
description: Phased build plan for Chief Command. Feeds the Projects dashboard in the app. Checkboxes parsed by project_parser.py; dated lines feed the Timeline tab.
type: project
originSessionId: be67e0d9-9428-4101-bdd8-1f21f3e45b19
---
# Chief Command — Roadmap

Single source of truth for where Chief Command is going. Each phase has a goal, concrete tasks underneath, and what "done" means. Completed phases are marked `[x]`. New phases go at the bottom. Checkbox progress and dated lines feed the in-app Projects dashboard tabs (Plan / Todo / Timeline).

## Current focus

**v1.1 — Gemini voice swap.** v1 shipped 2026-04-18 (voice chat + dispatch bridge). Next priority: swap local Whisper + Kokoro for Gemini Live API to cut end-to-end voice latency from 3-4s to ~1.5-2s. Brain stays on Anthropic API. Builds stay on Max subscription via dispatch bridge. Estimated cost: +$15/mo at 2 hrs/day chat.

---

## Milestones (Timeline)

- 2026-04-16: v2 shipped — Anthropic streaming API + browser VAD + usage/cost tracking + live agents + real projects
- 2026-04-18: v3 shipped — Team tab + Memory tab + project-context switcher + 7-tab nav
- 2026-04-18: Chief Context v1.1 merged — real memory injection + agent roster + project-switch intent + scoped-only design + Chef nickname
- 2026-04-18: Dispatch Bridge v1 shipped — voice can now say "build X" and dispatch to local `claude` CLI on Max subscription (zero API cost for the work)
- 2026-04-18: v1 SHIPPED — voice Chief with memory + dispatch end-to-end

---

## Phase 0 — v3 verification ✅ DONE

**Goal:** confirm v3 actually works on Neill's iPhone, not just in Forge's Chromium harness.

- [x] All v3 backend routes (team, memory, usage, context) return correct shapes
- [x] Full 5-reviewer sweep ran on every merge; CRITICAL + HIGH findings fixed
- [x] Forge Playwright browser harness: 7/7 routes PASS, screenshots captured, no render crashes
- [x] Audit log parser handles real section-header format; audit tab shows 2 real entries
- [x] Security: path traversal blocked, bcrypt hashing, JWT from env, rate limit per-IP, upload cap, fcntl-locked audit appends
- [x] Voice full chain verified on Neill's iPhone (VAD frames tick, speech events fire, transcript returns, Chief speaks back)
- [x] Usage tab confirmed rendering on Neill's iPhone (no black screen)
- [x] Team detail view confirmed rendering with Lessons blocks populated

**Shipped 2026-04-18.**

## Phase 2 — Dispatch Bridge ✅ DONE (shipped as v1)

**Goal:** heavy agent work flows from Chief Command to a `claude` CLI subprocess on Neill's Mac, running under Max — NOT API. Voice dispatches, subprocess executes, live output streams back to the TaskBubble while work runs.

### What landed

- [x] `backend/services/classifier.py` — Haiku classifier labeling every turn chat/task/status/cancel
- [x] `backend/services/dispatcher.py` — TaskDispatcher with `asyncio.create_subprocess_exec` spawning `claude --print`, env allowlist (strips ANTHROPIC_*, AWS_*, GITHUB_TOKEN, OPENAI_*, custom secrets), max-runtime watchdog (30 min default), SIGTERM→5s→SIGKILL cancel, 8KB task_spec cap, serialized_sender helper
- [x] `backend/services/repo_map.py` — scope-to-repo mapping with symlink containment (`_ALLOWED_ROOT = ~/Desktop`, resolve + relative_to)
- [x] `backend/app/websockets.py` — glue hook: `_route_user_turn` routes chat/task/status/cancel; task_id on every frame via closure-box pattern; WS disconnect calls dispatcher.cancel; per-connection asyncio.Lock wraps every send (no frame interleaving)
- [x] `frontend/src/components/TaskBubble.tsx` — live running/complete/cancelled states with stdout viewer + Cancel button
- [x] `frontend/src/pages/VoicePage.tsx` — task timeline interleaved with chat messages, routes by task_id
- [x] argv `--` separator + leading-dash reject in `_route_task` (flag-smuggling prevention)
- [x] 62/62 pytest green, 7/7 smoke, Forge SHIP with env-strip verified

**Shipped 2026-04-18 across commits `8ab5a7c`, `ba415f5`, `5902298`, `5931b46`.**

## Phase 3 — Per-project dashboards (in progress)

**Goal:** every Chief Command project (Arch, Chief Command, Butler, Archie) gets a rich dashboard matching Arch-style depth.

- [x] Infrastructure: `PlanTab` / `TodoTab` / `TimelineTab` / `IntegrationsTab` / `BuildsTab` components render from memory-file checkboxes + git log
- [x] Arch dashboard: iframes `archdashboard.netlify.app` (external Netlify app)
- [x] Chief Command plan file structured with phases + checkboxes + milestones → this file
- [ ] Verify CC dashboard Plan tab renders all 9 phases correctly
- [ ] Verify CC dashboard Todo tab groups by phase
- [ ] Verify CC dashboard Timeline tab shows dated milestones above
- [ ] Butler + Archie: populate plan files once those projects kick off

**"Done" = tap Chief Command project card, see all tabs populated.**

## Phase 4 — v1.1 Gemini voice swap (next)

**Goal:** cut end-to-end voice latency from 3-4s to ~1.5-2s by replacing local Whisper + Kokoro with Gemini Live API. Brain stays on Anthropic API. Builds stay on Max via dispatch bridge.

- [ ] Research Gemini Live API auth + audio streaming semantics
- [ ] `backend/services/stt.py` — replace Whisper with Gemini Live STT adapter (WebSocket client to Gemini, stream audio in, get transcript back)
- [ ] `backend/services/tts.py` — replace Kokoro with Gemini Live TTS adapter (send text, stream audio back)
- [ ] `GOOGLE_API_KEY` added to settings + env allowlist for dispatcher subprocess (if needed for `gh` + similar)
- [ ] Integration test: full round-trip on real audio, measure actual end-to-end latency
- [ ] Cost dashboard entry — track audio minutes + daily $ estimate
- [ ] Keep Whisper + Kokoro as fallback behind env toggle (`VOICE_PROVIDER=whisper|gemini`) for offline / cost-cap scenarios

**Cost target:** ~$4-8/mo at 30 min-1 hr daily voice. ~$15/mo at 2 hr/day.
**Latency target:** 1.5-2s end-to-end (vs 3-4s today).
**"Done" = Neill says voice feels like normal conversation on his iPhone.**

## Phase 5 — Chief Command internal dashboard polish

**Goal:** flesh out CC's in-app dashboard per Neill's ask. Timeline, master todo, version history, memory breakdown — all visible from the CC project card.

- [ ] Timeline tab shows commit history + milestones prettified (not just raw git log)
- [ ] Todo tab: master list of everything still [ ] across all memory files
- [ ] Versions sub-section in Plan tab: v2 / v3 / v1 dispatch / v1.1 etc.
- [ ] Memory files list with preview + quick-edit
- [ ] Live agent feed on a dedicated widget — current dispatched tasks + recent sweeps

**"Done" = tapping Chief Command project gives a real picture of plan, progress, history, and active work.**

## Phase 6 — Cost controls

**Goal:** bring monthly API burn down via tighter routing + hard stop. Less urgent after v1.1 lands (brain stays on API but build work is free via Max).

- [ ] Extend `backend/services/router.py` with Haiku 4.5 tier for short conversational replies + status checks
- [ ] Tune `classify_and_route` heuristics: Haiku for <50 tokens of expected output, Sonnet default, Opus on bridge phrase
- [ ] Backend hard-cap: when `/api/usage/summary.alert_level == "critical"`, disable `/ws/voice` LLM path
- [ ] Frontend: Usage tab shows clear red banner when hard-capped
- [ ] Settings-driven caps (`MONTHLY_HARD_CAP_CENTS` env var)

**"Done" = 1 hour of synthetic conversational use routes mostly to Haiku AND hitting critical threshold actually disables voice.**

## Phase 7 — Always-listening voice (wake-word)

**Goal:** current state says "Tap to start voice" — that's tap-to-activate, not always-on. Claude.ai voice is genuinely ambient. Match that.

- [ ] Decision: KEEP tap-to-activate (battery, privacy) and fix the copy, OR build actual always-listening?
- [ ] If always-listening: "Hey Chief" wake-word detection (Picovoice Porcupine? Open-source options?)
- [ ] Alternative: proximity / presence trigger (phone face-up while app open)
- [ ] Update VoicePage copy to match chosen model

**"Done" = voice UX matches the description.**

## Phase 8 — Gemini 2.5 Pro second-opinion reviewer ("Gem")

**Goal:** diversify model family for reviews + long-context research. Gemini's 2M context handles whole-repo scans; different blind spots than Claude reviewers.

- [ ] Install `google-genai` in backend venv
- [ ] `GOOGLE_API_KEY` in Settings + backfill pattern
- [ ] `backend/services/gemini.py` mirroring `llm.py` interface
- [ ] New named agent "Gem" — second-opinion reviewer + long-context researcher (>400k tokens)
- [ ] Seed `~/.claude/agents/memory/gem.md`
- [ ] Update roster to include Gem as 13th member — Opus-tier researcher cousin
- [ ] Wire Atlas's "use Gem when context > 400k" escalation

**"Done" = Gem appears in Team tab, fires on `/build` flows as a second review pass, finds things Claude reviewers missed.**

## Phase 9 — Mac Mini convergence (Butler host)

**Goal:** dispatch bridge currently depends on Neill's Mac being awake + logged in. For 24/7 reliability, move the dispatch target to the Mac Mini per `project_butler_orchestration.md`.

- [ ] Install Claude Code on Mac Mini
- [ ] Claude Code auth via Max account on Mac Mini
- [ ] Secure RPC from Chief Command backend to Mac Mini
- [ ] `repo_map` or dispatcher points at Mac Mini paths instead of local
- [ ] Failover: if Mac Mini unreachable, fall back to user's laptop, else surface offline

**"Done" = Chief Command dispatches work 24/7 regardless of laptop state.**

## Phase 10 — Observation + closing the loop

**Goal:** Chief's "watches all agents" promise actually observable by Neill over time, not just this-moment state.

- [ ] Live agent feed on a dedicated page — scrolling log of every agent dispatched, their lane, status, output summary, cost
- [ ] Cost per dispatched task, per agent, per day
- [ ] Pattern detection: which agents drift most? Which reviewers catch the most CRITICALs? Where's the bottleneck?
- [ ] "What did Riggs do last week?" — real answer, not a guess

**"Done" = Neill opens Chief Command and understands what all the agents collectively did over time.**

## Phase 11 — Claude.ai + MCP pivot (deferred decision)

**Goal:** evaluate whether Chief Command should be replaced by Claude.ai directly, with chiefcommand.app becoming a pure MCP server that dispatches to local `claude`. Pro: voice + memory + project context already solved by Claude.ai. Con: loses custom UI and long-running background task UX.

- [ ] Use v1.1 for a week, collect honest comparison data
- [ ] Install Claude mobile app, test voice + Projects feature head-to-head
- [ ] If Claude.ai covers 80%+ of Chief Command's value: design MCP server wrapper around dispatcher + classifier + repo_map
- [ ] Migrate or retire — don't let two similar UIs compete for the same mindspace

**"Done" = explicit decision documented in a memory file. Either "Chief Command stays, here's why" or "Chief Command → MCP server, retirement plan."**
