"""Sol build gate 2: every old bypass path is dead IN FACT, not by convention.

v1 served itself to the public internet through a Cloudflare tunnel, behind a
default password, with a Netlify production-deploy one-liner and an auto-updater
that pulled remote code and restarted the backend. Sol's sign-off requires those
paths to be physically unusable. These tests are the tripwire: if any of them
comes back — a restored script, a revived config, the kill switch removed — the
suite goes red.
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

KILLED_FILES = [
    "scripts/install.sh",       # default password 'chief' + tunnel DNS routing
    "scripts/deploy.sh",        # netlify deploy --prod
    "scripts/start.sh",         # launched the public Cloudflare tunnel
    "scripts/update.sh",        # auto-update: git pull + restart (AGENTS.md rule 2)
    "scripts/watch-updates.sh", # the cron form of the same
    "scripts/setup-siri.sh",    # pointed Siri at the public tunnel URL
    "cloudflared-config.yml",
    "frontend/netlify.toml",
]


def test_the_killed_launchers_stay_dead():
    revived = [f for f in KILLED_FILES if (REPO / f).exists()]
    assert not revived, f"old bypass paths have come back from the dead: {revived}"


def test_v1_backend_refuses_to_start():
    # The kill switch sits before v1's dependency imports, so any interpreter can
    # prove it fires — no v1 venv required. This is the same refusal a real
    # launch (uvicorn importing app.main) would hit.
    result = subprocess.run(
        [sys.executable, str(REPO / "backend" / "app" / "main.py")],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},  # explicitly WITHOUT the exhibit variable
        timeout=30,
    )
    assert result.returncode != 0, "v1 started — the kill switch is gone"
    assert "retired" in (result.stdout + result.stderr)


def test_v1_env_has_no_default_password_and_no_public_bind():
    env_file = REPO / "backend" / ".env"
    if not env_file.exists():
        return  # nothing to leak
    content = env_file.read_text()
    assert "OWNER_PASSWORD=chief" not in content, "the default password is back"
    assert "HOST=0.0.0.0" not in content, "v1 is configured to bind every interface again"
