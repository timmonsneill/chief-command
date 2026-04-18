"""Playwright browser harness for Forge end-user verification.

Reusable library — not a pytest file. Forge calls these functions directly.

Usage example:
    from tests.forge_browser import launch_authed_page, collect_page_errors, verify_route

    browser, ctx, page = await launch_authed_page("http://localhost:8000", "secret")
    errors = await collect_page_errors(page)
    result = await verify_route(page, "/voice", expected_visible_text=["Chief"])
    await browser.close()
"""

import asyncio
import time
from pathlib import Path
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    async_playwright,
)

# Default viewport: iPhone 14 Pro dimensions — catches mobile layout regressions.
_DEFAULT_VIEWPORT = {"width": 390, "height": 844}

# How long to wait for network idle before declaring a route loaded.
_NETWORK_IDLE_TIMEOUT_MS = 15_000

# How long to wait for the login form to appear.
_LOGIN_TIMEOUT_MS = 10_000


async def launch_authed_page(
    app_url: str,
    password: str,
    *,
    viewport: dict | None = None,
    extra_args: list[str] | None = None,
) -> tuple[Browser, BrowserContext, Page]:
    """Open a headless Chromium page, log into Chief Command, return ready-to-use page.

    Returns (browser, context, page) so the caller can close them when done.

    Args:
        app_url: Base URL of the running app, e.g. "http://localhost:8000".
        password: Owner password (from OWNER_PASSWORD env var).
        viewport: Browser viewport dict. Defaults to iPhone 14 Pro (390x844).
        extra_args: Additional Chromium launch args (e.g. fake audio flags).

    Returns:
        Tuple of (Browser, BrowserContext, Page) — all open and authenticated.
    """
    vp = viewport or _DEFAULT_VIEWPORT
    args = extra_args or []

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True,
        args=args,
    )
    context = await browser.new_context(
        viewport=vp,
        ignore_https_errors=True,
    )
    page = await context.new_page()

    # Navigate to root, which should redirect to login if unauthenticated.
    await page.goto(app_url, wait_until="domcontentloaded", timeout=_LOGIN_TIMEOUT_MS)

    # Fill in the password field and submit.
    # The login page renders a single password input.
    await page.wait_for_selector("input[type='password']", timeout=_LOGIN_TIMEOUT_MS)
    await page.fill("input[type='password']", password)
    await page.press("input[type='password']", "Enter")

    # Wait for navigation away from the login page.
    await page.wait_for_url(
        lambda url: "login" not in url,
        timeout=_LOGIN_TIMEOUT_MS,
    )

    return browser, context, page


async def collect_page_errors(page: Page) -> list[str]:
    """Attach listeners for pageerror + console errors.

    Returns a list reference that accumulates messages as the page runs.
    Call this BEFORE navigation so no errors are missed.

    Args:
        page: Playwright Page object (before navigating to the target route).

    Returns:
        A list that is populated in-place as errors occur.
    """
    errors: list[str] = []

    page.on("pageerror", lambda err: errors.append(f"[pageerror] {err}"))
    page.on(
        "console",
        lambda msg: errors.append(f"[console.{msg.type}] {msg.text}")
        if msg.type == "error"
        else None,
    )

    return errors


async def verify_route(
    page: Page,
    path: str,
    expected_visible_text: list[str] | None = None,
    screenshot_to: str | None = None,
) -> dict[str, Any]:
    """Navigate to a route, wait for network idle, verify rendered content.

    Args:
        page: Authenticated Playwright Page.
        path: Path to navigate to, e.g. "/voice" or "/team".
        expected_visible_text: Strings that should appear in the rendered body.
        screenshot_to: If provided, save a screenshot PNG to this file path.

    Returns:
        Dict with keys:
            uncaught_errors: list[str] — JS pageerrors collected since last call
            console_errors: list[str] — console.error messages collected
            visible_text_found: dict[str, bool] — per-string presence
            screenshot_path: str | None — path where screenshot was saved
    """
    # Attach fresh per-route error collectors BEFORE navigating.
    # Use named handlers so they can be removed after navigation, preventing
    # listener accumulation across multiple verify_route calls.
    uncaught: list[str] = []
    console_errs: list[str] = []

    def _on_pageerror(err: Exception) -> None:  # type: ignore[type-arg]
        uncaught.append(str(err))

    def _on_console(msg: Any) -> None:
        if msg.type == "error":
            console_errs.append(msg.text)

    page.on("pageerror", _on_pageerror)
    page.on("console", _on_console)

    try:
        # Navigate and wait for network to settle.
        # Build absolute URL from base: strip any existing path then append the route.
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(page.url)
        base_url = urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
        target_url = base_url + path if path.startswith("/") else path

        await page.goto(
            target_url,
            wait_until="networkidle",
            timeout=_NETWORK_IDLE_TIMEOUT_MS,
        )

        # Small settle wait so deferred JS errors (lazy imports, etc.) surface.
        await page.wait_for_timeout(500)

    finally:
        # Always remove listeners — even if navigation raises.
        page.remove_listener("pageerror", _on_pageerror)
        page.remove_listener("console", _on_console)

    # Check visible text against rendered body.
    visible_text_found: dict[str, bool] = {}
    if expected_visible_text:
        body_text = await page.inner_text("body")
        for term in expected_visible_text:
            visible_text_found[term] = term in body_text

    # Save screenshot if requested.
    screenshot_path: str | None = None
    if screenshot_to:
        Path(screenshot_to).parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=screenshot_to, full_page=False)
        screenshot_path = screenshot_to

    return {
        "uncaught_errors": uncaught,
        "console_errors": console_errs,
        "visible_text_found": visible_text_found,
        "screenshot_path": screenshot_path,
    }


async def test_voice_flow_with_fake_audio(
    app_url: str,
    password: str,
    wav_path: str,
) -> dict[str, Any]:
    """End-to-end voice test using Chromium's fake audio device.

    Launches a browser with fake audio capture pointed at a WAV file, logs in,
    navigates to /voice, taps the orb button, and waits for transcript + token
    events over the WebSocket.

    Args:
        app_url: Base URL, e.g. "http://localhost:8000".
        password: Owner password.
        wav_path: Absolute path to a mono 16kHz PCM WAV file.

    Returns:
        Dict with keys:
            transcript_received: str | None — text returned by Whisper
            tokens_streamed: int — number of SSE/WS token chunks received
            tts_audio_received: bool — whether audio playback started
            duration_ms: int — total elapsed time in milliseconds
            errors: list[str] — any JS or network errors encountered
    """
    fake_audio_args = [
        "--use-fake-device-for-media-stream",
        f"--use-file-for-fake-audio-capture={wav_path}",
        "--allow-file-access-from-files",
    ]

    start_ms = int(time.time() * 1000)
    errors: list[str] = []
    transcript_received: str | None = None
    tokens_streamed = 0
    tts_audio_received = False

    browser, context, page = await launch_authed_page(
        app_url,
        password,
        extra_args=fake_audio_args,
    )

    try:
        # Collect errors before navigating.
        page.on("pageerror", lambda err: errors.append(f"[pageerror] {err}"))
        page.on(
            "console",
            lambda msg: errors.append(f"[console.error] {msg.text}")
            if msg.type == "error"
            else None,
        )

        # Navigate to /voice.
        await page.goto(
            f"{app_url.rstrip('/')}/voice",
            wait_until="networkidle",
            timeout=_NETWORK_IDLE_TIMEOUT_MS,
        )

        # Intercept WebSocket messages to count token events.
        ws_messages: list[str] = []

        async def on_websocket(ws):  # type: ignore[no-untyped-def]
            ws.on("framereceived", lambda payload: ws_messages.append(str(payload)))

        page.on("websocket", on_websocket)

        # Tap the orb / record button — selector may vary; try common patterns.
        orb_selectors = [
            "button[aria-label*='record' i]",
            "button[aria-label*='speak' i]",
            "button[aria-label*='mic' i]",
            "[data-testid='orb']",
            "button.orb",
        ]
        orb_clicked = False
        for sel in orb_selectors:
            try:
                await page.click(sel, timeout=3_000)
                orb_clicked = True
                break
            except Exception:  # noqa: BLE001
                continue

        if not orb_clicked:
            errors.append("orb button not found — voice flow not triggered")
        else:
            # Wait up to 15s for a transcript to surface in the DOM or WS messages.
            try:
                await page.wait_for_function(
                    "document.body.innerText.includes('transcript') || "
                    "document.body.innerText.length > 200",
                    timeout=15_000,
                )
            except Exception:  # noqa: BLE001
                errors.append("timed out waiting for transcript in DOM")

            # Count token-bearing WS frames.
            tokens_streamed = sum(
                1 for m in ws_messages if "token" in m.lower() or "delta" in m.lower()
            )

            # Rough heuristic: TTS played if the page has an audio element or
            # the body mentions audio/playback.
            body_text = await page.inner_text("body")
            tts_audio_received = bool(
                await page.query_selector("audio") or "playing" in body_text.lower()
            )

            # Extract any visible transcript text.
            for sel in ["[data-testid='transcript']", ".transcript", "#transcript"]:
                el = await page.query_selector(sel)
                if el:
                    transcript_received = await el.inner_text()
                    break

    finally:
        await browser.close()

    end_ms = int(time.time() * 1000)
    return {
        "transcript_received": transcript_received,
        "tokens_streamed": tokens_streamed,
        "tts_audio_received": tts_audio_received,
        "duration_ms": end_ms - start_ms,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Quick self-test when run directly (not for production use)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    import sys

    async def _selftest() -> None:
        url = os.getenv("APP_URL", "http://localhost:8000")
        pw = os.getenv("OWNER_PASSWORD", "")
        if not pw:
            print("Set OWNER_PASSWORD env var to run self-test", file=sys.stderr)
            sys.exit(1)
        browser, ctx, page = await launch_authed_page(url, pw)
        print(f"Logged in. Current URL: {page.url}")
        await browser.close()

    asyncio.run(_selftest())
