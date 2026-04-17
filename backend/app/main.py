"""Chief Command Center — FastAPI application entry point."""

import logging
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
from db import init_db
from services.auth import create_token, require_auth, verify_password, hash_password
from services.agent_tracker import get_agents as tracker_get_agents
from services.project_parser import get_project, get_projects
from services.team_service import get_team, get_agent_memory, put_agent_memory
from services.memory_service import get_all_memory, get_memory_file, put_memory_file
from services.usage_tracker import (
    get_rolling_totals,
    get_session_totals,
    get_session_with_turns,
    list_sessions,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Chief Command Center",
    version="2.0.0",
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

app.include_router(ws_router)

_OWNER_HASH: str = hash_password(settings.OWNER_PASSWORD)

MONTHLY_WARNING_CENTS = 20_000
MONTHLY_CRITICAL_CENTS = 30_000


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    token: str
    expires_days: int


class UploadResponse(BaseModel):
    path: str
    filename: str


# Team / memory
class AgentMemoryResponse(BaseModel):
    name: str
    content: str
    updated_at: str


class AgentMemoryPutRequest(BaseModel):
    content: str


class MemoryFilePutRequest(BaseModel):
    content: str


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.post("/api/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest) -> LoginResponse:
    if not verify_password(body.password, _OWNER_HASH):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password")
    token = create_token(subject="owner")
    logger.info("Owner logged in")
    return LoginResponse(token=token, expires_days=settings.JWT_EXPIRE_DAYS)


@app.get("/api/auth/verify")
async def verify_auth(subject: str = Depends(require_auth)) -> dict[str, str]:
    return {"status": "valid", "subject": subject}


# ---------------------------------------------------------------------------
# Status / agents
# ---------------------------------------------------------------------------

@app.get("/api/status")
async def get_status(subject: str = Depends(require_auth)) -> dict:
    return {
        "api": "anthropic",
        "projects_dir": settings.PROJECTS_DIR,
        "tunnel_url": settings.TUNNEL_URL,
    }


@app.get("/api/agents")
async def get_agents_endpoint(subject: str = Depends(require_auth)) -> list[dict]:
    return tracker_get_agents()


@app.get("/api/agents/reviews")
async def get_agent_reviews(subject: str = Depends(require_auth)) -> list[dict]:
    return []


# ---------------------------------------------------------------------------
# Team (named roster + per-agent memory)
# ---------------------------------------------------------------------------

@app.get("/api/team")
async def api_get_team(subject: str = Depends(require_auth)) -> dict:
    agents = get_team()
    return {"agents": agents}


@app.get("/api/team/{name}/memory", response_model=AgentMemoryResponse)
async def api_get_agent_memory(
    name: str, subject: str = Depends(require_auth)
) -> AgentMemoryResponse:
    try:
        data = get_agent_memory(name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return AgentMemoryResponse(**data)


@app.put("/api/team/{name}/memory", response_model=AgentMemoryResponse)
async def api_put_agent_memory(
    name: str, body: AgentMemoryPutRequest, subject: str = Depends(require_auth)
) -> AgentMemoryResponse:
    try:
        data = put_agent_memory(name, body.content)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except OSError as exc:
        logger.error("Failed to write agent memory for %s: %s", name, exc)
        raise HTTPException(status_code=500, detail="Failed to write memory file")
    return AgentMemoryResponse(**data)


# ---------------------------------------------------------------------------
# Memory (global / per-project / per-agent / audit log)
# ---------------------------------------------------------------------------

@app.get("/api/memory")
async def api_get_memory(subject: str = Depends(require_auth)) -> dict:
    return get_all_memory()


@app.get("/api/memory/{filename}")
async def api_get_memory_file(
    filename: str, subject: str = Depends(require_auth)
) -> dict:
    try:
        return get_memory_file(filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.put("/api/memory/{filename}")
async def api_put_memory_file(
    filename: str, body: MemoryFilePutRequest, subject: str = Depends(require_auth)
) -> dict:
    try:
        return put_memory_file(filename, body.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OSError as exc:
        logger.error("Failed to write memory file %s: %s", filename, exc)
        raise HTTPException(status_code=500, detail="Failed to write memory file")


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

@app.get("/api/projects")
async def api_list_projects(subject: str = Depends(require_auth)) -> dict[str, object]:
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
    data = get_project(slug)
    if data is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return data


# ---------------------------------------------------------------------------
# File upload
# ---------------------------------------------------------------------------

@app.post("/api/upload", response_model=UploadResponse)
async def upload_file(file: UploadFile, subject: str = Depends(require_auth)) -> UploadResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    ext = Path(file.filename).suffix
    unique_name = f"{uuid.uuid4().hex}{ext}"
    dest = settings.upload_path / unique_name

    async with aiofiles.open(dest, "wb") as f:
        while chunk := await file.read(1024 * 64):
            await f.write(chunk)

    logger.info("File uploaded to %s", dest)
    return UploadResponse(path=str(dest), filename=file.filename)


# ---------------------------------------------------------------------------
# Sessions & usage (v2)
# ---------------------------------------------------------------------------

@app.get("/api/sessions")
async def api_list_sessions(subject: str = Depends(require_auth)) -> list[dict]:
    return await list_sessions(limit=50)


@app.get("/api/sessions/current")
async def api_current_session(subject: str = Depends(require_auth)) -> dict:
    sessions = await list_sessions(limit=1)
    if not sessions:
        return {}
    session = sessions[0]
    if session.get("ended_at"):
        return {}
    totals = await get_session_totals(session["id"])
    return {**session, **totals}


@app.get("/api/sessions/{session_id}")
async def api_get_session(session_id: str, subject: str = Depends(require_auth)) -> dict:
    data = await get_session_with_turns(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return data


@app.get("/api/usage/summary")
async def api_usage_summary(subject: str = Depends(require_auth)) -> dict:
    totals = await get_rolling_totals()
    month_cents = totals["month_cents"]

    if month_cents >= MONTHLY_CRITICAL_CENTS:
        alert_level = "critical"
    elif month_cents >= MONTHLY_WARNING_CENTS:
        alert_level = "warning"
    else:
        alert_level = "none"

    return {**totals, "alert_level": alert_level}


# ---------------------------------------------------------------------------
# Static files — serve the React frontend
# ---------------------------------------------------------------------------

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"

if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="static-assets")

    @app.get("/manifest.json")
    async def manifest():
        return FileResponse(FRONTEND_DIR / "manifest.json")

    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str):
        if full_path.startswith(("api/", "ws/", "docs", "openapi.json")):
            raise HTTPException(status_code=404)
        file_path = FRONTEND_DIR / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIR / "index.html")


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def on_startup() -> None:
    await init_db()
    logger.info("Chief Command Center v2 starting on %s:%s", settings.HOST, settings.PORT)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    logger.info("Chief Command Center stopped")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=True)
