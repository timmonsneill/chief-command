"""Chief Command Center — FastAPI application entry point."""

import logging
import shutil
import uuid
from pathlib import Path

import aiofiles
from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.websockets import router as ws_router
from config.settings import settings
from services.auth import create_token, require_auth, verify_password, hash_password
from services.claude_pipe import claude_pipe
from services.project_parser import get_project, get_projects, list_projects, parse_memory_index
from services.agent_tracker import get_agents as tracker_get_agents

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Chief Command Center",
    version="0.1.0",
    docs_url="/docs",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount WebSocket routes
app.include_router(ws_router)

# Precomputed password hash for the owner password
_OWNER_HASH: str = hash_password(settings.OWNER_PASSWORD)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    token: str
    expires_days: int


class StatusResponse(BaseModel):
    claude_reachable: bool
    claude_path: str
    projects_dir: str
    tunnel_url: str | None


class UploadResponse(BaseModel):
    path: str
    filename: str


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

@app.post("/api/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest) -> LoginResponse:
    if not verify_password(body.password, _OWNER_HASH):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid password",
        )
    token = create_token(subject="owner")
    logger.info("Owner logged in")
    return LoginResponse(token=token, expires_days=settings.JWT_EXPIRE_DAYS)


@app.get("/api/auth/verify")
async def verify_auth(subject: str = Depends(require_auth)) -> dict[str, str]:
    return {"status": "valid", "subject": subject}


# ---------------------------------------------------------------------------
# Status / agents
# ---------------------------------------------------------------------------

@app.get("/api/status", response_model=StatusResponse)
async def get_status(subject: str = Depends(require_auth)) -> StatusResponse:
    reachable = await claude_pipe.is_reachable()
    return StatusResponse(
        claude_reachable=reachable,
        claude_path=settings.CLAUDE_CODE_PATH,
        projects_dir=settings.PROJECTS_DIR,
        tunnel_url=settings.TUNNEL_URL,
    )


@app.get("/api/agents")
async def get_agents_endpoint(subject: str = Depends(require_auth)) -> list[dict]:
    return tracker_get_agents()


@app.get("/api/agents/reviews")
async def get_agent_reviews(subject: str = Depends(require_auth)) -> list[dict]:
    """Return recent review sweeps. Currently returns empty list since reviews
    are only populated during active build cycles."""
    return []


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

@app.get("/api/projects")
async def api_list_projects(
    subject: str = Depends(require_auth),
) -> dict[str, object]:
    projects = get_projects()
    return {"projects": projects}


@app.get("/api/projects/{project_id}")
async def api_get_project(
    project_id: str, subject: str = Depends(require_auth)
) -> dict[str, object]:
    data = get_project(project_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return data


@app.get("/api/share/{slug}")
async def api_share_project(slug: str) -> dict[str, object]:
    """Public endpoint for shared project dashboards — no auth required."""
    data = get_project(slug)
    if data is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return data


# ---------------------------------------------------------------------------
# File upload
# ---------------------------------------------------------------------------

@app.post("/api/upload", response_model=UploadResponse)
async def upload_file(
    file: UploadFile,
    subject: str = Depends(require_auth),
) -> UploadResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    ext = Path(file.filename).suffix
    unique_name = f"{uuid.uuid4().hex}{ext}"
    dest = settings.upload_path / unique_name

    async with aiofiles.open(dest, "wb") as f:
        while chunk := await file.read(1024 * 64):
            await f.write(chunk)

    logger.info("File uploaded: %s -> %s", file.filename, dest)
    return UploadResponse(path=str(dest), filename=file.filename)


# ---------------------------------------------------------------------------
# Static files — serve the React frontend
# ---------------------------------------------------------------------------

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"

if FRONTEND_DIR.exists():
    # Serve static assets (JS, CSS, images)
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="static-assets")

    # Serve other static files from dist root (manifest, icons, sw.js)
    @app.get("/manifest.json")
    async def manifest():
        return FileResponse(FRONTEND_DIR / "manifest.json")

    # SPA catch-all — must be LAST route
    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str):
        # Don't catch API or WebSocket routes
        if full_path.startswith(("api/", "ws/", "docs", "openapi.json")):
            raise HTTPException(status_code=404)
        # Try to serve the exact file first (e.g., favicon.ico)
        file_path = FRONTEND_DIR / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        # Otherwise serve index.html for SPA routing
        return FileResponse(FRONTEND_DIR / "index.html")


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def on_startup() -> None:
    logger.info("Chief Command Center starting on %s:%s", settings.HOST, settings.PORT)
    logger.info("Claude Code path: %s", settings.CLAUDE_CODE_PATH)
    logger.info("Projects dir: %s", settings.PROJECTS_DIR)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await claude_pipe.stop()
    logger.info("Chief Command Center stopped")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True,
    )
