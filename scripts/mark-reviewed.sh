#!/usr/bin/env sh
# Record that the reviewers ran on the CURRENT commit. The pre-push hook reads this
# and refuses to push anything the reviewers have not seen. New commits invalidate it.
# Running this before the reviewers actually ran is lying to the gate — don't.
set -e
git rev-parse --show-toplevel >/dev/null 2>&1 || { echo "Not in a git repo."; exit 1; }
# --git-common-dir so linked worktrees share one marker.
MARKER="$(git rev-parse --git-common-dir)/.reviewers-ran-at-hash"
HEAD_SHA=$(git rev-parse HEAD)
printf '%s\n' "$HEAD_SHA" > "$MARKER"
echo "✓ Marked $HEAD_SHA as reviewed. Any new commit after this blocks push again."
