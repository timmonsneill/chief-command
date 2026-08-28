# HOME + WORKERS — design gate and spec (Sol, 2026-08-28)

Gate on `docs/ARCHITECTURE_home_and_workers_2026-08-28.md` (owner direction) and queue
task 14. Cross-checked with GPT-5.6-sol: transcript `docs/sol/sol_home_and_workers.out`,
prompt `docs/sol/sol_home_and_workers_prompt.txt`. Two independent models, one codebase
read. Where they agreed I say so; where I checked something myself I say that too.

---

## For Neill, in plain English

**The idea is right. The plan as written is not, and one part of it would quietly break
the most important safety property this whole system has.**

The idea — one always-on machine in the cloud that holds the record and that you talk to,
and several muscle machines (laptop, the Studio when it lands) that do the heavy work and
can be off without it mattering — is correct and worth building. Nothing below argues
with that.

The problem is *who gets to say what happened*. Today, the same program that runs the
review panel is the program that writes down what the panel decided, and it runs on your
own machine. That is what makes "three different models checked this" mean something. The
plan hands that job to the muscle machines: they would run the reviews **and** report the
results. A machine that both does the work and grades its own homework is not a review
panel — and a single machine going wrong (a bug, or something nastier) could hand you a
perfect-looking set of green ticks on code nobody ever read. That is exactly the failure
this project was built to prevent.

The fix is small and it makes the system *simpler*, not more complicated: **the muscle
machines only build. The home machine does all the reviewing, all the recording, and all
the merging.** Reviewing is mostly waiting on other companies' servers, so a small cloud
box is perfectly good at it. Heavy building needs the Studio. Split it that way and every
existing safety rule keeps working exactly as it does now.

Three other things you should know:

1. **The wall is being planned on the wrong machine.** The separate agent account exists
   to contain a builder that goes wrong — and builders run on your laptop and your
   Studio, not on the cloud box. Building it only on the cloud box would leave both Macs
   exactly as exposed as they are today. The good news: moving the record and the keys to
   a different *machine* achieves a big chunk of what the wall was for, and does it better
   than a separate account ever could.

2. **I found a real hole in the current system while checking this** — nothing to do with
   the cloud move. A review that approved an old version of some work can be silently
   re-labelled as approving the *new* version, and the job then sails through every check
   as fully approved. I proved it against the real database, not on paper. It needs fixing
   whatever you decide about the cloud.

3. **Do not move the voice in the same step as everything else.** The voice is ninety
   percent of how you use this. It should be the last thing that moves, on its own, with a
   written way to put it back in three commands.

Order I recommend: build the new muscle-machine arrangement **on your laptop first**, where
nothing has moved and nothing can be lost. Only once it works there, stand up the cloud
box. Then move the record. Then the voice. Then plug the Studio in when it arrives — and by
then there is nothing left to migrate, because the arrangement already exists.

---

## VERDICT: STOP on the plan as written. The direction is GO.

Not "go away and rethink the idea" — the one-home/many-workers direction is correct and
stays. STOP means: **do not start the migration described in the direction doc's step 2**,
because three of its load-bearing assumptions are false against the actual code, and one
of them destroys the gauntlet.

GPT-5.6-sol reached STOP independently, on the same three assumptions. That is the highest
confidence level available here.

The path to GO is in REQUIRED CHANGES below. Every one of them is a design decision, not a
research project.

---

## The three false premises in the direction doc

The doc says (line 24-26): *"Workers ask the record for work (`claim_next_job` is already
atomic) and report back; the gatekeeper is the only writer of merge/deploy/spend. Moving
workers to other machines is plumbing on a design that already assumes it."*

1. **There is no pull loop.** `claim_next_job` (`harness/db/jobs.py:168`) is called from
   exactly two places, both non-production: `harness/tests/test_jobs.py` and
   `harness/scripts/smoke_dispatch.py`. The live path is push, in-process:
   `harness/dispatch.py:181-184` sets the job to `in_progress` and then calls
   `executor.start_in_background(job_id)` — a thread in the same process as the record.
   `claim_next_job` filters on `status='todo'`, which the live path never leaves a job in.
   Nothing in the running system "already assumes" a distributed worker.

2. **The gatekeeper is not the only writer of spend on the live path.**
   `harness/gauntlet.py:550-553` and `harness/dispatch.py:397-402` both `import gatekeeper`
   and call `gatekeeper.spend(...)` in-process on their own writable connection.
   `harness/gauntlet.py:888-892` calls `gatekeeper.handle(...)` in-process for merge. The
   loopback service exists (`gatekeeper.serve`) but is not the enforced boundary — this is
   already known and queued as task 7.

3. **The record is not the only thing that would have to cross the network.** The
   gatekeeper's central check is a **git** operation on the machine it runs on:
   `_branch_tip` reads `refs/heads/job/<id>` locally (`gatekeeper.py:404`), `_diff_bundle`
   recomputes `git diff base..tip` (`:476`), `_commit_holds_the_reviewed_work` runs
   `git show` (`:435`), `_repo_is_ready` requires `main` checked out and clean (`:485`),
   `_git_merge` merges into that working tree (`:500`). If the builder runs on a worker,
   the home has no git objects at all. Nothing anywhere pushes `job/<id>` (queue task 12
   records this). A `head_version` string arriving over HTTP is not a substitute for the
   objects — trusting it would delete the strongest check the system has.

---

## Ranked risks

### 1. CRITICAL — a worker that runs the panel can forge the entire panel
The schema verifies that a verdict's claimed tier and family match the `seats` table
(`schema.sql:781`). It cannot verify that a review *happened*. Completion counts distinct
passing seat names and family names (`schema.sql:875`, `schema.sql:905`). That is sound
only while trusted in-process code is what invoked the reviewers.

Over HTTP, a worker asserts "reviewer passed", "brain passed", "both saw version abc123" —
and the record accepts it, because those are exactly the facts it is designed to record.
One compromised or simply buggy worker manufactures a complete cross-family panel on code
nobody read, and the gatekeeper faithfully merges it. The `≥2 model families` property is
already only auditable rather than DB-enforced (MEMORY.md); this would move it from
"auditable" to "asserted by the least trusted machine in the system."

**This is the single reason the plan is STOP and not ADJUST.**

### 2. CRITICAL (live today, independent of this plan) — an approval can be re-pointed at a version it never saw
`guard_verdicts_are_append_only` (`schema.sql:708`) only fires when the verdict *word*
changes. `guard_verdict_identity_is_frozen` (`:716`) covers role, family, seat and tier.
**`reviewed_version`, `job_id`, `severity`, `summary` and `detail` are all freely
updatable.** I proved it against the real schema on a scratch database:

```
verdict recorded with reviewed_version='OLD-VERSION', job head_version='AAAA'
UPDATE verdicts SET reviewed_version='AAAA'  ->  ALLOWED
UPDATE jobs SET status='done'                ->  ALLOWED, job reached 'done'
```

A stale pass, re-labelled, satisfies `guard_full_panel`, `guard_family_floor` **and**
`guard_local_output_needs_review`. The review-to-version chain — Sol's build gate 3, the
thing the whole 2026-07-20 hardening pass existed to install — has a one-line bypass. Fix
this regardless of what happens to the cloud plan.

### 3. CRITICAL — the home cannot verify or merge a remote candidate (see false premise 3)
Everything the gatekeeper checks about *bytes* requires local git objects. There is no
worker-to-home git transport and nothing pushes `job/<id>` anywhere.

### 4. CRITICAL — a worker holding a provider key walks past every cap
The money guards (`schema.sql:637`, `:1066`, `:1081`, `:1094`) refuse ledger *inserts*.
They do not stop a machine that has an API key from calling the provider without inserting
anything. `gatekeeper.spend` reserves before the caller spends, but the caller is the one
that then makes the paid call (`gatekeeper.py:625`). On a remote worker, skipping the
reservation is trivial. No protocol fixes this: the vendor's own account limit is the only
real ceiling once a key is on a machine you don't control.

Related and live today: the mouth is a metered seat with `daily_cap_cents = 480`
(`seats.toml:44,63`), and `/api/voice/token` (`server.py:625`) checks the monthly total but
**records no usage row at all**. The voice's daily cap is currently fiction — nothing ever
writes a `role='voice'` usage row anywhere in the codebase.

### 5. HIGH — no lease, no fencing, no attempt ceiling, so a returning worker can overwrite a newer answer
`claim_next_job` records no worker, no token, no expiry, no generation, no maximum
attempts. `jobs.status` has no `stalled` value (`schema.sql:279`). Without a fencing
generation: worker A drops off, the home reassigns to worker B, B finishes, A reconnects
and reports its older answer over the top. A heartbeat does not fix this — every write has
to be conditional on the current attempt.

### 6. HIGH — "the wall on the home once" protects the wrong machines
The wall exists to contain the processes that read hostile text and edit code. Those are
builders, and builders run on macOS workers (`THE_WALL_owner_steps.md:46`,
`GPT_TASK_QUEUE.md:167`). A home that runs no builders needs *service separation*, not the
builder wall. Building it only on the Linux box leaves the laptop and the Studio exactly as
exposed as they are today. GPT-Sol independently confirmed this reading.

The compensating good news, which should be stated to the owner: putting the record and
the keys on a **different machine** achieves the larger half of the wall's purpose more
strongly than a same-machine user boundary ever could.

### 7. HIGH — panel requirements would be stamped by the wrong machine
`dispatch.panel_roster` (`dispatch.py:230`) asks `gauntlet.has_runner` (`gauntlet.py:382`),
which probes **this machine**: is `claude` logged in, is `codex` logged in, is `XAI_API_KEY`
set. It then writes `required_reviews` / `required_review_families` onto the job,
un-lowerable by trigger. On a bare home VM that refuses every job at the door
(`_refuse_a_panel_that_cannot_hold`), and it would never discover that the Studio could
have run the panel. Panel obligations must come from **policy**; worker availability
decides *when* work runs, never *how much review it needs*.

### 8. HIGH — the app has no authentication, and a worker is not a phone
There is no auth on any route (`server.py:152, 418, 570, 625, 754, 883`). "On the tailnet"
was an acceptable stand-in while the tailnet meant "Neill's own devices" — the open TODO
already flags it ("Real auth on the phone app. Being on the tailnet is not proof of being
Neill."). The moment workers join the tailnet, a compromised worker can dispatch jobs, read
the whole record, mint voice tokens (real money), and fill the disk through an unbounded
upload route.

### 9. MEDIUM-HIGH — the home is a new single point of failure with no stated failure behaviour
Moving to the cloud fixes "the office wifi went down". It does not fix VM failure, tailnet
failure, disk-full, a corrupted record, a failed restart, or a GCP account lockout. Text
and voice die together because both live on the home. That does not violate text-first by
itself, but the plan must state plainly what happens: work stops, nothing auto-replays, and
the manual fallback (open Claude Code / Codex by hand) is the answer.

### 10. MEDIUM — voice continuity is process-global and cannot survive a reconnect
One process-global `_chief_session` behind one lock (`server.py:691-692`). A restart loses
the conversation. The SSE producer deliberately outlives a vanished phone
(`server.py:826`), but its queue belongs to the original request — the phone cannot
reconnect and pick up sentences it missed. Today "the laptop was right there". A cloud VM
restarts for its own reasons.

### 11. LOW, but say it out loud — the `capabilities` table is decoration
`schema.sql:377` and the `safe_capabilities` view are referenced by **no trigger and no
Python outside tests** (verified by grep). It reads like an enforcement mechanism in a file
full of real ones. Either wire it or comment it as descriptive.

---

## THE DESIGN — what to build instead

One sentence: **workers build; the home reviews, records and merges.**

```
  WORKER (laptop, Studio)                 HOME (small Linux VM, GCP)
  ------------------------                --------------------------
  claim a BUILD task            <---->    the record (chief.db)  [only writer]
  fetch exact base commit       <-----    bare git repo per project (read-only)
  run builder in isolated clone           the gatekeeper (loopback, 3 verbs)
  push job/<id> to quarantine   ----->    quarantine import + git validation
  report progress + finished    ----->    computes tip, canonical diff, head_version
                                          RUNS THE WHOLE PANEL here
                                          makes every metered provider call here
                                          the app + the voice
```

Why this is smaller, not bigger, than the proposal: the reviewers are network-bound (a CLI
or an HTTPS call), not compute-bound. A small VM runs them fine. Only local models and
heavy builds need the Studio. Splitting on "build vs review" instead of "job vs report"
means `gauntlet.py` does not change at all, and every existing guard keeps exactly the
meaning it has today.

Blast radius of a fully compromised worker under this design: **it can submit bad code as a
candidate.** Which is what a builder is supposed to be able to do. It cannot write a
verdict, cannot set a version, cannot produce evidence, cannot spend, cannot merge.

### Divergence from GPT-Sol, stated
GPT-Sol's required-changes list decomposes work into per-stage `work_items` and `attempts`
with candidates, receipts and reservations as separate tables — eight new tables. That is
the right shape for a system where workers must run reviews. It is over-built for this one,
and Decision D (proportionality) exists precisely to stop that. Keeping the panel on the
home collapses it to **three** new tables and no change to the review code. I take
GPT-Sol's fencing, quarantine, provenance and tailnet-grants requirements and drop the
stage decomposition. If a future need forces reviews onto workers, the stage model is the
right thing to reach for then.

---

## REQUIRED CHANGES

Numbered, in build order. Each is a decision, not an investigation.

**Fix first, regardless of the cloud (these are live bugs):**

1. **Make a verdict fully immutable.** Extend `guard_verdict_identity_is_frozen` to cover
   `job_id`, `reviewed_version`, `verdict`, `severity`, `summary`, `detail` and
   `created_at` — every column. Resolve `needs_human` through a separate owner-decision
   row rather than by editing the original verdict (which is what `resolve_escalation`
   does today, `jobs.py:350`). Numbered migration + `schema.sql` in step, regression test
   reproducing the exploit above. **This is task 11's sibling and should be done with it.**
2. **Record voice spend.** `/api/voice/token` must reserve through the gatekeeper with
   `role='voice'` before minting a client secret, or the mouth's `daily_cap_cents = 480`
   means nothing. Refuse the mint if the reservation is refused.
3. **Close the in-process gatekeeper bypass** (queue task 7, already specified). Until the
   panel and dispatch ask the *service*, "gatekeeper down means irreversible things stop"
   is not true on the live path — and it must be true before a second machine exists.

**The worker protocol:**

4. **Three new tables, one migration.**
   - `workers(id, display_name, token_hash, kind, trust_domain, enabled, protocol_version,
     last_seen_at, notes)`
   - `worker_seats(worker_id, seat_id)` and `worker_projects(worker_id, project_id)` — what
     each machine is allowed to build, and for which projects.
   - `worker_reports(operation_id PRIMARY KEY, worker_id, job_id, attempt_no, received_at,
     response_json)` — at-least-once delivery with durable dedupe. A repeated
     `operation_id` returns the stored answer and writes nothing.
5. **Lease and fencing columns on `jobs`:** `claimed_by`, `attempt_no`, `lease_token_hash`,
   `lease_expires_at`, `heartbeat_at`, plus `'stalled'` added to the `status` CHECK.
   (GPT-Sol argues `stalled` belongs on an attempt, not the job. It is right in principle;
   with one claimable stage per job the distinction has no consequence, and one status
   value is far cheaper for the UI and the voice to explain. Revisit if stages ever land.)
6. **Claim is one atomic statement.** Extend `claim_next_job` into a single
   `UPDATE ... RETURNING` that matches on `status='todo'` **and** the worker's allowed
   seats and projects, and stamps `claimed_by`, `attempt_no+1`, a fresh lease token hash
   and `lease_expires_at` in the same write. Never SELECT-then-UPDATE.
7. **The home is the only lease authority.** Heartbeat every 30s; lease expires after 5
   minutes without renewal, on the **home's** clock. Only a home-side reaper may mark a job
   `stalled`. A stalled job returns to `todo` only after a further grace period.
8. **Every worker write carries its lease token and `attempt_no`.** A report from a
   superseded attempt is refused and kept only as a diagnostic note on the job. Without
   this, risk 5 is live.
9. **Never resume; always re-run.** A dead worker's clone is on the dead machine. A stalled
   job re-runs from scratch, from the stored base commit, in a fresh clone. Cap infra
   retries at 3 attempts; then `failed`, with a plain-English reason. A reviewer's genuine
   FAIL is a verdict, never an infra retry.
10. **`dispatch_local` stops starting threads.** It records the job and leaves it `todo`.
    The in-process executor becomes a worker process like any other, claiming over HTTP.

**Git — how the bundle reaches a worker and comes back:**

11. **The home is the canonical git source.** One bare repo per project on the home, served
    read-only over the tailnet (Tailscale SSH or git-http-backend; standard git, no custom
    protocol). A claim names **one exact base commit id** — never "current main".
12. **Candidates arrive in quarantine, and the home does the deriving.** The worker pushes
    `job/<id>` into a receive-only quarantine namespace (`refs/quarantine/job/<id>`) or
    uploads a `git bundle`. The **home** then: verifies the branch descends from the stored
    base, applies `git_policy.disallowed_paths` and a size limit, and computes `tip_commit`,
    the canonical diff (with the pinned diff options from queue task 12) and its sha256.
    **Workers never set `head_version` or `result`.** The home writes both, from the objects
    it holds.
13. **A `pre-receive` hook on the home's bare repos** that refuses any ref other than
    `refs/quarantine/job/<N>` for a job the pushing worker currently holds a live lease on.
    Without this hook, a worker with push access can move `main` and the gatekeeper is
    bypassed entirely. This is the highest-value single control in the git design.
14. **The gatekeeper keeps a normal checkout to merge into.** `_repo_is_ready` requires
    `main` checked out clean — a bare repo has no working tree. Keep one non-bare clone per
    project on the home, owned by the gatekeeper's service user, that nothing else touches.
    Combined with queue task 12's stored-base fix and GitHub branch protection (wall step
    1), merge becomes: verify against the **stored** base, merge locally, push a PR.

**Provenance — what makes a verdict mean something:**

15. **The home runs every reviewer.** No verdict is ever written from a worker report. This
    is the change that keeps guards 1, 5, 5b, 6 and 10 meaning what they mean today.
16. **Panel obligations come from policy, not from probing.** Stamp `required_reviews` /
    `required_review_families` from the gauntlet config plus a `config_version`, at
    dispatch. An offline reviewer delays a job; it never shrinks a panel. Keep
    `panel_roster`'s honest exclusion notes — just stop letting them set the number.
17. **Artifacts are uploaded as bytes, not as filenames.** `artifacts.path` is currently a
    path on the writer's disk; from a remote machine the home cannot open it, so
    `guard_tester_must_cite_artifacts` degrades to "no *filename*, no verdict". Add
    `content_sha256`, `size_bytes`, `storage_key`; the home stores the bytes and sets
    `captured_by` itself from which endpoint received them. **Never accept `captured_by`
    from a worker.** Bind artifacts to the job's `head_version` at capture (this is queue
    task 11 — it is now a prerequisite, not a nice-to-have).
18. **Never accept `builder_seat`/`builder_tier`/`builder_family` from a worker.** The home
    derives them from the task it assigned. Otherwise
    `guard_builder_identity_must_be_real` becomes self-certification.
19. **State the worker's allowed status transitions as a hard API contract:** a worker may
    move a job from `in_progress` to `review` or `failed`, and may append events. Nothing
    else. Every other transition is the home's.
20. **`projects.worker_access`, defaulting to 0 for Arch.** Enforced at task creation, and
    there is no generic "give me a repo" endpoint — a worker only ever receives a
    task-scoped git source. Arch's `repo_path IS NULL` already blocks it; this makes the
    rule explicit rather than incidental.

**Money and seats:**

21. **Metered keys never leave the home.** Metered seats (the xai reviewer, the mouth) run
    on the home, with the home's keys, exactly as `THE_WALL_owner_steps.md` step 1 already
    requires. All caps therefore stay one atomic ledger on one machine. **Drop the
    "cloud worker with API seats on the home box" idea** — colocating builder processes with
    the only record and the only gatekeeper is the one place they should never be. If a
    backup worker is genuinely needed later, a second small VM is materially safer.
22. **One seat per (machine, model).** `grinder_local` today means qwen2.5-coder:7b; on the
    Studio it would mean a 30b model. If both wear the same seat id, `jobs.builder_seat`
    stops telling you what actually built the thing, and the frozen builder identity
    freezes a name that means two things. Use `grinder_laptop` / `grinder_studio` — distinct
    seat ids, distinct models, same family. (Also close queue task 8 while here: rename
    `grok` to `reviewer_metered`; rule 7.)
23. **Set hard vendor-side spend limits before any of this** (wall step 2). Once a key can
    reach a machine you don't physically hold, the vendor's own cap is the only ceiling
    that cannot be argued with.

**The tailnet and the app:**

24. **Split tailnet access by purpose using Tailscale grants**, not by trust in the network.
    Owner devices reach the app. Worker-tagged devices (`tag:chief-worker`) reach the worker
    API and the git repos and nothing else. Nobody reaches the gatekeeper — it stays
    loopback, per its own docstring, and the worker API becomes a *client* of it.
25. **Per-worker bearer token, hashed at rest.** Worker identity comes from the token,
    never from a JSON field. Rotatable, revocable from the app.
26. **Authenticated owner session for the app.** A long-lived device credential paired once,
    on wifi, at home — never a login prompt that can appear mid-drive. This closes the
    already-open TODO item and is now load-bearing.
27. **Bound the upload route.** Size limit, type check, per-day quota. It is currently
    unbounded and would become a disk-fill vector from any tailnet device.

---

## What the guards do when the writer is an API

No guard breaks *mechanically* — they are DB triggers and they keep firing. About half stop
*meaning* what they mean today, because the writer stops being the witness. That sentence is
the whole answer, and the table is the detail.

| Guard | Under a remote writer |
|---|---|
| `guard_no_job_is_born_done` | Holds. Only the home creates jobs anyway. |
| `guard_builder_identity_must_be_real` | Becomes self-certification unless the home sets the seat (change 18). |
| `guard_builder_identity_is_frozen` | Holds — but freezes whatever the birth write said. |
| `guard_panel_size_is_fixed` / `guard_family_floor_is_fixed` | Hold, but preserve a number chosen by the wrong machine (change 16). |
| `guard_verdicts_are_append_only` | **Already incomplete today** — see risk 2. Fix before anything else. |
| `guard_verdict_identity_is_frozen` | Same. Omits `job_id` and `reviewed_version`. |
| `guard_verdicts_cannot_be_deleted` | Holds. Genuinely valuable. |
| `guard_verdict_must_cite_what_it_reviewed` | Requires a non-empty string, not the assigned candidate. Home-written verdicts fix this. |
| `guard_reviewer_identity_must_be_real` | Confirms seat metadata, never that the model ran. **The reason for change 15.** |
| `guard_local_output_needs_review` | Broken in meaning if the qualifying review can be reported remotely. |
| `guard_full_panel`, `guard_family_floor` | Same. These two are the core of the gauntlet. |
| `guard_a_failing_review_stops_it` | Sound for verdicts that reach the record; cannot help against a suppressed fail. |
| `guard_late_escalation_still_blocks_shipping` | Sound. Re-checks at ship time, which is the right shape. |
| `guard_unresolved_escalation` | Holds; escalation resolution must be owner-only. |
| `guard_models_cannot_forge_evidence` | **Fully broken over HTTP** — `captured_by` becomes a string a caller types (change 17). |
| `guard_evidence_cannot_be_empty` | One character satisfies it. Never was a provenance guard. |
| `guard_tester_must_cite_artifacts` | Broken remotely: matches on `job_id` only, and on a path the home cannot open. Needs change 17 + queue task 11. |
| `guard_no_self_family_testing` / `_verifying` | Only as good as the execution identity behind them. |
| `guard_ship_requires_a_passing_tester` | Currently refuses everything, correctly (no real tester exists). Would be forgeable the moment one does. |
| `guard_research_must_cite_sources` | Model-reported by design; needs an independently assigned verifier. |
| `guard_a_build_finishes_a_version` | Requires a non-empty string; the home must validate a real imported commit. |
| `guard_finished_version_is_frozen` | Holds after completion. |
| `guard_monthly_budget_is_hard`, `guard_daily/build/review_cap_is_hard`, `guard_no_negative_spend` | Atomic and correct **for recorded spend**. Irrelevant to an unbrokered key (risk 4). |
| `guard_gate_log_is_append_only` / `_cannot_be_deleted` | Hold, as long as only the gatekeeper writes that table. |
| `jobs_touch_updated_at` | Unaffected. |
| `capabilities` table / `safe_capabilities` view | Enforced by nothing. Wire it or label it descriptive. |

**Must move behind the home/gatekeeper:** verdict writing, artifact bytes and their origin
label, `head_version`, `result`, every metered provider call, every status transition to
`review`(from the panel)/`done`/`shipped`, merge, and approvals.

---

## The voice and the app on the home

**What does not change, and is the good news:** the audio never touches the home. The
browser fetches an ephemeral token (`server.py:625`, `voice.html:181`) and then opens
WebRTC **directly** to the vendor (`voice.html:202`). Moving the token issuer to a VM adds
one control round trip at the start of a session, not a permanent audio relay. Availability
goes up: a closed laptop lid currently kills the voice; a VM does not have a lid.

**What does change:**

- **Every Chief answer now crosses the WAN twice.** Phone -> home -> provider -> home ->
  phone (`voice.html:258` -> `/api/voice/ask/stream` -> `chief_live`). Against a Chief that
  already takes 1.4s to 8s that is tolerable, but it must be *measured*, not asserted.
  Budget, excluding model time: p95 ~250ms on a direct Tailscale path, ~750ms if relayed
  through DERP. Test from the real phone, on wifi **and** on cellular, and record which
  path each test used.
- **Direct vs relayed is a Cloud NAT setting.** A VM with no external IP needs Cloud NAT for
  egress at all, and behind ordinary NAT Tailscale may fall back to a DERP relay. GCP's
  endpoint-independent mapping with static port allocation is what makes direct connections
  likely. Set it deliberately; it is a voice-latency decision, not a networking footnote.
- **`tailscale serve`, never `tailscale funnel`.** Serve is tailnet-only; Funnel is the
  public internet, which is rule 3. One word apart. Put it in the runbook in those words.
- **The OpenAI key moves to a machine the owner does not hold.** Say that to him plainly.
  Mitigation is vendor-side hard caps (change 23) plus a key file readable only by the
  service user that needs it — the current single `~/.chief/env` loaded wholesale into the
  web process (`server.py:31-48`) is not the right shape for the cloud layout.
- **Conversation continuity needs a reconnect story.** Add `conversation_id`, `turn_id` and
  sentence sequence numbers; keep a short replay buffer so a dropped SSE connection resumes
  instead of re-running Chief; send SSE heartbeat comments during long pauses; replace the
  process-global session with one per authenticated conversation, expiring, with an explicit
  reset. `systemd` restarts the service; the session must survive that or rebuild silently
  from the recorded transcript.
- **One fixed failure sentence.** If the home or the tailnet is unreachable, the voice says
  exactly: *"Chief is unavailable. Nothing was started."* Never queue a driving command for
  later auto-replay. Keep the unsent text locally and require a deliberate resend.
- **Do not change the voice vendor in the same move.** Queue task 0 (Grok voice 2.0) is a
  rewrite of the browser audio path. Change one failure surface at a time.

---

## The cloud home's own hardening

- Separate GCP project, VPC, service accounts, backup bucket and IAM from Arch. Structural,
  not procedural.
- No external IP; no ingress rules at all; a custom VPC (not `default`, whose built-in rules
  allow SSH). Cloud NAT + Cloud Router for egress (~$5/mo for one VM). Tailscale SSH for
  administration; no public SSH.
- Shielded VM, disk encryption, deletion protection, a pinned image, and a deliberate patch
  procedure (rule 2 applies to the OS too).
- **Separate Linux users per service:** the web app, the record controller / worker API, the
  gatekeeper, and the backup job. The gatekeeper stays loopback and owns the merge checkout.
  Each secret is readable only by the service that needs it. `systemd` units with
  `Restart=always`, `NoNewPrivileges`, `ProtectHome`, `ProtectSystem=strict`, and an
  explicit `StateDirectory`.
- **The record moves out of the code checkout.** `chief.db` currently lives inside the repo
  at `harness/db/chief.db`. On the home it belongs on a dedicated persistent state disk, at
  a path from config (rule 4 — no machine-specific paths in code). Separate quotas for the
  app, git repos, artifacts, uploads and backups.
- **Backups:** `VACUUM INTO` or the SQLite online backup API — **never** a file copy of
  `chief.db` while WAL is active, because committed data can still be sitting in the `-wal`
  file. Encrypt, copy off-box to a bucket in the same isolated project, run
  `PRAGMA integrity_check` on a schedule, and do a real restore drill. A backup that has
  never been restored is a hope, not a backup.
- **The wall goes where the builders are.** The home needs service separation. The laptop
  and the Studio each need their own agent account (queue task 13, macOS). The home does not
  need the builder wall because it must not run builders.

---

## Migration order — nothing migrates twice

The direction doc's step 2 ("stand up the home: record, gatekeeper, app, voice, the wall")
is a big-bang cutover of the surface that is 90% of daily use. Sequence it instead. The key
insight: **build the protocol before the hardware, and the Studio's arrival becomes a
config line rather than a migration.**

0. **Fix the live bugs first** (changes 1-3). No new machines. These are wrong today.
1. **Split the process on the laptop, behind a flag.** Server + record + gatekeeper in one
   process; a separate `worker.py` that claims over HTTP and **cannot open the database
   file**. Everything is local; nothing moves; every protocol bug is found at zero
   migration risk. *This is the smallest reversible first step and it is the step the
   direction doc skips.* If the gauntlet cannot be preserved here, it will not be preserved
   in the cloud either.
2. **Prove one complete local path:** queued build -> lease -> quarantine push -> home
   computes the bundle -> home runs the panel -> candidate at `done`. Auto-merge stays off.
3. **Fix the git contract** (changes 11-14, plus queue task 12's stored base and size
   limit) while everything is still on one machine.
4. **Provision the cloud home with no authority.** Restore a *disposable copy* of the
   record; run the app and voice against it; verify the Tailscale grants; prove
   backup **and restore**. Nothing real depends on it yet.
5. **One controlled record cutover.** Stop dispatch, drain or cancel active work, take an
   online backup, stop the laptop writer, restore on the home, integrity-check, switch the
   configured home address. **Keep the laptop's database read-only for rollback. Never let
   both accept writes.** Write the rollback as three commands before doing the cutover.
6. **Move the voice last, on its own day.** Measure from the phone, on wifi and cellular,
   before calling it done.
7. **Join the laptop as the first remote worker.** This proves the real network path while
   the Studio is still a month away.
8. **The Studio joins with the same worker package and config fields.** No record migration
   — the arrangement already exists. This is the whole point of the ordering.
9. **Auto-merge from remote work goes on last**, after quarantine import and independent
   review provenance are proven, and after branch protection + PR flow works.
10. **A second cloud worker, if ever, on a second VM** — never on the home.

---

## Feature Acceptance Checklist (rule 9)

Each line: what exists, what it does, where it is reachable. An absence is a failure.

**Workers, in the app**
1. A **Machines** panel exists on the main screen (top-level nav, beside Projects). It lists
   every machine, each showing a plain-English name, online/offline, what it is allowed to
   build, and when it was last heard from.
2. Each machine row has a **Pause** control that stops it taking new work and lets its
   current work finish. Reachable: Machines panel, per row.
3. Each machine row has a **Turn off** control that stops it taking work at all, and says in
   plain English what is now waiting because of it. Reachable: Machines panel, per row.
4. Each machine row shows a **Reconnect code** control that issues a new token for that
   machine and invalidates the old one. Reachable: Machines panel, per row.
5. A machine that has not been heard from for longer than the lease shows as **Offline**
   within one minute, with the time since last contact in words ("about 4 minutes ago").

**Stalled and waiting work**
6. A job whose machine went silent shows a **Stalled** badge on its card, in plain English
   ("The machine doing this went quiet"), on the main job list without opening anything.
7. A stalled job card has a **Start it again** control that re-runs it from scratch on any
   eligible machine. Reachable: the job card.
8. A job that has been re-run three times shows as **Given up on**, with the reason, and
   does not silently re-queue. Reachable: main job list.
9. A queued job with no eligible machine online shows **Waiting for a machine** and names
   which one it needs, on the main job list.
10. A job card names **which machine built it**, always. Reachable: job card, no click.

**The record and the review**
11. Every verdict on a job card shows which model family gave it and which version it read.
    Reachable: job detail.
12. No verdict can be written by anything other than the home. Verified by test: a worker
    API call attempting to record a verdict is refused, and the refusal appears on the job.
13. A worker cannot set the version or the reviewed change. Verified by test: the home's
    computed commit id and diff are what the record holds, and a worker-supplied value is
    ignored and logged.
14. A verdict cannot be edited after it is written — including which version it reviewed.
    Verified by the regression test reproducing the exploit in risk 2.

**Money**
15. A **Spending** block shows this month's total, the cap, and today's spend per seat,
    including the voice. Reachable: main screen.
16. Starting a voice session records what it reserved. Verified: a `role='voice'` usage row
    exists after a session starts, and the voice refuses to start when the cap is reached,
    saying so in plain English.
17. No machine other than the home ever holds a paid provider key. Verified by inspection
    of the worker package: it ships with no key material and refuses to start if any is set.

**Voice and fallback**
18. The voice works from the phone against the home, tested on wifi **and** on cellular,
    with the measured time to first word recorded in the handoff.
19. When the home cannot be reached, the voice says exactly *"Chief is unavailable. Nothing
    was started."* and starts nothing. Reachable: pull the plug and try it.
20. The text channel works with the voice completely off. Reachable: the main screen, no
    microphone permission granted.
21. A dropped connection mid-answer resumes the same answer rather than re-running Chief.
    Reachable: turn airplane mode on and off mid-sentence.

**Access**
22. The app requires a paired device credential; an unpaired device on the tailnet gets
    nothing. Reachable: open the app from a tailnet device that has not been paired.
23. Pairing happens once, deliberately, and never appears as a prompt mid-conversation.
24. The gatekeeper is unreachable from the tailnet. Verified: a request to it from another
    tailnet device is refused at the network, not just at the token.
25. Arch is unreachable to every worker. Verified: a worker asking for Arch's code is
    refused, and the refusal is on the record.

**Safety of the move itself**
26. A written rollback exists — three commands that put the record back on the laptop —
    and it has been executed once, on the disposable copy, before the real cutover.
27. A restore from backup has been performed and the restored record passes
    `PRAGMA integrity_check`. Recorded in the worklog with the date.
