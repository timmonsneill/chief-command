# GPT Task Queue — serial, self-contained, safe lanes

Protocol: take the TOPMOST task not marked TAKEN/DONE. Mark `TAKEN <date>` when you
start, `DONE <date> — branch gpt/<name>` when finished. One branch per task. The rules
in `/AGENTS.md` bind every task. Nothing here ships directly — a task is DONE when the
GPT gauntlet is clean and the branch is pushed; the harness's cross-family panel merges.

This file is tracked in git on purpose (the Arch repo's copy lives in an ignored
folder and vanishes on a fresh clone).

---

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

## 2. [ ] A "what have we used" reader for the subscription seats (read-only script)
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

## 4. [ ] Wire the gatekeeper's merge into the flow (the door has no doorbell)
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

## 5. [ ] Gatekeeper HTTP surface: drain the body on early refusals
`harness/gatekeeper.py` `serve()`: the 403 and 413 paths return without reading the
request body, so on a keep-alive connection the leftover bytes are parsed as the next
request line. Read and discard `Content-Length` bytes (bounded) or send
`Connection: close` before returning. Add a test with a raw socket. No other changes.

## 6. [ ] Merge holds a write transaction across `git merge`
`gatekeeper.merge` runs `BEGIN IMMEDIATE` → `set_status` → `git merge` (up to 120s) →
`COMMIT`, while `connect()` sets a 15s busy timeout — so a slow merge turns concurrent
panel writers into "couldn't finish" skips. Either raise the busy timeout above the merge
timeout in `harness/db/jobs.py::connect`, or restructure so git runs outside the
transaction against a re-checked precondition. Say which and why in the worklog.

---

Done tasks get reviewed by the harness's cross-family panel before merge.
