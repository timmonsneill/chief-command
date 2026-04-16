# Chief Command Center

Voice-enabled command center for Claude Code. Two-way voice conversations, agent monitoring, project dashboards, and terminal access — all from your phone.

## 3 Commands to Run It

```bash
# 1. Install everything (Python deps, frontend build, Cloudflare tunnel, DNS)
./scripts/install.sh

# 2. Start the backend + tunnel
./scripts/start.sh

# 3. Open on your phone
# → https://chiefcommand.app
```

## What It Does

- **Voice** — Talk to Claude Code from your phone, anywhere in the world
- **Agents** — See which builders/reviewers are running in plain English
- **Terminal** — Run shell commands on your Mac remotely
- **Projects** — Dashboard for every active project with progress tracking

## Architecture

- **Frontend:** React PWA (Vite + Tailwind, dark mode)
- **Backend:** Python FastAPI + WebSockets
- **STT:** faster-whisper (medium model, Apple Silicon optimized, local, free)
- **TTS:** Kokoro (neural voices, local, free)
- **Claude:** Pipes into Claude Code CLI via Max subscription
- **Tunnel:** Cloudflare Tunnel for remote access via https://chiefcommand.app

## Siri Shortcut

Say **"Hey Siri, Chief"** to open the voice interface.

Run `./scripts/setup-siri.sh` for setup instructions.
