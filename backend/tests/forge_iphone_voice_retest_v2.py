"""CRITICAL re-test v2 — drive a real turn via TEXT input, not VAD.

Rationale: the fake-audio device loops the WAV, causing each VAD speech-end
to immediately be superseded by the next 3.6s of the same audio. That's a
test artifact masking the real question: when a turn DOES complete,
do messages render in the DOM on iPhone viewport?

This test:
  1. Launch Chromium iPhone 14 Pro viewport.
  2. Login, navigate to /voice.
  3. Type a message in the text input and submit -> drives a real turn.
  4. Watch WS frames. Wait for 'usage' event.
  5. INSPECT DOM: are user/assistant bubbles rendered?
  6. Take screenshots so we can see what the user would see.
  7. Additionally: while connected, tap "Tap to start voice" and
     verify the orb + VAD strip layout eats the message area.

Writes to /tmp/chief-iphone-voice-v2/
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
OUT_DIR = Path("/tmp/chief-iphone-voice-v2")
OUT_DIR.mkdir(parents=True, exist_ok=True)

WS_LOG = OUT_DIR / "ws.log"
DOM_LOG = OUT_DIR / "dom-states.log"
VERDICT = OUT_DIR / "verdict.txt"

WS_LOG.write_text("")
DOM_LOG.write_text("")

VIEWPORT = {"width": 390, "height": 844}
DPR = 3.0


def log_ws(entry: dict) -> None:
    with WS_LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def log_dom(label: str, payload) -> None:
    with DOM_LOG.open("a") as f:
        f.write(f"\n=== {label} @ {time.time():.2f} ===\n")
        f.write(json.dumps(payload, indent=2, default=str) + "\n")


async def dom_snapshot(page, label: str) -> dict:
    result = await page.evaluate(
        """() => {
            const userBubbles = document.querySelectorAll('.flex.justify-end');
            const assistantBubbles = document.querySelectorAll('.flex.justify-start');
            const scrollable = document.querySelector('.flex-1.overflow-y-auto');
            let scrollRect = null;
            if (scrollable) {
                const r = scrollable.getBoundingClientRect();
                scrollRect = {
                    y: r.y, height: r.height,
                    scrollHeight: scrollable.scrollHeight,
                    innerHTMLLen: scrollable.innerHTML.length,
                };
            }
            const bubbleTexts = [];
            for (const b of [...userBubbles, ...assistantBubbles]) {
                bubbleTexts.push({
                    cls: b.className.slice(0, 40),
                    text: (b.innerText || '').slice(0, 120),
                });
            }
            return {
                viewport: {w: window.innerWidth, h: window.innerHeight},
                userBubbles: userBubbles.length,
                assistantBubbles: assistantBubbles.length,
                scrollableRect: scrollRect,
                bubbleTexts: bubbleTexts,
                bodySnippet: document.body.innerText.slice(0, 400),
            };
        }"""
    )
    log_dom(label, result)
    return result


async def main() -> int:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
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
        )
        page = await context.new_page()

        page_errors: list[str] = []
        console_errors: list[str] = []
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on(
            "console",
            lambda m: console_errors.append(f"[{m.type}] {m.text}")
            if m.type == "error"
            else None,
        )

        def attach_ws(ws):
            log_ws({"event": "ws_open", "url": ws.url, "t": time.time()})
            ws.on(
                "framesent",
                lambda payload: log_ws({
                    "dir": "send",
                    "t": time.time(),
                    "payload": payload[:400] if isinstance(payload, str) else f"<binary {len(payload)}>",
                }),
            )
            ws.on(
                "framereceived",
                lambda payload: log_ws({
                    "dir": "recv",
                    "t": time.time(),
                    "payload": payload[:400] if isinstance(payload, str) else f"<binary {len(payload)}>",
                }),
            )

        page.on("websocket", attach_ws)

        # Login
        await page.goto(APP_URL, wait_until="domcontentloaded", timeout=15_000)
        await page.wait_for_selector("input[type='password']", timeout=10_000)
        await page.fill("input[type='password']", PASSWORD)
        await page.press("input[type='password']", "Enter")
        await page.wait_for_url(lambda u: "login" not in u, timeout=10_000)

        # Nav to /voice
        await page.goto(f"{APP_URL}/voice", wait_until="networkidle", timeout=15_000)
        await page.wait_for_timeout(600)
        await page.screenshot(path=str(OUT_DIR / "01-idle-initial.png"))
        await dom_snapshot(page, "01-idle initial")

        # Type in the text input to drive a real turn (no VAD needed)
        text_input = page.locator("input[type='text'][placeholder='Type a message...']")
        await text_input.wait_for(timeout=5_000)
        await text_input.fill("say hello back in 3 words")
        await page.click("button[type='submit']:has(svg.lucide-send)")

        # Wait for usage event (full turn completed)
        start_ts = time.time()
        usage_seen = False
        token_seen = False
        message_done_seen = False
        while time.time() - start_ts < 30:
            text = WS_LOG.read_text()
            if '"type":"usage"' in text or '"type": "usage"' in text:
                usage_seen = True
            if '"type":"token"' in text:
                token_seen = True
            if '"type":"message_done"' in text:
                message_done_seen = True
            if usage_seen:
                break
            await asyncio.sleep(0.3)

        await page.wait_for_timeout(800)
        await page.screenshot(path=str(OUT_DIR / "02-after-text-turn.png"))
        text_turn_dom = await dom_snapshot(page, "02-after text turn (idle layout)")

        # Now tap "Tap to start voice" to see active-session layout
        try:
            await page.click("button:has(svg.lucide-mic)", timeout=5_000)
            await page.wait_for_timeout(1_200)
        except Exception as e:
            log_dom("click start voice FAILED", str(e))

        await page.screenshot(path=str(OUT_DIR / "03-active-session.png"))
        active_dom = await dom_snapshot(page, "03-active session layout")

        # Full-page screenshot to see the whole layout
        await page.screenshot(path=str(OUT_DIR / "04-active-fullpage.png"), full_page=True)

        # Measure message area squeeze
        layout_measure = await page.evaluate(
            """() => {
                const scrollable = document.querySelector('.flex-1.overflow-y-auto');
                const orbArea = document.querySelector('.flex.flex-col.items-center.justify-center.py-6');
                const endCall = Array.from(document.querySelectorAll('button')).find(
                    b => b.innerText && b.innerText.toLowerCase().includes('end call')
                );
                const vadStrip = Array.from(document.querySelectorAll('div')).find(
                    d => d.children.length > 0 &&
                         d.innerText && d.innerText.includes('VAD status')
                );
                const bottomControls = document.querySelector('.px-4.pb-2.pt-3.bg-surface');

                function rect(el) {
                    if (!el) return null;
                    const r = el.getBoundingClientRect();
                    return {y: Math.round(r.y), h: Math.round(r.height),
                            w: Math.round(r.width), yBottom: Math.round(r.bottom)};
                }

                return {
                    viewport: {w: window.innerWidth, h: window.innerHeight},
                    orbArea: rect(orbArea),
                    messageScroll: rect(scrollable),
                    endCallBtn: rect(endCall),
                    vadStrip: rect(vadStrip),
                    bottomControls: rect(bottomControls),
                };
            }"""
        )
        log_dom("layout measurement (iPhone active session)", layout_measure)

        # Try a second turn via text input (still in active session)
        await text_input.fill("what is 2 plus 2")
        await page.click("button[type='submit']:has(svg.lucide-send)")

        turn2_start = time.time()
        turn2_usage = False
        while time.time() - turn2_start < 25:
            text = WS_LOG.read_text()
            if text.count('"type":"usage"') >= (1 if not usage_seen else 2) or \
               text.count('"type": "usage"') >= (1 if not usage_seen else 2):
                turn2_usage = True
                break
            await asyncio.sleep(0.3)

        await page.wait_for_timeout(1_000)
        await page.screenshot(path=str(OUT_DIR / "05-after-turn2-active.png"))
        active_final = await dom_snapshot(page, "04-active after turn 2")

        # Check scroll container visible content
        scroll_visible = await page.evaluate(
            """() => {
                const sc = document.querySelector('.flex-1.overflow-y-auto');
                if (!sc) return null;
                // Walk children checking which are in viewport
                const kids = Array.from(sc.children);
                return kids.map(k => {
                    const r = k.getBoundingClientRect();
                    return {
                        text: (k.innerText || '').slice(0, 80),
                        y: Math.round(r.y),
                        h: Math.round(r.height),
                        visible: r.height > 0 && r.bottom <= window.innerHeight && r.top >= 0,
                        partialVisible: r.top < window.innerHeight && r.bottom > 0,
                    };
                });
            }"""
        )
        log_dom("message children visibility in viewport", scroll_visible)

        await browser.close()

        # Build verdict
        lines = []
        lines.append("=" * 70)
        lines.append("FORGE iPHONE VOICE RE-TEST v2 — VERDICT")
        lines.append("=" * 70)
        lines.append(f"Turn 1 (text): usage_seen={usage_seen} token={token_seen} msg_done={message_done_seen}")
        lines.append(f"Turn 2 (text, during active session): usage_seen={turn2_usage}")
        lines.append(f"Page errors: {len(page_errors)}")
        for e in page_errors:
            lines.append(f"  - {e}")
        lines.append(f"Console errors: {len(console_errors)}")
        for e in console_errors[:20]:
            lines.append(f"  - {e}")
        lines.append("")
        lines.append(">>> DOM after FIRST text turn (idle layout):")
        lines.append(f"    userBubbles: {text_turn_dom.get('userBubbles')}")
        lines.append(f"    assistantBubbles: {text_turn_dom.get('assistantBubbles')}")
        lines.append(f"    bubbleTexts: {text_turn_dom.get('bubbleTexts')}")
        lines.append("")
        lines.append(">>> DOM after second turn (ACTIVE SESSION layout — the iPhone bug surface):")
        lines.append(f"    userBubbles: {active_final.get('userBubbles')}")
        lines.append(f"    assistantBubbles: {active_final.get('assistantBubbles')}")
        lines.append(f"    bubbleTexts: {active_final.get('bubbleTexts')}")
        lines.append(f"    scrollableRect: {active_final.get('scrollableRect')}")
        lines.append("")
        lines.append(">>> LAYOUT MEASUREMENT @ iPhone 390x844 active session:")
        lines.append(f"    orbArea:        {layout_measure.get('orbArea')}")
        lines.append(f"    messageScroll:  {layout_measure.get('messageScroll')}")
        lines.append(f"    endCallBtn:     {layout_measure.get('endCallBtn')}")
        lines.append(f"    vadStrip:       {layout_measure.get('vadStrip')}")
        lines.append(f"    bottomControls: {layout_measure.get('bottomControls')}")
        lines.append("")
        lines.append(">>> Message children visibility in viewport:")
        lines.append(json.dumps(scroll_visible, indent=2)[:2000])
        lines.append("")
        lines.append(">>> Analysis:")
        if active_final.get("userBubbles", 0) > 0 or active_final.get("assistantBubbles", 0) > 0:
            ms = layout_measure.get("messageScroll") or {}
            if ms.get("h", 0) < 150:
                lines.append(f"    ROOT CAUSE: Messages ARE in DOM ({active_final.get('userBubbles')}u/{active_final.get('assistantBubbles')}a) but scroll container is only {ms.get('h')}px tall. On iPhone 390x844 the orb (192px) + label + end-call + VAD strip + bottom controls leave almost no room for messages.")
            else:
                lines.append("    Messages in DOM and scroll container appears usable. Check visibility list above.")
        else:
            lines.append("    CRITICAL: Messages NOT in DOM despite turn completion. setMessages not firing OR turn_cancelled wiping state.")

        VERDICT.write_text("\n".join(lines))
        print("\n".join(lines))

        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
