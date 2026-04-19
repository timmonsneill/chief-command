"""Shared pytest fixtures for Chief Command backend tests.

Sets up env vars that pydantic-settings requires at import time.

Also neutralizes `services/__init__.py` eager-loading of STT/TTS singletons
(which pull in optional ML deps) so unit tests for individual services can run
without installing the full runtime dependency tree. We register a fake empty
`services` package before any test imports `from services import ...`.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

# Make backend/ the import root so `services.*` / `config.*` resolve.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

# Required by config.settings.Settings — any values will do for tests.
os.environ.setdefault("OWNER_PASSWORD", "test-password")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-real")


# Replace `services` package with an empty namespace so individual modules
# under it (repo_map, classifier, dispatcher, voice_provider, stt_google,
# tts_google) can be imported without pulling in STT/TTS/auth/Kokoro/faster-
# whisper. Submodule imports still work because Python will resolve them
# relative to the stub's __path__.
_services_pkg = types.ModuleType("services")
_services_pkg.__path__ = [str(_BACKEND_ROOT / "services")]  # type: ignore[attr-defined]
sys.modules["services"] = _services_pkg
