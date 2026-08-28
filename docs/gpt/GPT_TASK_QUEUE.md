# GPT Task Queue — serial, self-contained, safe lanes

Protocol: take the TOPMOST task not marked TAKEN/DONE. Mark `TAKEN <date>` when you
start, `DONE <date> — branch gpt/<name>` when finished. One branch per task. The rules
in `/AGENTS.md` bind every task. Nothing here ships directly — a task is DONE when the
GPT gauntlet is clean and the branch is pushed; the harness's cross-family panel merges.

This file is tracked in git on purpose (the Arch repo's copy lives in an ignored
folder and vanishes on a fresh clone).

---

## 0. [ ] Move the voice to Grok voice 2.0 (owner decision 2026-08-27) — needs Sol design first
What runs today: `harness/server.py::voice_token` mints an OpenAI client secret and
`harness/web/voice.html` connects to OpenAI over WebRTC. xAI's realtime API is
WebSocket-only (`wss://api.x.ai/v1/realtime?model=...`) with its own ephemeral-token
endpoint. Task: (a) confirm from docs.x.ai the exact model id for voice 2.0, the
ephemeral-token endpoint, function-tool support and a `tool_choice`-equivalent (the mouth
MUST hand every utterance to `ask_chief`; if xAI can't force that, STOP and report);
(b) a `/api/voice/token` that reads the mouth seat from `seats.toml` (provider →
vendor, never hardcoded) and mints the right token; (c) a WebSocket audio path in
voice.html (mic → PCM16 chunks → ws; audio deltas → playback; barge-in), keeping the
existing `ask_chief` round-trip; (d) pin the exact model id, never `grok-voice-latest`.
Owner tests from the phone at the end. Nothing else in the harness changes.

## 1. [ ] Record what the Grok reviewer actually spent (harness only)
`harness/gauntlet.py::_xai_review` makes a paid HTTP call and throws the response's
`usage` block away. The `usage` table (`harness/db/schema.sql`) and `record_usage()`
in `harness/db/jobs.py` exist for exactly this and are never written by any reviewer.
Task: have the xai runner return the token counts it got back, and have `_review_one`
write a `usage` row (role='review', input/output tokens, cost in cents from the
pricing pinned in `seats.toml` — add a `price_in_per_million` / `price_out_per_million`
pair to the grok seat rather than hardcoding). The runner signature is shared with the
CLI runners, so change it in a way that keeps `claude-cli` and `codex` working (they
can report zero). Tests: a stubbed response with usage → a usage row with the right
cents; a stubbed response with no usage block → a row with zeros, never a crash. Do
NOT touch the schema triggers or migrations.

## 2. [x] DONE 2026-08-27 (harness/usage_local.py + dashboard block) — A "what have we used" reader for the subscription seats (read-only script)
The binding constraint on Claude and Codex is rate limits, not money, and neither seat
writes to `usage`. Both leave records on disk: Codex writes
`~/.codex/sessions/<yyyy>/<mm>/<dd>/rollout-*.jsonl` with `total_token_usage` and a
`rate_limits` block (`used_percent`, `window_minutes`, `resets_at`, `plan_type`) in its
`token_count` events; Claude Code writes `~/.claude/projects/<project>/*.jsonl` with a
per-message `usage` block (`input_tokens`, `cache_read_input_tokens`, `output_tokens`).
Task: `harness/usage_local.py` — a function `local_usage(days=7) -> dict` returning,
per family (`gpt`, `claude`), tokens in/out for the window and, for gpt, the latest
rate-limit percentage and reset time; plus a `__main__` that prints it in plain
English ("Codex: 3% of this week's allowance used, resets Thursday"). Paths come from
`Path.home()`, never hardcoded (rule 4). Read-only; never modify those files. Tests
use tmp fixtures shaped like the real files. Do NOT wire it into the server yet — that
is a separate, spec'd task.

## 3. [ ] Pin the Grok reviewer to grok-4.6 and prove the pin
`harness/config/seats.toml` `[seats.grok]` says `grok-4.5`. xAI's own docs recommend
`grok-4.6` (released 2026-08-12) for code, same price. Task: change the model, and add
a test in `harness/tests/test_gauntlet_config.py` asserting no seat's `model` ends in
`-latest` or `latest` (xAI's aliases auto-update, which rule 2 forbids). No other
changes.

## 4. [x] DONE 2026-08-28 — Wire the gatekeeper's merge into the flow (the door has no doorbell)
`harness/gatekeeper.py::merge` is complete and tested (including against a real git repo)
but NOTHING calls it — no route in `harness/server.py`, no tool in `harness/chief.py`, no
step after the panel certifies a job. `dispatch.ship` is gone, so today nothing can move a
job to `shipped`. Task: after `gauntlet.run_panel` certifies a job (`result.certified`),
ask the gatekeeper to merge — through `gatekeeper.handle({"verb":"merge", ...})`, never by
importing `merge` directly into the worker (the point is that agents ASK). Note the ship
guard also needs a passing cross-family TESTER on the record (schema guard 6), so for now
a certified job without a tester verdict will be refused — log that refusal plainly on the
job, don't work around it. Tests: certified job + tester → shipped; certified job, no
tester → refused with a plain-English note, job stays `done`. Do NOT weaken any guard.

## 5. [x] DONE 2026-08-27 (Connection: close on refusals) — Gatekeeper HTTP surface: drain the body on early refusals
`harness/gatekeeper.py` `serve()`: the 403 and 413 paths return without reading the
request body, so on a keep-alive connection the leftover bytes are parsed as the next
request line. Read and discard `Content-Length` bytes (bounded) or send
`Connection: close` before returning. Add a test with a raw socket. No other changes.

## 6. [x] DONE 2026-08-28 — Merge holds a write transaction across `git merge`
`gatekeeper.merge` runs `BEGIN IMMEDIATE` → `set_status` → `git merge` (up to 120s) →
`COMMIT`, while `connect()` sets a 15s busy timeout — so a slow merge turns concurrent
panel writers into "couldn't finish" skips. Either raise the busy timeout above the merge
timeout in `harness/db/jobs.py::connect`, or restructure so git runs outside the
transaction against a re-checked precondition. Say which and why in the worklog.

## 7. [ ] Run the gatekeeper as its own process, and make the panel ASK it
Every GPT seat flagged this: `harness/gauntlet.py::_reserve_review_budget` imports
`gatekeeper.spend` and calls it in-process on the panel's own writable connection, so
"gatekeeper down means irreversible things stop" is not true on the live path. Task:
(a) start `gatekeeper.serve()` from the server's startup (same process is fine for now,
separate thread, loopback + token); (b) replace the in-process call with an HTTP request
to it (`handle`'s JSON shape), refusing the review as a SKIP if the service can't be
reached; (c) a test that stops the service and proves no money is reserved and no
provider is called. Keep `gatekeeper.spend` importable for tests only.

## 8. [ ] Rename the `grok` seat to a ROLE name (rule 7)
`harness/config/seats.toml` `[seats.grok]` is a vendor name used as a seat identity; the
roster in `[gauntlet] reviewers` names it too. Rename to `reviewer_metered` (seat id,
roster, live `seats` row via `sync_seats`, tests that reference "grok" as a seat id —
NOT the `family = "grok"` value, which is correct). Nothing in orchestration may
reference a provider by name. One task, no behaviour change; all tests green.

## 9. [x] DONE 2026-08-28 (candidate-only) — Real code builders — Sol's GO version ONLY (STOP on the full plan, 2026-08-27)
Sol's gate: docs/sol/sol_real_code_builders.out. The full plan (builder edits, tests run,
auto-merge) is STOPPED until a real OS boundary exists (the wall). What is GO tonight —
"candidate generation, no merge":
- a `claude-cli` builder in executor.py that runs `claude -p` with the LOCKDOWN on every
  call: `--setting-sources "" --strict-mcp-config --restricted --safe-mode`, a per-job
  `--settings` file, `--max-budget-usd`, prompt on stdin (variadic flags swallow a
  positional prompt). A startup test fails if any flag is missing.
- in a STANDALONE CLONE per job (not a `git worktree` — worktrees share .git, hooks,
  refs and config with the live repo, so a builder with a shell can move main or poison
  what the gatekeeper later reads). Precedent: ~/code-projects/chief-command-worktrees/.
- the harness commits; FULL 40-char sha as head_version; `bundle_kind` column ('text' |
  'diff') stamped at dispatch and frozen by trigger (numbered migration — a deliberate
  Claude-session change, reviewed).
- commit-time AND merge-time allowlist for diff jobs: plain text files only; refuse
  binaries, symlinks, submodules (mode 160000), exec-bit changes, .gitattributes,
  .git/hooks, merge commits. Refuse, don't render.
- the panel reviews `git diff <merge-base main>..<tip>` for diff jobs (fidelity check:
  reviewers saw a faithful, complete picture of THAT commit) — with the hardened,
  structured-verdict reviewers (branch gpt/harden-reviewers).
- a lane memory for THIS repo per builder seat (the ~/.claude/agents/memory files are
  Arch-EMR conventions — wrong project; do not prepend them).
- **It stops at `done`. Nothing runs builder-written tests; nothing merges.** Neill reads
  the diff in the morning. Executing and merging come after the wall, in that order.

## 10. [x] DONE 2026-08-28 — Merge loop (task 4) — fix Sol's finding before it merges
`harness/tester.py` records a pytest log as artifact kind `trace` to satisfy
`guard_tester_must_cite_artifacts` — that launders "the tests passed" into "someone
drove the app", making a green suite sufficient to ship. Record test runs as `exit_code`
+ `stdout` artifacts (kinds already in the schema), record NO tester verdict from a unit
test log, and never run tests in a worktree whose diff contains anything but
`chief_output/*.txt` (that is executing model-written code as the owner). The
ask-the-gatekeeper wiring stays; guard 6 will refuse until a real driving tester exists,
and the job's spoken line says so plainly.

## 11. [ ] Pin the tester's evidence to the version (schema, deliberate)
`guard_tester_must_cite_artifacts` (schema.sql ~829) matches artifacts on job_id only.
A screenshot captured for version A would satisfy a tester verdict on version B. Add a
`reviewed_version` (or `head_version` snapshot) column on artifacts, stamped at capture,
and make the guard require `a.reviewed_version IS NEW.reviewed_version`. Numbered
migration + schema.sql in step; `test_live_db_matches_schema` must pass after applying.
Design with Sol first (it's a guard change).

## 12. [ ] Merge-time bundle contract, a commit-time size limit, and pushing job/N
`gatekeeper.merge` recomputes `git merge-base main tip` at merge time and compares
that FRESH computation against the stored bundle — which means the base it
verifies against can differ from the base the panel actually reviewed against if
`main` moved in between (task #9's design review flagged this: the check can be
correct today and still verify against the wrong thing tomorrow). Store the FULL
base commit id (not just the diff text) and the exact `git diff` options used
(`--no-renames`, pinned) at build time, and make `gatekeeper.merge` verify against
THAT STORED BASE, never a recomputed one — if `main` has moved, that's a new
integration and needs a fresh review, not a silent re-verify. Separately:
`_claude_cli_build`/`_commit_diff_in_clone` have no upper bound on how large a
change they'll accept before committing — a commit-time size limit (mirroring
gauntlet.py's MAX_CODE_CHARS truncation-avoidance design for reviews) belongs here
too, refused BEFORE a commit exists rather than truncated after. And today nothing
ever pushes `job/<id>` anywhere — when auto-merge is eventually enabled (after the
real OS isolation boundary Sol's design gate is still waiting on), the
gatekeeper's merge needs to push the merged `main` to the real remote, behind the
same lock, with the same "refuse and say so" discipline as every other step in
that function. Design each of these with Sol before building, same as task #9
itself was.

## 13. [ ] Run builders and reviewers as the `chiefagent` account (the wall, harness side)
BLOCKED until the owner creates a standard (non-admin) macOS user `chiefagent` and signs
in to Claude Code + Codex as that user once. Then, in this order, each with a live check:
1. Keys: `~/.chief/env` stays owned by the owner, mode 600. Builders/reviewers receive NO
   vendor keys. The xai reviewer runs in the harness process (owner), not as the agent.
2. Layout: `/Users/chiefagent/work/job-<id>/` owned by chiefagent for clones; the record
   (`harness/db/chief.db`), the real repos and `~/.chief` unreadable to chiefagent
   (`chmod 700` on the owner's home is the simplest — verify Jess/Chief repos too).
3. Launch-as-agent: ONE sudoers rule scoped to exactly one wrapper script
   (`scripts/run-as-agent.sh <job-dir> <argv…>`), no password, nothing else. The wrapper
   sets a minimal env (PATH, HOME=/Users/chiefagent, USER=chiefagent) and cds into the
   job dir. Never `sudo -u chiefagent bash`.
4. Executor: clone into the agent's work dir as the OWNER, `chown -R chiefagent`, run the
   builder through the wrapper; read the result back as the owner. Reviewers (claude-cli,
   codex) likewise through the wrapper — they read untrusted text too.
5. Give builders back `Bash` + `--max-turns`, and run the project's tests inside the
   clone through the wrapper, recording exit_code/stdout artifacts (still NOT a tester
   verdict — guard 6 stays; a real driving tester is separate).
6. Gatekeeper: stays owner-only; that's the bridge. Add the loopback service (task 7).
7. Verification (LIVE, as chiefagent): `ls ~<owner>/.chief` denied; `security find-generic-
   password` finds nothing; opening chief.db refused; `git push` to the real repo refused;
   builder can edit/test/commit in its own dir. Record the outputs in the worklog.
Design with Sol first (it's the security boundary). Rule 4: no hardcoded /Users paths —
read the agent user + work dir from seats.toml `[agent_account]`.

## 14. [ ] HOME + WORKERS — Sol design pass, then the worker protocol
Read docs/ARCHITECTURE_home_and_workers_2026-08-28.md. Design, with Sol (ask-sol.sh):
(a) the worker protocol — a worker on another machine claims a job, receives the frozen
bundle/clone source, reports events, artifacts, head_version and result back THROUGH the
gatekeeper/home API (never the sqlite file); heartbeat + STALLED marking when a worker
goes silent; per-worker seat lists (what it may build/review) and caps; (b) the home on a
Linux box — the wall (task 13) built there once; (c) how the voice token step and the app
move unchanged. Output: a spec with a Feature Acceptance Checklist (rule 9). No code
until Sol says GO.

---

Done tasks get reviewed by the harness's cross-family panel before merge.
