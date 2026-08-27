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
