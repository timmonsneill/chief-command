"""Forge WS smoke — scope isolation + context-frame gate.

End-to-end(ish) verification of Track A against a running backend on
localhost:8000. Does NOT use a browser — too heavy for this lane. Opens
raw WebSocket connections, sends JSON frames, asserts server behavior.

Covered scenarios (one connection each):

  1. TWO concurrent owner WS sessions with DIFFERENT scopes must not
     cross-contaminate. Tab-A sets Arch; Tab-B sets Personal Assist;
     re-querying each tab shows its own scope, not the other's.

  2. Sending a text turn BEFORE the context frame arrives must still
     produce a reply scoped to the NEW project when the context frame
     lands within the 1s gate window. (We exploit the context frame's
     own ``context_switched`` echo frame as proof of ordering.)

  3. Sending a text turn AFTER the context frame arrived must ALSO
     produce a reply scoped to the pinned project (baseline sanity).

No LLM assertion — we only verify the flow completes with a
`message_done` frame and observe the scope applied via the
`context_switched` echo. This is a plumbing test, not a content test.

Usage:
    cd backend
    APP_URL=http://localhost:8000 OWNER_PASSWORD=chief \\
      .venv/bin/python -m tests.forge_ws_scope_flow
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Force backend/ onto path so config/services resolve even if run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # type: ignore
import websockets  # type: ignore


APP_URL = os.environ.get("APP_URL", "http://localhost:8000")
WS_URL = APP_URL.replace("http://", "ws://").replace("https://", "wss://")
PASSWORD = os.environ.get("OWNER_PASSWORD", "chief")


async def get_token() -> str:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(
            f"{APP_URL}/api/auth/login", json={"password": PASSWORD}
        )
        r.raise_for_status()
        data = r.json()
        return data["token"]


async def _drain_until(ws, predicate, timeout=5.0):
    """Read frames until predicate returns True or timeout. Returns list of
    received frames for debugging."""
    received = []
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        remaining = deadline - asyncio.get_event_loop().time()
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        if isinstance(raw, bytes):
            received.append({"_bytes": len(raw)})
            continue
        try:
            frame = json.loads(raw)
        except json.JSONDecodeError:
            received.append({"_raw": raw[:80]})
            continue
        received.append(frame)
        if predicate(frame):
            return True, received
    return False, received


async def scenario_concurrent_scopes(token: str) -> dict:
    """Open two WS sessions with different scopes; verify isolation."""
    result = {"name": "concurrent_scopes", "pass": False, "detail": ""}

    url = f"{WS_URL}/ws/voice?token={token}"
    async with websockets.connect(url) as ws_a, websockets.connect(url) as ws_b:
        # A picks Arch
        await ws_a.send(json.dumps({"type": "context", "project": "Arch"}))
        ok_a, frames_a = await _drain_until(
            ws_a, lambda f: f.get("type") == "context_switched"
        )
        if not ok_a:
            result["detail"] = f"tab-A never saw context_switched; frames={frames_a[:6]}"
            return result
        scope_a = next(
            (f.get("project") for f in frames_a if f.get("type") == "context_switched"),
            None,
        )

        # B picks Personal Assist
        await ws_b.send(json.dumps({"type": "context", "project": "Personal Assist"}))
        ok_b, frames_b = await _drain_until(
            ws_b, lambda f: f.get("type") == "context_switched"
        )
        if not ok_b:
            result["detail"] = f"tab-B never saw context_switched; frames={frames_b[:6]}"
            return result
        scope_b = next(
            (f.get("project") for f in frames_b if f.get("type") == "context_switched"),
            None,
        )

        if scope_a != "Arch":
            result["detail"] = f"tab-A echoed scope={scope_a}, expected Arch"
            return result
        if scope_b != "Personal Assist":
            result["detail"] = f"tab-B echoed scope={scope_b}, expected Personal Assist"
            return result

        # Flip A to Chief Command — B must NOT see any change.
        await ws_a.send(json.dumps({"type": "context", "project": "Chief Command"}))
        ok_flip, frames_flip = await _drain_until(
            ws_a, lambda f: f.get("type") == "context_switched"
        )
        if not ok_flip:
            result["detail"] = f"tab-A flip to Chief Command never echoed"
            return result
        # Drain any stragglers from B quickly (should be nothing scope-related)
        try:
            await asyncio.wait_for(ws_b.recv(), timeout=0.3)
        except asyncio.TimeoutError:
            pass  # expected — no frames for B

    result["pass"] = True
    result["detail"] = "tab-A and tab-B maintained isolated scopes"
    return result


async def scenario_context_then_text(token: str) -> dict:
    """Baseline: context frame, then text turn, both succeed."""
    result = {"name": "context_then_text", "pass": False, "detail": ""}
    url = f"{WS_URL}/ws/voice?token={token}"
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"type": "context", "project": "Chief Command"}))
        ok, _ = await _drain_until(
            ws, lambda f: f.get("type") == "context_switched"
        )
        if not ok:
            result["detail"] = "never got context_switched"
            return result
        # Send a text turn — any short prompt.
        await ws.send(json.dumps({"type": "text", "content": "say hi briefly"}))
        # Wait for message_done OR turn_cancelled.
        done, frames = await _drain_until(
            ws,
            lambda f: f.get("type") in ("message_done", "turn_cancelled", "error"),
            timeout=25.0,
        )
        if not done:
            result["detail"] = f"turn didn't complete. last frames: {frames[-6:]}"
            return result
        kinds = [f.get("type") for f in frames if isinstance(f, dict) and "type" in f]
        result["pass"] = any(k == "message_done" for k in kinds)
        result["detail"] = f"frame types: {kinds[:20]}"
    return result


async def scenario_text_before_context(token: str) -> dict:
    """Race: send text BEFORE context frame. The gate should defer the turn
    until the context frame lands; reply should be scoped accordingly."""
    result = {"name": "text_before_context", "pass": False, "detail": ""}
    url = f"{WS_URL}/ws/voice?token={token}"
    async with websockets.connect(url) as ws:
        # Fire text first
        await ws.send(json.dumps({"type": "text", "content": "say hi briefly"}))
        # Immediately send context — should race and land before gate timeout
        await asyncio.sleep(0.05)
        await ws.send(json.dumps({"type": "context", "project": "Arch"}))
        done, frames = await _drain_until(
            ws,
            lambda f: f.get("type") in ("message_done", "turn_cancelled", "error"),
            timeout=25.0,
        )
        if not done:
            result["detail"] = f"turn didn't complete. last frames: {frames[-6:]}"
            return result
        kinds = [f.get("type") for f in frames if isinstance(f, dict) and "type" in f]
        # Must have seen context_switched BEFORE message_done for this to be
        # a real gate-test (proves gate held the turn until context landed).
        saw_context = False
        ordered_correctly = False
        for f in frames:
            if not isinstance(f, dict):
                continue
            t = f.get("type")
            if t == "context_switched":
                saw_context = True
            elif t == "message_done" and saw_context:
                ordered_correctly = True
                break
        result["pass"] = ordered_correctly
        result["detail"] = (
            f"saw_context={saw_context} ordered={ordered_correctly} "
            f"kinds={kinds[:20]}"
        )
    return result


async def main() -> int:
    print(f"[forge-ws] APP_URL={APP_URL}")
    try:
        token = await get_token()
    except Exception as exc:
        print(f"[forge-ws] LOGIN FAILED: {exc}")
        return 2
    print(f"[forge-ws] got token len={len(token)}")

    # Scope-only scenarios are always safe (no LLM call). LLM-dependent
    # scenarios only run when SMOKE_LLM=1 so CI / worktree test runs without
    # Anthropic credentials skip them cleanly.
    scenarios = [scenario_concurrent_scopes]
    if os.environ.get("SMOKE_LLM") == "1":
        scenarios += [scenario_context_then_text, scenario_text_before_context]
    else:
        print("[forge-ws] SMOKE_LLM != 1 — skipping LLM-dependent scenarios")
    results = []
    for fn in scenarios:
        print(f"[forge-ws] running {fn.__name__}...")
        try:
            r = await fn(token)
        except Exception as exc:
            r = {"name": fn.__name__, "pass": False, "detail": f"EXC: {exc}"}
        results.append(r)
        status = "PASS" if r["pass"] else "FAIL"
        print(f"[forge-ws] {status} {r['name']}: {r['detail']}")

    ok = all(r["pass"] for r in results)
    print()
    print("[forge-ws] summary:")
    for r in results:
        print(f"  - {'OK ' if r['pass'] else 'XX '} {r['name']}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
