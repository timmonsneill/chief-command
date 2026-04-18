"""Forge ad-hoc voice page smoke — validates the VAD stale-closure fix.

Verifies:
  1. Login
  2. /voice renders
  3. Tapping "Tap to start voice" triggers navigator.mediaDevices.getUserMedia
  4. VAD status flips idle -> starting -> listening
  5. Frame counter in the debug strip advances > 0
  6. No fatal console errors
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Ensure we can import the harness
sys.path.insert(0, str(Path(__file__).resolve().parent))

from forge_browser import launch_authed_page  # noqa: E402


APP_URL = os.getenv("APP_URL", "http://localhost:8000")
PASSWORD = os.getenv("OWNER_PASSWORD", "chief")
WAV_PATH = str(Path(__file__).parent / "fixtures" / "hello_chief.wav")


async def main() -> int:
    if not Path(WAV_PATH).exists():
        print(f"[warn] fake-audio WAV missing at {WAV_PATH} — mic calls will still work, just silent")
        fake_audio_args = [
            "--use-fake-device-for-media-stream",
            "--use-fake-ui-for-media-stream",
        ]
    else:
        fake_audio_args = [
            "--use-fake-device-for-media-stream",
            "--use-fake-ui-for-media-stream",
            f"--use-file-for-fake-audio-capture={WAV_PATH}",
            "--allow-file-access-from-files",
            "--autoplay-policy=no-user-gesture-required",
        ]

    results: dict = {}
    errors_all: list[str] = []
    console_all: list[str] = []

    browser, context, page = await launch_authed_page(
        APP_URL,
        PASSWORD,
        extra_args=fake_audio_args,
    )

    # Auto-grant mic permission
    await context.grant_permissions(["microphone"], origin=APP_URL)

    page.on("pageerror", lambda err: errors_all.append(f"[pageerror] {err}"))
    page.on(
        "console",
        lambda msg: (
            console_all.append(f"[{msg.type}] {msg.text}")
            if msg.type in ("error", "warning")
            else None
        ),
    )

    # Inject a getUserMedia watcher BEFORE navigating.
    await context.add_init_script("""
        (function() {
            window.__gumCalls = 0;
            window.__gumConstraints = [];
            if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
                const orig = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
                navigator.mediaDevices.getUserMedia = function(constraints) {
                    window.__gumCalls += 1;
                    try { window.__gumConstraints.push(JSON.stringify(constraints)); } catch {}
                    return orig(constraints);
                };
            }
        })();
    """)

    try:
        # --- Check 1: /voice renders ---
        await page.goto(f"{APP_URL.rstrip('/')}/voice", wait_until="networkidle", timeout=15_000)
        body_text = await page.inner_text("body")
        results["voice_rendered"] = "Tap to start voice" in body_text or "voice" in body_text.lower()
        print(f"[check] /voice renders: {results['voice_rendered']}")

        # Take pre-tap screenshot
        await page.screenshot(path="/tmp/chief-smoke/voice-pre-tap.png")

        # Check VAD status text — should be 'idle' before tap
        vad_status_pre = None
        for candidate in ["idle", "starting", "listening", "error"]:
            if candidate in body_text:
                vad_status_pre = candidate
                break
        results["vad_status_pre_tap"] = vad_status_pre
        print(f"[check] VAD status pre-tap: {vad_status_pre}")

        # --- Check 2: tap the mic button ---
        # The idle screen has a round Mic button with text "Tap to start voice"
        tap_button = await page.query_selector("button:has(svg.lucide-mic)")
        if not tap_button:
            # Try alt selector
            tap_button = await page.query_selector("button")
        if not tap_button:
            print("[fail] could not find start button")
            results["tap_found"] = False
            return 1
        results["tap_found"] = True

        # Click it
        await tap_button.click()
        print("[action] tapped mic button")

        # --- Check 3: wait for status to flip ---
        # We'll poll up to 8s for status text "listening" or "starting" to appear.
        status_progression: list[str] = []
        listening_seen = False
        starting_seen = False
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                txt = await page.inner_text("body")
            except Exception:
                await asyncio.sleep(0.25)
                continue
            # Parse out the debug-strip status line — look for "VAD status" block
            if "listening" in txt and "VAD status" in txt:
                listening_seen = True
                status_progression.append("listening")
                break
            if "starting" in txt and "VAD status" in txt:
                if "starting" not in status_progression:
                    status_progression.append("starting")
                    starting_seen = True
            if "error" in txt and "VAD error" in txt:
                status_progression.append("error")
                break
            await asyncio.sleep(0.2)

        results["vad_status_progression"] = status_progression
        results["vad_listening_reached"] = listening_seen
        print(f"[check] VAD status progression: {status_progression}")

        # --- Check 4: gUM was called ---
        gum_calls = await page.evaluate("window.__gumCalls || 0")
        gum_constraints = await page.evaluate("window.__gumConstraints || []")
        results["gum_calls"] = gum_calls
        results["gum_constraints"] = gum_constraints
        print(f"[check] navigator.mediaDevices.getUserMedia called: {gum_calls} time(s)")
        if gum_constraints:
            print(f"        constraints: {gum_constraints}")

        # --- Check 5: frame counter advances ---
        # Wait up to 5s, then parse the debug strip for "Frames processed".
        # Use JS to grab the specific element text.
        frame_count_final = 0
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                frames_text = await page.evaluate("""
                    (() => {
                        const rows = document.querySelectorAll('div');
                        for (const r of rows) {
                            if (r.textContent && r.textContent.startsWith('Frames processed')) {
                                const spans = r.querySelectorAll('span');
                                if (spans.length >= 2) return spans[1].textContent;
                            }
                        }
                        return null;
                    })()
                """)
                if frames_text:
                    try:
                        frame_count_final = int(frames_text.strip())
                        if frame_count_final > 0:
                            break
                    except ValueError:
                        pass
            except Exception:
                pass
            await asyncio.sleep(0.25)

        results["frame_count_final"] = frame_count_final
        print(f"[check] VAD frameCount after start: {frame_count_final}")

        await page.screenshot(path="/tmp/chief-smoke/voice-post-tap.png")

        # --- Check 6: /api/status reachable ---
        try:
            api_result = await page.evaluate("""
                fetch('/api/status', {credentials: 'include'})
                    .then(r => r.status)
                    .catch(e => 'fetch_error: ' + e.message)
            """)
            results["api_status_response"] = api_result
            print(f"[check] /api/status via page fetch: {api_result}")
        except Exception as e:
            results["api_status_response"] = f"error: {e}"

        # --- Check 7: capture any VAD error text from the strip ---
        vad_error_text = await page.evaluate("""
            (() => {
                const rows = document.querySelectorAll('div');
                for (const r of rows) {
                    if (r.textContent && r.textContent.startsWith('VAD error')) {
                        const spans = r.querySelectorAll('span');
                        if (spans.length >= 2) return spans[1].textContent;
                    }
                }
                return null;
            })()
        """)
        results["vad_error_text"] = vad_error_text
        if vad_error_text:
            print(f"[check] VAD error strip: {vad_error_text}")

    finally:
        results["console_errors"] = console_all
        results["pageerrors"] = errors_all
        await browser.close()

    print("\n=== Results ===")
    print(json.dumps(results, indent=2, default=str))
    print("\n=== Console errors/warnings ===")
    for c in console_all[:30]:
        print("  ", c)
    print("\n=== Uncaught page errors ===")
    for e in errors_all[:30]:
        print("  ", e)

    # Pass criteria
    ok = (
        results.get("voice_rendered")
        and results.get("tap_found")
        and results.get("gum_calls", 0) >= 1
        and results.get("vad_listening_reached")
        and results.get("frame_count_final", 0) > 0
        and not errors_all
    )
    print(f"\nOVERALL: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
