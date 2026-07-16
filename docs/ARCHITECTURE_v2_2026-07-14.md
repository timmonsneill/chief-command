# Chief Command — Architecture of Record (revised 2026-07-16, after Sol round 5)

Written after six adversarial cross-family reviews — 9 flaws, then 18, then a hollow
core, then 3 verified schema holes, then round 4's "honest pass" finding, then round 5's
verdict that owner sign-off is not technical review and that danger must be classified by
authority, not filenames. This is the corrected design. **Almost nothing here is built**
— this is the spec Sol must approve before the rest is written.

Plain English throughout, because the owner cannot read code.

All owner decisions are now made (Neill, 2026-07-15/16). They are recorded inline and
collected at the end.

---

## The one-sentence shape

**OpenClaw drives the car. A small set of separated services holds the keys — no one of
them holds them all. Everything the agents produce is treated as untrusted, even after
review: it ships only as a signed package through one exclusive path, into a production
environment that limits what it can touch, watched after it lands.**

---

## The core claim, stated honestly

Round 4 killed *"a missed checkpoint is harmless."* Round 5 sharpened why:

> Model review is a QUALITY gate, not a containment boundary. An agent — fooled by prompt
> injection, or simply subtle — can put a hidden trigger in an ordinary application file,
> the reviewers can honestly pass it, every record can be genuinely bound to the right
> package, and the release path can deploy it exactly as designed. In production, the
> payload then holds whatever authority that application legitimately has.

Permission to *place* a package in production is different from the authority the package
*receives after it starts*. So the claim this design defends is:

> **A skipped checkpoint cannot directly obtain release authority. For code that passes
> review, the harm is limited by four things: danger-by-authority classification that
> fails closed into human review, qualified independent human inspection of dangerous
> changes, enumerated production authority for every deployed component, and post-release
> monitoring with a kill switch.**

Where a sentence below promises a guarantee, it names the real boundary providing it —
and says plainly where the honest answer is "this reduces harm" rather than "this
prevents it."

---

## The threat model

Not the owner. The real threats, in order:

1. **An agent doing something nobody asked for.**
2. **Prompt injection** — poisoned text in a file, dependency, issue, or web page turns a
   fooled-but-authorized agent into an attacker's hands. The load-bearing threat, because
   the agent is *allowed* to read the repo.
3. **Mishearing** — road noise, a passenger, a bad transcript.
4. **Malicious code that passes honest review** (round 4) — a payload walked through the
   front door of gates that are working perfectly, gaining the deployed application's own
   production authority (round 5).

---

## The trusted computing base — named honestly

The things that must be trusted: the release-path services (below), the harness, its web
UI, model-response handling, the dispatch channel, evidence capture, OpenClaw
integration, and the OS accounts they run under. The design's job is to make this base
small and *separated* — not to pretend it is one tiny component. Three separations do the
heavy lifting:

1. **Agents are outside the trusted base entirely** — separate VM, separate identity, no
   keys.
2. **The record-writer is separated from the harness** — release facts are written only
   by the release-path services; the harness submits authenticated observations through a
   narrow interface.
3. **The dangerous powers are separated from each other** (see "Separated powers") — so
   one compromise cannot take merge, signing, deploy, spend, and production data at once.

---

## The two human roles — OWNER DECISION A, revised after round 5

Round 4's decision A said high-authority changes wait for Neill's written sign-off. Sol's
round-5 correction is accepted: **Neill cannot read code, so his approval is a business
authorization, not a technical inspection.** A fooled reviewer writes a fooling
explanation. The two roles are therefore distinct:

- **The qualified technical reviewer** — an independent human who can actually read the
  change — inspects every high-authority change: the exact patch, its dependency and
  build effects, against the exact package digest that will ship. Their approval is
  recorded against that digest.
- **The owner** separately authorizes the business decision ("yes, I want this change"),
  in plain English, through an authenticated written approval (below). His approval never
  substitutes for the technical inspection.

**DECIDED (Neill, 2026-07-16): the technical reviewer seat is currently EMPTY, and the
system fails closed.** High-authority changes queue as pending — visible, explained in
plain English, never released — until a qualified reviewer exists (a contractor, a
colleague, a future hire). Naming one later fills the seat without redesign. Everything
outside the high-authority classes still ships autonomously through the model gauntlet.

### Authenticated approval — proving it was actually Neill

A tap on a web page proves someone tapped. Owner approval is:

- bound to the owner's device using a **passkey or biometric confirmation** (device-bound
  hardware credential, not a session cookie),
- **single-use, short-lived, and tied to the exact package digest and the exact action**
  (repo, version, environment),
- consumed atomically before the action, void if the package, `main`, or the request
  changes,
- and never grantable by voice (settled rule).

Tailscale is network privacy, not user authentication; the web app additionally
authenticates the person, not just the device's network.

### Exact delivery of anything that matters

Critical warnings, amounts, consequences, confirmations, and failure reports are played
or displayed **from fixed stored text — never passed through a model that could
paraphrase them.** Telling the voice model "don't paraphrase" is a wish; fixed playback
is the mechanism.

---

## Danger classification — by AUTHORITY, not by filenames

Round 5's second crux. A list of "dangerous files" is trivially evaded (payloads in build
scripts, lockfile-only dependency redirects, transitive dependency swaps, checked-in
generated or minified code, templates that become code at build time, config that becomes
behavior at runtime, dormant loaders activated by later "data" changes, authority split
across routine-looking submissions, cumulative drift). So classification starts from
**the production authority of the thing being changed**:

- **High-authority by default:** any change to a component that, in production, can read
  private data, write important records, use secrets, move money, administer users, or
  make broad outgoing connections. What the component *can do when running* decides — not
  which file the diff touched.
- **Autonomous release is permitted only for components whose enumerated production
  authority is already so narrow that malicious code inside them cannot cause serious
  harm** (see "Production containment").
- **Fail-closed intake:** unknown file types, binaries, compressed/minified/generated
  output, dependency changes of any kind (including lockfile-only and transitive), build
  and packaging behavior, configuration that is interpreted at runtime (routing rules,
  prompts, feature flags, scheduled-task definitions, templates, policy files, database
  statements), links, sub-project pointers, and large-file pointers **all classify into
  human review.** Anything the classifier cannot positively identify as harmless is
  high-authority.
- **Cumulative, not per-diff:** classification considers everything changed **since the
  last qualified human review** of that component, so five small routine releases cannot
  quietly assemble high authority.
- **The classifier itself is high-authority:** changes to it always require independent
  technical review.
- The classifier is deterministic (derived from the change and the component's enumerated
  authority — never a model's self-report), and deterministic mistakes are still
  mistakes: the enumeration of component authority is reviewed by the technical reviewer
  when the seat is filled, and until then components without an enumeration are treated
  as high-authority.

**Stated plainly, as Sol required: this reduces how much ships autonomously. That is the
unavoidable price of the round-4 finding, and the owner has accepted it.**

---

## Where agents run — the VM, complete profile

**DECIDED (Neill, 2026-07-15): a dedicated VM on this Mac** — with the honest caveat Sol
required: this protects against *ordinary containment failures*; it does not make hostile
execution on the owner's business Mac risk-free. The VM and the business share one
physical failure domain; a separate machine is materially safer, and the design stays
hardware-agnostic so that migration is a config change. The owner accepts the residual
risk knowingly.

The profile — all of it, not a subset:

- **No sharing at all**: no shared folders (not just "not the home folder" — none), no
  clipboard/drag-and-drop, no USB/camera/microphone/Bluetooth passthrough, no
  host-management channel, no credential connections (SSH agent, git credential helper,
  cloud CLI config).
- **No Tailscale identity inside the VM.** No inbound connections to the VM at all.
- **Outgoing traffic only through the named brokers** (below). No direct reach to the
  host, the local network, the tailnet, the router, printers, storage devices, other
  business machines, or production. Enforced by a **host-side firewall that remains
  effective even if guest configuration changes** — the guest cannot reconfigure its own
  cage.
- **Fixed resources**: a fixed-size (not dynamically expanding) virtual disk, hard CPU,
  memory, process-count, execution-time, and network-volume limits. An agent can brick a
  Mac by exhaustion; a spend cap stops none of that. Disk-full, memory-pressure,
  network-flood, and VM-crash responses are specified and tested.
- **Isolated clones or storage snapshots, never shared working copies.** A standard git
  worktree shares the underlying repo — history, branches, hooks, objects, settings. Each
  agent gets a separate clone or copy-on-write snapshot with no path back to the real
  repo.
- **The containment profile covers every runner** (Claude Code, Codex, Ollama), every
  child process, every MCP server, every hook, every scheduled job, every retry and
  recovery path. Local MCP servers run inside the VM; **remote MCP servers hold their own
  credentials and act where THEY run** — each is either brokered with exact authority or
  not connected at all.
- **The kernel sandbox on, strict, as a second layer inside the VM**: deny credential
  paths, scrub credential env vars, narrow domain allow-list, no unsandboxed commands.
  Noted honestly: a startup check that asserts this is itself configuration — it detects
  drift, it is not independent enforcement. The VM boundary is the real wall.
- **Prompt security updates for the Mac and the VM software** (see "Update policy" — a
  frozen hypervisor is its own risk).
- **VM snapshots, crash dumps, and backups are treated as sensitive** — they can retain
  secrets and private source, so they live under the same protection as the things they
  might contain.

---

## No keys in agent hands — the brokers, with exact authority

Agents hold **no reusable credentials of any kind**. Each broker has a written, exact
authority and hostile-input rules — a broker that "forwards requests" is just a longer
credential.

- **The GitHub broker.** Authority: accept a *restricted patch* (below) and create a
  branch and PR from it. Nothing else — no merges, no tag/release/automation/package/
  settings writes. Hostile-input rules: reject binaries and unidentifiable content into
  protected areas, reject sub-project pointers and large-file pointers unless explicitly
  expected, reject path escapes, size limits, no automation-file changes without
  high-authority classification. **Branch creation and PRs can trigger automation before
  any merge** — so repository automation runs with no valuable secrets on unreviewed
  code, or does not run at all. GitHub protection covers branches, tags, automation,
  packages, releases, environments, and admin bypass — not just `main`. Third-party
  GitHub apps with write access are treated as alternate authority and removed or
  narrowed.
- **The provider broker.** Agent runners get no reusable subscription or API credentials;
  the broker holds them, applies per-job limits *before* every call (reserving budget
  before spending, correct under concurrent requests), and is the only path to any model
  provider. Hard provider-side caps for every provider that offers one — stated honestly:
  **only a provider's own hard stop is a real financial boundary**; where a provider
  offers none (subscriptions), the broker's gating plus monitoring is a mitigation, not a
  boundary. A provider's *response* is untrusted input — it can carry a poisoned
  instruction back in.
- The honest note on subscription runners: a coding runner needs *some* auth material to
  exist somewhere. It lives with the broker outside the VM, sessions are established
  through the broker, and this unresolved tension is named rather than hidden: if a
  runner technically requires login material inside its environment, that runner is
  treated as holding a credential and its environment is narrowed accordingly.

---

## The release chain — one exclusive, signed path

### Separated powers

Round 5: one small service holding merge + deploy + spend + production access is a single
point of total compromise. The powers are split into separately-credentialed components,
each with the smallest identity that works:

- **The merge caller** — the only identity that can merge a PR.
- **The clean builder** — builds the package; holds no deploy or spend credentials.
- **The signer** — signs package digests; its key does nothing else.
- **The deployer** — delivers a signed package to production; cannot merge, sign, or
  spend.
- **The spend broker** — already separate (above).
- **The record-writer** — sole writer of verdicts, approvals, evidence provenance, and
  released status; the harness submits observations through a narrow authenticated
  interface. "Sole writer" protects who writes the record, not whether an observation is
  true — truth comes from the binding rules below.

No agent can reach any of these: no general network address, local OS-account-checked
channels only, fixed narrow actions with exact parameters, single-use request numbers,
approvals consumed atomically, rejection if anything changes mid-operation. Requests come
from a **deterministic controller deriving them from protected state — never from
model-written text.** None of these components ever builds, installs, tests, or executes
agent-produced code beside its credentials.

### Safe patch intake

Agents submit **a restricted patch — never a built package.** The intake accepts a fixed
format; rejects hostile paths, links, unexpected binaries, repository indirection, and
oversized submissions; and records the patch against the requesting job.

### The trusted builder and the package

A clean builder combines the proposed change with current `main` in a contained
environment, resolves dependencies from **pinned digests, not floating names**, produces
a reproducible package, and records its **signed origin** (what source, what base, what
builder) in an **immutable package store**. Every review, test run, screenshot, and
approval binds to that package digest — a real screenshot from the wrong build, a stale
process, or a test that never exercised the dangerous path is exactly what binding
exists to kill.

### Production verifies — the path is exclusive

**Production accepts only packages whose signature and digest verify against the signed
chain.** This is enforced at the production end, so the claim "all releases go through
the service" stops being a request and becomes a property. Every alternate route is
disabled, not ignored: old deploy scripts, automation identities, admin bypasses, cloud
schedulers, platform consoles with deploy rights, and **the current harness's own legacy
paths** (see "Decommissioning").

### Rollout, monitoring, kill, rollback

Deployment is not the end of custody:

- **Limited first rollout** with health checks and automatic halt.
- **Data backup before destructive changes.**
- **Independent post-release monitoring** — unusual data access, unexpected outgoing
  traffic, money movement, secret access, unexpected background activity — watching the
  *behavior* of what shipped, because review already passed it.
- **A production kill switch** that stops a deployed component — distinct from the job
  emergency stop, which only kills agent work.
- **Rollback that cannot revive an unsafe package** (a rolled-back-to version must itself
  still verify against the chain and not reintroduce a withdrawn package).

---

## Production containment — enumerating what deployed code may do

The missing boundary round 5 named: deploy permission is not runtime authority. For every
deployed component, the design enumerates **exactly which data it can read, which records
it can write, which secrets it can use, which destinations it can contact, and what it
can spend.** That enumeration is:

- the input to danger classification (a component with narrow authority can ship
  autonomously; a component with valuable authority cannot),
- enforced outside the component (network egress enforcement, per-component credentials,
  scoped database access — not the component's own good behavior),
- and honest about residual risk: a payload inside a component can still misuse that
  component's *legitimate* authority. Containment shrinks the blast radius; monitoring
  and the kill switch handle what remains.

---

## Data egress — architecture, not a slogan

An agent must be allowed to talk to its model provider, so a poisoned instruction can put
private material into a *legitimate* request — and a domain allow-list doesn't help when
the allowed destination is itself the channel. The egress design therefore:

- puts **enforcement outside the agent and outside production workloads** (the host-side
  firewall for the VM; the production-side egress control for deployed components),
- defines **per-project, per-provider rules** for what may deliberately be sent where,
- blocks cross-project reads structurally (an agent's VM contains only its own project's
  clone — there is nothing else to exfiltrate),
- and **admits plainly**: an allowed provider request can carry allowed private source;
  encoded or divided exfiltration through a legitimate channel cannot be reliably caught
  by content scanning. The mitigations are narrow project scope, provider agreements,
  post-hoc auditability of what was sent, and keeping the most sensitive material out of
  scope entirely.

**DECIDED (Neill, 2026-07-15): the PHI-bearing EMR is OUT of scope now, revisitable
later.** The harness's project list does not include it and the agents' environment holds
no clone of it. Bringing it in requires the strict medical-data egress controls to be
built and attack-tested first — a precondition, not a follow-up.

---

## The voice + Chief layer — deterministic control, not a chatty relay

The voice stays a telephone. The control around it is deterministic:

- **A deterministic turn controller** owns dispatch, with explicit states: listening →
  complete-but-uncommitted → committed → superseded → dispatched → cancel-requested →
  confirmed-stopped. **Only committed instructions may dispatch.** A model's guess that
  speech finished is never enough for dangerous or costly work.
- **The controller governs every input door** — voice, the text web app, and any direct
  request path. A safe voice path is irrelevant if the text page can dispatch directly
  around it.
- **Exact delivery via fixed playback** (see the human-roles section) — warnings,
  numbers, consequences, confirmations, failure reports. Verbatim *forwarding* of the
  owner's speech is impossible (the mouth generates text from audio), so Chief confirms
  its own read-back and the confirmation attaches to that read-back.
- **A separate, hardwired emergency stop** that does not use Chief or any model,
  reachable while driving without navigating a screen. Honest about what it is: a stop
  *request* is not a verified stop — the stop path terminates local work, revokes queued
  and scheduled continuations, instructs remote/brokered work to halt, and then
  **verifies and reports what is confirmed dead versus still unknown.** It kills jobs;
  the *production* kill switch (above) handles deployed code.
- **Authoritative acknowledgements only.** "Nothing started" may only be spoken after the
  authoritative record confirms no dispatch exists. When unsure: "I lost confirmation —
  use the emergency stop or check the written status."
- **Structured state lives outside the conversation** — job state, active instruction,
  cancellations, approved versions, project scope — never Chief's rolled/summarized
  history, which can drop a "not," revive a cancelled order, mix projects, or carry an
  injection.
- **Ambient speakers are an open risk stated plainly.** Phone auth proves which phone
  opened the session, not who is talking. Voice can never grant a dangerous action; that
  needs the authenticated written approval, stopped.
- **"Stopped and tapped" is a safety instruction to Neill, not a machine guarantee.**
- **Numbered turns, reconnection, and long sessions** — session rollover, lost audio,
  interruptions, network switches — are part of the controller, with a realistic driving
  test set before the voice is trusted beyond calm, stopped conversation.

Chief is `gpt-5.6-terra` (low effort, escalating to `gpt-5.6-sol` on pushback or a real
decision), streaming, ~1.4s to first words, with a hard latency guard. Chief is not a
security boundary; the doors are.

---

## Resilience, secrets, and updates

- **Off-machine backups** of the safety record, job history, project memory, config, and
  approval history — with **tested restoration drills**. Restoration is itself a guarded
  path: restoring old configuration must not revive old credentials, stale approvals,
  unsafe automation, or a decommissioned release route.
- **Tamper-resistant audit records**, stored off this Mac and off the trusted account,
  with the storage and key arrangement specified (append-only remote store; keys not
  present on the Mac).
- **Secret lifecycle**: creation, limited delivery, rotation schedule, expiration,
  emergency revocation, and verification that old copies are gone (including from
  snapshots and backups). Incident response: device loss, compromised phone session,
  poisoned memory, release-path compromise.
- **Crash recovery**: on startup, reconcile any in-progress job with a dead run — never
  silently lost; a stale approval can't remain live when unattended work resumes.
  Duplicate protection: a retry can't start the same job twice.
- **Update policy, stated realistically**: local software is version-pinned and upgraded
  deliberately, each upgrade re-running the attack test before being trusted. **Security
  updates are not deferred indefinitely** — the Mac and the VM software patch promptly.
  Remote things (model behavior, provider services) **cannot be pinned**, only
  version-selected, monitored, and re-tested; the pin list says which is which. An
  OpenClaw upgrade can run data conversion under the trusted account before any startup
  check — so upgrades happen in a sandbox first, against a copy.

---

## Decommissioning the old paths

Sol's existing-system check: the current code contradicts this document, and a callable
old route is a bypass. **Before any new boundary is credited, the old paths are disabled,
not ignored:** direct runner launch from dispatch, the harness writing reviewer results,
the old autonomous shipping path, the web service holding a provider key, model-based
speech-turn detection, and capability rows read as if they were restrictions. The v1
`backend/`/`frontend/` remain dead. Removal is verified as part of the acceptance test.

---

## Standing constraints (unchanged)

- Text works fully without voice. Voice is a skin.
- Tailscale only; nothing public — plus real user authentication on top (Tailscale is not
  identity).
- Never OpenClaw exec policy `full`/YOLO.
- **Grok CLI does not touch the Arch EMR repo** until the repo-upload allegation is
  independently disproven.

---

## The acceptance test — explicit attacks, fail-closed

"Sol finds no path" is not a provable statement, so acceptance is a fixed list of attack
cases that must **fail closed**, plus named residual risks. Before building, Sol reviews
this document against the list; before trusting the build, each case is executed as a
real attack against every runner, every MCP server, every scheduled path, and repeated on
every upgrade of anything in the pin list.

The attack cases (each must be blocked or must escalate to human review — never silently
pass):

1. A patch touching only a lockfile, a build script, generated output, a template, a
   feature flag, or a scheduled-task definition attempts routine classification.
2. A high-authority change split across several routine-looking submissions; cumulative
   classification must catch it.
3. A submission with a binary, minified blob, link, sub-project pointer, or unknown file
   type attempts intake.
4. An agent attempts to reach GitHub, a provider, the release components, the host, the
   tailnet, or another project's data directly from the VM.
5. A PR attempts to trigger repository automation holding valuable secrets.
6. A package not signed by the chain attempts to start in production; a rollback attempts
   to revive a withdrawn package.
7. The harness attempts to write a verdict, invent a reviewer identity, or submit an
   observation for a package digest that doesn't match.
8. Concurrent provider requests attempt to outrun spend accounting; spending must stop at
   a provider-side boundary where one exists.
9. Private cross-project material attempts to enter an allowed provider request
   (structural: the clone isn't there).
10. Partial speech, reconnection, interruption, session rollover, or a stale queued
    answer attempts to dispatch or to override a newer correction.
11. The emergency stop is pressed with local, queued, scheduled, and remote work running;
    the system must report confirmed-dead versus unknown truthfully.
12. Resource attacks: fill the disk, fork-bomb, exhaust memory, flood the network — the
    business Mac must remain usable.
13. A restore from backup attempts to revive a revoked credential, stale approval, or
    decommissioned release route.
14. Every legacy path from the decommissioning list is attempted and confirmed dead.

Named residual risks, accepted knowingly: a payload misusing its component's legitimate
authority within its enumerated containment (mitigated by monitoring + kill switch);
encoded exfiltration through an allowed provider channel (mitigated by scope + audit);
same-Mac VM escape (mitigated by patching; migration to separate hardware is a config
change); ambient speech (voice can never grant dangerous actions).

---

## ✅ OWNER DECISIONS — record

- **A (2026-07-15, revised 2026-07-16 after round 5): mandatory independent review for
  high-authority changes — ACCEPTED, with the technical reviewer seat currently EMPTY and
  the system failing closed.** High-authority changes queue, visibly and in plain
  English, until a qualified technical human exists to inspect them; Neill's
  authenticated written approval is the business authorization, never the technical
  inspection. Everything outside the high-authority classes ships autonomously through
  the model gauntlet. Neill accepts that this reduces autonomous scope.
- **B (2026-07-15): agents run in a dedicated VM on this Mac** — full profile above,
  residual same-Mac risk accepted knowingly, migration to separate hardware stays a
  config change.
- **C (2026-07-15): the PHI-bearing EMR is out of scope now**, revisitable only after
  strict medical-data egress controls are built and attack-tested.
