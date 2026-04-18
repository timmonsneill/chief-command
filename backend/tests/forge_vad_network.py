"""Log every network request under /vad/ to diagnose which file ORT wants."""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forge_browser import launch_authed_page  # noqa: E402


async def main() -> None:
    url = os.getenv("APP_URL", "http://localhost:8000")
    pw = os.getenv("OWNER_PASSWORD", "chief")

    browser, ctx, page = await launch_authed_page(
        url,
        pw,
        extra_args=[
            "--use-fake-device-for-media-stream",
            "--use-fake-ui-for-media-stream",
        ],
    )
    await ctx.grant_permissions(["microphone"], origin=url)

    hits: list[tuple[str, int, str]] = []

    def on_response(resp):
        u = resp.url
        if "/vad/" in u or "onnx" in u or "ort-wasm" in u or ".wasm" in u:
            try:
                ct = resp.headers.get("content-type", "?")
            except Exception:
                ct = "?"
            hits.append((u, resp.status, ct))

    page.on("response", on_response)

    await page.goto(f"{url}/voice", wait_until="networkidle", timeout=15_000)
    btn = await page.query_selector("button:has(svg.lucide-mic)")
    if btn:
        await btn.click()
    await page.wait_for_timeout(5_000)

    for u, s, ct in hits:
        print(f"  {s} {ct:40s} {u}")

    await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
