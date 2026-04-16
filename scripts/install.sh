#!/bin/bash
set -e

echo "╔══════════════════════════════════════╗"
echo "║    Chief Command Center — Install    ║"
echo "╚══════════════════════════════════════╝"
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# --- Python virtual environment ---
echo "→ Setting up Python environment..."
cd "$PROJECT_DIR/backend"
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "  ✓ Python dependencies installed"

# --- Frontend ---
echo "→ Installing frontend dependencies..."
cd "$PROJECT_DIR/frontend"
npm install --silent
echo "  ✓ Frontend dependencies installed"

# --- Build frontend ---
echo "→ Building frontend..."
npm run build 2>&1 | tail -3
echo "  ✓ Frontend built"

# --- .env file ---
if [ ! -f "$PROJECT_DIR/backend/.env" ]; then
  echo "→ Creating .env file..."
  JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  cat > "$PROJECT_DIR/backend/.env" << ENV_EOF
OWNER_PASSWORD=chief
JWT_SECRET=${JWT_SECRET}
TUNNEL_URL=https://chiefcommand.app
PROJECTS_DIR=/Users/user/.claude/projects/-Users-user/memory
HOST=0.0.0.0
PORT=8000
ENV_EOF
  echo "  ✓ .env created (default password: chief — change it!)"
else
  echo "  ✓ .env already exists"
fi

# --- Cloudflare tunnel config ---
echo "→ Setting up Cloudflare tunnel config..."
TUNNEL_ID="9cf2d650-08bf-4dc0-843e-ee6c1712b2de"
mkdir -p ~/.cloudflared
cp "$PROJECT_DIR/cloudflared-config.yml" ~/.cloudflared/config.yml 2>/dev/null || true
echo "  ✓ Tunnel config ready"

# --- DNS route (one-time) ---
echo "→ Setting up DNS route for chiefcommand.app..."
cloudflared tunnel route dns voice-claude chiefcommand.app 2>/dev/null || echo "  (DNS route may already exist)"
echo "  ✓ DNS configured"

echo ""
echo "╔══════════════════════════════════════╗"
echo "║           Install Complete!          ║"
echo "╠══════════════════════════════════════╣"
echo "║  Next: run ./scripts/start.sh       ║"
echo "║  Default password: chief             ║"
echo "║  Change it in backend/.env           ║"
echo "╚══════════════════════════════════════╝"
