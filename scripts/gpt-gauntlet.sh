#!/usr/bin/env bash
# GPT-only reviewer gauntlet for Codex sessions in Chief Command.
#
# Runs four codex reviewer personas over the current branch's diff vs a base ref.
# All four seats are ONE model family, so this COMPLEMENTS but never replaces the
# cross-family panel (Claude + GPT + Grok) that the harness itself runs before
# anything merges — see AGENTS.md rules 6-8.
#
# Usage:  ./scripts/gpt-gauntlet.sh [base-ref]      (default base: origin/main)
# Output: docs/gpt/gauntlet/<stamp>-<branch>/<seat>.md — read all four, fix every
#         CRITICAL and HIGH, commit, re-run until every seat says GO, then log the
#         folder + verdicts in docs/gpt/GPT_WORKLOG.md.
#
# Two lessons from the Arch repo's version of this script are built in:
#   * `--sandbox read-only` has been seen to edit files anyway (2026-07-31). So the
#     working tree is checked after EVERY seat, and a dirty tree fails the run.
#   * codex treats a non-tty stdin as more input and waits for EOF forever, which
#     hangs unattended runs. Every call reads from /dev/null.
set -uo pipefail
BASE="${1:-origin/main}"
BRANCH=$(git branch --show-current)
case "$BRANCH" in
  gpt/*) ;;
  *) echo "Run this from a gpt/* work branch (you are on '${BRANCH:-detached}')." >&2; exit 1 ;;
esac
# Pinned on purpose (rule 2): the codex default model changes under you; a review
# from a different model than yesterday's is a different review. Bump deliberately.
CODEX_MODEL="${CODEX_MODEL:-gpt-5.6-sol}"
if ! command -v codex >/dev/null 2>&1; then
  echo "codex CLI not found on PATH." >&2; exit 1
fi
if [ -n "$(git status --porcelain)" ]; then
  echo "Working tree is not clean (tracked or untracked). Commit or stash first — the" >&2
  echo "gauntlet reviews commits, and it needs a clean tree to know what a reviewer touched." >&2
  exit 1
fi
OUT="docs/gpt/gauntlet/$(date +%m%d-%H%M)-${BRANCH//\//_}"
mkdir -p "$OUT"
echo "Gauntlet: $BRANCH vs $BASE → $OUT/"

COMMON="You are one seat of a multi-seat code review in this repo. Review ONLY the changes on branch $BRANCH relative to $BASE: start from 'git diff $BASE...HEAD' and read as much surrounding live code as you need to judge it (never review the diff blind). Read AGENTS.md first — its rules bind the code (seats not vendor names; no route around the database guards; text-first; no machine-specific paths); flag any drift as a finding. Run the tests with '.venv/bin/python -m pytest harness/tests/ -q' and report the count. Report ranked findings (CRITICAL/HIGH/MEDIUM/LOW), each with file:line and a CONCRETE failure scenario (inputs/state -> wrong outcome). Refute your own weak findings before reporting. End with an explicit verdict line: GO or NO-GO."

FAILED=0
run_seat() {
  local name="$1" lens="$2"
  echo "── seat: $name"
  if codex exec -m "$CODEX_MODEL" -c model_reasoning_effort=high --sandbox read-only \
       "$COMMON Your lens: $lens" </dev/null >"$OUT/$name.md" 2>"$OUT/$name.err"; then
    echo "   done → $OUT/$name.md  (verdict: $(grep -oE '\b(NO-GO|GO)\b' "$OUT/$name.md" | tail -1 || echo 'none found'))"
  else
    echo "   FAILED — see $OUT/$name.err"; FAILED=1
  fi
  # A changed tree after a read-only seat is a broken reviewer, not a review. We do
  # NOT revert: this script cannot tell a reviewer's edit from an edit Neill made in
  # another window while it ran, and 'git checkout -- .' would erase his. Fail loudly,
  # leave the tree exactly as found, and let a person look. (The run's own output dir
  # is the one expected untracked path.)
  local dirty
  dirty=$(git status --porcelain | grep -v "^?? $OUT" || true)
  if [ -n "$dirty" ]; then
    echo "   ⚠ the working tree changed while seat '$name' ran (read-only sandbox):" >&2
    echo "$dirty" >&2
    echo "   Nothing was reverted. Inspect with 'git diff', then decide. This run does not count." >&2
    FAILED=1
  fi
}

run_seat bughunter "RUNTIME BUGS — exception paths that leave a job half-written, sqlite transaction misuse, None handling, thread-safety in the review panel, off-by-one in the review floors, contracts drifting between gatekeeper.py and the schema triggers."
run_seat security  "SECURITY — any way an agent can merge, deploy, or spend without the gatekeeper; shell/git injection via job fields; API keys reaching logs, events, or the database; SQL built from strings; a reviewer sending anything beyond the bundle it was handed; anything bound to a public interface instead of loopback/tailnet."
run_seat wiring    "WIRING — is every change reachable end-to-end (config → seat sync → dispatch → worker → panel → record → dashboard)? Orphaned code, dead flags, built-but-never-called functions, a runner nothing routes to, a migration the live DB never got."
run_seat hygiene   "HYGIENE + SPEC MATCH — hardcoded values that belong in seats.toml, provider names where a seat id belongs, stale comments contradicting new behaviour, dead code, test quality (would the new tests pass without the change?), and does the diff match the queue task's stated intent with no scope creep? Anything Neill will read must be plain English — flag filenames or jargon in spoken_summary / status strings."

echo
if [ "$FAILED" -ne 0 ]; then
  echo "At least one seat FAILED or misbehaved. This run does not count. Fix and re-run."
  exit 1
fi
echo "All seats done. Read the four reports in $OUT/."
echo "Fix every CRITICAL and HIGH, commit, and RE-RUN this script until all seats say GO."
echo "Then log the outcome (folder path + verdicts) in docs/gpt/GPT_WORKLOG.md."
echo "REMINDER: this does NOT clear anything to merge. The harness's cross-family panel does that."
