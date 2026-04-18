"""Hold VAD open for 15s with fake audio and verify speech events fire."""

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forge_browser import launch_authed_page  # noqa: E402


async def main() -> int:
    url = os.getenv("APP_URL", "http://localhost:8000")
    pw = os.getenv("OWNER_PASSWORD", "chief")
    wav = str(Path(__file__).parent / "fixtures" / "hello_chief.wav")

    browser, ctx, page = await launch_authed_page(
        url,
        pw,
        extra_args=[
            "--use-fake-device-for-media-stream",
            "--use-fake-ui-for-media-stream",
            f"--use-file-for-fake-audio-capture={wav}",
            "--allow-file-access-from-files",
        ],
    )
    await ctx.grant_permissions(["microphone"], origin=url)

    await page.goto(f"{url}/voice", wait_until="networkidle", timeout=15_000)
    btn = await page.query_selector("button:has(svg.lucide-mic)")
    if not btn:
        print("[fail] start button missing")
        return 1
    await btn.click()
    print("[action] tapped start")

    # Observe debug strip over 12s
    last_snapshot = None
    deadline = time.time() + 12
    while time.time() < deadline:
        snap = await page.evaluate("""
            (() => {
                const out = {};
                for (const r of document.querySelectorAll('div')) {
                    const t = r.textContent || '';
                    if (t.startsWith('VAD status')) out.status = r.querySelectorAll('span')[1]?.textContent;
                    else if (t.startsWith('Frames processed')) out.frames = r.querySelectorAll('span')[1]?.textContent;
                    else if (t.startsWith('Speech events')) out.events = r.querySelectorAll('span')[1]?.textContent;
                    else if (t.startsWith('Last audio samples')) out.samples = r.querySelectorAll('span')[1]?.textContent;
                }
                return out;
            })()
        """)
        if snap != last_snapshot:
            print(f"  t={time.time()-deadline+12:5.1f}  {snap}")
            last_snapshot = snap
        await asyncio.sleep(0.5)

    final = await page.evaluate("""
        (() => {
            const out = {};
            for (const r of document.querySelectorAll('div')) {
                const t = r.textContent || '';
                if (t.startsWith('VAD status')) out.status = r.querySelectorAll('span')[1]?.textContent;
                else if (t.startsWith('Frames processed')) out.frames = r.querySelectorAll('span')[1]?.textContent;
                else if (t.startsWith('Speech events')) out.events = r.querySelectorAll('span')[1]?.textContent;
                else if (t.startsWith('Last audio samples')) out.samples = r.querySelectorAll('span')[1]?.textContent;
            }
            return out;
        })()
    """)
    print(f"\nfinal: {final}")

    await browser.close()

    frames = int(final.get("frames") or 0)
    events = final.get("events") or ""
    ok = final.get("status") == "listening" and frames > 100
    print(f"\nOVERALL: {'PASS' if ok else 'FAIL'} — status={final.get('status')} frames={frames} events={events}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
