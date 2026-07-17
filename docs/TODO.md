# Chief Command — Open TODOs

**Persistent across sessions. Read at the start of every session. Update as things are
done — don't let this rot.**

Last updated: 2026-07-17 (session 3 — rounds 5 AND 6 rejected with deepening demands;
Neill called the spiral: **Decision D, the proportionality reset**. Arch doc rewritten
v3; Sol's role reframed; round 7 running)

**Owner decisions:** (D, 2026-07-17 — supersedes A) the harness is a DEVELOPMENT
harness benchmarked against Neill's daily terminal use of coding agents; the gauntlet IS
the approval — passing work merges autonomously; Neill only gates production deploys
(manual), spend past hard caps, and repos he explicitly dials to tap-to-merge; Sol
verifies claims and names residual risks — it is no longer asked to certify no attack
path exists. (B) agents run in a dedicated VM on this Mac. (C) the PHI-bearing EMR is
out of scope, revisitable only after attack-tested egress controls exist.

---

## 🔴 NEILL'S TASKS — only he can do these

These are the highest-leverage items on the entire project. Ask him about them at the
start of a session if they're still open.

- [ ] **Rotate the xAI API key.** It was pasted into a chat transcript. Real money.
      → `console.x.ai`
- [ ] **Rotate the OpenAI platform key.** Same — pasted into a transcript. Real money.
      → `platform.openai.com` → API keys
      *(After rotating, update `~/.chief/env` — it's chmod 600, not in the repo.)*
- [ ] **Turn on GitHub branch protection for `main`.** ~5 minutes.
      **This is the single highest-leverage safety item in the project.** It permanently
      blocks force-push and direct writes to main, enforced on GitHub's servers — where
      no agent and no model can argue past it. It works whether or not any of our code
      does.
- [ ] **Get production credentials off this machine** — shell profiles, `.env` files, the
      environment that launches agents. If an agent can't authenticate, no gate is
      needed. **This is the only real defence against prompt-injection exfiltration**,
      because the agent is *authorized* to read the repo.
- [ ] **Decide: does Grok touch the Arch EMR repo?** Recommendation: **NO**, pending
      independent verification. A wire-level analysis alleges Grok's CLI uploads entire
      repos — including `.env` secrets — to xAI. Single-source and unverified, but there
      is PHI in that repo, and no sandbox helps (the agent is allowed to read it).

---

## 🟠 SECURITY — ours to do

- [ ] **Regenerate the OpenClaw gateway token.** Sol found it in a readable backup
      settings file. *Context: Claude generated this token during setup; it is a LOCAL
      token for a service bound to Neill's tailnet, not a cloud credential. Lower stakes
      than the API keys — regenerate, don't panic.*
- [ ] **Turn the kernel sandbox ON, strict.** It's real (Seatbelt, kernel-enforced) but:
      - OpenClaw's sandbox is **OFF by default**
      - Claude Code and Codex both **read the entire disk by default**, including
        `~/.ssh` and `~/.aws`
      - Needs: `allowUnsandboxedCommands: false`, deny credential paths, scrub credential
        env vars, narrow the allowed domains (**never** a bare `github.com` wildcard —
        domain fronting)
- [ ] **Never set OpenClaw's exec policy to `full`/YOLO** — its own docs say that launches
      Claude Code with `--permission-mode bypassPermissions`. Use `auto` or `allowlist`.
- [ ] **Real auth on the phone app.** Being on the tailnet is not proof of being Neill.

---

## 🔵 BEFORE ANY BUILDING — design freeze is in effect

Neill's instruction: *"stop building new shit until we have this all nailed down, and
sol has reviewed it and approved it as well."*

- [x] **Verify Sol's exploits against the schema.** DONE 2026-07-14. All three
      reproduced against the real schema. Two fixed in `schema.sql` (verdicts now
      un-deletable; approvals can't be born granted) with regression tests in
      `test_sol_attacks.py` — 79 tests pass. The third (nonexistent screenshot path) is
      unfixable by a trigger and became a gatekeeper requirement in the arch doc.
      Transcripts now in `docs/sol/` (survive reboot).
- [x] **Rewrite `ARCHITECTURE_v2_2026-07-14.md`** DONE 2026-07-14. Restructured around
      Sol's 8 review questions; added the seam table, the plain-file-DB trust answer, the
      credential-absence-via-separate-user answer, and folded in the verified exploits.
- [~] **Send it back to Sol** (`./ask-sol.sh`). Round 4 REJECTED (honest-pass finding).
      Round 5 REJECTED (owner sign-off ≠ technical review; authority-based
      classification). Round 6 REJECTED (13 more changes + 16 missing designs — the loop
      was diverging; see `SOL_VERDICT_ROUND6_2026-07-16.md`). **Decision D re-scoped the
      design** (no machine release authority; terminal benchmark) and reframed Sol's
      task: verify claims, check the benchmark, name residual risks, find scope leaks.
      Round 7 running on the v3 doc.
- [ ] **Only then build.**

---

## 🟡 THE BUILD, once approved

### Make it actually work (it currently doesn't)
- [ ] **Dispatch.** Nothing runs today. *"Chief says 'Putting Riggs on it,' and no
      builder starts."* The text app returns "Got it" and starts nothing; the voice Chief
      has no dispatch tool.
- [ ] **Wire the streaming Chief to the voice.** `chief_live.py` is real and hits ~1.4s
      to first words — and is **not connected**. The voice path still spawns a fresh
      process per utterance, which is the exact lag it was built to fix.
- [ ] **Make the gauntlet actually run.** `run_gauntlet()` returns a *list of names* and
      never launches anyone.
- [ ] **One worktree per agent.** They currently all share one real project folder.
- [ ] **Use the tiering.** It's written; dispatch never calls it.

### The two voice bugs Sol found
- [ ] **Force the tool call.** `tool_choice: "auto"` lets the mouth **answer on its own
      instead of calling Chief** — the whole "everything goes to Chief" rule is
      unenforced.
- [ ] **Accept that "verbatim forwarding" is impossible.** The mouth hears audio and
      *generates* a text argument — that's another model interpreting him, not a
      transcript. Design around it.

### Fix the record layer
- [ ] **Tie reviews to a version.** Today: approve version A, builder changes it to B,
      **the old approval still counts.** Sol calls this the most dangerous flaw — it
      produces *"believable green checks on code nobody reviewed."*
- [ ] **A fail must condemn a VERSION, not the job forever.** Right now a fixed build can
      never ship.
- [ ] **Block DELETING verdicts** (editing fail→pass is blocked; deleting isn't).
- [ ] **Reserve budget BEFORE the provider call.** Refusing to *record* a charge doesn't
      unspend the money.
- [ ] **Agents get NO write access to the safety database.** *"The database cannot be the
      jailer if the prisoner can rewrite the jail."*

### The missing pieces
- [ ] **A trusted gatekeeper service** holding all irreversible powers (merge, deploy,
      spend). Agents *ask*; it checks the approval, does exactly that one thing, refuses
      everything else. **This is what makes a database permission mean anything.**
- [ ] **A hard emergency stop** that doesn't depend on Chief being alive.
- [ ] **Separate cancellation** for: the speech, Chief's current thought, queued work, a
      running builder. *("No, wait" currently stops the mouth but NOT the work.)*
- [ ] **Numbered conversation turns** so an old answer can't arrive after a newer
      correction.
- [ ] **Duplicate protection** so a retry can't start the same job twice.
- [ ] **Crash recovery** — reconcile jobs after sleep, power loss, network failure,
      OpenClaw restart.
- [ ] **Latency guard.** Chief spiked to 20–35s. A silent car is a failure — hard timeout
      → *"I'm struggling, nothing's started."*
- [ ] **A realistic driving test set** — road noise, passengers, radio, weak signal,
      interruptions, self-corrections.

---

## ⚫ RULES THAT ARE SETTLED — don't relitigate

- **Voice = telephone.** It classifies nothing. Every attempt leaked.
- **Chief is NOT a security boundary.** The doors are (GitHub, missing credentials, the
  kernel).
- **The threat is NOT Neill.** It's (1) agents doing unasked things, (2) prompt
  injection, (3) mishearing. Don't build ceremony that guards against him.
- **No approving dangerous actions by voice while driving.** Chief explains aloud;
  approval waits for a written tap when stopped.
- **Adopt OpenClaw as the engine. Stop rebuilding it.**
- **Get Sol to attack everything. Don't trust green tests.** Three reviews found 9 flaws,
  then 18, then a hollow core. Claude found none of them itself.
