#!/bin/bash
set -e

echo "→ Building frontend..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR/frontend"
npm run build

echo "→ Deploying to Netlify..."
netlify deploy --prod --dir=dist --site=chief-command

echo "✓ Deployed!"
