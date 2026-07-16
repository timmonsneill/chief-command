# Sol's Verdict — Round 5 (2026-07-15)

Sol attacked the updated `ARCHITECTURE_v2_2026-07-14.md` — the version with Neill's three
owner decisions folded in. Full transcript: `docs/sol/sol_round2.out` (ask-sol numbering;
5th review overall). **Verdict: NOT approved.**

Closing line: *"The design is not ready to build. The owner decisions are recorded, but
decision A was not implemented as required: written owner consent is not qualified
independent review. Until that and production runtime containment are corrected, the
round-4 attack remains open."*

---

## The two findings that matter most

### 1. Neill's sign-off is not a technical review

Decision A said high-authority changes wait for Neill's written approval. Sol's point:
**Neill cannot read code, so his approval is a business authorization based on an
explanation the agent wrote.** A fooled reviewer produces a fooling explanation. What Sol
requires: a **qualified, independent technical human** inspects the dangerous change;
Neill separately authorizes the business decision. If no qualified reviewer is available,
those changes **do not release** (fail closed).

This is a new owner decision: who is that human, or do high-authority changes simply
queue until one exists?

### 2. Classify danger by AUTHORITY, not by filenames

A path-based classifier ("did the change touch the auth files?") is trivially evaded. Sol
listed the evasions concretely: payloads in build/packaging scripts, lockfile-only
dependency redirects, transitive dependency swaps, checked-in generated/minified code,
schema/template files that become code at build time, config that becomes behavior at
runtime (routing rules, prompts, feature flags, scheduled-task definitions), dormant
loaders activated by a later "data" change, authority split across several
routine-looking submissions, and cumulative drift across five small releases.

The fix: **any change to a component that holds valuable production authority is
high-authority by default.** Autonomous release only for components whose runtime access
is already harmless. Unknown/binary/generated/dependency/build/config content **fails
into human review**. Classification considers cumulative change since the last human
review. Changes to the classifier itself always get independent review.

**Consequence Sol states plainly: this reduces how much ships autonomously. That is the
unavoidable price of the round-4 finding.**

---

## The other required design changes (Sol's ranking, condensed)

3. **Define production containment.** What every deployed component can read, write,
   spend, contact, reveal. The missing boundary against code that passes review — deploy
   permission ≠ the authority the code gets after it starts.
4. **Make the release path exclusive.** Production accepts ONLY packages signed through
   this chain; disable every old deploy script, automation identity, admin bypass.
5. **Complete trusted package chain.** Agent submits a restricted patch (never a built
   package); a clean builder builds, signs, stores immutably; production verifies the
   signature before starting it.
6. **Egress as architecture, not slogan.** Enforcement outside the agent and production
   workloads; per-project/per-provider rules; admit generic scanning can't stop encoded
   exfiltration to an allowed destination.
7. **Every broker's exact authority + hostile-input rules** (GitHub automation runs on PR
   creation — before merge; patches can carry links, binaries, submodule pointers).
8. **Complete the VM profile.** No shared folders/devices/clipboard at all, fixed (not
   expanding) disk, no host/local-network/tailnet reachability, broker-only egress, no
   inbound, no Tailscale identity inside, host-side firewall. State the residual risk of
   same-Mac honestly.
9. **Authenticated approval + exact delivery.** Critical warnings played from fixed text
   (never model-paraphrased); written approval device-bound, short-lived, single-use,
   tied to the exact package and action.
10. **Separate the dangerous powers.** Merge, package-signing, deploy, spend, production
    data must not all live in one small service — one compromise takes everything.
11. **Rollout, monitoring, kill, rollback.** Limited first deploy, health checks,
    independent post-release monitoring, a way to kill malicious code already running in
    production (the voice stop only kills jobs), rollback that can't revive a bad package.
12. **Rewrite the acceptance test.** "Sol finds no path" is not provable; use explicit
    attack cases, independent inspection, stated residual risks, fail-closed results.

## Also flagged

- **14 missing designs** (§7 of transcript): qualified-reviewer mechanics, per-app
  production authority, authenticated approval, fail-closed classifier, safe patch
  intake, trusted builder, production-side verification, rollout/rollback, post-release
  detection, production kill switch, web/API auth beyond Tailscale, power separation,
  secret lifecycle, realistic update policy (can't forbid security updates forever).
- **Existing code contradicts the doc** and must be *disabled*, not ignored: dispatch
  launches runners directly, the harness writes reviewer results, old shipping path
  allows autonomous release, the web service holds a provider key, model-based speech
  turn detection, capability rows treated as restrictions.
- **VM on the business Mac:** acceptable only with the full profile above, and the doc
  must state it protects against ordinary containment failures — it does not make
  hostile execution on the personal Mac risk-free.
- A long "must be proven during the build" list — preserved in the transcript — that
  becomes the build-phase attack checklist.
