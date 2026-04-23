---
name: Chief Command — Roadmap & Plan
description: Phased build plan for Chief Command. Feeds the Projects dashboard in the app. Checkboxes parsed by project_parser.py; dated lines feed the Timeline tab.
type: project
originSessionId: be67e0d9-9428-4101-bdd8-1f21f3e45b19
---
# Chief Command — Roadmap

Single source of truth for where Chief Command is going. Each phase has a goal, concrete tasks underneath, and what "done" means. Completed phases are marked `[x]`. New phases go at the bottom. Checkbox progress and dated lines feed the in-app Projects dashboard tabs (Plan / Todo / Timeline).

---

## Milestones (Timeline)

- 2026-04-16: v2 shipped — Anthropic streaming API + browser VAD + usage/cost tracking + live agents + real projects
- 2026-04-18: v3 shipped — Team tab + Memory tab + project-context switcher + 7-tab nav
- 2026-04-18: Chief Context v1.1 merged — real memory injection + agent roster + project-switch intent + scoped-only design + Chef nickname
- 2026-04-18: Dispatch Bridge v1 shipped — voice can now say "build X" and dispatch to local `claude` CLI on Max subscription (zero API cost for the work)
- 2026-04-18: v1 SHIPPED — voice Chief with memory + dispatch end-to-end
- 2026-04-20: UI overhaul — light theme + steel-blue + amber + Fraunces/Inter + collapsible icon-rail sidebar
- 2026-04-20: Project dashboards live — CC data moved into repo (`backend/data/projects/`), parser + 5 tabs rendering
- 2026-04-20: Voice hardening — server-side TTS speed, VAD endpoint tuning, classifier gaps, gRPC+AEC fixes
- 2026-04-20: PM2 ecosystem deployed — auto-update script, cloudflared Tailscale, dropped Netlify dependency
- 2026-04-21: Voice hardening v2 merged — scope-isolation fix + barge-in tightening + inline task activity row
- 2026-04-21: Forge Playwright MCP wired — WS smoke tests, idempotent seed user
- 2026-04-22: Voice cost tracking shipped — STT seconds + TTS chars recorded per turn, per-provider rollups, UsagePage cost cards + $50 warn banner
- 2026-04-22: Settings key/value table added — persistent settings storage

---

## Phase 0: v3 verification ✅

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

---

## Phase 2: Dispatch Bridge ✅

**Goal:** heavy agent work flows from Chief Command to a `claude` CLI subprocess on Neill's Mac, running under Max — NOT API. Voice dispatches, subprocess executes, live output streams back to the TaskBubble while work runs.

- [x] `backend/services/classifier.py` — Haiku classifier labeling every turn chat/task/status/cancel
- [x] `backend/services/dispatcher.py` — TaskDispatcher with `asyncio.create_subprocess_exec` spawning `claude --print`, env allowlist (strips ANTHROPIC_*, AWS_*, GITHUB_TOKEN, OPENAI_*, custom secrets), max-runtime watchdog (30 min default), SIGTERM→5s→SIGKILL cancel, 8KB task_spec cap, serialized_sender helper
- [x] `backend/services/repo_map.py` — scope-to-repo mapping with symlink containment (`_ALLOWED_ROOT = ~/Desktop`, resolve + relative_to)
- [x] `backend/app/websockets.py` — glue hook: `_route_user_turn` routes chat/task/status/cancel; task_id on every frame via closure-box pattern; WS disconnect calls dispatcher.cancel; per-connection asyncio.Lock wraps every send (no frame interleaving)
- [x] `frontend/src/components/TaskBubble.tsx` — live running/complete/cancelled states with stdout viewer + Cancel button
- [x] `frontend/src/pages/VoicePage.tsx` — task timeline interleaved with chat messages, routes by task_id
- [x] argv `--` separator + leading-dash reject in `_route_task` (flag-smuggling prevention)
- [x] 62/62 pytest green, 7/7 smoke, Forge SHIP with env-strip verified

**Shipped 2026-04-18 across commits `8ab5a7c`, `ba415f5`, `5902298`, `5931b46`.**

---

## Phase 3: Per-project dashboards ✅

**Goal:** every Chief Command project (Arch, Chief Command, Personal Assist) gets a rich dashboard matching planned depth.

- [x] Infrastructure: `PlanTab` / `TodoTab` / `TimelineTab` / `IntegrationsTab` / `BuildsTab` components render from memory-file checkboxes + git log
- [x] CC data moved into repo (`backend/data/projects/`) — PROJECTS.json + memory files
- [x] `project_parser.py` — parses `## Phase N`, `- [x]`/`- [ ]`, `YYYY-MM-DD - Label` patterns
- [x] Chief Command plan file structured with phases + checkboxes + milestones
- [x] Arch dashboard configured — `project_archie_voice_app.md` + `project_archie_cost_model.md`
- [x] Personal Assist dashboard configured — `project_pa_plan.md` + `project_pa_decisions.md`
- [x] `project_arch_plan.md` created — comprehensive Arch phase plan, 6 phases, 100+ todos (2026-04-22)
- [ ] Verify CC dashboard Plan tab renders all phases correctly
- [ ] Verify CC dashboard Todo tab groups by phase
- [ ] Verify CC dashboard Timeline tab shows dated milestones

**"Done" = tap any project card, see all tabs populated with accurate state.**

---

## Phase 4: Voice hardening ✅

**Goal:** voice feels natural and reliable on Neill's iPhone — no stuck states, no mis-paced speech, barge-in works.

- [x] Server-side TTS speed honors user preference (backend reads `current_speed` parameter)
- [x] VAD endpoint tuning — reduced false positives, better silence detection
- [x] Classifier gap fixes — status/cancel turns no longer misfired as chat
- [x] gRPC + AEC fixes for Google STT voice path
- [x] Per-subject WS scope keying + context-frame gate (scope isolation bug fixed)
- [x] Barge-in tightening — grace window tuned, "speaking" state delayed until audio actually plays
- [x] Cancel semantics tightened + STT silence timeout
- [x] Narration honors user speed — stuck-state failsafe added
- [x] `current_speed` threaded through all TTS paths including task-dispatch fallbacks
- [x] Inline task activity row in voice chat transcript
- [x] Forge WS scope-isolation smoke tests

**Shipped 2026-04-20 and 2026-04-21. Merged as `voice-hardening-2026-04-21` (bf20501).**

---

## Phase 5: Usage + voice cost tracking ✅

**Goal:** Neill can see exactly what Chief Command is costing — Claude API + STT + TTS — with real numbers, not guesses.

- [x] `backend/db.py` — idempotent voice-usage columns (`stt_seconds`, `tts_chars`, `stt_provider`, `tts_provider`) on `turns` table
- [x] Settings key/value table for persistent settings storage
- [x] `feat(voice): tag STT/TTS services with provider_name for usage tracking`
- [x] `feat(voice): record STT seconds + TTS chars per turn, emit voice block on usage`
- [x] `feat(api): expose voice costs on /api/usage/summary + /api/sessions/current`
- [x] `feat(usage): voice cost tracking (STT + TTS) with per-provider rollups`
- [x] `feat(types): voice usage interfaces for UsagePage + live usage event`
- [x] `feat(usage-ui): voice cost cards + $50 warn banner on UsagePage`
- [x] Rolling Claude API cost bucketed by `turns.created_at` — correct daily rollups
- [x] TTS chars tallied on successful synthesis only (not on enqueue)
- [x] `stt_seconds`/`current_speed` threaded through task-dispatch cancel paths

**Shipped 2026-04-22.**

---

## Phase 6: Cost controls (next)

**Goal:** bring monthly API burn down via tighter routing + hard stop.

- [ ] Extend `backend/services/router.py` with Haiku 4.5 tier for short conversational replies + status checks
- [ ] Tune `classify_and_route` heuristics: Haiku for <50 tokens of expected output, Sonnet default, Opus on bridge phrase
- [ ] Backend hard-cap: when `/api/usage/summary.alert_level == "critical"`, disable `/ws/voice` LLM path
- [ ] Frontend: Usage tab shows clear red banner when hard-capped
- [ ] Settings-driven caps (`MONTHLY_HARD_CAP_CENTS` env var)
- [ ] 2-week evaluation window: track daily API cost for baseline before tuning

**"Done" = 1 hour of synthetic conversational use routes mostly to Haiku AND hitting critical threshold actually disables voice.**

---

## Phase 7: Internal dashboard polish

**Goal:** CC's in-app dashboard reflects real plan, progress, history, and active work.

- [ ] Timeline tab shows commit history + milestones prettified (not just raw git log)
- [ ] Todo tab: master list of everything still `[ ]` across all memory files
- [ ] Versions sub-section in Plan tab: v2 / v3 / v1 dispatch / voice hardening etc.
- [ ] Memory files list with preview + quick-edit
- [ ] Live agent feed widget — current dispatched tasks + recent sweeps

**"Done" = tapping Chief Command project gives a real picture of plan, progress, history, and active work.**

---

## Phase 8: Always-listening voice (wake-word)

**Goal:** move from tap-to-activate toward ambient voice.

- [ ] Decision: keep tap-to-activate (battery, privacy) and fix copy — OR build actual always-listening
- [ ] If always-listening: "Hey Chief" wake-word detection (Picovoice Porcupine or open-source)
- [ ] Alternative: proximity/presence trigger (phone face-up while app open)
- [ ] Update VoicePage copy to match chosen model

**"Done" = voice UX matches the description.**

---

## Phase 9: Gemini 2.5 Pro second-opinion reviewer ("Gem")

**Goal:** diversify model family for reviews + long-context research.

- [ ] Install `google-genai` in backend venv
- [ ] `GOOGLE_API_KEY` in Settings + backfill pattern
- [ ] `backend/services/gemini.py` mirroring `llm.py` interface
- [ ] New named agent "Gem" — second-opinion reviewer + long-context researcher (>400k tokens)
- [ ] Seed `~/.claude/agents/memory/gem.md`
- [ ] Update roster to include Gem as 13th member — Opus-tier researcher cousin
- [ ] Wire Atlas's "use Gem when context > 400k" escalation

**"Done" = Gem appears in Team tab, fires on `/build` flows as a second review pass.**

---

## Phase 10: Mac Mini convergence (Butler host)

**Goal:** dispatch bridge 24/7 reliability — move dispatch target to Mac Mini.

- [ ] Install Claude Code on Mac Mini
- [ ] Claude Code auth via Max account on Mac Mini
- [ ] Secure RPC from Chief Command backend to Mac Mini
- [ ] `repo_map` or dispatcher points at Mac Mini paths instead of local
- [ ] Failover: if Mac Mini unreachable, fall back to user's laptop, else surface offline

**"Done" = Chief Command dispatches work 24/7 regardless of laptop state.**

---

## Phase 11: Observation + closing the loop

**Goal:** Chief's "watches all agents" promise is actually observable over time.

- [ ] Live agent feed on a dedicated page — scrolling log of every agent dispatched, lane, status, output summary, cost
- [ ] Cost per dispatched task, per agent, per day
- [ ] Pattern detection: which agents drift most? Which reviewers catch the most CRITICALs?
- [ ] "What did Riggs do last week?" — real answer, not a guess

**"Done" = Neill opens Chief Command and understands what all the agents collectively did over time.**

---

## Phase 12: Claude.ai + MCP pivot (deferred decision)

**Goal:** explicit decision on whether Chief Command stays or becomes a pure MCP server.

- [ ] Use current voice for a week, collect honest comparison data vs. Claude mobile app
- [ ] Install Claude mobile app, test voice + Projects feature head-to-head
- [ ] If Claude.ai covers 80%+ of Chief Command's value: design MCP server wrapper around dispatcher + classifier + repo_map
- [ ] Migrate or retire — don't let two similar UIs compete for the same mindspace
- [ ] Document decision in a memory file either way

**"Done" = explicit decision documented. Either "Chief Command stays, here's why" or "Chief Command → MCP server, retirement plan."**
