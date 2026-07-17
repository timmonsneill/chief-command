# Chief Command — Architecture of Record (v3, 2026-07-17)

Seven adversarial reviews got here. Rounds 4–6 proved something important, then
over-proved it: because earlier drafts gave the machine the power to deploy itself to
production, Sol correctly demanded the full security apparatus of an autonomous release
authority — signing-key governance, independent build reproduction, multi-person
administrator custody — an architecture a one-person project cannot build or operate,
with demands deepening every round rather than converging.

The owner ended the spiral with the observation that resets the whole frame: **he ships
agent-written code from his terminal every day.** Claude Code and Codex writing,
reviewing, and pushing code under standard tooling is normal practice for millions of
developers. The previous drafts weren't wrong about the attacks; they were wrong about
the scope. This version fixes the scope.

Plain English throughout, because the owner cannot read code.

---

## OWNER DECISION D (2026-07-17) — the proportionality reset

**The benchmark: this harness must be no more dangerous than the owner running Claude
Code in his terminal today — and safer where safety is cheap.** It is a *development*
harness. It is not, and does not claim to be, an autonomous production-release
authority.

Concretely:

- **"Ship" means landing code on the owner's own repos** — the thing git makes
  reversible, and the thing agents already do from his terminal daily. After the
  gauntlet passes, work merges autonomously by default, exactly like his terminal
  workflow — with GitHub branch protection blocking the one irreversible git action
  (rewriting history) on repos where it's enabled.
- **Deploying to a live production service is out of scope for agents.** Where a
  project has a real production deployment, that step stays a manual owner action, as
  it is today. No deployer identity, no signing chain, no production kill switch —
  because there is no machine release path to protect.
- **Per-repo dial.** The owner can mark any repo (or change class) as
  *tap-to-merge* — the gauntlet passes, the PR waits for his one-tap merge on GitHub,
  which branch protection enforces server-side. Default for ordinary personal repos is
  autonomous merge; the dial exists for anything he decides matters more.
- **The PHI-bearing EMR remains fully out of scope** (Decision C) — not a dial
  setting; it is absent from the harness's world entirely.

This supersedes the round-5/6 release-chain design (preserved in git history and in
`SOL_VERDICT_ROUND5/6`). The reviewer-seat model (Decision A) applied to an autonomous
release authority; with release authority removed, it collapses to something the owner
already does: deciding what merges into his own projects. Its useful residue survives
as the per-repo dial and the standing rule that the gauntlet runs before anything
lands.

### What Sol is asked to do now

Not to certify that no attack path exists — Sol itself proved that question has no
finite answer for any system that runs powerful agents. Sol's job is: **verify the
design honestly does what it claims, verify the claims match the code as built, and
name the residual risks in plain English so the owner accepts them knowingly** — the
same risks he already accepts, unnamed, every time he opens his terminal.

---

## The one-sentence shape

**OpenClaw drives the car. Agents work in a sealed workspace with no keys, everything
they do is recorded and reviewed, work lands on the owner's repos like it does from his
terminal — and the few genuinely irreversible things (history rewrite, production
deploys, real money, patient data) sit behind doors no agent can open.**

---

## The threat model

Not the owner. The real threats, in order:

1. **An agent doing something nobody asked for.**
2. **Prompt injection** — poisoned text in a file, dependency, issue, or web page turns
   a fooled-but-authorized agent into an attacker's hands. Load-bearing, because the
   agent is *allowed* to read the repo.
3. **Mishearing** — road noise, a passenger, a bad transcript.
4. **Code that passes honest review** (the round-4 finding, still true at this scope):
   a subtle payload can survive the gauntlet and land in a repo. What contains it here
   is not review but *consequence limits*: it lands in a git-reversible repo, in a
   project with no customer data, with no credentials to steal in the workspace, no
   production to reach, and a record of exactly which job produced it.

## What protects against those threats — the doors, honestly labeled

| Protection | Kind | What it actually stops |
|---|---|---|
| GitHub branch protection | Server-side boundary | History rewrite and direct pushes to protected branches; enforces tap-to-merge where dialed |
| Credentials absent from agent reach | Absence | Exfiltration and misuse of what isn't there |
| The sealed workspace (VM) | Kernel/hypervisor boundary | Agents touching the owner's files, business, or network |
| Brokered GitHub/provider access | Narrow authority | Agents holding reusable keys; runaway spend at brokered providers |
| Provider-side hard caps | External boundary | Spend past the cap, where the provider offers a real one |
| The gauntlet + job record | Quality gate + audit | Bad work landing *unexamined*; "what did the overnight run do?" going unanswerable |
| Git itself | Reversibility | Makes a bad merge a fixable event, not a disaster |

The table is the design's honesty mechanism: the first five are boundaries; the
gauntlet is explicitly *not* one (round 4's lesson); git reversibility is what makes
quality-gate failures survivable.

---

## Where agents run — the sealed workspace

**DECIDED (Neill, 2026-07-15): a dedicated VM on this Mac**, migratable to separate
hardware by config. The profile keeps everything cheap-and-real from round 6; the
honest caveat stands — this protects against ordinary containment failures, and the
owner knowingly accepts the residual risk of hostile execution sharing his business
Mac:

- No shared folders, clipboard, or device passthrough; no credential connections; no
  host-management channel; no Tailscale identity inside; no inbound connections.
- Outgoing traffic only through the named brokers, enforced by a host-side firewall the
  guest cannot reconfigure.
- Fixed-size disk; hard CPU, memory, process, time, and network limits — an agent must
  not be able to brick the Mac by exhaustion. Disk-full/memory/flood/crash responses
  tested.
- Isolated clones per agent — never shared working copies (a git worktree shares
  history and hooks; a clone doesn't).
- The profile covers every runner (Claude Code, Codex, Ollama), child process, hook,
  MCP server, scheduled job, retry, and recovery path. Local MCP servers run inside;
  remote MCP servers hold their own credentials and are individually allowed with
  stated authority, or not connected.
- Kernel sandbox on, strict, as the second layer inside the VM. Startup checks detect
  drift; the VM is the wall.
- Prompt security updates for the Mac and VM software; snapshots and crash dumps
  treated as sensitive.

## No keys in agent hands — the brokers

- **GitHub broker.** Agents don't hold GitHub credentials. They hand finished work to
  the broker, which pushes the branch, opens the PR, and — on repos dialed to
  autonomous — merges after the gauntlet passes. Its token has the narrowest scope
  GitHub allows; repository automation on unreviewed branches runs with no valuable
  secrets or not at all. Repos dialed to tap-to-merge are enforced by branch
  protection, not by the broker's good behavior.
- **Provider broker.** Agent runners get no reusable API or subscription credentials;
  the broker applies per-job budget *before* each call and is the only path to a
  provider. Hard provider-side caps wherever offered — stated honestly: only the
  provider's own cap is a real financial boundary; elsewhere the broker is a strong
  mitigation. Provider *responses* are untrusted input.
- Subscription runners that technically require login material inside their
  environment are treated as holding a credential and narrowed accordingly — named,
  not hidden.

## The gauntlet and the record

- Every dispatched job gets a row; no silent work. Reviews, verdicts, and evidence
  bind to the exact commit they examined (the round-3 lesson: a verdict that outlives
  the code it reviewed is worthless). A fail condemns a version, not the job forever;
  verdicts can't be deleted or edited pass-ward (enforced in the schema, already
  built and regression-tested).
- Local model output never lands without higher-tier review (schema guard, already
  enforced).
- The record is the *audit*, not the jailer — agents have no write access to it, and
  the doors above don't consult it to work. A corrupted record misleads; it doesn't
  unlock anything.

## The voice + Chief layer

Unchanged from round 5's design — the settled rules hold:

- Voice is a telephone; it classifies nothing. Every input door (voice, text app, web)
  goes through one deterministic turn controller: only committed instructions
  dispatch; numbered turns so a stale answer can't override a newer correction.
- Critical warnings, amounts, and confirmations delivered from fixed stored text —
  never model-paraphrased. Chief confirms its own read-back; approval attaches to the
  read-back.
- A hardwired emergency stop that doesn't route through Chief, reachable while
  driving; honest semantics — it reports confirmed-dead versus unknown, and kills
  jobs (there is no production workload to kill at this scope).
- Separate cancellation for speech, Chief's thought, queued work, running builders.
  Latency guard: hard timeout → "I'm struggling, nothing's started," spoken only after
  the record confirms nothing started.
- No dangerous approvals by voice; the per-repo tap-to-merge tap happens on GitHub,
  stopped, authenticated as a person (Tailscale is network privacy, not identity —
  the web app authenticates the user).
- Ambient speakers stated plainly as an open risk: voice can create work and spend
  from the routine budget; it can never open a door.
- Chief is `gpt-5.6-terra` streaming (~1.4s), escalating to `gpt-5.6-sol` on pushback;
  Chief is not a security boundary. Realistic driving test set before trusting the
  voice beyond calm, stopped conversation.

## Resilience

- Off-machine backups of the record, config, and memory, with tested restores; a
  restore must not revive revoked credentials or dead paths.
- Crash recovery reconciles in-progress jobs on startup; duplicate protection so a
  retry can't double-start a job.
- Secret lifecycle: rotation, expiration, revocation, device-loss response.
- Version-pin local software; deliberate, sandbox-first upgrades (OpenClaw pinned
  `2026.6.11`); security updates for the Mac and VM software are not deferred. Remote
  model/provider behavior can only be version-selected and monitored — said plainly.

## Decommissioning

Before the new paths are trusted, the old ones die, verified: direct runner launch
from dispatch, harness-written verdicts, the old shipping path, the provider key in the
web service, model-based speech-turn detection. v1 `backend/`/`frontend/` stay dead.

---

## Standing constraints (unchanged)

- Text works fully without voice. Voice is a skin.
- Tailscale only, nothing public — plus real user authentication on top.
- Never OpenClaw exec policy `full`/YOLO.
- No Google/Gemini. Providers referenced by seat, never by name.
- Grok CLI does not touch the Arch EMR repo until the upload allegation is disproven.

---

## The acceptance test — proportionate and finite

Sol reviews this document against the claims table and residual-risk list, then the
build proves, as executed attacks:

1. An agent in the VM cannot read owner files, reach the host/tailnet/local network,
   or find any reusable credential.
2. Resource attacks (disk fill, fork bomb, memory, network flood) leave the Mac
   usable.
3. An agent cannot push, merge, or spend except through its broker; broker tokens
   can't rewrite history or touch protected repos' merge rules.
4. Tap-to-merge repos: nothing merges without the owner's authenticated tap —
   enforced by GitHub with the broker's token unable to bypass it.
5. Spend stops at provider-side caps under concurrent and retried requests.
6. The record refuses agent writes; verdicts can't be deleted or edited pass-ward;
   evidence binds to the commit it examined.
7. Partial speech, reconnection, rollover, or a stale queued answer cannot dispatch
   or override a newer correction; the emergency stop reports truthfully.
8. Every decommissioned legacy path is confirmed dead.
9. Re-run on every upgrade of anything pinned.

**Residual risks, named for the owner to accept knowingly** (the same ones his
terminal use already carries, plus the always-on difference):

- A subtle payload can pass honest review and land in a repo — contained by
  reversibility, absence of credentials/production/customer data in scope, and the
  audit trail; not prevented.
- Source code and project content necessarily flow to the model providers the seats
  use; an injected instruction could deliberately include private project material in
  a legitimate request. Scope (no EMR, no customer data) is the mitigation.
- An always-on harness acts without the owner watching, unlike his terminal; the
  record, budgets, and voice status reports are the compensations.
- Same-Mac VM escape via hypervisor bug: mitigated by patching; migration to separate
  hardware is a config change.
- A compromised or spoofed voice/text session can create work and burn routine budget
  (never open doors).

---

## ✅ OWNER DECISIONS — record

- **A (2026-07-15/16):** made under the autonomous-release framing; superseded by D.
  Its residue: the gauntlet always runs, and the per-repo tap-to-merge dial exists.
- **B (2026-07-15):** agents run in a dedicated VM on this Mac; residual same-Mac risk
  accepted; migration stays a config change.
- **C (2026-07-15):** the PHI-bearing EMR is out of scope; revisit requires
  attack-tested medical-data egress controls first.
- **D (2026-07-17):** the proportionality reset. The harness is a development harness
  benchmarked against the owner's daily terminal use of coding agents; agents merge to
  ordinary repos autonomously after the gauntlet; production deployment stays manual;
  irreversible powers stay behind server-side and physical doors; Sol names residual
  risks rather than certifying impossibility.
