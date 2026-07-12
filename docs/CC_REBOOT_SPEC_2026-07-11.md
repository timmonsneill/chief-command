# Voice-First Multi-Model Agent Harness — Build Spec

> **Status: OWNER-DELIVERED REBOOT SPEC (2026-07-11, verbatim).** Neill: "I essentially have an entirely new spec for chief command.. I literally probably want to start over with it." This supersedes prior CC plans as the direction of record pending the design conversation. The existing CHIEF_COMMAND_V3_SPEC.md and running v1 backend remain for reference/salvage — nothing deleted.

**Project codename:** Chief Command v2 (rename at will)
**Owner:** Neill
**Prime directive:** Two-way voice is the product. Everything else is plumbing behind it. But the build order is text-first, voice-second — the voice layer must be a swappable skin over a harness that works fully via text, so a voice problem can never block the whole project again.

---

## 1. What this is

An always-on personal agent orchestration system. Neill talks to it (push-to-talk, from his phone anywhere or at his desk) and it routes work to a stack of AI models: a frontier orchestrator, an autonomous coding workhorse, a free local grinder, and an escalation reviewer. It runs builds, reviews its own output through a multi-agent gauntlet, and reports back by voice.

## 2. Hardware & environment

- **Phase 0 host:** Neill's current MacBook Pro (Apple Silicon). Everything must run here first. Detect available RAM at setup and size the local model accordingly (see §5.3).
- **Phase 2+ host:** a Mac Studio (target ~64GB M4 Max) to be purchased later. **Hard requirement: the entire system must be hardware-agnostic** — one config file change (hostnames/endpoints) migrates it. No machine-specific paths hardcoded.
- **OS:** macOS. Services managed via `launchd` so they survive reboots.
- **Networking:** Tailscale mesh. The harness binds to the tailnet, never the public internet. Phone and MacBook reach it via Tailscale hostname. No port forwarding, no exposed services.

## 3. Core harness: OpenClaw

OpenClaw is the orchestration backbone — gateway, session management, agent memory, and the routing layer to all model providers.

Requirements:
- Install pinned to a specific version. **No auto-updates.** Version bumps are deliberate, tested events (the Gemini CLI shutdown is the cautionary tale — vendor and tooling behavior changes under you).
- All provider connections defined in config, each swappable independently.
- Runs as a persistent daemon under `launchd`.
- Expose a local HTTP API + web UI over the tailnet (this is what the phone/desktop clients hit).
- Text chat interface is the first deliverable and permanent fallback. If voice dies, text always works.

## 4. Model providers & routing

Four seats. Each one behind a named role in config so providers can be swapped without touching orchestration logic.

### 4.1 Orchestrator — OpenAI Codex (ChatGPT subscription)
- Connect via Codex's official OAuth route for third-party frameworks (subscription-based, no API billing). This is sanctioned by OpenAI.
- Role: top-level planning, task decomposition, structured builds, running the reviewer gauntlet (§6).
- Uses Codex CLI's native subagent system (GA since March 2026): custom agents as TOML files in `~/.codex/agents/`, up to 8 parallel workers, `max_depth = 1`.
- Model routing within Codex: flagship model (GPT-5.5 / 5.6 when available) for orchestration and judgment; mini-tier model for cheap parallel subagent fanout.

### 4.2 Autonomous workhorse — Grok Build (SuperGrok subscription)
- xAI explicitly sanctions headless (`-p`) and third-party-harness use on subscription, including OpenClaw via OAuth. Use it for exactly that: unattended, long-running build jobs dispatched by the harness.
- Role: volume autonomous coding — migrations, test backfills, large refactors. Supports up to 8 parallel subagents in git worktrees.
- Treat xAI's permissiveness as revocable. Isolate behind the provider abstraction like everyone else.

### 4.3 Local grinder — Ollama (free, 24/7)
- Serve via Ollama's network API so any machine on the tailnet can use it.
- Model sizing by available RAM:
  - MacBook Pro phase: whatever fits comfortably — Qwen3-Coder 30B class if 36GB+, otherwise a 7–14B coder. It's a junior dev either way.
  - Studio phase (64GB): Qwen3-Coder 30B resident full-time alongside the voice stack.
- Role: overnight/background grinding — scaffolding, boilerplate, test writing, first-draft fixes. **Design rule: local output never ships without a subscription-tier review pass.**

### 4.4 Escalation reviewer — Claude (Max subscription + credit pool)
- Two modes, and the distinction matters:
  - **Interactive (subscription, effectively free):** Neill opens a Claude Code session (locally or via SSH) and reviews the gauntlet's flagged queue himself with Claude. Human-initiated = subscription limits.
  - **Headless (credit pool):** the harness may invoke `claude -p` for automated deep reviews, drawing on the Max plan's included monthly agent credits. Budget-cap this in config (e.g., N automated Claude reviews/day) so it never silently overruns the free allotment.
- Role: highest-judgment work — architecture review, contested reviewer verdicts, the bug the local model looped on three times.
- This seat is optional/droppable. The system must run fully without it.

### 4.5 Explicitly excluded
- **Google/Gemini:** ToS prohibits third-party OAuth use (names OpenClaw specifically), and consumer Gemini CLI access ended June 18, 2026. Do not integrate. Revisit only via paid API keys if ever needed.

## 5. Voice layer (the product)

### 5.1 The core architecture decision — voice skin ≠ brain
The voice layer and the orchestrator are **separate**. A fast, local voice front-end handles listening and speaking; it makes **tool calls** to the GPT-5.6 orchestrator (Codex, §4.1) which holds the harness memory, subagents, and reasoning. You are talking *to your actual orchestrator* — the local pipeline is just its ears and mouth. This is what kills the Chief Command latency wound: the voice layer acknowledges in ~300ms ("on it, kicking that off") while 5.6 reasons and builds underneath. Perceived latency is the voice layer's, not the builder's.

This means you get 5.6's full intelligence — interrogating a build, pushing back, inspecting its own work — with local, free, low-latency voice on top. The orchestrator is never a dumbed-down voice model; it's the real brain with all the context and subagents.

### 5.2 Interaction model
- **Push-to-talk** on all clients. Hold/tap mic button → speak → release → response. No wake word, no open mic in v1.
- Full duplex feel via streaming: user should hear the start of the reply in **under ~1.5 seconds** from releasing the button. That's the latency budget and the acceptance test.

### 5.3 Pipeline (all local, all free) — the chosen path
Decision: **local Whisper + Kokoro, NOT OpenAI realtime voice.** Rationale is cost at Neill's usage pattern (a couple hours/day, indefinitely):
- OpenAI's realtime voice (gpt-realtime-2) does **not** run on the ChatGPT/Codex subscription — it's separate metered OpenAI Platform API billing (~$0.18–0.46/min for the full model). At 2 hrs/day that's roughly **$700–1000+/month**. Even gpt-realtime-mini with prompt caching (~$0.05–0.10/min) lands around **$150–250/month** for daily hours-long use. Not worth it for a single-user personal builder.
- Local pipeline = **$0**, runs on the machine being bought anyway, and the streaming design below already hits the ~1.5s target. The only thing given up is the last increment of speech-to-speech polish/barge-in — irrelevant for "kick off and talk through a build."

Pipeline:
- **STT:** faster-whisper (or whisper.cpp) running on the host. Streaming transcription — begin transcribing as audio arrives, don't wait for end-of-utterance.
- **TTS:** Kokoro. **Sentence-chunked synthesis:** begin playback the moment the first sentence of the response is ready while the rest generates. This is the single most important latency decision — perceived latency = time-to-first-sentence, not total response time.
- **Plumbing:** use Pipecat (or equivalent mature framework) for the streaming/turn-taking pipeline rather than hand-wiring WebSocket audio. Chief Command v1 died hand-wiring this; don't repeat it.
- Voice responses should be *spoken-length* — the harness prompts models for a short verbal summary, with full detail written to the session log/text UI.
- **Optional swap-in (later):** the voice front-end sits behind the same provider abstraction as everything else, so gpt-realtime-mini can be A/B'd against the local stack if you ever want to feel the difference. Metered, off by default, per-session cost cap if enabled. Reserve paid realtime voice for short transactional interactions (Jess's territory), never hours-long builder sessions.

- **Phone (primary):** a mobile web app (PWA) served by the harness over Tailscale. Big push-to-talk button, streaming audio out, text transcript visible, text input as fallback. Native wrapper only if PWA mic handling on iOS proves unreliable — evaluate honestly and early, since iOS Safari mic/background-audio quirks are the likeliest pain point.
- **Desktop:** same web UI, plus optional global-hotkey push-to-talk menubar utility (later phase).

## 6. Agent gauntlet (ported from Claude Code)

Neill has an existing Claude Code setup: orchestrator + ~10 specialized agents, a 6-reviewer gauntlet, and a tester agent. Port it:

- Convert Claude Code agent markdown definitions → Codex TOML agents (same instructions, new wrapper). Keep the originals so Claude Code remains usable in parallel.
- `AGENTS.md` is the cross-agent standard — Codex, Grok Build, and Claude Code all read it. Single source of truth for project conventions. (Codex also reads CLAUDE.md as fallback; converge on AGENTS.md.)
- SKILL.md skills are portable across clients; carry them over as-is.
- Gauntlet flow: builder produces work → up to 6 reviewers run **in parallel** as Codex subagents (mini-tier models) → tester agent validates → disagreements or flagged items escalate to the Claude seat or to Neill's interactive review queue.
- **Model diversity rule:** reviewer panel should draw from at least two model families (e.g., GPT-mini reviewers + Grok reviewer + local reviewer). Different models miss different bugs.
- Codex does not auto-spawn custom agents — delegation must be written explicitly into the workflow prompts / AGENTS.md.

## 7. Memory & state

- Port Neill's existing memory files (CLAUDE.md / MEMORY.md-style markdown) into a harness-level `memory/` directory loaded into orchestrator context.
- Session state, job queue, and build history in SQLite on the host.
- Every dispatched job gets a persistent record: what was asked, which provider ran it, gauntlet verdicts, final status. Voice query "what did the overnight run do?" must be answerable from this.
- Git as audit trail for everything the agents produce.

## 8. Build phases & acceptance criteria

**Phase 1 — Text harness (the machine).**
OpenClaw daemon on MacBook Pro; Codex OAuth connected; Ollama serving a local model; web UI reachable from phone over Tailscale. ✅ *Accept: from the phone, type "have the local model write X, then have Codex review it" and it happens, with the job recorded.*

**Phase 2 — Voice loop, local, wired to the orchestrator.**
Whisper + Kokoro + Pipecat pipeline on the desktop client, making tool calls into the GPT-5.6 orchestrator with full harness memory. ✅ *Accept: push-to-talk at desk, talk through a build with the orchestrator (not a stand-in), hear the answer begin in <1.5s while heavy work runs underneath.*

**Phase 3 — Voice from phone.**
PWA push-to-talk over Tailscale. ✅ *Accept: from the golf course parking lot, dispatch a build by voice and hear confirmation.*

**Phase 4 — Autonomy & gauntlet.**
Grok Build seat connected; full reviewer gauntlet ported; overnight queue running; Claude escalation wired with credit budget cap. ✅ *Accept: queue 3 tasks at night by voice; wake to a triaged, gauntlet-reviewed PR stack and a spoken morning summary on request.*

**Phase 5 — Studio migration.**
Buy the Studio (or M5 Ultra, pending October). Migration = config change + data copy. ✅ *Accept: full system live on Studio in under a half day; MacBook Pro demoted to client.*

## 9. Risks & standing constraints

- **Vendor policy drift is the #1 risk.** Anthropic already moved headless to credits; Google killed consumer CLI access; xAI's openness is young. Mitigation: provider abstraction, pinned versions, no single-vendor dependency, text fallback always.
- **Claude headless = credit pool, always.** Never architect around gray-area workarounds (e.g., puppeting interactive sessions programmatically) — Anthropic bans aggressive circumvention, and the sanctioned path is cheap anyway.
- **iOS PWA audio quirks** — likeliest engineering pain in Phase 3; prototype early.
- **Local model overconfidence** — enforce the never-ship-unreviewed rule in the pipeline itself, not by convention.
- **Token burn** — parallel gauntlets multiply usage; instrument per-provider usage counters from day one and surface them in the UI.

## 10. External connection: Jess (future)

Jess (Neill's personal assistant, built separately on the Google/Gemini stack in her own project) will connect to this Builder as a **relay over the tailnet** — not a merge. The Builder exposes a small message-in / result-out endpoint; Jess drops build requests onto the Builder's queue and reads results back. "One voice, two brains": Neill talks to Jess, she forwards build tasks to the Builder and relays the answer. No shared substrate, no shared voice pipeline — just a fat tailnet pipe (no HIPAA constraint between these two). Build this endpoint so it's ready when Jess wants it; Jess's own scope lives in her separate spec.

## 11. Out of scope (v1)

Wake word / always-listening; open-mic interruption (barge-in); multi-user access; trillion-parameter local models; any Google integration; home-automation integrations. **Jess's personal-assistant capabilities are explicitly out of scope for this spec** — she is a separate app on a separate stack (§10).
