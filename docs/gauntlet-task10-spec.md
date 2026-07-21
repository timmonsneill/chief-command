# Task #10 — Make the full review gauntlet run (parallel panel)

**Status:** spec, pre-build. Design-gate with Sol before writing code.
**Owner ask:** "Make the full review panel run (right now it's one reviewer)."

## Where it is now (grounded in the code)

- `harness/dispatch.py::run_gauntlet(conn, job_id, cfg)` returns a list of reviewer
  **names** (skipping over-budget ones) and **launches nobody, records no verdict**.
- The executor already has a *working single-reviewer* path:
  `harness/executor.py::_run_one_review(...)` → resolves the seat, picks a reviewer fn
  from `_REVIEWERS` (keyed by provider), runs it, and calls
  `harness/db/jobs.py::record_verdict(...)` bound to the reviewed version, emitting events.
- Config — `harness/config/seats.toml [gauntlet]`:
  `reviewers = ["brain", "grinder_paid", "grinder_local"]`, `min_model_families = 2`,
  `escalate_to = "reviewer"`.
- `record_verdict` snapshots the reviewing seat's **family** and pins `reviewed_version`.
- The schema trigger already refuses to ship a job without a passing higher-tier
  **cross-family** verdict on **this** version. The panel's job is to *produce* those
  verdicts across ≥2 families — not to re-implement the guard.

## What to build

1. **Parallel fan-out.** `run_gauntlet` runs every configured reviewer seat concurrently
   (threads, like `start_in_background`) against **one frozen bundle** — the same
   `head_version` + built code snapshot handed to every reviewer, so no two verdicts bind
   to different versions.
2. **Per reviewer:** skip-if-over-budget stays, but **log the skip** (no silent caps);
   reserve budget **before** the provider call; run via the shared reviewer path; record a
   verdict bound to the frozen `reviewed_version`.
3. **Panel decision:**
   - Count **families that actually produced a verdict**. If `< min_model_families`, the
     job **cannot be certified** — it stays parked. Never count a family that didn't run.
   - Contested (some pass, some fail) → escalate to the `escalate_to` seat for a tiebreak.
4. **Return structured result** (seats run, families covered, verdicts, escalation), not
   names.

## Risks to pressure-test (for Sol)

- **Frozen bundle:** guarantee every reviewer sees the identical version; nothing mutates
  mid-panel.
- **Budget reservation race:** two reviewers reserving concurrently must not both pass a
  cap — reserve atomically before the call (ties to the #11 gatekeeper principle).
- **Family-floor honesty:** over-budget/errored reviewers shrink family coverage; a panel
  that can't reach 2 families must **fail closed** (parked), not ship. Don't fake a family.
- **SQLite + threads:** each thread needs its own connection; connections aren't shareable.
- **Do not route around the DB guard.** The trigger is the boundary; the panel feeds it.

## Out of scope

- The metered reviewers (grok/gpt seats) need the leaked keys rotated first — build and
  test with the Claude-CLI reviewers; wire the metered seats behind the rotation.
- The #11 gatekeeper (sole holder of merge/deploy/spend) is a separate task; #10 only
  produces verdicts, it does not grant irreversible powers.
