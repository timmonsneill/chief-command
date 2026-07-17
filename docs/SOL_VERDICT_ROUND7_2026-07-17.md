# Sol's Verdict — Round 7, on the v3 re-scoped design (2026-07-17)

Full transcript: `docs/sol/sol_round4.out`. **Verdict: conditional — the closest to
approval in seven rounds.**

Closing line: *"The proportionality reset is correct. Once the four narrow design
changes above are made and the build proves the listed doors, I would sign off on it as
an honest one-person development harness."*

---

## The four design changes required for sign-off

1. **Repository admission rules.** The big one. Before any repo gets autonomous merge,
   a recorded admission decision must prove **merging does nothing but change source**:
   no deploy-on-merge, no publish-on-push, the repo is not itself the live product, no
   other machine auto-pulls its main branch, no automation with production secrets, no
   agent automation running before the gauntlet. Any "yes" → the repo is excluded,
   the automation is disabled, or the owner's tap is explicitly the deploy step.
2. **Explicit broker and connected-tool exclusions.** No connected tool (MCP server
   etc.) may hold production credentials, publishing authority, repo administration,
   branch-rule bypass, release/package authority, or live-data access — excluded **by
   rule**, not by per-tool documentation. Brokers must be unable to change repo
   settings, branch rules, releases, packages, or other repos.
3. **Correct the claims and the benchmark wording.** Git reverses *source*, not
   consequences (can't unpublish a secret, un-run automation, undo damaged data). The
   harness is not "no more dangerous than the terminal" — it is **safer in blast
   radius** (VM, credential absence, caps) and **more autonomous** (unattended,
   voice-triggered, overnight scale, more injection surface). Say it that way.
4. **Resolve voice intent honestly.** Either voice-originated work waits for an
   authenticated non-voice confirmation before automatic merge (confirmation of
   INTENT, not code review — protects against mishearing/ambient speech), or the owner
   explicitly accepts a higher chance of unwanted-but-source-reversible merges.
   → OWNER CALL, put to Neill 2026-07-17.

## Scope leaks found in the CURRENT repo (fix during decommissioning regardless)

- A live production deploy command (Netlify) and a public Cloudflare tunnel config +
  startup machinery that binds broadly and opens the tunnel; an installer that creates
  a default password. Executable today, readable and editable by agents. "v1 is dead"
  is insufficient while these remain runnable.
- The current voice/text paths launch Codex/Claude **directly on the host**, outside
  the record, brokers, and VM — restoring exactly the authority Decision D removed.
  Every direct host launch must die, not just the main dispatch path.
- EMR exclusion is configuration discipline, not structure — needs an explicit project
  allowlist with the EMR permanently refused absent a new recorded owner decision.

## Corrections to the record's claims

- The live database still runs the OLD schema: verdicts deletable, tester countable as
  a reviewer — the hardened triggers exist in source but were never applied to the
  existing DB. The doc's "already built and regression-tested" claim must be removed
  until the live record is migrated and re-attacked. The one job marked "shipped" has
  no run id, branch, or exact version attached.
- Reviews are not yet bound to the exact code version merged (the known flaw, still
  open in the build).

## Benchmark judgment (honest version to adopt)

Safer than terminal use for: the Mac, credentials, production access, spend.
More dangerous than terminal use for: unwanted repository changes and routine spend at
scale, unattended. Proportionate if stated and accepted that way.

## Also delivered

- A completed residual-risk list (10 items to fold in — manual deploy of fooled code,
  source-revert ≠ consequence-revert, broker/account/provider compromise, egress via
  legitimate requests/logs, cumulative merges, availability, voice audio to provider,
  licensing).
- An 11-item build-must-prove list (branch protection live with no broker bypass,
  everything in the VM, brokers as sole action paths, reserve-before-spend under
  concurrency, verdict-to-version binding, live record migrated, hostile voice tests,
  legacy routes confirmed dead, keys rotated before unattended runs).
