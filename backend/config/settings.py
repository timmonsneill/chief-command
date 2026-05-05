"""Application settings loaded from environment variables."""

import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Chief Command Center configuration.

    All values can be overridden via environment variables or a .env file
    located in the backend directory.
    """

    OWNER_PASSWORD: str  # No default — MUST be set in .env
    JWT_SECRET: str  # No default — MUST be set in .env (tokens invalidated on restart otherwise)
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_DAYS: int = 30
    ALLOWED_ORIGINS: str = "https://chiefcommand.app,http://localhost:3000,http://localhost:8000"

    CLAUDE_CODE_PATH: str = shutil.which("claude") or "claude"
    PROJECTS_DIR: str = str(Path.home() / ".claude" / "projects")
    MEMORY_SUBDIR: str = "-Users-user/memory"

    # CC dashboard data directory — PROJECTS.json + the project-level md files
    # the dashboard reads. Lives INSIDE this repo (not under ~/.claude/projects)
    # so app data ships with the code that reads it. See
    # backend/data/projects/. Previously parked in MEMORY_SUBDIR which was a
    # category error (Claude Code memory dir used as app-data).
    PROJECTS_DATA_DIR: str = str(
        (Path(__file__).resolve().parent.parent / "data" / "projects")
    )

    ANTHROPIC_API_KEY: Optional[str] = None

    TUNNEL_URL: Optional[str] = None
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    UPLOAD_DIR: str = "/tmp/chief-uploads"

    # ------------------------------------------------------------------ #
    # Phase 2 — Gemini brain (AI Studio API key OR Vertex AI service-account)
    # ------------------------------------------------------------------ #
    # The brain auto-detects which auth path to use at client init:
    #   1. If GEMINI_API_KEY (or its alias GOOGLE_API_KEY) is set, the
    #      AI Studio path is used — no project/location/IAM required, just
    #      enable "Generative Language API" in GCP and mint a key.
    #   2. Otherwise the Vertex AI service-account path is used, requiring
    #      GOOGLE_APPLICATION_CREDENTIALS pointed at a JSON key with the
    #      Vertex AI roles, plus the project + location below.
    # See services/gemini_brain._get_client for the resolution order.
    GEMINI_API_KEY: Optional[str] = None
    GOOGLE_API_KEY: Optional[str] = None  # alias accepted by genai SDK

    # Project + region used by gemini_brain when it falls back to the
    # ``google-genai`` Vertex AI client. Defaults match the existing
    # ``chief-command-voice`` GCP project (same one Phase 1.1 voice uses);
    # location is the lowest-latency region for the owner's home network.
    # Override either via environment variables if Vertex moves elsewhere.
    VERTEX_AI_PROJECT: str = "chief-command-voice"
    VERTEX_AI_LOCATION: str = "us-central1"

    # ------------------------------------------------------------------ #
    # Voice provider selection (Phase 1.1)
    # ------------------------------------------------------------------ #
    # "local" (default) uses faster-whisper + Kokoro, no cloud creds needed.
    # "google" swaps in Cloud Speech v2 streaming + Chirp3 HD TTS — requires
    # GOOGLE_APPLICATION_CREDENTIALS pointing at a service account JSON.
    VOICE_PROVIDER: str = "local"
    GOOGLE_APPLICATION_CREDENTIALS: Optional[str] = None
    GOOGLE_TTS_VOICE: str = "en-US-Chirp3-HD-Aoede"
    GOOGLE_STT_LANGUAGE: str = "en-US"
    # STT streaming silence timeout (ms). If Google hasn't emitted an
    # is_final result in this window of silence, we surface the best interim
    # transcript so the backend isn't hanging on Google. 500ms balances
    # responsiveness (feels snappy) against noisy inputs (where interim
    # results flicker before settling into a final).
    GOOGLE_STT_SILENCE_TIMEOUT_MS: int = 500

    # ------------------------------------------------------------------ #
    # Stage 4 — Live API resilience knobs
    # ------------------------------------------------------------------ #
    # Daily hard cap (USD) checked at WS open + after each turn. Mirrors the
    # ``DAILY_COST_CAP_DOLLARS`` env var read inside ``services.usage_tracker``;
    # surfaced here so the value is visible in the settings inspector. Source
    # of truth on read remains the env var so a runtime override doesn't need
    # a settings reload.
    DAILY_COST_CAP_DOLLARS: float = 15.00

    # How many times the voice WS will rebuild a LiveSession (using the
    # cached session resumption handle) on a receive-pump exception before
    # giving up and emitting an error frame. 2 covers transient Vertex
    # hiccups + the 10-min server-side rotation; a 3rd drop in one WS
    # connection implies a real problem and is not worth retrying.
    LIVE_RECONNECT_MAX_RETRIES: int = 2

    # Session resumption handles are valid for 2 hours per Live API spec.
    # Past that we drop the cached handle and the WS must rebuild a fresh
    # session (losing server-side context). Stored as seconds for direct
    # comparison against monotonic timestamps.
    LIVE_RESUMPTION_HANDLE_MAX_AGE_S: int = 7200

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        "extra": "ignore",
    }

    @property
    def memory_dir(self) -> Path:
        return Path(self.PROJECTS_DIR) / self.MEMORY_SUBDIR

    @property
    def projects_data_dir(self) -> Path:
        """Directory containing PROJECTS.json + per-project dashboard md files.

        Lives inside the repo at ``backend/data/projects/`` so it's versioned
        alongside the code that reads it.
        """
        return Path(self.PROJECTS_DATA_DIR)

    @property
    def upload_path(self) -> Path:
        p = Path(self.UPLOAD_DIR)
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()


# Expose GOOGLE_APPLICATION_CREDENTIALS into os.environ so the Google client
# libraries (which read that env var directly, not our settings object) can
# find the service account JSON regardless of whether the owner sets the var
# in the shell, .env, or both. No-op in local mode.
if settings.GOOGLE_APPLICATION_CREDENTIALS and not os.environ.get(
    "GOOGLE_APPLICATION_CREDENTIALS"
):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = settings.GOOGLE_APPLICATION_CREDENTIALS
    logger.info(
        "GOOGLE_APPLICATION_CREDENTIALS exported from settings: %s",
        settings.GOOGLE_APPLICATION_CREDENTIALS,
    )
