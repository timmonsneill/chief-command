"""Application settings loaded from environment variables."""

import secrets
import shutil
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Chief Command Center configuration.

    All values can be overridden via environment variables or a .env file
    located in the backend directory.
    """

    OWNER_PASSWORD: str = "chief"
    JWT_SECRET: str = secrets.token_urlsafe(32)
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_DAYS: int = 30

    CLAUDE_CODE_PATH: str = shutil.which("claude") or "claude"
    PROJECTS_DIR: str = str(Path.home() / ".claude" / "projects")
    MEMORY_SUBDIR: str = "-Users-user/memory"

    TUNNEL_URL: Optional[str] = None
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    UPLOAD_DIR: str = "/tmp/chief-uploads"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
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
