"""CRITICAL re-test: voice chat on iPhone viewport.

Prior Forge SHIP verdict was wrong — owner tested on iPhone and chat messages
never rendered despite 3 full turns completing server-side.

This test:
  1. Launches Chromium with iPhone 14 Pro viewport + fake audio (our WAV).
  2. Logs in, navigates to /voice, taps "Tap to start voice".
  3. Captures EVERY WebSocket frame sent/received to /tmp/chief-iphone-voice/ws.log.
  4. Waits for usage event (proves a turn completed).
  5. Inspects the DOM: are message bubbles rendered?
  6. If messages are in DOM, measures layout: is the scrollable message
     container actually visible at iPhone viewport?
  7. Verifies End Call button is tappable.
  8. Tests barge-in echo: during TTS playback, does VAD fire spuriously?

Writes:
  /tmp/chief-iphone-voice/ws.log           every WS frame in order
  /tmp/chief-iphone-voice/dom-states.log   DOM inspection at each key moment
  /tmp/chief-iphone-voice/{initial,after-tap,after-first-turn,full-page}.png
  /tmp/chief-iphone-voice/verdict.txt      PASS/FAIL with root cause
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright

APP_URL = "http://localhost:8000"
PASSWORD = os.environ.get("OWNER_PASSWORD", "chief")
WAV_PATH = "/tmp/hello.wav"
OUT_DIR = Path("/tmp/chief-iphone-voice")
OUT_DIR.mkdir(parents=True, exist_ok=True)

WS_LOG = OUT_DIR / "ws.log"
DOM_LOG = OUT_DIR / "dom-states.log"
VERDICT = OUT_DIR / "verdict.txt"

# iPhone 14 Pro
VIEWPORT = {"width": 390, "height": 844}
DPR = 3.0

# Reset output files
WS_LOG.write_text("")
DOM_LOG.write_text("")


def log_ws(entry: dict) -> None:
    with WS_LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def log_dom(label: str, payload: dict) -> None:
    with DOM_LOG.open("a") as f:
        f.write(f"\n=== {label} @ {time.time():.2f} ===\n")
        f.write(json.dumps(payload, indent=2, default=str) + "\n")


async def inspect_dom(page, label: str) -> dict:
    """Capture the state of the messages, orb, end call button, viewport."""
    result = await page.evaluate("""() => {
        const userBubbles = document.querySelectorAll('.flex.justify-end');
        const assistantBubbles = document.querySelectorAll('.flex.justify-start');
        const chiefBg = document.querySelectorAll('[class*="bg-chief"]');
        const surfaceBg = document.querySelectorAll('[class*="bg-surface-raised"]');

        // Scrollable message container
        const scrollable = document.querySelector('.flex-1.overflow-y-auto');
        let scrollRect = null;
        if (scrollable) {
            const r = scrollable.getBoundingClientRect();
            scrollRect = {
                x: r.x, y: r.y, width: r.width, height: r.height,
                scrollHeight: scrollable.scrollHeight,
                clientHeight: scrollable.clientHeight,
            };
        }

        // End Call button
        const endCallButtons = Array.from(document.querySelectorAll('button')).filter(
            b => b.innerText && b.innerText.toLowerCase().includes('end call')
        );
        let endCallRect = null;
        if (endCallButtons.length > 0) {
            const r = endCallButtons[0].getBoundingClientRect();
            endCallRect = {
                x: r.x, y: r.y, width: r.width, height: r.height,
                visible: r.width > 0 && r.height > 0 &&
                         r.top >= 0 && r.bottom <= window.innerHeight,
            };
        }

        // Orb container
        const orb = document.querySelector('.relative.flex.items-center.justify-center');
        let orbRect = null;
        if (orb) {
            const r = orb.getBoundingClientRect();
            orbRect = {x: r.x, y: r.y, width: r.width, height: r.height};
        }

        // VAD debug strip
        const vadStrips = Array.from(document.querySelectorAll('div')).filter(
            d => d.innerText && d.innerText.includes('VAD status')
        );
        let vadRect = null;
        if (vadStrips.length > 0) {
            const r = vadStrips[0].getBoundingClientRect();
            vadRect = {x: r.x, y: r.y, width: r.width, height: r.height};
        }

        // Text of first few messages
        const bubbleTexts = [];
        for (const b of [...userBubbles, ...assistantBubbles].slice(0, 10)) {
            bubbleTexts.push({
                class: b.className.slice(0, 50),
                text: (b.innerText || '').slice(0, 100),
            });
        }

        return {
            url: location.href,
            viewport: {w: window.innerWidth, h: window.innerHeight},
            userBubbleCount: userBubbles.length,
            assistantBubbleCount: assistantBubbles.length,
            chiefBgCount: chiefBg.length,
            surfaceRaisedBgCount: surfaceBg.length,
            scrollableRect: scrollRect,
            endCallRect: endCallRect,
            orbRect: orbRect,
            vadRect: vadRect,
            bubbleTexts: bubbleTexts,
            bodyInnerTextSnippet: document.body.innerText.slice(0, 500),
        };
    }""")
    log_dom(label, result)
    return result


async def main() -> int:
    fake_audio_args = [
        "--use-fake-device-for-media-stream",
        "--use-fake-ui-for-media-stream",
        "--autoplay-policy=no-user-gesture-required",
        f"--use-file-for-fake-audio-capture={WAV_PATH}",
        "--allow-file-access-from-files",
    ]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=fake_audio_args)
        context = await browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=DPR,
            has_touch=True,
            is_mobile=True,
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                "Mobile/15E148 Safari/604.1"
            ),
            permissions=["microphone"],
        )
        # Explicit mic grant for localhost origin too:
        await context.grant_permissions(["microphone"], origin=APP_URL)

        page = await context.new_page()

        console_errors: list[str] = []
        page_errors: list[str] = []
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on(
            "console",
            lambda m: console_errors.append(f"[{m.type}] {m.text}")
            if m.type == "error"
            else None,
        )

        # WebSocket capture
        def attach_ws(ws):
            log_ws({"event": "ws_open", "url": ws.url, "t": time.time()})
            ws.on(
                "framesent",
                lambda payload: log_ws({
                    "dir": "send",
                    "t": time.time(),
                    "size": len(payload) if payload else 0,
                    "payload": (
                        payload[:500] if isinstance(payload, str)
                        else f"<binary {len(payload)} bytes>"
                    ),
                }),
            )
            ws.on(
                "framereceived",
                lambda payload: log_ws({
                    "dir": "recv",
                    "t": time.time(),
                    "size": len(payload) if payload else 0,
                    "payload": (
                        payload[:500] if isinstance(payload, str)
                        else f"<binary {len(payload)} bytes>"
                    ),
                }),
            )
            ws.on("close", lambda: log_ws({"event": "ws_close", "t": time.time()}))

        page.on("websocket", attach_ws)

        # 1) Login
        await page.goto(APP_URL, wait_until="domcontentloaded", timeout=15_000)
        await page.wait_for_selector("input[type='password']", timeout=10_000)
        await page.fill("input[type='password']", PASSWORD)
        await page.press("input[type='password']", "Enter")
        await page.wait_for_url(lambda u: "login" not in u, timeout=10_000)

        # 2) Navigate to /voice
        await page.goto(f"{APP_URL}/voice", wait_until="networkidle", timeout=15_000)
        await page.wait_for_timeout(800)
        await page.screenshot(path=str(OUT_DIR / "initial.png"), full_page=False)
        await inspect_dom(page, "initial (before tap)")

        # 3) Tap "Tap to start voice" button
        # It's the big Mic-icon button — select by role=button containing Mic
        # The button has class including 'w-28 h-28 rounded-full ... bg-chief'
        start_btn = page.locator("button").filter(has_text="").filter(
            has=page.locator("svg")
        ).first
        # Simpler: find the button near "Tap to start voice" text
        tap_text = page.locator("text=Tap to start voice")
        await tap_text.wait_for(timeout=5_000)
        # Click the sibling button (the big orb with mic icon)
        await page.click(
            "button:has(svg.lucide-mic)",
            timeout=5_000,
        )

        await page.wait_for_timeout(1_500)
        await page.screenshot(path=str(OUT_DIR / "after-tap.png"), full_page=False)
        await inspect_dom(page, "after tap (VAD should start)")

        # 4) Wait up to 30s for a 'usage' WS frame to confirm turn completed
        start_ts = time.time()
        usage_seen = False
        transcript_seen = False
        while time.time() - start_ts < 35:
            text = WS_LOG.read_text()
            if '"type": "usage"' in text or '"type":"usage"' in text:
                usage_seen = True
            if '"type": "transcript"' in text or '"type":"transcript"' in text:
                transcript_seen = True
            if usage_seen:
                break
            await asyncio.sleep(0.5)

        # Snapshot after first turn (or timeout)
        await page.wait_for_timeout(500)
        await page.screenshot(path=str(OUT_DIR / "after-first-turn.png"), full_page=False)
        first_turn_dom = await inspect_dom(
            page,
            f"after first turn (usage_seen={usage_seen}, transcript_seen={transcript_seen})",
        )

        # Full page screenshot for layout analysis
        await page.screenshot(path=str(OUT_DIR / "full-page.png"), full_page=True)

        # 5) Inspect React state — inject a hook into the messages container to
        # read its children count + innerHTML length
        inner_dom_probe = await page.evaluate("""() => {
            const scrollable = document.querySelector('.flex-1.overflow-y-auto');
            if (!scrollable) return {scrollableFound: false};
            return {
                scrollableFound: true,
                childCount: scrollable.childElementCount,
                innerHTMLLength: scrollable.innerHTML.length,
                innerHTMLSample: scrollable.innerHTML.slice(0, 800),
                offsetHeight: scrollable.offsetHeight,
                clientHeight: scrollable.clientHeight,
                scrollHeight: scrollable.scrollHeight,
                computedDisplay: getComputedStyle(scrollable).display,
                computedOverflow: getComputedStyle(scrollable).overflowY,
            };
        }""")
        log_dom("inner message container probe", inner_dom_probe)

        # 6) End Call button tappability
        end_call_eval = await page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button')).filter(
                b => b.innerText && b.innerText.toLowerCase().includes('end call')
            );
            if (btns.length === 0) return {found: false};
            const btn = btns[0];
            const r = btn.getBoundingClientRect();
            const obstructed = document.elementFromPoint(
                r.x + r.width/2, r.y + r.height/2
            );
            return {
                found: true,
                rect: {x: r.x, y: r.y, width: r.width, height: r.height},
                insideViewport: r.top >= 0 && r.bottom <= window.innerHeight &&
                                 r.left >= 0 && r.right <= window.innerWidth,
                obstructedBy: obstructed ? {
                    tag: obstructed.tagName,
                    className: obstructed.className?.toString().slice(0, 80),
                    isTheButton: obstructed === btn || btn.contains(obstructed),
                } : null,
            };
        }""")
        log_dom("end call button tappability", end_call_eval)

        # Count ws frames by type
        ws_text = WS_LOG.read_text()
        frame_counts = {
            "transcript": ws_text.count('"type": "transcript"') + ws_text.count('"type":"transcript"'),
            "token": ws_text.count('"type": "token"') + ws_text.count('"type":"token"'),
            "tts_start": ws_text.count('"type": "tts_start"') + ws_text.count('"type":"tts_start"'),
            "tts_end": ws_text.count('"type": "tts_end"') + ws_text.count('"type":"tts_end"'),
            "turn_cancelled": ws_text.count('"type": "turn_cancelled"') + ws_text.count('"type":"turn_cancelled"'),
            "usage": ws_text.count('"type": "usage"') + ws_text.count('"type":"usage"'),
            "interrupt_sent": ws_text.count('"type": "interrupt"') + ws_text.count('"type":"interrupt"'),
            "binary_recv": ws_text.count('<binary'),
        }

        # Wait ~8s more to see if TTS echo triggers spurious interrupt
        await page.wait_for_timeout(8_000)
        ws_text_late = WS_LOG.read_text()
        late_interrupts = ws_text_late.count('"type": "interrupt"') + ws_text_late.count('"type":"interrupt"')
        interrupts_during_tts = late_interrupts - frame_counts["interrupt_sent"]

        final_dom = await inspect_dom(page, "final state (after barge-in wait)")

        await browser.close()

        # Verdict
        lines = []
        lines.append("=" * 70)
        lines.append("FORGE iPHONE VOICE RE-TEST VERDICT")
        lines.append("=" * 70)
        lines.append(f"Transcript WS frame seen: {transcript_seen}")
        lines.append(f"Usage WS frame seen: {usage_seen}")
        lines.append(f"Frame counts: {frame_counts}")
        lines.append(f"Page errors: {len(page_errors)}")
        for e in page_errors:
            lines.append(f"  - {e}")
        lines.append(f"Console errors: {len(console_errors)}")
        for e in console_errors[:20]:
            lines.append(f"  - {e}")
        lines.append("")
        lines.append("DOM @ after first turn:")
        lines.append(f"  userBubbles: {first_turn_dom.get('userBubbleCount')}")
        lines.append(f"  assistantBubbles: {first_turn_dom.get('assistantBubbleCount')}")
        lines.append(f"  scrollableRect: {first_turn_dom.get('scrollableRect')}")
        lines.append(f"  orbRect: {first_turn_dom.get('orbRect')}")
        lines.append(f"  vadRect: {first_turn_dom.get('vadRect')}")
        lines.append(f"  endCallRect: {first_turn_dom.get('endCallRect')}")
        lines.append("")
        lines.append("Inner container probe:")
        lines.append(json.dumps(inner_dom_probe, indent=2)[:1500])
        lines.append("")
        lines.append(f"Spurious interrupts during TTS echo window: {interrupts_during_tts}")
        lines.append("")
        lines.append("Final DOM:")
        lines.append(f"  userBubbles: {final_dom.get('userBubbleCount')}")
        lines.append(f"  assistantBubbles: {final_dom.get('assistantBubbleCount')}")
        lines.append(f"  bubbleTexts: {final_dom.get('bubbleTexts')}")

        VERDICT.write_text("\n".join(lines))
        print("\n".join(lines))

        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
