#!/bin/bash

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

cd "$PROJECT_DIR"

CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$CURRENT_BRANCH" != "main" ]; then
  echo "[$(date)] On feature branch '$CURRENT_BRANCH' — skipping auto-update."
  exit 0
fi

git fetch origin main --quiet

LOCAL=$(git rev-parse main)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" != "$REMOTE" ]; then
  echo "[$(date)] New code detected — updating..."
  git pull origin main
  cd "$PROJECT_DIR/frontend"
  npm run build
  pm2 restart chief-backend
  echo "[$(date)] Update complete."
else
  echo "[$(date)] Already up to date."
fi
