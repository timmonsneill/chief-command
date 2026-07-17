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

**The benchmark, stated the honest way (Sol round 7):** compared to the owner running
Claude Code in his terminal today, this harness is **safer in blast radius** — the VM,
credential absence, and hard caps protect the Mac, the secrets, production, and the
wallet better than a bare terminal does — and **more autonomous**: it runs unattended,
takes voice-triggered work, operates at overnight scale, and reads more injectable
content than a supervised session. The extra autonomy risk lands almost entirely on
*unwanted-but-source-reversible repository changes and routine spend*, and the owner
accepts that explicitly (Decision E). It is a *development* harness. It is not, and
does not claim to be, an autonomous production-release authority.

Concretely:

- **"Ship" means landing code on the owner's own repos** — the thing git makes
  reversible, and the thing agents already do from his terminal daily. After the
  gauntlet passes, work merges autonomously by default, exactly like his terminal
  workflow — with GitHub branch protection blocking the one irreversible git action
  (rewriting history) on repos where it's enabled.
- **Autonomous merge is only available to ADMITTED repos.** Admission is a recorded,
  per-repo decision proving that **merging does nothing but change source**: merging
  does not deploy a site or service; the repo is not itself the live product; no other
  machine auto-pulls its main branch; no push/PR publishes a package, updates
  infrastructure, alters scheduled work, or contacts outside systems; no repository
  automation holds production secrets; no automation runs agent-written code before
  the gauntlet finishes. Any "yes" → the automation is disabled, the repo is excluded,
  or the owner's authenticated tap is explicitly designated as the manual deploy step.
  Projects are an explicit **allowlist** — a repo that was never admitted simply does
  not exist to the harness, and **the EMR is structurally refused**: adding it
  requires a new recorded owner decision, not a config edit.
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
| Git itself | Reversibility of SOURCE | Makes a bad merge usually recoverable as a source change |

The table is the design's honesty mechanism: the first five are boundaries; the
gauntlet is explicitly *not* one (round 4's lesson). And git's reversibility is stated
precisely: **git reverses source, not consequences.** It cannot unpublish a leaked
secret, undo automation that already ran, retract a downloaded package, or repair
damaged live data — which is exactly why repos where a merge *has* consequences are
refused admission to autonomous merge.

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
  MCP server, scheduled job, retry, and recovery path. Local MCP servers run inside.
  Connected tools are governed by an **exclusion rule, not per-tool documentation**:
  no connected tool may hold production credentials, cloud administration, publishing
  authority, repository administration, branch-rule bypass, live-database access, or
  patient data. A tool that would recreate the removed release authority is refused,
  period.
- Kernel sandbox on, strict, as the second layer inside the VM. Startup checks detect
  drift; the VM is the wall.
- Prompt security updates for the Mac and VM software; snapshots and crash dumps
  treated as sensitive.

## No keys in agent hands — the brokers

- **GitHub broker.** Agents don't hold GitHub credentials. They hand finished work to
  the broker, which pushes the branch, opens the PR, and — on admitted repos — merges
  after the gauntlet passes. Its token has the narrowest scope GitHub allows, and the
  broker is **unable** (not just untasked) to change repository settings, branch
  rules, releases, packages, deployment environments, or repos outside the allowlist.
  Because pushing a branch can itself start repository automation *before* the
  gauntlet, **the broker refuses the push** unless that automation is known (from the
  admission record) to hold no valuable credentials, no broad repo authority, and no
  production effect. Repos dialed to tap-to-merge are enforced by branch protection,
  not by the broker's good behavior.
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
  verdicts can't be deleted or edited pass-ward. **Honest status (Sol round 7): these
  rules are hardened in the schema source but the LIVE database still runs the old,
  weaker rules — startup never migrated it.** No credit is claimed until the live
  record is migrated and re-attacked; the same applies to review-to-version binding,
  which the current build does not yet enforce.
- Local model output never lands without higher-tier review (same caveat: enforced in
  schema source; live migration required before credit).
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
- **OWNER DECISION E (2026-07-17): voice-originated jobs merge autonomously too.**
  Sol offered a choice — an authenticated non-voice "yes I really asked for that" tap
  before voice-originated work merges, or explicit acceptance of the risk that a
  misheard or ambient-speech instruction produces merged (source-reversible, admitted-
  repo-only) changes nobody wanted. **Neill chose fully hands-free and accepts that
  risk knowingly.** The blast radius of that acceptance is bounded by admission rules:
  a wrong merge changes source in a repo where merges have no consequences beyond
  source.
- Ambient speakers stated plainly as an open risk: voice can create work, spend from
  the routine budget, and (per Decision E) cause unwanted-but-reversible merges in
  admitted repos; it can never open a door.
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

Before the new paths are trusted, the old ones die, verified — "v1 is dead" is not
sufficient while the paths remain runnable (Sol round 7 found these live in the repo
today):

- Direct runner launch from dispatch, and **every other direct host launch** — the
  current voice/text paths start Codex/Claude directly on the business Mac, outside
  the record, brokers, and VM, which quietly restores the authority Decision D
  removed.
- Harness-written verdicts; the old autonomous shipping path.
- The provider key held in the web service; model-based speech-turn detection.
- **The live production deploy command (Netlify), the public Cloudflare tunnel config
  and the startup machinery that opens it, and the installer that creates a default
  password** — removed or physically disabled, not just unused, because agents working
  on this repo can read and edit anything runnable.
- The live database migrated to the hardened rules (the old, weaker triggers are what
  actually run today).
- v1 `backend/`/`frontend/` stay dead.

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
   can't rewrite history, change repo settings or branch rules, publish releases or
   packages, or touch any repo outside the allowlist.
4. Tap-to-merge repos: nothing merges without the owner's authenticated tap —
   enforced by GitHub with the broker's token unable to bypass it.
4b. Admission holds: a repo whose merge deploys, publishes, or triggers privileged
   automation is refused autonomous merge; a push whose branch automation would run
   with valuable credentials is refused by the broker; the EMR cannot be added by
   config alone.
5. Spend stops at provider-side caps under concurrent and retried requests.
6. The record refuses agent writes; verdicts can't be deleted or edited pass-ward;
   evidence binds to the commit it examined.
7. Partial speech, reconnection, rollover, or a stale queued answer cannot dispatch
   or override a newer correction; the emergency stop reports truthfully.
8. Every decommissioned legacy path is confirmed dead.
9. Re-run on every upgrade of anything pinned.

**Residual risks, named for the owner to accept knowingly** (expanded per Sol round
7 — these are accepted, not contained):

- A subtle payload can pass honest review and land in a repo — contained by source
  reversibility, admission rules, absence of credentials/production/customer data in
  scope, and the audit trail; not prevented. **If the owner later manually deploys
  that code, it can harm production — the manual step stops autonomous release; it
  does not make the code correct.**
- **Reverting source cannot undo consequences**: a disclosed secret, corrupted live
  data, a published package, or an external action already taken stays done.
- Source code and project content necessarily flow to the model providers the seats
  use; an injected instruction could deliberately include private project material in
  a legitimate request — and encoded exfiltration through an allowed channel cannot be
  reliably scanned away. Scope (no EMR, no customer data) is the mitigation.
  **Continuous voice additionally sends the owner's speech — and possibly nearby
  conversation — to the voice provider.**
- An always-on harness acts without the owner watching; misheard or ambient speech can
  start work and (Decision E) merge it. The record, budgets, admission rules, and
  voice status reports are the compensations.
- **Many individually harmless merges can accumulate into a dangerous whole that no
  single review sees** — the audit trail is the recovery tool, not a preventer.
- A bug or compromise in a broker, a GitHub account, a model provider, a connected
  tool, or a software update could grant more authority than intended; the record can
  be incomplete or written falsely by a compromised trusted service.
- Same-Mac VM escape via hypervisor bug: mitigated by patching; migration to separate
  hardware is a config change.
- A compromised or spoofed voice/text session can create work and burn routine budget
  (never open doors).
- **Availability is not guaranteed**: provider outages, exhausted subscription
  allowances, queue flooding, or a failed broker can stop the harness without damage.
- Agent-written code can carry licensing or ownership problems even when it works.

---

## ✅ OWNER DECISIONS — record

- **A (2026-07-15/16):** made under the autonomous-release framing; superseded by D.
  Its residue: the gauntlet always runs, and the per-repo tap-to-merge dial exists.
- **B (2026-07-15):** agents run in a dedicated VM on this Mac; residual same-Mac risk
  accepted; migration stays a config change.
- **C (2026-07-15):** the PHI-bearing EMR is out of scope; revisit requires
  attack-tested medical-data egress controls first.
- **D (2026-07-17):** the proportionality reset. The harness is a development harness
  benchmarked honestly against the owner's daily terminal use of coding agents (safer
  in blast radius, more autonomous); agents merge to ADMITTED repos autonomously after
  the gauntlet; production deployment stays manual; irreversible powers stay behind
  server-side and physical doors; Sol names residual risks rather than certifying
  impossibility.
- **E (2026-07-17):** voice-originated jobs merge autonomously like typed ones. Neill
  knowingly accepts that misheard or ambient speech can produce unwanted,
  source-reversible merges in admitted repos, rather than adding a confirm-intent tap.
