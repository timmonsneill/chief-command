# Chief Command v2 — Phase 1 Harness

The text harness. Voice is a swappable skin over this, and this works fully without it —
which is the one thing the spec got exactly right, and the reason v1's voice problem
could never happen again here.

**Phase 1 acceptance (spec §8):** from the phone, type *"have the local model write X,
then have Codex review it"* and it happens, with the job recorded.

---

## What's here

| Path | What it is |
|---|---|
| `db/schema.sql` | Job store. **The two structural guards live here** (see below). |
| `db/jobs.py` | Job store API — create, atomic-claim, verdicts, budgets, reporting. |
| `config/seats.toml` | The named roles from §4. Providers swap behind these; nothing else references a provider by name. |
| `tests/test_jobs.py` | 14 tests. The guard tests are the load-bearing ones. |

## Why this exists at all

Most of the spec is already OpenClaw: the four seats are `agents.list[]`, the dispatcher
is `sessions_spawn` (non-blocking, per-spawn model override), the gauntlet is the stock
`autoreview` skill, and the tailnet + launchd daemon ship in core. **Rebuilding those
would have been the mistake.**

Three things core does *not* give us, and they're what this directory is:

1. **Durable job history.** OpenClaw's subagent sessions auto-archive after ~60 minutes
   and get soft-deleted. That's session archival, not the queryable build history §7
   wants ("what did the overnight run do?").
2. **Per-seat budget caps.** Core has no per-agent cost or token budget of any kind.
3. **The never-ship-unreviewed rule**, enforced structurally.

## The guards

§9 says to enforce the local-output rule *"in the pipeline itself, not by convention."*
That means the database, not the prompt. **Rules that live in prompts quietly stop
holding at 4am.**

- **Guard 1 — local output never ships unreviewed.** A job built by a `local`-tier seat
  cannot reach `done` without a *passing* verdict from a higher tier. Another local seat
  can't rescue it (two juniors are not a senior). A failing review can't rescue it.
  Re-tiering a seat after the fact can't retroactively legitimize it — `reviewer_tier`
  is snapshotted at write time.
- **Guard 2 — escalations must be answered, not outrun.** An unresolved `needs_human`
  verdict blocks completion.

Both are SQLite triggers. The write simply fails. `tests/test_jobs.py` proves it.

---

## Setup

### Already done on this machine
- ✅ OpenClaw **pinned to 2026.6.11** (no `latest` — there were three breaking releases
  in under 90 days in Q1 2026; version bumps are deliberate, tested events)
- ✅ Ollama installed + serving on `127.0.0.1:11434`
- ✅ `qwen2.5-coder:7b` pulled — **sized for this box, see below**
- ✅ Job store + guards + tests

### ⚠️ Hardware reality (Phase 0 RAM check, §2)
**This machine is an M2 Pro with 16 GB.** The spec's 30B-class local model wants 36 GB+.
So the grinder is a **7B coder** — a genuinely junior dev. That's survivable precisely
because Guard 1 makes it *impossible* to ship its output unreviewed. Bump to
`qwen3-coder:30b` on the Studio (§8 Phase 5) by changing one line in `seats.toml`.

### Steps that need you (interactive auth — I can't do these)

```bash
# 1. Tailscale — the harness binds to the tailnet, never the public internet (§2)
brew install --cask tailscale
# then sign in via the app, and:
tailscale status

# 2. Codex OAuth (orchestrator seat)
npm install -g @openai/codex
codex login          # opens a browser; uses your ChatGPT subscription

# 3. Grok (workhorse seat) — ⚠️ TEST THE TIER FIRST, see below
openclaw configure   # walk the provider setup

# 4. Gateway on the tailnet + launchd
openclaw gateway install
openclaw gateway --bind tailnet
```

### 🚩 Test the Grok tier before you count on it
xAI's launch said Grok Build is available to all SuperGrok subscribers, but there's a
live bug report that OAuth **403s for standard SuperGrok ($30/mo)** with the backend
apparently gated to SuperGrok **Heavy ($300/mo)**. Cheap test, expensive assumption.
If it 403s, the workhorse seat falls back to the orchestrator or the grinder.

---

## Things the spec got wrong (corrected in `seats.toml`)

Full detail in `docs/OSS_RESEARCH_2026-07-11.md`. The load-bearing ones:

- **The Claude credit pool does not exist.** Anthropic announced it, then *paused it
  before it took effect*. `claude -p` draws on normal subscription limits today. The cap
  in `seats.toml` is forward-proofing, not a current necessity.
- **Codex parallel workers: 6, not 8.** `max_threads` defaults to 6.
- **Codex OAuth is tolerated, not sanctioned.** No OpenAI doc blesses it, and both other
  major vendors closed this exact door within five months. Keep the API-key swap ready —
  the provider abstraction makes that nearly free.
- **The voice head is a new seat the spec didn't have.** §5.1 had the voice layer talking
  straight to the orchestrator. GPT-5.6-sol measures **~13s to first token** against a
  1.5s budget. The head is fast and deliberately not smart; the orchestrator becomes a
  dispatch target whose latency is invisible because you're never waiting on it.

## Run the tests

```bash
.venv/bin/python -m pytest harness/tests/ -q
```
