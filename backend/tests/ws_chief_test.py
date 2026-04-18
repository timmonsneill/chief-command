"""Integration test for Chief Context v1 respin.

Logs in, opens the voice WebSocket, runs three scenarios:

  (a) "switch to Arch"           -> expect context_switched {project: "Arch"}
  (b) "show me all the files"    -> expect NO context_switched frame
  (c) second turn after (a)      -> expect cache_read_input_tokens > 0

Prints per-scope system prompt token counts at the end.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request
from pathlib import Path

# Allow the script to run from anywhere with deps from the main backend venv.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import websockets  # type: ignore[import-untyped]


BASE_URL = os.environ.get("CHIEF_BASE_URL", "http://127.0.0.1:8010")
WS_URL = BASE_URL.replace("http", "ws") + "/ws/voice"

OWNER_PASSWORD = os.environ.get("CHIEF_OWNER_PASSWORD")
if not OWNER_PASSWORD:
    env_file = Path("/Users/user/Desktop/chief-command/backend/.env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("OWNER_PASSWORD="):
                OWNER_PASSWORD = line.split("=", 1)[1]
                break
if not OWNER_PASSWORD:
    raise SystemExit("Need OWNER_PASSWORD env or backend/.env")


def login() -> str:
    data = json.dumps({"password": OWNER_PASSWORD}).encode()
    req = urllib.request.Request(
        BASE_URL + "/api/auth/login",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        body = json.load(resp)
    return body["token"]


async def _collect_until_done(ws, timeout: float = 60.0) -> list[dict]:
    events: list[dict] = []
    async def _recv_all() -> None:
        try:
            while True:
                msg = await ws.recv()
                if isinstance(msg, bytes):
                    events.append({"type": "__binary__", "size": len(msg)})
                    continue
                data = json.loads(msg)
                events.append(data)
                if data.get("type") == "usage":
                    return
        except websockets.ConnectionClosed:
            return
    await asyncio.wait_for(_recv_all(), timeout=timeout)
    return events


async def run() -> int:
    token = login()
    print(f"[OK] logged in, token len={len(token)}")

    # Run each scenario on its own WS connection for clarity.
    token_counts: dict[str, int] = {}
    cache_reads: dict[str, int] = {}

    async with websockets.connect(f"{WS_URL}?token={token}", max_size=10 * 1024 * 1024) as ws:
        # (a) switch to Arch
        await ws.send(json.dumps({"type": "text", "content": "Switch to Arch. What's Arch?"}))
        events = await _collect_until_done(ws)
        switched = [e for e in events if e.get("type") == "context_switched"]
        usage_a = next((e for e in events if e.get("type") == "usage"), {})
        assert switched, "expected context_switched event for 'Switch to Arch' — got none"
        assert switched[0].get("project") == "Arch", f"expected project=Arch, got {switched[0]}"
        token_counts["Arch-turn1"] = usage_a.get("input_tokens", 0)
        cache_reads["Arch-turn1"] = usage_a.get("cached_tokens", 0)
        print(f"[OK] (a) Switch to Arch -> context_switched, input={usage_a.get('input_tokens')} cached={usage_a.get('cached_tokens')}")

        # (c) second turn — Arch scope — should hit cache
        await ws.send(json.dumps({"type": "text", "content": "Any recent builds there?"}))
        events = await _collect_until_done(ws)
        usage_c = next((e for e in events if e.get("type") == "usage"), {})
        token_counts["Arch-turn2"] = usage_c.get("input_tokens", 0)
        cache_reads["Arch-turn2"] = usage_c.get("cached_tokens", 0)
        assert usage_c.get("cached_tokens", 0) > 0, f"expected cache hit on turn 2, got {usage_c}"
        print(f"[OK] (c) turn 2 cache hit: cached={usage_c['cached_tokens']} input={usage_c['input_tokens']}")

    async with websockets.connect(f"{WS_URL}?token={token}", max_size=10 * 1024 * 1024) as ws:
        # (b) false-positive check — "show me all the files"
        await ws.send(json.dumps({"type": "text", "content": "Show me all the files."}))
        events = await _collect_until_done(ws)
        switched = [e for e in events if e.get("type") == "context_switched"]
        usage_b = next((e for e in events if e.get("type") == "usage"), {})
        assert not switched, f"expected NO context_switched for 'show me all the files' but got {switched}"
        print(f"[OK] (b) false-positive guard held — no context_switched frame, input={usage_b.get('input_tokens')}")
        token_counts["ChiefCommand-default-turn1"] = usage_b.get("input_tokens", 0)
        cache_reads["ChiefCommand-default-turn1"] = usage_b.get("cached_tokens", 0)

    print("\n=== Token counts per scope/turn ===")
    for k, v in token_counts.items():
        print(f"  {k:<30} input={v:>7}  cached={cache_reads.get(k, 0):>7}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
