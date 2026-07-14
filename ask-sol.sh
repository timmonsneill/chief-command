#!/bin/bash
# Send the architecture doc to Sol for adversarial review.
# Run:  ./ask-sol.sh
# Each round's full transcript is kept in docs/sol/ so the attack→fix→re-attack
# history survives reboots (the old version wrote to a /tmp scratchpad and nearly
# lost round 2).
set -u
REPO="$(cd "$(dirname "$0")" && pwd)"
PROMPT="$REPO/docs/sol/sol_design_review.txt"
N=1
while [ -e "$REPO/docs/sol/sol_round${N}.out" ]; do N=$((N + 1)); done
OUT="$REPO/docs/sol/sol_round${N}.out"

echo "Sending the design to Sol at high effort (round $N). This takes 5-15 minutes..."
codex exec -c model_reasoning_effort=high "$(cat "$PROMPT")" > "$OUT" 2>&1
echo
echo "================ SOL'S REVIEW (round $N) ================"
tail -c 14000 "$OUT"
echo
echo "Full transcript: $OUT"
