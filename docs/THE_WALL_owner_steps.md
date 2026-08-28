# The Wall — three things only Neill can do (about an hour, once)

The gatekeeper is a **door**: it checks the record before anything merges, deploys, or
spends. But every agent still runs as *you*, on *your* account, with *your* keys — so a
compromised or confused agent could walk around the door. These three steps are the
wall. Nothing I build substitutes for them; each is enforced somewhere no agent can
argue with.

## 1. Turn on branch protection for `main` (5 minutes) — do this first

This is enforced on GitHub's servers. Even if everything on this Mac goes wrong, nobody
— no agent, no model, not even you by accident — can force-push or write straight to
`main`.

1. Open https://github.com/timmonsneill/chief-command/settings/branches
2. Click **Add branch ruleset** (or "Add rule" on older layouts).
3. Ruleset name: `protect main`. Enforcement: **Active**. Target branches: add
   **Default branch** (that's `main`).
4. Tick: **Restrict deletions**, **Block force pushes**, **Require a pull request
   before merging** (required approvals can stay at 0 — the point is that direct pushes
   to `main` are refused; merges go through a pull request the record can be checked
   against).
5. Save. Then, on your Mac, ask me (or Codex) to switch the harness to "push a branch and
   open a pull request" instead of pushing `main` — that's a small change on our side
   and it's expected.

Do the same for the Arch repo when you're ready.

## 2. Get the real keys off this machine (15 minutes)

Today the keys live in `~/.chief/env` and are loaded into every process the harness
starts — including the agents. An agent that can read the disk can read them.

The fix has two halves; do the first now, the second with step 3:

- **Now:** open your OpenAI, xAI and Anthropic dashboards and set **hard monthly spend
  limits** on each key ($100 / $30 / $100 are sensible; you can raise them later).
  This caps the damage of any leak, whatever else happens.
- **With step 3:** the keys move to a file only the harness's own account can read (see
  below), so agents running as the separate user cannot open it.

Also: search your shell profile files (`~/.zshrc`, `~/.zprofile`, `~/.bash_profile`) for
any line starting with `export` that contains `KEY` or `TOKEN`, and delete those lines.
Keys should live in exactly one place.

## 3. A separate macOS account for the agents (30–40 minutes, needs your password)

Right now, "agent" and "Neill" are the same user to the operating system. Once they're
different users, the record (`chief.db`), the keys, and the real repository can be
made unreadable/unwritable to the agents — and then the gatekeeper is the **only** way
through, by construction rather than by politeness.

1. System Settings → Users & Groups → **Add Account** → Standard (not Admin). Name it
   `chiefagent`. Give it a long random password and store it in your password manager.
2. Tell me (or Codex) it exists. From there the harness-side work is: run agents as
   that user, keep the record and keys owned by *your* account, and give agents a
   directory of their own for worktrees. That is the queued "run the gatekeeper as its
   own process" task — it's the first thing to do *after* this account exists, not
   before.

## What to say when it's done

Just: "branch protection is on", "limits are set", "chiefagent exists". I'll take it
from there — including switching pushes to pull requests, which will start failing on
purpose the moment step 1 is on.
