#!/usr/bin/env python3
"""Smoke test — verify all main-nav routes render without JS crashes.

Run from the repo root or from the backend directory:
    python3 tests/smoke_all_pages.py

Requires the backend to be running (default: http://localhost:8000).
Requires OWNER_PASSWORD to be set in the environment or in backend/.env.

Screenshots land in /tmp/chief-smoke/<route-slug>.png.
Exit code 0 = all clean, 1 = one or more pages failed.
"""

import asyncio
import os
import sys
from pathlib import Path

# Allow running from repo root or from inside backend/.
_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
sys.path.insert(0, str(_BACKEND))

# Load .env so OWNER_PASSWORD is available when running locally.
_env_file = _BACKEND / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

from tests.forge_browser import launch_authed_page, verify_route  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

APP_URL = os.getenv("APP_URL", "http://localhost:8000")
PASSWORD = os.getenv("OWNER_PASSWORD", "")
SCREENSHOT_DIR = Path("/tmp/chief-smoke")

# Route list: (path, expected_visible_text)
# Keep expected strings conservative — things that MUST be in the rendered body.
ROUTES: list[tuple[str, list[str]]] = [
    ("/voice", []),                                   # Voice orb page — minimal static text
    ("/team", []),                                    # Team roster — dynamic agent names
    ("/agents", []),                                  # Agents page — dynamic agent list
    ("/projects", ["Projects"]),                      # Projects list — static heading
    ("/usage", ["Today", "Week", "Month"]),            # Usage dashboard — tab labels
    ("/memory", ["Global", "Per-project"]),            # Memory page — tab labels
    ("/terminal", []),                                # Terminal — no reliable static text
]


def _route_slug(path: str) -> str:
    """Convert /voice -> voice, /team -> team, etc."""
    return path.lstrip("/") or "root"


async def run_smoke() -> int:
    """Run smoke tests. Return 0 on success, 1 on any failure."""
    if not PASSWORD:
        print(
            "ERROR: OWNER_PASSWORD not set. Export it or add it to backend/.env.",
            file=sys.stderr,
        )
        return 1

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Chief Command smoke test — {APP_URL}")
    print(f"Screenshots → {SCREENSHOT_DIR}")
    print(f"Viewport: 390x844 (iPhone 14 Pro)")
    print("-" * 60)

    browser, ctx, page = await launch_authed_page(APP_URL, PASSWORD)

    results: list[dict] = []

    try:
        for path, expected_text in ROUTES:
            slug = _route_slug(path)
            screenshot_path = str(SCREENSHOT_DIR / f"{slug}.png")

            try:
                result = await verify_route(
                    page,
                    path,
                    expected_visible_text=expected_text or None,
                    screenshot_to=screenshot_path,
                )
            except Exception as exc:  # noqa: BLE001
                result = {
                    "uncaught_errors": [f"[navigate failed] {exc}"],
                    "console_errors": [],
                    "visible_text_found": {t: False for t in expected_text},
                    "screenshot_path": None,
                }

            uncaught = result["uncaught_errors"]
            console_errs = result["console_errors"]
            missing = [
                t for t, found in result.get("visible_text_found", {}).items() if not found
            ]

            passed = not uncaught and not console_errs and not missing
            status = "PASS" if passed else "FAIL"

            results.append(
                {
                    "path": path,
                    "status": status,
                    "uncaught": uncaught,
                    "console_errs": console_errs,
                    "missing_text": missing,
                    "screenshot": result.get("screenshot_path"),
                }
            )

            # Print one-line summary per route.
            detail_parts = []
            if uncaught:
                detail_parts.append(f"{len(uncaught)} uncaught error(s)")
            if console_errs:
                detail_parts.append(f"{len(console_errs)} console error(s)")
            if missing:
                detail_parts.append(f"missing text: {missing}")

            detail = f"  [{', '.join(detail_parts)}]" if detail_parts else ""
            print(f"  {status}  {path}{detail}")

    finally:
        await browser.close()

    # ---------------------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------------------
    passed_count = sum(1 for r in results if r["status"] == "PASS")
    failed_count = len(results) - passed_count

    print("-" * 60)
    print(f"Results: {passed_count} passed, {failed_count} failed out of {len(results)} routes")

    if failed_count:
        print("\nFailed routes:")
        for r in results:
            if r["status"] != "PASS":
                print(f"  {r['path']}")
                for err in r["uncaught"]:
                    print(f"    uncaught: {err}")
                for err in r["console_errs"]:
                    print(f"    console.error: {err}")
                for txt in r["missing_text"]:
                    print(f"    missing text: {txt!r}")

    print(f"\nScreenshots written to {SCREENSHOT_DIR}/")
    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(run_smoke())
    sys.exit(exit_code)
