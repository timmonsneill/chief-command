# Task #11 — The gatekeeper

**Status:** spec, pre-build. Design-gate with Sol before writing code.
**Owner ask:** "Build the gatekeeper — the one service that holds merge, deploy and spend."

## The one-sentence version

Today, an agent that wants to merge code, deploy, or spend money simply does it. The
gatekeeper is the single service that holds those powers instead — agents *ask*, it
checks the record, it does exactly that one thing, and it refuses everything else.

## Why the database alone isn't enough

Sol's line, and it's the whole task: *"The database cannot be the jailer if the prisoner
can rewrite the jail."*

The schema guards are real and they work — but every agent process today opens the same
SQLite file with full write access. An agent that wanted to ship unreviewed work does not
need to defeat a trigger; it can insert its own approval, or write its own passing
verdict, or record a zero cost. The guards stop *mistakes*. They do not stop *reach*.

So #11 is not another guard. It is a **separation of powers**:

| Who | May do |
|---|---|
| Agents | read the DB; write their own work and events; **ask** for irreversible things |
| Gatekeeper | the only holder of merge / deploy / spend; the only writer of approvals and budget |

## Where it is now (grounded in the code)

- `harness/dispatch.py::ship()` sets status to `shipped`. Anything that imports dispatch
  can call it. Guards decide, but the *call* is available to everyone.
- `harness/db/jobs.py::record_usage()` — spend is recorded by whoever is spending. The
  cap trigger refuses a breach, which is real, but the same process could write a 0.
- `harness/gauntlet.py::_reserve_review_budget()` reserves before a call — the right
  shape, in the wrong place. It should be *asking the gatekeeper*, not writing usage.
- The `approvals` table already exists with guards (`guard_no_approval_is_born_granted`,
  `guard_an_approval_is_used_once`, `guard_no_approval_without_readback`). **Nobody
  grants approvals today.** That is the gatekeeper's job.
- Migrations 001–006 harden the record. None of them constrain *who holds the pen*.

## What to build

1. **A separate process** on loopback (never the tailnet, never public), with its own
   database connection. It is the only component whose sqlite handle is writable for
   `approvals`, `usage`, and the `shipped` transition.
2. **Three verbs, and nothing else.** `merge(job)`, `deploy(target)`, `spend(seat, cents)`.
   No generic "run this". A gatekeeper that can be asked to run arbitrary commands is
   just a shell with extra steps.
3. **It checks the record itself** — it never trusts the asker's account of the record.
   For `merge`: the panel passed, on the current version, with the family floor met, no
   unresolved escalation. It re-reads all of that.
4. **Reserve before spend.** `spend` is a reservation with a receipt; the provider call
   happens after, and reconciles against the receipt.
5. **Refuse loudly and legibly.** Every refusal is an event on the job in plain English,
   because a silent refusal reads exactly like a thing that never got asked.
6. **Deploys stay manual** (Decision D — Neill gates production deploys). The gatekeeper
   *can* deploy; it will only do so on a recorded owner approval, tapped, not spoken.

## The part that makes it real (and the part that will be argued)

**Agents must lose write access to the safety tables.** Until that happens, the
gatekeeper is a polite convention — a door next to a hole in the wall. SQLite has no
per-table permissions, so this needs one of:

- **(a) A second OS user** owning the DB file; agents connect through the gatekeeper's
  socket for anything privileged and hold a read-only handle otherwise. Strongest.
  Matches the arch doc's "credential absence via separate user" answer.
- **(b) Split databases** — a `safety.db` only the gatekeeper can write, and a `work.db`
  agents own. Weaker (an agent running as the same user can still open the file), but
  far less machinery.

Recommend **(a)**, staged: build the service and the three verbs first (useful
immediately), then move the file ownership. Do not let the ownership move block the
service — but do not call #11 done until it has happened, because without it the
prisoner still has the pen.

## Risks to pressure-test (for Sol)

- **The gatekeeper becomes a shell.** Any drift toward a generic "do this for me" verb
  destroys the whole point. What stops that drift?
- **Trusting the asker.** Anywhere the gatekeeper takes a job id and believes something
  the caller said about it is a hole.
- **Confused deputy.** An agent asks the gatekeeper to merge job 7 when it was working on
  job 9. How does the gatekeeper know who is asking, and about what?
- **Availability.** If the gatekeeper is down, everything irreversible stops. That is the
  correct failure — verify nothing falls back to the old direct path when it's unreachable
  (that fallback would be the bypass).
- **Prompt injection.** The agent is authorized to read the repo; a hostile file can tell
  it to ask for a merge. The gatekeeper's answer must not depend on the agent's reasoning
  at all — only on the record.
- **The emergency stop (#13)** must be able to halt the gatekeeper, and must not itself
  depend on the gatekeeper being alive.

## Out of scope

- Real phone-app auth (separate; "on the tailnet" is not proof of being Neill).
- The Arch EMR repo — out of scope entirely (Decision C, PHI). Grok does not touch it
  (owner decision, 2026-07-21).
