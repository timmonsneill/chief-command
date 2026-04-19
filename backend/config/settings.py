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

    ANTHROPIC_API_KEY: Optional[str] = None

    TUNNEL_URL: Optional[str] = None
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    UPLOAD_DIR: str = "/tmp/chief-uploads"

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
