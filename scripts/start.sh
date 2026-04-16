#!/bin/bash
set -e

echo "╔══════════════════════════════════════╗"
echo "║   Chief Command Center — Starting   ║"
echo "╚══════════════════════════════════════╝"
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Activate Python venv
source "$PROJECT_DIR/backend/.venv/bin/activate"

# Start Cloudflare tunnel in background
echo "→ Starting Cloudflare tunnel..."
cloudflared tunnel --config ~/.cloudflared/config.yml run voice-claude &
TUNNEL_PID=$!
echo "  ✓ Tunnel running (PID: $TUNNEL_PID)"
echo "  ✓ Remote access: https://chiefcommand.app"
echo ""

# Start FastAPI backend
echo "→ Starting Chief backend..."
cd "$PROJECT_DIR/backend"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!
echo "  ✓ Backend running on http://localhost:8000 (PID: $BACKEND_PID)"
echo ""

echo "╔══════════════════════════════════════╗"
echo "║          Chief is Online!            ║"
echo "╠══════════════════════════════════════╣"
echo "║  Local:  http://localhost:8000       ║"
echo "║  Remote: https://chiefcommand.app    ║"
echo "║                                      ║"
echo "║  Press Ctrl+C to stop everything     ║"
echo "╚══════════════════════════════════════╝"

# Trap Ctrl+C to kill both processes
trap "echo ''; echo 'Shutting down...'; kill $TUNNEL_PID $BACKEND_PID 2>/dev/null; exit 0" INT TERM

# Wait for either process to exit
wait
