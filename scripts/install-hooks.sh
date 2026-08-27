#!/usr/bin/env sh
# Hooks are not tracked by git; this puts ours in place. Run once per clone.
set -e
ROOT=$(git rev-parse --show-toplevel)
cp "$ROOT/scripts/hooks/pre-push" "$(git rev-parse --git-common-dir)/hooks/pre-push"
chmod +x "$(git rev-parse --git-common-dir)/hooks/pre-push"
echo "✓ pre-push hook installed."
