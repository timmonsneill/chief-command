# Chief Command — Architecture of Record (2026-07-14)

Written after five adversarial cross-family reviews — 9 flaws, then 18, then a hollow
core, then 3 verified schema holes, then the deepest finding of all: **an agent can get
harmful code deployed by passing every gate honestly, no forgery required.** This is the
corrected design. **Almost nothing here is built** — this is the spec Sol must approve
before the rest is written.

Plain English throughout, because the owner cannot read code.

Three questions in this document are marked **OWNER DECISION**. They are not the agent's
to settle — they reverse or constrain directions Neill personally set, and Sol has made
them conditions of approval. They are collected at the end.

---

## The one-sentence shape

**OpenClaw drives the car. A tiny release service holds the only keys. Everything else —
including the code the agents write — is treated as untrusted until a narrow, contained
release path lets exactly one reviewed package through.**

---

## The core claim, stated honestly

The previous version claimed *"a missed checkpoint is harmless."* **That is false, and
the reason is the most important sentence in this document:**

> Model review is a QUALITY gate, not a containment boundary. An agent — fooled by prompt
> injection, or simply subtle — can put a harmful payload in its branch, the model
> reviewers can inspect it and honestly pass it, the tester can screenshot the happy path,
> the database can honestly record real passes, and the release service can deploy it.
> Then the deployed code holds the production authority the agent never had.

Branch protection controls *who merges*. It does not prove merged code is safe. A
screenshot proves one visible flow worked. It does not prove there is no hidden payload.

So the claim this design actually defends is the weaker, true one:

> **A skipped checkpoint cannot directly obtain release authority. Production containment,
> independent review, and narrow deployment privileges limit the harm of code that passes
> review.**

Everything below serves that claim. Where a sentence promises a guarantee, it names the
real boundary that provides it — and calls out where the honest answer is "this reduces
harm" rather than "this prevents it."

---

## The threat model

Not the owner. The real threats, in order:

1. **An agent doing something nobody asked for.**
2. **Prompt injection** — poisoned text in a file, dependency, issue, or web page turns a
   fooled-but-authorized agent into an attacker's hands. The load-bearing threat, because
   the agent is *allowed* to read the repo.
3. **Mishearing** — road noise, a passenger, a bad transcript.

And now, promoted to first-class by round 4:

4. **Malicious code that passes honest review** — threats 1 and 2 don't need to beat a
   gate; they can walk a payload through the front door of a gate that's working
   perfectly.

---

## The trusted computing base — named honestly

The previous version claimed the release service was "the only trusted component." **False.**
The things that must be trusted are:

- The release service (holds the keys).
- The larger harness, its web UI, model-response handling, the dispatch channel, evidence
  capture, OpenClaw integration, deploy scripts, and the OS account they run under —
  because they produce the facts the release service acts on.

The design's job is to make this base **as small and as separated as possible**, not to
pretend it's one component. Two separations do the heavy lifting:

1. **The agents are outside the trusted base entirely** — different OS identity, isolated
   storage, no keys (below).
2. **The release service is separated FROM the rest of the harness** — it is the sole
   writer of release facts; the harness submits authenticated observations through a narrow
   interface and does not share the release account. Compromising the harness must not hand
   over both the record and the release caller's identity.

---

## Where agents run — containment

**OWNER DECISION B** governs the strength here (VM on this Mac vs. a separate host). The
mechanism is the same either way:

- **A dedicated virtual machine (minimum) or separate host (stronger)** runs all agent
  execution. **No** owner-home sharing, **no** clipboard sharing, **no** credential
  connections (SSH agent, git credential helper, cloud CLI config), **no** host-management
  connection, **no** shared project history. The release service and production credentials
  live OUTSIDE it.
- **Isolated clones or storage snapshots, never shared working copies.** A standard git
  worktree shares the underlying repo — history, branches, hooks, objects, settings. An
  agent that can commit could corrupt shared history or damage other agents' branches, and
  deleting its folder wouldn't undo it. Each agent gets a separate clone or a copy-on-write
  snapshot with no path back to the real repo.
- **A full containment profile, specified and tested**, covering every runner (Claude Code,
  Codex, Ollama), every child process, every MCP server, every hook, every scheduled job,
  every retry and recovery path — plus hard **resource limits**: CPU, memory, disk,
  child-process count, execution time, network volume. An agent can brick the Mac by
  exhaustion (fill the disk, fork-bomb, exhaust memory); a spend cap stops none of that.
- **The kernel sandbox on, strict**: deny credential paths (`~/.ssh`, `~/.aws`, keychain),
  scrub credential env vars, narrow the domain allow-list (never a bare `github.com`
  wildcard — domain fronting), `allowUnsandboxedCommands: false`. Noted honestly: a startup
  check that asserts these is itself configuration; it detects drift, it is not independent
  enforcement. The real boundary is the VM/host separation, with the sandbox as a second
  layer inside it.

---

## No keys in agent hands — the broker

Agents hold **no reusable credentials of any kind** — not GitHub write, not provider API
keys, not subscription login material sitting in a readable file.

- **A GitHub broker.** Agents do not push or open PRs. They hand a patch or a built package
  to a broker; the broker creates the branch and the PR. GitHub protection covers branches,
  tags, automation, packages, releases, production environments, and admin bypass — **not
  just `main`**. (A token that can open a PR can usually also overwrite unprotected
  branches, trigger paid automation, or run untrusted branch code in automation that holds
  secrets. Narrow the authority to exactly "submit a patch.")
- **A model/provider broker.** Agent runners get no reusable subscription or API
  credentials; the broker holds them, applies per-job limits *before* every call, and is
  the only path to a provider. This is required because a runner that can call its provider
  directly can burn subscription limits or paid calls without ever reserving budget — so
  "the release service holds all spend authority" is false unless provider access is
  brokered too. Every metered provider also gets a hard provider-side cap (only a provider's
  own cap is a real financial boundary; our record is a soft early warning).

The honest note on runner authentication: a coding runner using a subscription login needs
*some* auth material to exist. It is held by the broker, not placed in the agent
environment — and this is exactly why agents live behind the VM/host boundary.

---

## The release service — the only irreversible power

A small, separate service (a few hundred lines, the harshest-reviewed code in the system)
holds every dangerous power: merge, deploy, spend, touch production. Concretely:

- **Sole writer of release facts.** Verdicts, approvals, evidence provenance, and released
  status are written only by the release service (or a release-account process), never by
  the general harness directly. The harness submits authenticated observations through a
  narrow interface.
- **Not reachable by agents, and not a blind messenger.** No general network address agents
  can reach; a local OS channel that checks the caller's actual account; no reusable
  token in an agent-readable file; a fixed list of narrow actions with exact parameters
  (repo, version, environment); single-use request numbers; approvals consumed atomically
  before acting; rejection if `main`, the artifact, or the approval changes mid-operation.
  Release requests come from a **deterministic controller that derives them from protected
  state — never from model-written text.**
- **Never runs untrusted code beside its credentials.** It must not build, install, test,
  or execute agent-produced code in the environment that holds release credentials. This is
  the single most dangerous mistake it could make.
- **Re-verifies everything from its own records** before acting: the exact version passed
  the full panel, no fail or unresolved escalation, a live single-use read-back approval
  (with a tested recovery plan if irreversible) exists.

### Evidence and reviews bound to one exact thing

The database only proves observations were *recorded*. To mean anything, every review and
artifact is bound to a single: request, source version, base version, **built package
digest**, reviewer run, test run, and proposed release action. A real screenshot is
worthless if it's from the wrong build, a stale process, a harmless screen while malice
hides elsewhere, or a test that never exercised the dangerous path. "The file exists" (the
round-3 hole) is necessary but nowhere near sufficient — the binding to the digest is what
makes it evidence.

### The package-based release chain

Review and test a **clean package** built from the proposed version combined with current
`main`, in the contained environment. Bind every verdict and artifact to that package's
digest. **Deploy that exact package** — never a rebuilt approximation, never fresh
untrusted build code next to release credentials.

---

## Untrusted-code policy — the round-4 crux

Because model review can honestly pass malicious code, **agent-produced code is an
untrusted payload even after it passes the gauntlet.** The design's response:

- **Containment limits blast radius.** The deployed code inherits only the narrow
  production privileges the release path grants — not the owner's full authority.
- **A data-egress policy** (below) limits what a payload can exfiltrate even if it runs.
- **Independent human review for high-authority change classes** — **OWNER DECISION A.**
  Sol's condition: changes that can gain production authority, alter dependencies, change
  automation, handle secrets, change auth or money, modify stored data, or change the
  release service itself must get a qualified human review, or be prohibited from
  auto-release. This is where the "autonomous ship" direction and containment collide.

---

## Data egress — protecting more than credentials

The first design guarded credentials and ignored everything else worth protecting: private
source, business plans, issue contents, conversation history, project memory, customer
data, and **medical data in other projects**. An agent must be allowed to talk to its model
provider — so a poisoned instruction can stuff private material into a *legitimate* model
request, and a narrow domain allow-list doesn't help when the allowed destination is itself
a channel that carries data out.

So: an **enforced egress policy** defines what each project may send to each provider, and
blocks customer/medical/private/cross-project data independently of credential scanning.
**OWNER DECISION C** — whether the PHI-bearing EMR is ever in scope for this harness at
all — sets how heavy this must be.

---

## The voice + Chief layer — deterministic control, not a chatty relay

The voice stays a telephone, but round 4 made clear the control around it must be
deterministic, not model-judgment:

- **A deterministic turn controller** owns dispatch, with explicit states: listening →
  complete-but-uncommitted → committed → superseded → dispatched → cancel-requested →
  confirmed-stopped. **Only committed instructions may dispatch.** A model's guess that
  speech finished is never enough for dangerous or costly work. This is what stops
  "delete the— no, archive it" from dispatching the deletion on the partial.
- **Exact delivery for anything that matters.** Warnings, numbers, consequences,
  confirmations, and failure reports are delivered verbatim — never paraphrased by the
  voice model, which can change meaning in both directions. (Verbatim *forwarding* of the
  owner's speech is impossible — the mouth generates text from audio — so Chief confirms its
  own read-back, and the confirmation attaches to that read-back, not to the mouth's guess.)
- **A separate, hardwired emergency stop** that does not use Chief or any model, and is
  reachable while driving without navigating a screen. A single tool that routes through
  Chief means "stop everything" dies if Chief hangs.
- **Authoritative acknowledgements only.** The 8-second "nothing started" message may only
  be spoken after the turn is cancelled and the authoritative job record confirms no
  dispatch exists — otherwise a network delay makes it a lie while work is actually running.
  When unsure, say "I lost confirmation, use the emergency stop or check the written status."
- **Structured state lives outside the conversation.** Job state, which instruction is
  active, what was cancelled, which version was approved, whether an approval is live, and
  which project is in scope come from structured records — never from Chief's rolled/
  summarized history, which can drop a "not," revive a cancelled order, mix projects, or
  carry an injection.
- **Ambient speakers are an open risk stated plainly.** Phone auth proves which phone opened
  the session, not who is talking. A passenger, radio, or podcast can utter a routine
  dispatch or a stop. Voice is not strong enough to be an approval boundary; the mitigation
  is that voice can never grant a dangerous action (that needs the written, stopped,
  ideally biometric confirmation), and dangerous dispatch is gated regardless of who spoke.
- **"Stopped and tapped" is a safety instruction to Neill, not a machine guarantee.** The
  system cannot know the car is stopped. It is never stated as an enforced fact.
- **Reconnection and long sessions** — session rollover, lost audio, phone calls,
  navigation interruptions, network switches, and restoring exact conversation state after
  a voice session expires — are part of the controller, with a realistic driving test set
  (road noise, passengers, radio, weak signal, interruptions, self-corrections, ambiguous
  confirmations) before the voice is trusted beyond calm, stopped conversation.

Chief itself is `gpt-5.6-terra` (low effort, escalating to `gpt-5.6-sol` on pushback or a
real decision), streaming, ~1.4s to first words. Terra-as-Chief isn't the risk; Chief
*deciding routing and whether a correction supersedes* is, which is why the controls around
it are deterministic.

---

## Resilience — backups, audit, incident response

Round 4 restored items that fell out of the prior draft:

- **Off-machine backups** of the safety record, job history, project memory, config, and
  approval history — with **tested restoration drills**, not just backups that exist.
- **Tamper-resistant audit records** stored off this Mac and off the trusted account, so a
  compromise of either can't quietly rewrite history.
- **Incident response**: emergency credential revocation, device-loss handling, compromised
  phone-session handling, poisoned-memory recovery, and release-service compromise recovery.
- **Crash recovery**: on startup, reconcile any `in_progress` job with a dead run id — never
  silently lost — and ensure a stale approval can't remain live when unattended work
  resumes.

---

## What "pinned" must actually cover

Pinning OpenClaw alone is not enough — an upgrade can run data conversion under the trusted
account before any startup check runs, and it doesn't pin the runner programs, MCP servers,
plugins, model behavior, provider APIs, OS behavior, packages, or deploy tools. The pin
list covers **all** of these, and every upgrade re-runs the full attack test (below) before
it's trusted.

---

## Standing constraints (unchanged)

- Text works fully without voice. Voice is a skin.
- Tailscale only; nothing public.
- Version-pin everything; deliberate upgrades only; never OpenClaw exec policy `full`/YOLO.
- **Grok CLI does not touch the Arch EMR repo** until the repo-upload allegation is
  independently disproven.

---

## The acceptance test

**Before building:** Sol attacks this document and finds no path by which an agent can
bypass the record, forge or misbind evidence, change any protected GitHub state, spend past
a hard provider cap, reach production credentials, get untrusted code deployed with
production authority *without the required independent review*, exfiltrate private/medical
data, exhaust the host, or get a dangerous action performed without a live read-back
approval bound to the exact package.

**Before trusting the build:** the same list, run as a real attack test against every agent
runner, every MCP server, every scheduled path, and on every upgrade of anything in the pin
list. Anything that can do one of those stays disabled until the route is closed.

---

## ⚠️ OWNER DECISIONS — Neill's to make, not the agent's

These three reverse or constrain directions Neill set, and Sol has made them conditions of
approval. They are teed up for him; the design is written to accept whichever way he goes.

- **A. Autonomy vs. mandatory human review for high-authority changes.** Sol requires a
  qualified human review (or a ban on auto-release) for the dangerous change classes, because
  honest model review can pass malicious code. This limits "the gates replace the human
  gate." His own stated process — *Sol must approve* — points at accepting it; his autonomy
  goal points against. **This is the crux of the rejection; nothing gets Sol's approval
  without resolving it.**
- **B. Where agents run** — a VM on this Mac (Sol's minimum) or a separate physical machine
  (Sol's stronger), given this Mac runs his real business. Cost/hardware call.
- **C. Whether the PHI-bearing EMR is ever in scope** for this harness, which sets how heavy
  the egress policy and provider rules must be. Keeping it permanently out is a legitimate,
  simplifying answer.
