#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "→ Pulling latest code..."
cd "$PROJECT_DIR"
git pull

echo "→ Building frontend..."
cd "$PROJECT_DIR/frontend"
npm run build

echo "→ Restarting backend..."
pm2 restart chief-backend

echo "✓ Chief Command updated and running."
