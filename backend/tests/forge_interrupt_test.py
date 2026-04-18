"""Verify the {type: interrupt} -> {type: turn_cancelled, reason: barge-in} flow."""

import asyncio
import json
import os
import sys

import httpx
import websockets


async def main() -> int:
    app_url = os.getenv("APP_URL", "http://localhost:8000")
    password = os.getenv("OWNER_PASSWORD", "chief")

    # 1. Login -> JWT
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{app_url}/api/auth/login", json={"password": password})
        if r.status_code != 200:
            print(f"[fail] login returned {r.status_code}: {r.text}")
            return 1
        token = r.json()["token"]
        print(f"[ok] got token ({len(token)} chars)")

    # 2. Open WS
    ws_url = app_url.replace("http", "ws") + f"/ws/voice?token={token}"
    print(f"[action] connecting to {ws_url[:80]}...")

    turn_cancelled_received = False
    turn_cancelled_reason = None
    tokens_before_cancel = 0
    got_any_token = False
    all_frames: list = []

    async with websockets.connect(ws_url) as ws:
        # Fire a text turn that will produce a long response, so we have time
        # to interrupt it.
        long_prompt = (
            "Please count slowly from one to fifty, one number per line, "
            "and include a short comment on each number. Be thorough and verbose."
        )
        await ws.send(json.dumps({"type": "text", "content": long_prompt}))
        print("[action] sent text turn")

        # Wait for first token to arrive so we know streaming has started.
        deadline = asyncio.get_event_loop().time() + 30
        interrupt_sent = False
        while asyncio.get_event_loop().time() < deadline:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=5)
            except asyncio.TimeoutError:
                print("[fail] timed out waiting for frames")
                break

            if isinstance(msg, bytes):
                all_frames.append(("bytes", len(msg)))
                continue

            all_frames.append(("text", msg[:120]))
            try:
                data = json.loads(msg)
            except Exception:
                continue

            t = data.get("type")

            if t == "token":
                got_any_token = True
                if not interrupt_sent:
                    tokens_before_cancel += 1
                # After ~5 tokens, send the interrupt.
                if tokens_before_cancel >= 5 and not interrupt_sent:
                    print(f"[action] sending interrupt after {tokens_before_cancel} tokens")
                    await ws.send(json.dumps({"type": "interrupt"}))
                    interrupt_sent = True

            if t == "turn_cancelled":
                turn_cancelled_received = True
                turn_cancelled_reason = data.get("reason")
                print(f"[ok] received turn_cancelled reason={turn_cancelled_reason}")
                break

            if t == "message_done":
                if interrupt_sent:
                    # Sometimes message_done still lands if cancel arrived after stream finished.
                    print("[info] got message_done after interrupt")
                else:
                    print("[fail] stream completed before interrupt fired")
                    break

    print(f"\n[summary] got_any_token={got_any_token} tokens_before_cancel={tokens_before_cancel} "
          f"turn_cancelled={turn_cancelled_received} reason={turn_cancelled_reason}")
    print(f"[summary] total frames observed: {len(all_frames)}")

    ok = got_any_token and turn_cancelled_received and turn_cancelled_reason == "barge-in"
    print(f"\nOVERALL: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
