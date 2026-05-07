#!/bin/bash

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

cd "$PROJECT_DIR"

CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$CURRENT_BRANCH" != "main" ]; then
  echo "[$(date)] On feature branch '$CURRENT_BRANCH' — skipping auto-update."
  exit 0
fi

git fetch origin main --quiet

BEHIND=$(git rev-list main..origin/main --count)

if [ "$BEHIND" -gt 0 ]; then
  echo "[$(date)] Origin/main has $BEHIND new commit(s) — updating..."
  git pull --ff-only origin main || { echo "[$(date)] Non-fast-forward; aborting."; exit 1; }
  cd "$PROJECT_DIR/frontend"
  npm run build
  pm2 restart chief-backend
  echo "[$(date)] Update complete."
else
  echo "[$(date)] Up to date with origin/main (local may be ahead, that's fine)."
fi
