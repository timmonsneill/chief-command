# Chief Command Browser Tests

Playwright-powered end-user verification harness for Forge. These tests drive a real Chromium browser through the app so runtime React crashes, missing renders, and UX regressions are caught before they ship.

## Prerequisites

- Backend running on port 8000 (or set `APP_URL` env var)
- `OWNER_PASSWORD` set in `backend/.env` or exported in your shell
- Chromium installed via Playwright (`backend/.venv/bin/playwright install chromium`)

## Running the smoke test

From the repo root:

```bash
python3 backend/tests/smoke_all_pages.py
```

Or from inside the backend directory:

```bash
python3 tests/smoke_all_pages.py
```

The script loads `backend/.env` automatically, so you don't need to export `OWNER_PASSWORD` separately if it's in that file.

## Where screenshots land

All screenshots are written to `/tmp/chief-smoke/<route>.png` — one per route. After a run:

```
ls /tmp/chief-smoke/
# voice.png  team.png  agents.png  projects.png  usage.png  memory.png  terminal.png
```

Open them with any image viewer to see exactly what Chromium rendered.

## Adding new routes

Edit the `ROUTES` list in `backend/tests/smoke_all_pages.py`:

```python
ROUTES: list[tuple[str, list[str]]] = [
    ("/voice", []),
    ("/team", []),
    # Add your new route here:
    ("/settings", ["Settings", "Profile"]),   # path, expected visible strings
]
```

- The first element is the path (must start with `/`).
- The second element is a list of strings that MUST appear in the rendered page body. Use `[]` if there is no reliable static text to check.

## Using the library in Forge scripts

```python
from tests.forge_browser import launch_authed_page, collect_page_errors, verify_route

browser, ctx, page = await launch_authed_page("http://localhost:8000", password)
errors = await collect_page_errors(page)   # attach before navigating
result = await verify_route(page, "/usage", expected_visible_text=["Today"])
await browser.close()
```

## Voice end-to-end test

```python
from tests.forge_browser import test_voice_flow_with_fake_audio

result = await test_voice_flow_with_fake_audio(
    "http://localhost:8000",
    password,
    wav_path="tests/fixtures/hello_chief.wav",
)
print(result)
```

The WAV at `tests/fixtures/hello_chief.wav` says "Hello Chief, can you hear me?" in Alex (macOS TTS voice).

## Known limitations

- **Chromium only.** These tests do not cover iOS Safari, Firefox, or WebKit. Real device testing requires a separate setup.
- **Fake audio is lossy for VAD.** The `--use-file-for-fake-audio-capture` flag feeds the WAV through Chromium's fake media stack. Silence detection and VAD edge cases may differ from real microphone input.
- **No pytest integration (yet).** These are plain async Python scripts, not pytest fixtures. Integration with a test runner is a future task.
- **No CI runner configured.** The scripts are designed to be called manually by Forge or a shell script. Hooking into CI is a separate task.
