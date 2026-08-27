# Codex bridge for Chief Command — 2026-08-27

**What Neill asked for:** "the same thing as Arch for Chief" — open a terminal, start a
Codex session, and Codex knows the project, has its own queue of work and its own memory
of what it did, and runs its own reviewer gauntlet.

**What it is:** three plain files and three scripts. There is no memory *system*; the
memory is the queue ("what next") and the worklog ("what I did"), re-read from disk each
session. AGENTS.md is the contract Codex reads on startup (Chief already had this — its
CLAUDE.md is literally `@AGENTS.md`, so one file binds both harnesses).

## Feature Acceptance Checklist (rule 9)

Each line: what exists, what it does, where it's reachable. Verified by driving it.

| # | Item | Verified |
|---|---|---|
| 1 | Typing `codex` in the repo loads `AGENTS.md`, which now has a "Codex sessions" section with read-order, hard rules, verification bar, gauntlet, shipping, when-stuck | ✅ |
| 2 | `docs/gpt/GPT_TASK_QUEUE.md` exists, is tracked by git, has ≥1 real safe task with the TAKEN/DONE protocol | ✅ |
| 3 | `docs/gpt/GPT_WORKLOG.md` exists, is tracked by git, append-only by convention | ✅ |
| 4 | `./scripts/gpt-gauntlet.sh` refuses to run on `main` and on a dirty tree; from a `gpt/*` branch it runs 4 `codex exec` seats read-only, each from `/dev/null`, writes `docs/gpt/gauntlet/<stamp>-<branch>/<seat>.md`, prints each seat's GO/NO-GO | ⏳ run in progress |
| 5 | After every seat the script checks `git status`; a seat that modified files reverts them and fails the run | ⏳ run in progress |
| 6 | `./scripts/install-hooks.sh` installs `scripts/hooks/pre-push`; `git push` is BLOCKED with a plain message when no reviewer marker exists or the marker is older than HEAD; `./scripts/mark-reviewed.sh` on HEAD unblocks it | ✅ |
| 7 | The pre-push hook runs the harness tests and blocks a red suite | ✅ |
| 8 | Nothing in the kit references a machine-specific path (rule 4) | ✅ |

Verified 2026-08-27: 1–3 by inspection + `git check-ignore`; 6 by `git push --dry-run`
blocked then allowed after `mark-reviewed.sh` (and this exposed a stale `core.hooksPath`
pointing at a Desktop folder from before the repo moved — no hook had fired here in weeks;
removed); 7 by running the hook by hand with a red marker; 8 by grep for `/Users/`.
4–5 by the first real run, recorded in `docs/gpt/GPT_WORKLOG.md`.
