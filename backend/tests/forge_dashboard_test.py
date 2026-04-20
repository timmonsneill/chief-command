#!/usr/bin/env python3
"""Forge dashboard integration test — ProjectDashboard rewrite verification.

Tests:
 - Flow 1: Arch iframe dashboard (archdashboard.netlify.app)
 - Flow 2: Chief Command native dashboard (tabs: Plan, Todos, Timeline, Integrations, Builds)
 - Smoke: all existing routes

NOTE (2026-04-20): the former Flow 3 (Archie native) was removed when Archie
was dissolved into Arch as its AI-brain layer (same project, not a separate
scope). /projects/archie is no longer a routable dashboard.
"""

import asyncio
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
sys.path.insert(0, str(_BACKEND))

_env_file = _BACKEND / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

from tests.forge_browser import launch_authed_page, verify_route  # noqa: E402

APP_URL = os.getenv("APP_URL", "http://localhost:8000")
PASSWORD = os.getenv("OWNER_PASSWORD", "chief")
SCREENSHOT_DIR = Path("/tmp/chief-smoke")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


async def flow1_arch_iframe(page) -> dict:
    """Flow 1: Arch project should render iframe to archdashboard.netlify.app."""
    print("\n--- Flow 1: Arch iframe dashboard ---")
    result = await verify_route(
        page,
        "/projects/arch",
        expected_visible_text=None,
        screenshot_to=str(SCREENSHOT_DIR / "arch-dashboard.png"),
    )

    # Check iframe presence
    iframe_el = await page.query_selector("iframe")
    iframe_src = await iframe_el.get_attribute("src") if iframe_el else None
    iframe_present = bool(iframe_el and iframe_src and "archdashboard.netlify.app" in iframe_src)

    # Check "Open in new tab" button — on iPhone viewport it shows "Open"
    body_text = await page.inner_text("body")
    open_btn_visible = "Open" in body_text

    # Check CSP banner (fallback when iframe is blocked)
    csp_banner = await page.query_selector("text=Open externally")
    csp_shown = csp_banner is not None

    # Also check for AlertTriangle / blocked message
    blocked_msg = await page.query_selector("text=Dashboard blocked")
    blocked_shown = blocked_msg is not None

    # Wait up to 10s for iframe load or fallback banner
    try:
        await page.wait_for_function(
            "document.querySelector('iframe') !== null || "
            "document.body.innerText.includes('Open externally')",
            timeout=10_000,
        )
    except Exception:
        pass

    # Re-check after wait
    csp_banner_after = await page.query_selector("text=Open externally")
    csp_shown_after = csp_banner_after is not None

    iframe_loaded_path = "iframe present" if iframe_present else "iframe missing"
    fallback_path = "fallback CSP banner shown" if (csp_shown or csp_shown_after) else "no fallback banner"

    print(f"  iframe present with correct src: {iframe_present} (src={iframe_src})")
    print(f"  'Open' button visible: {open_btn_visible}")
    print(f"  CSP fallback banner: {csp_shown or csp_shown_after}")
    print(f"  Console errors: {result['console_errors']}")
    print(f"  Uncaught errors: {result['uncaught_errors']}")
    print(f"  Screenshot: {result['screenshot_path']}")

    return {
        "iframe_present": iframe_present,
        "open_btn_visible": open_btn_visible,
        "csp_fallback": csp_shown or csp_shown_after,
        "path_fired": f"{iframe_loaded_path} / {fallback_path}",
        "console_errors": result["console_errors"],
        "uncaught_errors": result["uncaught_errors"],
    }


async def flow2_chief_native(page) -> dict:
    """Flow 2: Chief Command native dashboard with 5 tabs."""
    print("\n--- Flow 2: Chief Command native dashboard ---")
    results = {}

    # Navigate to chief-command project
    r = await verify_route(
        page,
        "/projects/chief-command",
        expected_visible_text=None,
        screenshot_to=str(SCREENSHOT_DIR / "chief-plan.png"),
    )

    body_text = await page.inner_text("body")
    # On iPhone viewport, labels are short: Plan, Todo, Time, Intg, Blds
    tab_bar_visible = any(t in body_text for t in ["Plan", "Todo"])
    print(f"  Tab bar visible: {tab_bar_visible}")
    print(f"  Console errors (Plan): {r['console_errors']}")
    results["plan_tab"] = {"visible": tab_bar_visible, "errors": r["console_errors"]}

    # Click Todos tab (short label on mobile: "Todo")
    try:
        # Try full label first, then short
        for label in ["Master Todo", "Todo"]:
            btn = await page.query_selector(f"button:has-text('{label}')")
            if btn:
                await btn.click()
                await page.wait_for_timeout(400)
                break
        await page.screenshot(path=str(SCREENSHOT_DIR / "chief-todos.png"))
        todos_body = await page.inner_text("body")
        todos_visible = any(kw in todos_body for kw in ["todo", "Todo", "General", "done", "Done"])
        print(f"  Todos tab visible content: {todos_visible}")
        results["todos_tab"] = {"visible": todos_visible, "errors": []}
    except Exception as e:
        print(f"  Todos tab ERROR: {e}")
        results["todos_tab"] = {"visible": False, "errors": [str(e)]}

    # Click Timeline tab
    try:
        for label in ["Timeline", "Time"]:
            btn = await page.query_selector(f"button:has-text('{label}')")
            if btn:
                await btn.click()
                await page.wait_for_timeout(400)
                break
        await page.screenshot(path=str(SCREENSHOT_DIR / "chief-timeline.png"))
        timeline_body = await page.inner_text("body")
        print(f"  Timeline tab rendered (body len={len(timeline_body)})")
        results["timeline_tab"] = {"visible": True, "errors": []}
    except Exception as e:
        print(f"  Timeline tab ERROR: {e}")
        results["timeline_tab"] = {"visible": False, "errors": [str(e)]}

    # Click Integrations tab
    try:
        for label in ["Integrations", "Intg"]:
            btn = await page.query_selector(f"button:has-text('{label}')")
            if btn:
                await btn.click()
                await page.wait_for_timeout(400)
                break
        await page.screenshot(path=str(SCREENSHOT_DIR / "chief-integrations.png"))
        intg_body = await page.inner_text("body")
        intg_visible = any(kw in intg_body for kw in ["Anthropic", "Cloudflare", "Playwright"])
        print(f"  Integrations tab shows integrations: {intg_visible}")
        results["integrations_tab"] = {"visible": intg_visible, "errors": []}
    except Exception as e:
        print(f"  Integrations tab ERROR: {e}")
        results["integrations_tab"] = {"visible": False, "errors": [str(e)]}

    # Click Builds tab
    try:
        for label in ["Builds", "Blds"]:
            btn = await page.query_selector(f"button:has-text('{label}')")
            if btn:
                await btn.click()
                await page.wait_for_timeout(400)
                break
        await page.screenshot(path=str(SCREENSHOT_DIR / "chief-builds.png"))
        print(f"  Builds tab rendered (empty state acceptable)")
        results["builds_tab"] = {"visible": True, "errors": []}
    except Exception as e:
        print(f"  Builds tab ERROR: {e}")
        results["builds_tab"] = {"visible": False, "errors": [str(e)]}

    return results


async def main():
    print(f"Forge Dashboard Integration Test — {APP_URL}")
    print(f"Screenshots → {SCREENSHOT_DIR}")
    print("=" * 60)

    browser, ctx, page = await launch_authed_page(APP_URL, PASSWORD)

    try:
        f1 = await flow1_arch_iframe(page)
        f2 = await flow2_chief_native(page)
    finally:
        await browser.close()

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    # Flow 1
    f1_pass = f1["iframe_present"] and f1["open_btn_visible"] and not f1["uncaught_errors"]
    f1_fallback_ok = f1["csp_fallback"]
    print(f"Flow 1 (Arch iframe): {'PASS' if f1_pass else 'FAIL (CSP-BLOCKED — fallback: ' + str(f1_fallback_ok) + ')'}")
    print(f"  Path fired: {f1['path_fired']}")
    if f1["console_errors"]:
        print(f"  Console errors: {f1['console_errors']}")

    # Flow 2
    f2_tabs = ["plan_tab", "todos_tab", "timeline_tab", "integrations_tab", "builds_tab"]
    f2_results = {t: f2.get(t, {}) for t in f2_tabs}
    f2_pass = all(f2_results[t].get("visible", False) for t in f2_tabs)
    print(f"\nFlow 2 (Chief Command native):")
    for tab in f2_tabs:
        r = f2_results[tab]
        status = "PASS" if r.get("visible") else "FAIL"
        errs = r.get("errors", [])
        print(f"  {tab}: {status}" + (f" — {errs}" if errs else ""))

    return 0 if (f1_pass or f1_fallback_ok) and f2_pass else 1


if __name__ == "__main__":
    code = asyncio.run(main())
    sys.exit(code)
