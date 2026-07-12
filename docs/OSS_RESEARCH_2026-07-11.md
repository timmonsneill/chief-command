# Chief Command v2 — Open-Source Survey & Spec Corrections (2026-07-11)

Research pass requested by Neill before any scaffolding. Four parallel research lanes:
OpenClaw ecosystem, local voice stack, vendor-claim verification, gauntlet/job-queue prior art.

**Headline:** roughly 70% of the spec already exists as maintained open source. The genuinely
novel work is three things — durable job history, budget/quality gates enforced in the database,
and the iPhone voice client. Everything else is adopt, fork, or crib.

**But four factual claims in the spec are wrong or stale**, and one of them (the Anthropic credit
pool) is load-bearing for a design decision that is currently solving a non-problem. Fix the spec
before building.

---

## 1. Spec corrections (do these first)

| # | Spec claim | Verdict | Action |
|---|---|---|---|
| 1 | Claude headless `claude -p` draws from a Max-plan **monthly agent-credit pool** (§4.4) | **WRONG** — announced, then **paused before taking effect**. Anthropic's own support article: *"We're pausing the changes… For now, nothing has changed."* `claude -p` draws on normal subscription limits. | Delete §4.4's credit-pool mechanics and the budget-cap-as-necessity framing. Keep headless usage *isolated* as forward-proofing (Anthropic says it will re-propose with notice). **Do keep** the real constraint: subscription OAuth tokens in third-party tools are prohibited (terms Feb 20 2026, billing enforcement Apr 4 2026). |
| 2 | Codex subagents: **up to 8 parallel workers** (§4.1) | **WRONG** — `agents.max_threads` **defaults to 6**. The "8" looks like contamination from the Grok Build claim. (`max_depth = 1` and the `~/.codex/agents/` TOML layout are **correct**; GA March 2026 **confirmed**.) | Fix fan-out arithmetic to 6, or set `max_threads` explicitly and validate empirically. |
| 3 | Codex OAuth for third-party harnesses is **"sanctioned by OpenAI"** (§4.1) | **WRONG as stated.** It *works* — it's what OpenClaw's OpenAI provider uses — but no OpenAI doc sanctions it, and OpenAI's ToS forbids sharing account credentials / programmatic extraction outside permitted surfaces. It is **tolerated, not sanctioned**. | **This is the biggest latent risk in the spec.** See §5 below. |
| 4 | OpenAI realtime voice ~$0.18–0.46/min → $700–1000/mo (§5.3) | **WRONG — off by ~10×.** See **§1c**, which supersedes this row. Real figure at Neill's usage: **~$25/mo (mini), ~$76/mo (full).** The architectural half (not in the subscription; separate metered billing) is CONFIRMED. Model name stale (`-2.1`). | **The sole stated justification for the local voice stack has collapsed.** Re-litigate on true numbers. Also kill the "realtime = GPT-5.6" premise — it's a distilled family with a Sept-2024 cutoff. |
| 5 | Google/Gemini excluded — ToS bans third-party OAuth (names OpenClaw), consumer CLI dead June 18 2026 (§4.5) | **CONFIRMED, both halves.** ToS names OpenClaw explicitly; mass bans were *enforced* Feb 2026; consumer access ended June 18. | No change. §4.5 is correct. |
| 6 | Grok Build: xAI sanctions headless + third-party harness on SuperGrok, 8 parallel subagents in worktrees (§4.2) | **PARTLY TRUE, with a tier trap.** Headless `-p` and worktree-isolated subagents **confirmed**. But "8" is reported, not guaranteed; and the "sanction" is *inferred* from xAI shipping OAuth, not stated in ToS. **Live bug report: `xai-oauth` returns HTTP 403 for standard SuperGrok ($30/mo) — backend appears gated to SuperGrok Heavy ($300/mo)**, despite the launch announcement saying all SuperGrok. | **Cheap test, expensive assumption.** Empirically verify the OAuth path on the actual account tier before this seat goes in the plan. |
| 7 | Voice STT: faster-whisper or whisper.cpp (§5.3) | **WRONG for this hardware.** faster-whisper has **no Metal support** on Apple Silicon — it silently falls back to CPU. | Use **MLX Whisper**. It's 30–40% faster than whisper.cpp *and* ships in Pipecat as `WhisperSTTServiceMLX`. Faster and less work — not a tradeoff. |

---

## 1b. THE BIG ONE — the orchestrator seat is wrong (raised by Neill, 2026-07-11)

Neill: *"Codex is slow… it may not be a great orchestrator. Surely we can do OpenClaw with a solid
subscription model at the head that can do two-way voice and isn't slow af."*

**He's right, and the numbers are worse than anyone guessed.**

### The measured reality

| Model | Time to first token |
|---|---|
| **GPT-5.6-sol (high effort)** — *the spec's orchestrator* | **13.0s** |
| GPT-5.6-sol (max effort) | ~150s |
| Claude Fable 5 (max effort) | ~109s |
| Claude Sonnet 4.6 (non-reasoning) | 1.44s |
| Claude Haiku 4.5 | ~1.04s |
| Grok 4.1 Fast (non-reasoning) | ~2s reported — **UNVERIFIED, load-bearing, benchmark it** |
| Gemini 2.5 Flash-Lite | 0.35s (excluded by §4.5 — listed for calibration only) |

**13 seconds to first token, against a 1.5s budget.** That is not a tuning problem; it's ~10× over.

**The dominant variable is reasoning effort, not model family.** Every frontier model collapses to
unusable TTFT at high effort, and every one of them lands ~1–1.5s at low/non-reasoning. So the
question was never "which vendor" — it's "how hard is the thing you're talking to trying to think."

### The design flaw

§5.1 has the voice layer tool-calling "the GPT-5.6 orchestrator which holds the harness memory,
subagents, and reasoning." **That routes every voice turn through a deep reasoning model** — even
"what did the overnight run do?" The spec then papers over it with a canned ~300ms "on it, kicking
that off" while the slow model grinds. That's a bandaid on a design problem.

### The fix — split two roles the spec fused into one seat

This pattern has a name: **Talker–Reasoner** (Google DeepMind, arXiv 2410.08328), framed on
Kahneman's System 1 / System 2. The voice community converged on the same shape independently
("Fast Talker / Slow Thinker").

- **Talker / the head** — *who you talk to.* Fast, non-reasoning, excellent tool-calling. **Not
  smart.** It is ears, mouth, and dispatcher. Sub-second TTFT or the voice loop feels dead.
- **Reasoner / the seats** — *who does the work.* Allowed to be slow, because **you are never
  waiting on it.** Deep planning and long builds are async by nature.

**What makes this clean rather than clever:** OpenClaw's `sessions_spawn` is **always non-blocking**
— returns `runId` + `childSessionKey` immediately, with a per-spawn `model` override. The head fires
"spawn a Codex build, spawn three reviewers," gets run ids back in milliseconds, and turns straight
around and talks to you. It **never blocks on the slow seat.** Status comes from the SQLite job
records when asked. The router-dispatches-to-subagents shape is already a documented OpenClaw idiom
(reported 80–90% cost reduction vs. running the big model for everything).

**So Codex being slow is fine. Codex being the thing you *talk to* is not.** Demote it from "the brain
you converse with" to "a seat you dispatch to" and its latency stops mattering entirely.

### ⚠️ The landmine — this kills the Claude Max head, and possibly the whole subscription premise

**CLI-based runtimes cannot be in a per-turn voice loop:**

- `claude -p` subprocess: **3–5s overhead per call.** Cited discussion says this makes "use cases
  like voice assistants **prohibitively slow**."
- Claude Agent SDK `query()`: **~12s overhead per call**, no hot-process reuse
  ([claude-agent-sdk-typescript#34](https://github.com/anthropics/claude-agent-sdk-typescript/issues/34)).
- With process pre-warming/reuse: drops to <1s. Without it, you eat the full cost **every turn**.
- Direct Messages API: 1–3s end-to-end.

This collides head-on with the constraint from §1: **`agentRuntime.id: "claude-cli"` is the only
*legal* way to use the Claude Max subscription (Anthropic bans subscription OAuth in third-party
tools) — and it is exactly the path that costs 3–5s per turn.**

> **You cannot have both "Claude Max subscription" and "snappy voice head." They are mutually
> exclusive.** Claude-via-CLI is fine as a *dispatch target* (4s startup on a 90-second background
> job is noise). It cannot be the ears and mouth.

**And the broader implication Neill has not priced in:** ChatGPT/Codex and SuperGrok are *chat/CLI
subscriptions, not API credit.* The head needs a **direct streaming API path** to hit 1.5s — which
likely means **the head seat requires pay-as-you-go API billing regardless of which subscription he
holds.** The saving grace: the head is a non-reasoning dispatcher on short turns — the *cheapest
possible seat*. Likely **dollars a month, not a cost problem.** But it must be confirmed before the
architecture is committed, because "everything runs on flat-rate subscriptions" is currently an
unstated premise of the whole spec, and it does not survive contact with the latency budget.

The economics still work, and arguably work *better*: **the expensive thinking runs on flat-rate
subscription seats; only the tiny dispatcher head touches metered API.**

### Recommended seating

- **Head (Talker):** in preference order — (1) **Grok 4.1 Fast Non-Reasoning** (purpose-built for
  agentic tool-calling, no CoT overhead, subscription already held — *but its TTFT is unverified and
  load-bearing*); (2) **Claude Haiku 4.5** via direct API (~1.04s, best TTFT consistency — needs API
  billing, not the Max sub); (3) **GPT-5.6 at `none`/`low` effort** (OpenAI's own guidance names
  `low` for latency-sensitive work — but same family as the 13s number, so verify).
- **Explicitly NOT the head:** GPT-5.6-sol at high/max, Claude Fable, **anything behind `claude-cli`.**
- **Dispatch targets (Reasoners):** GPT-5.6-sol high/max via Codex; Claude Opus/Fable via
  `claude-cli`; Grok Build; Ollama. All spawned non-blocking via `sessions_spawn` with explicit
  per-spawn `model`. Their multi-second startup is irrelevant in the background.

### 🔬 Spike C (new, and it gates the architecture)

**A 20-turn TTFT bake-off of the three head candidates**, on a representative short conversational
turn *that includes a tool call in the response*, measured **from the OpenClaw agent boundary** (not
the raw provider endpoint) so harness overhead is included.

The published numbers are directionally right, but **the Grok figure is the load-bearing unknown,
and the head-model choice is the one decision here that is expensive to reverse.** Run this before
writing the seating into the spec.

---

## 1c. OpenAI Realtime as the voice head — the cost argument has collapsed (2026-07-11)

Neill: *"OpenAI released a new live voice thing… it's expensive af, ~$20-30/hr… but they have a lite
that's cheaper. This would take out the voice stack and bring latency down."*

**Verdict: the architecture is right and *sanctioned*, the cost fear is unfounded in both
directions — but there's a hard external blocker. Decide by spike, not by argument.**

### The cost math, corrected

**Everyone's numbers were wrong, including the spec's.**

| Estimate | Verdict |
|---|---|
| Neill's ~$20–30/hr | **Wrong by ~15–20×.** Even worst-case uncached *continuous call* density is ~$7.50/hr. |
| Spec §5.3's $700–1000/mo (and "$150–250 even for mini") | **Wrong by ~10×.** |
| The $0.04–0.10/min benchmark | *Correct, but for the wrong workload* — that's a call-center rate (two people talking nonstop). |

**The mechanic everyone missed: you are billed per audio token, not per minute of open session.**
OpenAI does not charge for connection time. With push-to-talk, audio streams *only while the button
is held*. **Silence, thinking time, and reading the text log cost $0.** The spec's error was billing
every wall-clock minute of a 2-hour session as a fully-saturated conversational minute. A real
builder session is maybe **15–25% audio-active**.

**Modeled at Neill's actual pattern** (2 hrs/day, PTT, short spoken summaries, cached prompt prefix):

| | gpt-realtime-2.1 | **gpt-realtime-2.1-mini** |
|---|---|---|
| Per hour | ~$1.27 | **~$0.42** |
| **Per month (60 hrs)** | **~$76** | **~$25** |
| Chattier sensitivity case | ~$167/mo | ~$59/mo |

**~$25/mo is not a real cost.** The local-stack decision was made on math that was off by an order of
magnitude, and **deserves to be re-litigated on true numbers** — whatever we end up choosing.

### ❌ Kill the "realtime = GPT-5.6" premise

It isn't. `gpt-realtime-2.1` and `-mini` are a **separate distilled family** with a **Sept 30, 2024
knowledge cutoff** and 128k context. GPT-5.6-sol has a **Feb 2026 cutoff** and 1.05M context. They
are 17 months and an order of magnitude of context apart. Mini is explicitly *"a distilled reasoning
model for faster, lower-cost realtime voice."* The configurable reasoning-effort knob is probably
what created the "it's 5.6" impression.

**Owner-level correction:** §5.1's emotional core — *"you are talking to your actual orchestrator"* —
is **false under BOTH architectures.** Either way, the head is a small fast model that dispatches to
the real brain. That's fine (see §1b: it's the *right* design), but the spec should stop promising
otherwise.

### ✅ OpenClaw already has the exact pattern — natively

The **voice-call plugin** bundles OpenAI as a realtime provider and exposes an
**`openclaw_agent_consult`** tool by default: the realtime model calls **back into the full OpenClaw
agent** for deeper reasoning and normal OpenClaw tools. **OpenClaw keeps ownership of tool execution
and harness context; the realtime model is purely the conversational shell.**

That is Talker–Reasoner, natively, as a sanctioned design. The hypothesis isn't just sound — it's
the documented pattern. It also directly answers Neill's *"I need deep reasoning when coming up with
ideas"*: `openclaw_agent_consult` **is** the escalation path from the fast mouth to the slow brain.

### 🚨 The blocker — and it's someone else's open bug

**[OpenClaw #80196](https://github.com/openclaw/openclaw/issues/80196), open since May 10 2026,
unresolved:** OpenClaw's realtime voice provider is **hardcoded to OpenAI's beta protocol.** Pointed
at a GA model it fails with `Missing required parameter: 'session.type'`. Two gates: the
`OpenAI-Beta: realtime=v1` header, and a missing GA session schema in the transport layer.

**So `gpt-realtime-2.1` / `-mini` are NOT usable through OpenClaw today without patching.**
`gpt-realtime-1.5` (beta) works. Workarounds: brittle manual patches, stay on the beta model, or
**build a standalone Python bridge outside OpenClaw** — which is *exactly the hand-wired-audio-
plumbing trap that killed v1.* (Related open issues: #90456, #5606, #13245, #71195.)

### Other things that bite

- **Hard 60-minute session cap.** A 2-hour builder session needs ≥2 reconnects with context
  rehydration — and a fresh session means a **cold cache** (cost spike + a memory seam). Needs
  design, not hand-waving.
- **Prompt caching is load-bearing, not an optimization.** Without it the context-replay term goes
  from $0.168/hr → **$13.44/hr**. That is the entire difference between "cheap" and "unusable."
  Requires a stable prefix — never interpolate timestamps into the system prompt.
- **Adopting realtime does NOT remove Pipecat** (if we go the Pipecat route). It removes **Whisper
  and Kokoro**. Pipecat supports realtime as a first-class service (`OpenAIRealtimeLLMService`).
- **Tool calling is good, not great.** Slightly below text-model accuracy; harder to debug (the text
  intermediate is implicit); a tool round-trip adds **400–800ms**. The real failure mode: the model
  fires a tool call then goes **silent**, the user assumes it dropped and interrupts, corrupting
  state. Mitigation is *spoken preambles* — **which §5.1 already invented independently** ("on it,
  kicking that off").
- ⚠️ **Cautionary:** OpenClaw's voice-call plugin currently uses realtime for **transcription only**,
  and clocks **5–10s latency** because generation still routes through the embedded agent.
  STT-only realtime does **not** solve the head-latency problem.
- Realtime needs a **Platform API key** — metered, separate from the ChatGPT/Codex subscription.
  (Spec already got this right.)

### The honest case for staying local

$0 forever · no 60-min cap · no vendor drift (**§9's named #1 risk — and a metered Platform API is
exactly the surface OpenAI is most likely to reprice**) · offline · no audio leaves the machine.

**The honest case against:** hand-wiring streaming audio **is what killed v1**, and the paid path now
costs ~$25/mo rather than the $150–250 the spec assumed.

### 🔬 Spike D (new) — settle this empirically, ~1 day

Two questions, both cheap, and together they decide the whole voice architecture:
1. **Does OpenClaw's realtime provider work against `gpt-realtime-2.1-mini`, or does #80196 bite?**
2. **Is prior assistant audio retained in the replayed context as *audio* (expensive) or as *text
   transcript* (~16× cheaper)?** Docs don't say. Run a 30-min session and read the dashboard.
   (latent.space's advice to "replace audio messages with text messages" implies audio *is* retained
   by default — if so, my cost model above is optimistic.)

**Do not rewrite the spec until these are answered.** The cost argument that drove the original
local-stack decision has collapsed; the remaining blocker is technical and external.

---

## 1d. 🔴 OWNER OVERRIDE — push-to-talk is dead (Neill, 2026-07-11)

> *"I kinda want you to be one as didn't work. It was fucked. I don't want push to talk. I wanna be
> able to just talk back and forth, two way voice. Fuck push to talk."*
>
> *"I just wanna be driving in my car and being able to talk to my builders."*

**This overrides the spec. §5.2 ("Push-to-talk on all clients… No wake word, no open mic in v1") and
§11 (wake word = out of scope) are REVOKED by the owner.**

New hard requirement, at the level of §Prime-directive:

- **Hands-free, continuous, two-way voice.** No button. Barge-in expected.
- **Must work while driving** — phone in a car, hands on the wheel.
- This is a *product* requirement, not a phase-3 nicety. **v1 died on a bad voice experience; a
  version that requires holding a button is, in the owner's judgment, the same failure.**

### What this changes, and what it doesn't

**Doesn't change:** open-mic/VAD turn detection is the OpenAI Realtime API's **default**;
`turn_detection: null` (push-to-talk) is the **opt-out**. Barge-in/interruption is native. So
hands-free is *strictly less work* than PTT on the realtime path. **The voice backend is not the
constraint.**

**Does change — and this is the real cost:**
1. **The PTT cost model in §1c is now invalid.** PTT's "you don't pay for silence" saving is gone if
   the mic streams continuously. **Mitigation: local VAD gating on the device** — only open the
   stream when speech is actually detected. Needs sizing (see Spike E).
2. **§9's "iOS PWA audio quirks" risk is now the critical path, not a phase-3 footnote.** A PWA
   very likely **cannot** hold a background mic or run wake-word detection with the screen locked.
   **This may force a native iOS app** — a scope change the spec does not contemplate anywhere.
3. **CarPlay entitlements are gated by Apple to specific app categories** and may be a hard blocker
   for a custom personal app. Needs verification.
4. **Wake word may not even be needed** for the car case: phone mounted/on-screen, app in
   foreground, conversation simply stays open for the drive — like a phone call. Wake word is only
   required for *locked-phone-in-pocket* invocation. **Scope the car case first; it's much cheaper.**

### ✅ RESOLVED — hands-free is achievable, and cheaper than PTT

**Answer to the owner's literal question — *"can I only get that if I build my own voice stack?"* —
NO. Building our own stack gets us NOTHING here. The Realtime API does open-mic hands-free out of
the box, better and with less work.**

**The hands-free constraint is 100% an iOS/client problem, entirely orthogonal to the voice
backend.** Realtime vs. local-stack changes latency, cost, and privacy — it changes **nothing**
about whether the mic can be open while driving. Pick the backend on other merits; solve hands-free
separately.

#### Open mic is the DEFAULT (confirmed from docs)

- `turn_detection` **defaults to `server_vad`**. **`turn_detection: null` IS push-to-talk** — the
  opt-out. **Reverting §5.2 is a deletion, not a build.**
- **Use `semantic_vad` with `eagerness: "low"`** — a classifier that judges whether the speaker
  actually *sounded finished*. This is the right call for a car: **a driver pauses mid-sentence to
  merge, and plain `server_vad` will cut him off.** Semantic VAD is specifically designed not to.
- **Barge-in is native** — `interrupt_response: true` + `create_response: true`. User speech during
  assistant playback truncates the assistant's turn.

#### Open-mic cost: a non-issue

Audio is duration-encoded: user audio = **600 tok/min**. Worst case, if silence were billed raw:
**$0.36/hr (mini)** / $1.15/hr (full). A 45-min drive = **$0.27 on mini.** Rounding error next to the
conversation itself.

Community consensus (medium confidence, forum-sourced, *not* a documented billing guarantee) is that
**uncommitted silence is not billed at all** under server VAD, since non-speech is discarded and
never committed as an input item. **Don't architect around this — measure it** (15-min experiment:
stream silence, read `response.usage`). Local-VAD gating is available if needed (Pipecat ships
`SileroVADAnalyzer`; set `turn_detection=False` and audio never leaves the device during silence) —
but at $0.36/hr worst case, **don't build it preemptively.** The real cost driver in long sessions is
context replay, not silence.

#### 🚨 iOS: one hard NO, one hard blocker, and a sleeper

| Path | Verdict |
|---|---|
| **PWA, background / locked screen, wake word** | 🔴 **IMPOSSIBLE.** Not hard — *impossible.* iOS suspends PWAs shortly after backgrounding; service workers get only narrow event-driven windows and **cannot** do continuous audio ([WebKit #211018](https://bugs.webkit.org/show_bug.cgi?id=211018)). State this to the owner without hedging. |
| **PWA, foreground, phone mounted, open mic** | ✅ **WORKS TODAY. This is the 90% answer.** Tap once on entering the car, then talk freely the whole drive, with barge-in. Add **Wake Lock API** to keep the screen awake. ~**a day of work, mostly deleting PTT logic.** Gotcha: use a single-page shell with **no hash routing** — iOS standalone-mode `getUserMedia` re-prompts on navigation ([WebKit #185448](https://bugs.webkit.org/show_bug.cgi?id=185448), [#215884](https://bugs.webkit.org/show_bug.cgi?id=215884)). |
| **Siri App Intent as launcher → hands off to our session** | ⭐ **THE SLEEPER. Best effort-to-payoff ratio.** *"Hey Siri, ask Chief…"* works **hands-free, screen locked, phone in pocket**, needs **no special entitlement and no wake-word engine** — Siri gives us the system-level wake word for free, then our app takes over the mic and runs the full open-mic Realtime session. Requires a thin native shell. Ceiling (inferred): Siri is a *launcher*, not a host — you don't run a Realtime session inside Siri's voice loop. |
| **Native app + Porcupine + `UIBackgroundModes: audio`** | ✅ Achievable. True custom *"hey Chief"*, screen off, in-pocket. **Sideloading removes the App Store constraint entirely** — `UIBackgroundModes: audio` is a *normal capability*, not a restricted entitlement. No review, no rejection risk. Costs: a real native app, a wake-word integration, and a **persistent red mic dot in the status bar** (cannot be suppressed). |
| **CarPlay** | 🔴 **HARD BLOCKER. CUT IT.** iOS 26.4 *did* add exactly the right category (voice-based conversational apps, built for ChatGPT/Gemini-class assistants) — but **CarPlay entitlements are Apple-granted per-category, case-by-case, and cannot be self-signed.** No personal provisioning profile can carry one. Apple granting a one-man internal tool a conversational-AI CarPlay entitlement is ~zero. Also: Apple is explicitly *not* handing the wake-word slot to third parties — Siri stays the system assistant in CarPlay. |

#### 🔧 Owner data point: the Archie app (real prior experience, and it corrects the above)

Neill: *"We got a native iOS app for Archie, the app still has to be open. And we haven't gotten it
to work yet."*

**My hypothesis was that a WebView-wrapped app can never get background mic. That is WRONG — and the
truth is better news.**

- **[WebKit #233419](https://bugs.webkit.org/show_bug.cgi?id=233419) is RESOLVED CONFIGURATION
  CHANGED (Oct 2024).** A WKWebView's `getUserMedia` **does** survive backgrounding — **if the host
  app's Info.plist declares `UIBackgroundModes: audio`.** Reporter confirmation on iOS 17.5.1:
  *"Without adding 'audio' to the UIBackgroundModes it didn't work, but with that setting the
  microphone works in the WKWebView while the app is in the background."*
- **Archie's symptom is the textbook signature of that flag being missing from the BUILT app.**
  Capacitor/Cordova regenerate Info.plist and routinely drop it. **10-minute test: unzip the built
  `.app`, check its Info.plist for `UIBackgroundModes = [audio]` — not the source plist, the built
  one.** This may fix Archie outright.
- **But do NOT build Chief on WebView background mic.** It works, but it's WebKit behavior, not an
  Apple API contract — undocumented, and it regressed across iOS 14/15/16 (three separate bugs). You
  get **no control over the AVAudioSession category, no interruption handling, no route-change
  handling, and no ability to reactivate after a phone call.** Every failure mode below becomes
  unfixable by you.

#### 🔴 The hard truth: always-on wake-word-in-pocket is NOT a solved problem

**Do not promise this. The evidence says iOS reclaims it for a meaningful fraction of users — even
in fully native apps.**

- Apple's contract is conditional: the app keeps running in background *"as long as it is recording
  audio content."* An idle session gets suspended.
- **[Apple Forums #776055](https://developer.apple.com/forums/thread/776055):** a *shipping* native
  background-recording app reports a subset of users **routinely get recordings killed** by the
  watchdog. They stripped the entire view hierarchy on background to get CPU to 0% in the profiler,
  saw "massive improvements," and **still see background terminations.** Apple publishes **no CPU or
  memory thresholds** for what survives.
- **Picovoice's own FAQ hedges:** background wake-word "is controlled by the operating system, and
  Picovoice **cannot guarantee** that this will be possible in future releases of iOS."
- Every phone call, Siri invocation, or app grabbing the mic is an interruption you must catch and
  recover from — and recovery is not always granted. User swipes the app away → dead, nothing
  relaunches it. Plus a permanent orange mic indicator and real battery drain.
- **Shazam's auto-detect is NOT a third-party pattern** — it's an OS feature. Can't be replicated.
- Otter/voice-recorders are **background *recording*** (user explicitly started it, knows it's
  running) — **not always-on ambient listening.** Different thing.

**Neill has already run this experiment once and lost. Don't send him back in.**

#### ⚠️ Siri's real ceiling (corrects the "sleeper" call above)

Siri's wake word runs on the OS's always-on low-power processor — **your app needs zero background
mic and isn't running at all until invoked.** That part is confirmed and it's the whole appeal. But:

- **Background intent** (`openAppWhenRun = false`) + `authenticationPolicy = .alwaysAllowed` → **works
  fully hands-free, phone locked, in pocket.** Siri speaks the result. ✅
- **Foreground intent** (`openAppWhenRun = true`) on a **locked** device → **prompts the user to
  unlock, and the shortcut is interrupted.** Siri does not dismiss the lock screen. ❌
- **So "Hey Siri, ask Chief" cannot launch straight into a live open-mic conversation on a locked
  phone.** It can *answer a question* hands-free while locked. To enter a conversation, you unlock
  (a Face ID glance — a gate, but a small one).
- Uncertainty flagged: some devs report `.alwaysAllowed` still prompting in certain configs. **Device
  test, not a spec guarantee.**

#### Wake-word engines (only needed for the pocket case)

**Porcupine** (free tier covers personal use; ~$6K/yr only bites on commercial distribution) — native
iOS + macOS SDKs, type-to-train custom words, **97.1% detection at 10 dB SNR on a noise dataset that
includes car/traffic noise.** **openWakeWord** (Apache-2.0, free) has no first-party iOS SDK — you own
the ONNX/TFLite port.

⚠️ **Word choice matters more than engine choice.** *"Hey Chief"* is a **weak** wake word — short,
common phoneme, easily false-triggered by the word "chief" in speech and by radio/podcast audio.
Use a longer, rarer phrase: **"hey chief command"** or **"okay chief."**

### Recommended path (revised after the Archie data point)

1. **Foreground PWA, mounted phone, open mic (`semantic_vad`, eagerness low, barge-in on).** The 90%
   answer, ~a day, no native code, **no background audio needed at all** — which is precisely why it
   sidesteps the wall Archie hit. **Do this now.**
2. **Siri background App Intent** (`.alwaysAllowed`) → *"Hey Siri, ask Chief what the overnight run
   did"* answers hands-free, locked, in-pocket, with **zero background mic.** Doesn't open a
   conversation — answers a question. **Spec as the follow-on; it covers most pocket use.**
3. **Native capture layer** (`AVAudioSession(.playAndRecord)` + `AVAudioEngine` tap + interruption/
   route-change handling) **if and only if** we need screen-off long sessions. Model on
   `@capgo/capacitor-audio-recorder` (MIT). Note: **no maintained Capacitor plugin does background
   wake-word** — that's custom native (Porcupine SDK + own bridge).
4. **Always-on custom "hey Chief" from a sleeping pocket — DO NOT PROMISE.** Achievable-ish, not
   reliable. Revisit only if #1 and #2 genuinely don't cover the need.
5. **CarPlay — cut from the roadmap.** Entitlement is Apple-granted and unsignable.

**Cheap immediate win for the owner, unrelated to Chief:** check Archie's **built** `.app` Info.plist
for `UIBackgroundModes: audio`. If missing, that one line may be the whole bug.

---

## 2. What already exists (don't rebuild it)

### OpenClaw core covers ~80% of the harness

- **The four seats** = OpenClaw's `agents.list[]`. Each entry gets its own `model` (+ `fallbacks`),
  `workspace`, `tools` profile, `identity`. All four providers natively supported:
  - **Codex** — supported, but a distinct runtime path (`codex` plugin + native app-server harness). Fiddliest of the four; budget real time.
  - **Grok/xAI** — supported; SuperGrok OAuth is the documented recommended path (subject to the tier trap above).
  - **Ollama** — supported, auto-detected at `127.0.0.1:11434`.
  - **Claude headless** — supported via `agentRuntime.id: "claude-cli"` (reuses `claude -p` under your own session auth). This is the escalation seat.
- **The dispatcher** = native subagents (`sessions_spawn`). Non-blocking, returns a run id,
  and **each spawn takes an explicit `model` param** — so one orchestrator turn fans out across
  Grok + Ollama + Claude simultaneously. `maxConcurrent` default 8; `maxSpawnDepth` 2 is the
  documented orchestrator pattern. **Do not build a custom dispatcher.**
- **The reviewer gauntlet** = the stock `autoreview` skill. Runs multiple reviewers against one
  **frozen bundle** (identical input — this matters), with per-reviewer model and reasoning-effort
  overrides: `autoreview --reviewers codex,claude --model codex=gpt-5.6-sol --thinking codex=high`.
  That is most of §6, for free.
- **Tailnet + launchd** = Gateway binds loopback:18789, **auto-configures Tailscale Serve**,
  serves the Control UI on the same port; `openclaw gateway install` writes the launchd service.
  §2's networking and daemon requirements are shipped features.

**Version pinning is not optional.** Three breaking releases in under 90 days in Q1 2026
(2026.3.2, 2026.3.8, 2026.3.13). Exact pins are honored and not overwritten by the updater.
This is precisely the vendor-drift risk §9 names — pin an exact version, upgrade deliberately.

### The voice server half is already built

**`kwindla/macos-local-voice-agents`** — reference implementation by Pipecat's originator
(Kwindla Kramer, Daily CEO). Stack: Silero VAD · smart-turn v2 · **MLX Whisper · Kokoro TTS** ·
WebRTC · React client. Claims **<800ms voice-to-voice on M-series** *with a local LLM in the loop*.
Our budget is 1.5s to first audio with the orchestrator doing the thinking — real headroom.
⚠️ Only 17 commits and **LICENSE unverified — check before forking.** Treat as demo-grade code
to learn from, not a maintained dependency.

**Pipecat** (BSD-2, v1.0 April 2026) is the framework. Critically, **push-to-talk is an in-tree
abstraction** — `PushToTalkUserTurnStrategies` (see `pipecat-examples/push-to-talk`): client sends
`{type:"push_to_talk", state:"start"/"stop"}`, server-side aggregator collects transcription only
during the hold and discards anything outside the window. **We do not hand-wire turn-taking.**
That is the exact failure mode that killed v1 (§5.3: *"Chief Command v1 died hand-wiring this"*).

### Gauntlet: someone already proved the riskiest assumption

**AdamsReview** (MIT, 240★, active) dispatches **up to 7 parallel sub-agent lenses** and — the key
finding — **runs on plain Claude Code subscription session auth, no API keys**. Its `/codex-review`
shells out to **Codex CLI as a genuine cross-family peer**, and `/review --ensemble` merges Codex
findings with the Claude lenses. That is §6's "at least two model families, on subscription seats"
rule *working in public today*. It was the part of the spec I'd have bet against.

Its `/walkthrough` (interactive human review of uncertain findings) is the model for the human
escalation queue; `/promote` escalates a finding past a gate.

**Also worth cribbing:**
- **open-code-review** (Apache-2.0) — 8-phase Tech Lead orchestration with a **Discourse phase**
  (reviewers challenge each other *before* synthesis) — the disagreement-surfacing mechanism §6
  wants before escalation. Per-reviewer model assignment is first-class. Persists to SQLite.
- **agent-review-panel** — **blind independent scoring before debate** (anti-groupthink), plus a
  post-judge gate that re-verifies any P0/P1 the judge itself introduced (catches judge
  hallucination). Note: it hard-pins every subagent to one model and manufactures diversity via
  personas — the *opposite* of our premise. Take the protocol, discard the model policy. Blind
  scoring works *better* for us, since our reviewers are genuinely different models.
- **ai-code-reviewer** (calimero) — verdict aggregation formula: cluster findings, weight by **how
  many agents independently agree**, rank by severity × agreement, stop when findings converge.

### Job queue: ORCH is the closest whole system

**ORCH** (MIT, 560 commits, v1.0.27 July 2026, 1,954 passing tests):
- State machine **`todo → in_progress → review → done`** — the `review` state is a first-class gate,
  i.e. a ready-made gauntlet hook.
- **8 CLI adapters: Claude Code, Codex, Cursor, Pi, OpenCode, Grok, Antigravity, and Shell.**
  The generic Shell adapter covers Ollama and anything else. **This adapter layer is the single
  most reusable artifact in the entire survey.**
- **Every agent gets an isolated git worktree on its own branch; `main` untouched until approved.**
  That's §7's git-as-audit-trail, already built.
- Exponential-backoff retry + **zombie-task detection with re-queue** (matters a lot for unattended
  overnight runs).
- ❌ **The mismatch:** ORCH's design stance is *"zero infrastructure — no database."* State is
  YAML/JSON/JSONL in `.orchestry/`. That directly contradicts §7's queryable build history.
  Fork the adapters + state machine; replace the persistence.

---

## 3. What we actually build (the real scope)

The survey's central finding: **the spec sits in a genuine gap.** Every project with a good
reviewer panel is either single-model-family or API-key-billed. Every project with a good job queue
either has no database (ORCH) or doesn't dispatch (clu). **Nothing does gauntlet + queryable
history + subscription-seat auth together.** That gap is the thing worth building — and it's much
narrower than the spec assumes.

1. **The SQLite job/history schema.** Nobody hands you this. It's §7's queryable build history
   ("what did the overnight run do?"). ~A day of work, not a dependency. Read **clu**'s schema
   first — specifically its **atomic task claiming via `UPDATE … RETURNING` with a subquery**, which
   solves parallel-reviewer race conditions for free — and open-code-review's tables second.
   Note OpenClaw's subagent sessions **auto-archive after 60 min and get soft-deleted** — that's
   session archival, *not* durable job records. This layer is genuinely missing.

2. **Enforce the quality/budget rules in the database, not in prompts.** §5.3's *"local output never
   ships without a subscription-tier review"* must be a **transition guard + schema constraint**: a
   job whose builder was the Ollama seat is *structurally unable* to reach `done` without a
   subscription-tier verdict row. §9 already says to enforce this "in the pipeline itself, not by
   convention" — this is what that means concretely. **Rules that live in prompts quietly stop
   holding at 4am.** Same for per-seat spend caps: OpenClaw has **no per-agent cost/token budget
   anywhere in core**, so this is ours regardless.

3. **The iPhone PWA voice client.** Nobody has shipped an open-source streaming-Whisper + Kokoro +
   iOS-PWA push-to-talk assistant. The server half is solved prior art; **this half we write.**

---

## 4. Spikes to run before writing client code

**Spike C (§1b — the head-model TTFT bake-off) is the highest priority of the three**, because it
gates the architecture rather than the client. A and B below gate the client. All are cheap; all
have clean fallbacks. §9 guessed iOS audio would be the pain — it turns out to be two specific,
testable questions rather than a swamp.

- **Spike A — WebRTC over Tailscale, iPhone Safari → Mac.** No STUN/TURN on a tailnet; `aiortc`
  should negotiate host candidates directly over the Tailscale interface, but **no prior art
  confirms Pipecat SmallWebRTC over Tailscale specifically**, and this is the likeliest thing to
  break. **Fallback: Pipecat's WebSocket transport** — over a tailnet, WebSocket audio is entirely
  viable and sidesteps all of ICE. Honestly, consider *starting* there.
- **Spike B — `getUserMedia` in *installed standalone PWA* mode on the actual iPhone.** Long WebKit
  bug history (#185448, #252465), status in 2026 is "mostly fixed but verify."
  **Fallback: ship as a Safari tab** rather than an installed PWA.

Other confirmed iOS landmines: never call `getUserMedia` twice (the second call mutes the first
stream's tracks and you **cannot** programmatically unmute them — call once, keep a global stream,
use `MediaStream.clone()`); `playsinline` is mandatory; device IDs are randomized per page load, so
don't persist them. Audio playback needs a user gesture — but push-to-talk *is* one, so unlock the
`AudioContext` on first button press.

---

## 5. The strategic risk to put in front of Neill

**The spec's reliance on Codex third-party OAuth is the biggest latent danger in the document.**

It works today. But OpenAI has not sanctioned it, its ToS forbids credential sharing, and **both
other major vendors have closed this exact door in the last five months**:

- **Anthropic** — terms updated Feb 20 2026 prohibiting subscription OAuth in third-party tools;
  billing enforcement live Apr 4 2026.
- **Google** — ToS names OpenClaw explicitly; **mass account bans** from mid-Feb 2026 (some spilling
  into Gmail/Workspace access), then a system-wide unban Mar 2; consumer CLI killed June 18.

OpenAI is the last vendor that hasn't clamped down. **That is a timing position, not a policy
position.** §9 already calls vendor policy drift the #1 risk — this is that risk, with a name.

**Mitigation:** design so the Codex auth path is **swappable to a paid API key without
re-architecture**. OpenClaw's provider abstraction gives us this nearly for free. Cheap insurance
against being the next OpenClaw headline.

---

## 6. Ranked recommendations

**Adopt as-is**
- OpenClaw core, **pinned to an exact version** — seats as `agents.list[]`, `codex` plugin for the
  orchestrator, `agentRuntime.id: "claude-cli"` for the Claude seat.
- Native subagents for dispatch (`maxSpawnDepth: 2`, per-spawn `model` override).
- `autoreview` skill as the gauntlet spine.
- `openclaw gateway install` (launchd) + Tailscale Serve auto-config.
- **Pipecat** (BSD-2) as the voice framework; `kokoro-onnx` via `pipecat-ai[kokoro]` for TTS;
  `WhisperSTTServiceMLX` for STT.

**Fork / vendor**
- **`kwindla/macos-local-voice-agents`** as the voice-server skeleton (verify LICENSE first).
  Three surgical changes: rip out VAD/smart-turn → `PushToTalkUserTurnStrategies`; replace the local
  LLM with a `FrameProcessor` that forwards to the orchestrator and streams the reply back
  sentence-by-sentence into Kokoro; replace the React desktop client with the PWA.
- **ORCH's adapter layer + state machine** (not ORCH itself) — back it with our SQLite, drop
  `.orchestry/`.
- **Lobster + PR #20** if we want deterministic bounded review loops — **loops are not in mainline**;
  the fix is an unmerged PR. Vendor at a pinned commit with #20 cherry-picked rather than waiting
  on upstream.

**Crib, don't depend**
- AdamsReview (panel shape + subscription-CLI auth pattern + `/walkthrough` human queue).
- agent-review-panel (blind-score-before-debate; post-judge verification gate).
- open-code-review (Discourse phase; SQLite schema).
- calimero (severity × agreement-count aggregation).
- clu (SQLite atomic claiming, checkpoints).
- `claude-to-codex` / `ccode-to-codex` — **take the mapping spec, hand-write the ~200 lines.** Both
  are experimental (`claude-to-codex` self-describes as *"primarily vibe-coded"*, 6★) and **silently
  drop hooks, `maxTurns`, and memory settings.** Our agent definitions are the actual product —
  don't run an unvetted converter over them. Note the non-obvious effort remap
  (`high`→`xhigh`, `medium`→`high`).

**Ignore**
- **vibe-kanban** — 27.3k★ and the obvious choice a year ago, but **officially sunsetting.** If any
  part of the plan was implicitly anchored to it, retire that assumption now.
- LiteLLM as infrastructure — it's an HTTP gateway; our seats are *spawned CLI subprocesses under
  session auth*. Structural mismatch, plus its Anthropic OAuth pass-through is buggy (open issues
  #29190, #13380) and ToS-gray. Optional adapter for the API-key reviewers only; never the spine.
- LiveKit Agents (room model / SFU is overkill for single-user), Vocode, Claworc.

**AGENTS.md note (§6):** Codex reads it natively; **Claude Code's support is *not* fully established**
(open feature request anthropics/claude-code#6235). Robust pattern that works today: make
**AGENTS.md the single source of truth and CLAUDE.md a one-line `@AGENTS.md` import** — don't rely
on fallback behavior.

---

## 7. Suggested build order (unchanged in shape, cheaper than planned)

Phase 1 (text harness) is now mostly *configuration* rather than construction: pinned OpenClaw +
four seats in `agents.list[]` + Tailscale Serve + launchd, then the two things core doesn't give us
(SQLite job records, the never-ship-unreviewed transition guard). The Phase-1 acceptance test from
the spec — *"from the phone, type 'have the local model write X, then have Codex review it' and it
happens, with the job recorded"* — is reachable much faster than the spec assumes.

Run **Spike A and Spike B early** (they gate Phase 3's design, and the WebSocket fallback is a
decision to make *before* building the client, not after). Run the **Grok tier test** before
committing the §4.2 seat.
