"""End-to-end voice round-trip test using Chromium's fake audio capture.

Runs Chromium with a pre-recorded WAV file spoofed as the microphone. This
lets Forge (and CI) exercise the audio-in → STT → LLM → TTS → audio-out
pipeline without real hardware.

Chromium flags used:
  --use-fake-ui-for-media-stream    auto-grants mic permission (no popup)
  --use-fake-device-for-media-stream swaps real devices for fake ones
  --use-file-for-fake-audio-capture=PATH  plays WAV as mic input

Fixtures (in backend/tests/fixtures/voice_samples/):
  query_longform.wav  — 1s silence + "Hey Chief, what time is it" + 3s silence
  barge_short.wav     — 1s silence + "Stop" + 3s silence
  silence_10s.wav     — pure silence (baseline sanity)

Usage (from backend/):
    OWNER_PASSWORD=... python -m tests.forge_voice_fakemic

Reports: transcript received, assistant reply, console [voice] speech-start /
speech-end log stream, all pageerrors.
"""

import asyncio
import os
import sys
import time
from pathlib import Path

from playwright.async_api import Page

# Allow `python -m tests.forge_voice_fakemic` or direct script execution.
sys.path.insert(0, str(Path(__file__).parent.parent))
from tests.forge_browser import launch_authed_page  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "voice_samples"

APP_URL = os.environ.get("APP_URL", "http://localhost:8000")
OWNER_PASSWORD = os.environ.get("OWNER_PASSWORD")


def fake_mic_args(wav_path: Path) -> list[str]:
    """Chromium launch args that spoof the mic with a pre-recorded WAV file.

    Chromium loops the file by default — fine for our fixtures which have
    trailing silence that keeps the mic "quiet" after the utterance.
    """
    if not wav_path.exists():
        raise FileNotFoundError(f"fixture missing: {wav_path}")
    return [
        "--use-fake-ui-for-media-stream",
        "--use-fake-device-for-media-stream",
        f"--use-file-for-fake-audio-capture={wav_path}",
        # Workaround for Chromium headless WebAudio: without this, the Web
        # Audio API can stay suspended when backgrounded.
        "--autoplay-policy=no-user-gesture-required",
    ]


async def _capture_voice_events(page: Page) -> tuple[list[str], list[str], list[str]]:
    """Wire up listeners for [voice] console logs, pageerrors, and WS frames.

    Returns three lists (voice_logs, pageerrors, ws_frames) that fill in-place
    as the page runs.
    """
    voice_logs: list[str] = []
    pageerrors: list[str] = []
    ws_frames: list[str] = []

    def on_console(msg):
        text = msg.text
        if "[voice]" in text:
            voice_logs.append(f"{time.time():.2f} [{msg.type}] {text}")
        elif msg.type == "error":
            pageerrors.append(f"[console.error] {text}")

    def on_pageerror(err):
        pageerrors.append(f"[pageerror] {err}")

    page.on("console", on_console)
    page.on("pageerror", on_pageerror)

    # WebSocket observability: log every frame in and out of /ws/voice so we
    # can see if tts_start/tts_end/transcript/token events arrive in order.
    def _fmt_frame(direction: str, payload):
        # Playwright Python emits framesent/framereceived with the raw payload
        # (str for text, bytes for binary). No `.payload` attribute.
        if isinstance(payload, (bytes, bytearray)):
            return f"{direction} [binary {len(payload)}B]"
        s = str(payload)
        return f"{direction} {s[:200]}"

    def on_ws(ws):
        if "/ws/voice" not in ws.url:
            return
        ws.on("framesent", lambda p: ws_frames.append(_fmt_frame("→", p)))
        ws.on("framereceived", lambda p: ws_frames.append(_fmt_frame("←", p)))

    page.on("websocket", on_ws)

    return voice_logs, pageerrors, ws_frames


async def run_audio_in_roundtrip(
    wav_path: Path,
    *,
    wait_for_reply_s: float = 25.0,
) -> dict:
    """Start a voice conversation with the given fake-mic WAV, wait for reply.

    Flow:
      1. Launch Chromium with fake audio = wav_path
      2. Log in, navigate to /voice
      3. Click the mic button (starts VAD + begins listening)
      4. Wait up to wait_for_reply_s for an assistant message bubble
      5. Stop conversation, close browser, return findings

    Returns dict with: assistant_text, voice_logs, pageerrors, ws_frames, success.
    """
    if not OWNER_PASSWORD:
        raise RuntimeError("OWNER_PASSWORD env var required")

    browser, _ctx, page = await launch_authed_page(
        APP_URL,
        OWNER_PASSWORD,
        extra_args=fake_mic_args(wav_path),
    )
    voice_logs, pageerrors, ws_frames = await _capture_voice_events(page)

    try:
        # Navigate to /voice and wait for the "Start voice conversation" button.
        await page.goto(f"{APP_URL}/voice", wait_until="domcontentloaded")
        # Mic button uses aria-label "Start voice conversation" / "End voice conversation".
        mic_sel = "button[aria-label='Start voice conversation']"
        await page.wait_for_selector(mic_sel, timeout=10_000)

        # Click to start conversation. unlockAudio() needs a user gesture.
        await page.click(mic_sel)

        # Give VAD/WS a moment to spin up.
        await asyncio.sleep(0.5)

        # Wait for an assistant bubble (role=assistant -> justify-start) to render.
        # Assistant bubbles are `<div class="flex justify-start"> <div ...><p>text</p>...`.
        # We skip the "thinking" dots placeholder (which only contains divs, not <p>).
        deadline = time.time() + wait_for_reply_s
        assistant_text = ""
        user_bubble_text = ""
        stable_for = 0.0
        last_text = None
        while time.time() < deadline:
            # Find assistant bubbles: flex.justify-start containing a <p>.
            texts = await page.evaluate(
                """() => {
                    const bubbles = Array.from(document.querySelectorAll('div.flex.justify-start'));
                    return bubbles
                        .map(b => {
                            const p = b.querySelector('p.text-sm');
                            return p ? p.innerText : '';
                        })
                        .filter(Boolean);
                }"""
            )
            user_texts = await page.evaluate(
                """() => {
                    const bubbles = Array.from(document.querySelectorAll('div.flex.justify-end'));
                    return bubbles
                        .map(b => {
                            const p = b.querySelector('p.text-sm');
                            return p ? p.innerText : '';
                        })
                        .filter(Boolean);
                }"""
            )
            if user_texts:
                user_bubble_text = "\n---\n".join(user_texts)
            if texts:
                current = "\n---\n".join(texts)
                if current == last_text:
                    stable_for += 0.5
                    # Consider the stream "done" when text has been stable for 2s.
                    if stable_for >= 2.0:
                        assistant_text = current
                        break
                else:
                    stable_for = 0.0
                    last_text = current
                assistant_text = current  # keep latest even if we exit via deadline
            await asyncio.sleep(0.5)

        # End conversation cleanly (aria-label flips when active).
        try:
            await page.click(
                "button[aria-label='End voice conversation']", timeout=2000
            )
        except Exception:
            pass

        # Give any in-flight events 1s to flush into our log buffers.
        await asyncio.sleep(1.0)

        return {
            "success": bool(assistant_text),
            "assistant_text": assistant_text,
            "user_bubble_text": user_bubble_text,
            "voice_logs": voice_logs,
            "pageerrors": pageerrors,
            "ws_frames": ws_frames[-80:],  # last 80 frames to cap output
        }
    finally:
        await browser.close()


async def run_text_in_tts_out(wait_for_reply_s: float = 25.0) -> dict:
    """Text composer → backend → assistant bubble + TTS audio.

    Uses silence_10s.wav as the fake mic (VAD never fires) and types a message
    into the text composer. Verifies assistant bubble rendered AND binary TTS
    frames arrived over /ws/voice.
    """
    if not OWNER_PASSWORD:
        raise RuntimeError("OWNER_PASSWORD env var required")

    browser, _ctx, page = await launch_authed_page(
        APP_URL,
        OWNER_PASSWORD,
        extra_args=fake_mic_args(FIXTURES / "silence_10s.wav"),
    )
    voice_logs, pageerrors, ws_frames = await _capture_voice_events(page)

    try:
        await page.goto(f"{APP_URL}/voice", wait_until="domcontentloaded")
        # Composer input — single input inside the Composer div.
        await page.wait_for_selector("input[type='text'], input:not([type])", timeout=10_000)
        # Type a prompt and submit.
        probe = "Say the word 'roger' and nothing else."
        # Focus and fill the composer input.
        composer_input = await page.query_selector("input[type='text'], input:not([type])")
        if not composer_input:
            raise RuntimeError("composer input not found")
        await composer_input.click()
        await composer_input.fill(probe)
        await composer_input.press("Enter")

        # Wait for assistant bubble.
        deadline = time.time() + wait_for_reply_s
        assistant_text = ""
        last = None
        stable = 0.0
        while time.time() < deadline:
            texts = await page.evaluate(
                """() => {
                    const bubbles = Array.from(document.querySelectorAll('div.flex.justify-start'));
                    return bubbles
                        .map(b => {
                            const p = b.querySelector('p.text-sm');
                            return p ? p.innerText : '';
                        })
                        .filter(Boolean);
                }"""
            )
            if texts:
                cur = "\n---\n".join(texts)
                assistant_text = cur
                if cur == last:
                    stable += 0.5
                    if stable >= 2.0:
                        break
                else:
                    stable = 0.0
                    last = cur
            await asyncio.sleep(0.5)

        await asyncio.sleep(1.0)
        # Count binary TTS frames received.
        tts_binary_frames = sum(1 for f in ws_frames if f.startswith("← [binary"))
        tts_start_seen = any("tts_start" in f for f in ws_frames)
        tts_end_seen = any("tts_end" in f for f in ws_frames)
        transcript_seen = any("transcript" in f for f in ws_frames)

        return {
            "success": bool(assistant_text) and tts_binary_frames > 0,
            "assistant_text": assistant_text,
            "prompt_sent": probe,
            "tts_binary_frames": tts_binary_frames,
            "tts_start_seen": tts_start_seen,
            "tts_end_seen": tts_end_seen,
            "transcript_seen_incorrectly": transcript_seen,  # should be False w/ silent mic
            "voice_logs": voice_logs,
            "pageerrors": pageerrors,
            "ws_frames": ws_frames[-60:],
        }
    finally:
        await browser.close()


async def smoke_all_routes() -> dict:
    """Load each primary route with silent fake mic and log pageerrors per route."""
    if not OWNER_PASSWORD:
        raise RuntimeError("OWNER_PASSWORD env var required")
    browser, _ctx, page = await launch_authed_page(
        APP_URL,
        OWNER_PASSWORD,
        extra_args=fake_mic_args(FIXTURES / "silence_10s.wav"),
    )
    routes = ["/voice", "/projects", "/memory", "/agents", "/usage", "/team", "/terminal"]
    results: dict[str, dict] = {}
    try:
        for r in routes:
            errs: list[str] = []

            def _pe(err, buf=errs):
                buf.append(f"[pageerror] {err}")

            def _ce(msg, buf=errs):
                if msg.type == "error":
                    buf.append(f"[console.error] {msg.text}")

            page.on("pageerror", _pe)
            page.on("console", _ce)
            try:
                await page.goto(f"{APP_URL}{r}", wait_until="domcontentloaded", timeout=15_000)
                await asyncio.sleep(1.5)
                body_len = await page.evaluate("document.body.innerText.length")
            except Exception as e:
                errs.append(f"[nav-error] {e}")
                body_len = 0
            finally:
                page.remove_listener("pageerror", _pe)
                page.remove_listener("console", _ce)
            results[r] = {"errors": errs, "body_len": body_len}
    finally:
        await browser.close()
    return results


async def _main():
    print(f"APP_URL={APP_URL}")
    print(f"FIXTURES={FIXTURES}")

    print("\n=== audio-in roundtrip (query_longform.wav) ===")
    result = await run_audio_in_roundtrip(FIXTURES / "query_longform.wav")
    print(f"success={result['success']}")
    print(f"user_bubble_text={result.get('user_bubble_text','')[:400]}")
    print(f"assistant_text={result['assistant_text'][:400]}")
    print(f"pageerrors={result['pageerrors']}")
    print(f"voice_logs ({len(result['voice_logs'])}):")
    for line in result["voice_logs"]:
        print(f"  {line}")
    print(f"ws_frames ({len(result['ws_frames'])}):")
    for line in result["ws_frames"]:
        print(f"  {line}")

    print("\n=== text-in/tts-out (silent mic) ===")
    tout = await run_text_in_tts_out()
    print(f"success={tout['success']}")
    print(f"prompt_sent={tout['prompt_sent']}")
    print(f"assistant_text={tout['assistant_text'][:400]}")
    print(f"tts_start_seen={tout['tts_start_seen']}  tts_end_seen={tout['tts_end_seen']}  "
          f"binary_frames={tout['tts_binary_frames']}  stray_transcript={tout['transcript_seen_incorrectly']}")
    print(f"pageerrors={tout['pageerrors']}")
    print(f"voice_logs ({len(tout['voice_logs'])}):")
    for line in tout["voice_logs"]:
        print(f"  {line}")

    print("\n=== smoke: all routes (silent mic) ===")
    smoke = await smoke_all_routes()
    for route, info in smoke.items():
        tag = "PASS" if not info["errors"] and info["body_len"] > 50 else "FAIL"
        print(f"  {tag} {route} body_len={info['body_len']} errors={info['errors']}")


if __name__ == "__main__":
    asyncio.run(_main())
