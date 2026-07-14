# Sol's Verdict — Round 4, on the REWRITTEN architecture (2026-07-14)

Sol (GPT-5.6-sol, high effort, ~236k tokens) attacked `ARCHITECTURE_v2_2026-07-14.md`
after the rewrite that answered its eight questions. Full transcript:
`docs/sol/sol_round1.out` (the ask-sol numbering restarted; this is the 4th review
overall). **Verdict: NOT approved.** And it's the deepest finding yet.

---

## The one finding that matters

> **An agent can put a harmful payload into its working copy, fool the model reviewers,
> receive genuine passing records, and have the release service deliberately deploy it.
> Once deployed, that code gains access to production data and credentials.**
>
> **No database forgery or direct release-service call is required. Every stated door can
> work exactly as designed and the harmful action still occurs.**

This kills the core claim. "A missed checkpoint is harmless" was wrong in a way I didn't
see: it's not just the *skipped* checkpoint that's dangerous — a *fully honored* path is
too, because **model review is a quality gate, not a containment boundary.** Branch
protection controls *who merges*; it does not prove merged code is safe. A screenshot
proves one visible flow worked; it does not prove there's no hidden payload.

The honest core claim Sol will accept:

> A skipped checkpoint cannot directly obtain release authority — while production
> containment, independent review, and narrow deployment privileges limit the harm of
> code that passes review.

That is substantially weaker, and it must be stated that weakly.

---

## The 12 required changes, ranked (Sol's words, distilled)

1. **Move all agent execution into a dedicated VM or separate host.** No owner files,
   credentials, browser data, clipboard sharing, host-management connection, or shared
   project history. Put the release service OUTSIDE that environment.
2. **The release service is the SOLE owner and writer of release state.** The general
   harness runs under a different identity and cannot write verdicts, approvals, evidence
   provenance, or released status directly — it submits authenticated observations
   through a narrow interface. Compromising the harness must not hand over both the record
   and the release caller's identity.
3. **Treat agent-produced code as an untrusted payload EVEN AFTER model review.** Require
   a qualified *human* review for changes that can gain production authority, alter
   dependencies, change automation, handle secrets, change auth or money, modify stored
   data, or change the release system itself. Otherwise prohibit auto-release of those
   classes. *(⚠️ This directly limits the "autonomous ship" direction — see OWNER
   DECISIONS below.)*
4. **Isolated clones or storage snapshots, not shared working copies.** Agents must not
   share Git history, branches, hooks, or project settings. A standard worktree shares the
   underlying repo — deleting the folder doesn't undo corruption of shared history.
5. **Remove direct GitHub write authority from agents.** Agents submit a patch/package to
   a broker; the broker creates the branch and PR. Protect branches, tags, automation,
   packages, releases, production environments, and admin settings — not just `main`.
6. **Define an exact package-based release chain.** Review and test a clean package built
   from the proposed version + current main; bind every verdict and artifact to its
   digest; deploy that same package. Never run untrusted build/install code beside release
   credentials.
7. **Broker all model and provider access.** Agent runners get NO reusable subscription or
   API credentials; the broker applies per-job limits before every call; hard provider-side
   caps for every metered provider. (The release service can't hold "all spend authority"
   while agents and the voice can make their own provider calls.)
8. **Enforced data-egress policy.** Define what each project may send to each provider;
   block customer/medical/private/cross-project data independently of credential scanning.
   A poisoned instruction can put private material into a legitimate model request; a
   narrow domain allow-list doesn't help when the allowed destination is itself an exfil
   channel. (PHI in the EMR makes this non-optional.)
9. **Specify and test the full containment profile** — every runner, child process, MCP
   server, hook, scheduled job, retry, recovery path — plus CPU/memory/disk/process/
   time/network limits. (An agent can brick the Mac by exhaustion; a budget cap stops none
   of that.)
10. **Replace the voice flow with a deterministic turn controller.** No dispatch from
    partial speech; authoritative acknowledgements only; exact warnings without voice-model
    paraphrasing; a separate hardwired emergency-stop path that doesn't use Chief; structured
    state OUTSIDE conversation history; reconnection and long-session tests.
11. **Add off-machine backups, tamper-resistant audit records, restoration drills,
    credential-revocation procedures, device-loss handling.**
12. **Rewrite the core claim** to the honest, weaker version above.

---

## The other structural corrections

- **"The release service is the only trusted component" is false.** The trusted base
  includes the whole harness, its web UI, model-response handling, the dispatch channel,
  evidence capture, OpenClaw integration, deploy scripts, and the OS account. The release
  service may be small, but it trusts facts made by a much larger, more exposed system.
- **Evidence needs a protected binding to the exact package/environment/test/candidate.**
  A real screenshot can be from the wrong build, a stale process, a harmless screen while
  malice hides elsewhere, or a test that never exercises the dangerous path. "The file
  exists" is inadequate.
- **Spend:** only a provider's own hard cap is a real financial boundary. Subscription
  limits and non-OpenAI providers aren't covered by the $100 ceiling.
- **"Stopped and tapped," "cancellation stops a builder," "MCP inherits the sandbox,"
  "startup checks prevent drift"** — all four are stated stronger than their enforcement.
  A tap proves someone tapped; a stop signal is a request; some MCP servers are remote and
  hold their own credentials; a startup check is itself configuration the same compromise
  can disable.
- **Ambient speakers:** phone auth proves which phone opened the session, not who's
  talking. A passenger, radio, or podcast can issue a routine dispatch or a stop.
- **Private information beyond credentials** (source, business plans, issues, memory,
  customer/medical data) has no egress rule at all.

---

## ⚠️ OWNER DECISIONS — these are Neill's, not the agent's

Sol's conditions force choices that reverse or constrain directions Neill personally set.
I will NOT bake these in unilaterally. Flagging for him:

- **A. Autonomy vs. mandatory human review (Sol #3).** Sol says high-authority change
  classes (auth, money, dependencies, deploy config, data migrations, the release service
  itself) must have a qualified human review or be barred from auto-release, because honest
  model review can pass malicious code. This limits "the gates replace the human gate."
  **Neill's own process ("Sol must approve") points at accepting this. His autonomy goal
  points against it. He decides.**
- **B. Where agents run.** VM on the same Mac (Sol's stated minimum) vs. a separate
  physical machine (Sol's "stronger"), given this Mac also runs his real business. Hardware
  + cost decision.
- **C. Whether the EMR (with PHI) is ever in scope for this harness at all**, given the
  egress-policy burden that adds. Possibly keep it permanently out.
