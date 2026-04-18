"""Team service — named agent roster and per-agent memory file I/O.

Roster is sourced from the canonical roster file at
~/.claude/projects/-Users-user/memory/project_agent_roster.md
and hardcoded here for fast, stable access.  Names and roles mirror that file
exactly; do not rename without owner sign-off.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.memory_paths import AGENT_MEMORY_DIR as _AGENT_MEMORY_DIR

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical roster — mirrors project_agent_roster.md
# ---------------------------------------------------------------------------

ROSTER: list[dict[str, Any]] = [
    {
        "name": "Chief",
        "role": "Orchestrator",
        "lean": "Watches all agents, appends lessons to their memory, catches drift",
        "model": "opus",
        "tier": "chief",
        "description": "Top-level orchestrator. Spawns builders, monitors progress, catches drift.",
    },
    {
        "name": "Atlas",
        "role": "Researcher",
        "lean": "Knowledge, maps, deep-dive research",
        "model": "opus",
        "tier": "opus",
        "description": "Deep research and knowledge mapping across all projects.",
    },
    {
        "name": "Forge",
        "role": "Integration tester",
        "lean": "Proves-it-works, fire-tested, end-to-end verification",
        "model": "opus",
        "tier": "opus",
        "description": "Full integration pass after builds — proves the system actually works.",
    },
    {
        "name": "Riggs",
        "role": "Builder — backend",
        "lean": "Systems, FastAPI, SQL, async, infra, migrations",
        "model": "sonnet",
        "tier": "sonnet",
        "description": "Backend builder. FastAPI routes, SQL, infra, and migrations.",
    },
    {
        "name": "Finn",
        "role": "Builder — frontend",
        "lean": "React, Tailwind, iOS quirks, animations",
        "model": "sonnet",
        "tier": "sonnet",
        "description": "Frontend builder. React, Tailwind, iOS quirks, and animations.",
    },
    {
        "name": "Nova",
        "role": "Builder — glue/data",
        "lean": "Parsers, dashboards, LLM wiring, metrics, cross-cutting",
        "model": "sonnet",
        "tier": "sonnet",
        "description": "Glue builder. Parsers, LLM wiring, dashboards, and cross-cutting work.",
    },
    {
        "name": "Vera",
        "role": "Security reviewer",
        "lean": "Verify, vigilance, security audit",
        "model": "sonnet",
        "tier": "sonnet",
        "description": "Security review. Catches auth flaws, injection risks, and data exposure.",
    },
    {
        "name": "Hawke",
        "role": "Bug hunter",
        "lean": "Eagle eye, edge cases, regression hunting",
        "model": "sonnet",
        "tier": "sonnet",
        "description": "Bug hunter. Eagle eye for edge cases and regressions.",
    },
    {
        "name": "Sable",
        "role": "Hygiene reviewer",
        "lean": "Dark/tidy, clean sweeper, dead code",
        "model": "sonnet",
        "tier": "sonnet",
        "description": "Hygiene reviewer. Trims dead code and keeps the codebase clean.",
    },
    {
        "name": "Pax",
        "role": "Practical reviewer (wiring)",
        "lean": "Pragmatic, wiring checker, integration sanity",
        "model": "sonnet",
        "tier": "sonnet",
        "description": "Wiring checker. Ensures components actually connect end-to-end.",
    },
    {
        "name": "Quill",
        "role": "QA verifier",
        "lean": "Precise, requirements scribe, acceptance criteria",
        "model": "sonnet",
        "tier": "sonnet",
        "description": "QA verifier. Validates against requirements and acceptance criteria.",
    },
    {
        "name": "Hip",
        "role": "HIPAA reviewer (Arch only)",
        "lean": "HIPAA compliance, PHI audit — Arch project only",
        "model": "sonnet",
        "tier": "sonnet",
        "description": "HIPAA reviewer fired exclusively on Arch project builds.",
    },
]

# Build a quick name -> roster entry lookup
_ROSTER_BY_NAME: dict[str, dict[str, Any]] = {a["name"].lower(): a for a in ROSTER}


# ---------------------------------------------------------------------------
# Memory file helpers
# ---------------------------------------------------------------------------

def _memory_path(name: str) -> Path:
    """Absolute path to a named agent's memory file."""
    return _AGENT_MEMORY_DIR / f"{name.lower()}.md"


def _read_memory_file(name: str) -> tuple[str, str | None]:
    """Return (content, updated_at_iso).  Content is '' when file is missing.

    Refuses to follow symlinks — rejects silently and returns empty.
    """
    path = _memory_path(name)
    if not path.exists() or path.is_symlink():
        return "", None
    try:
        mtime = path.stat().st_mtime
        updated_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
        content = path.read_text(encoding="utf-8")
        return content, updated_at
    except OSError as exc:
        logger.warning("Could not read memory file for %s: %s", name, exc)
        return "", None


def _write_memory_file(name: str, content: str) -> str:
    """Write content to the agent's memory file.  Returns updated_at ISO timestamp.

    Rejects writes through symlinks — callers shouldn't be able to redirect
    writes to an arbitrary path via a planted symlink.
    """
    path = _memory_path(name)
    if path.exists() and path.is_symlink():
        raise OSError(f"Refusing to overwrite symlinked memory file for {name}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        mtime = path.stat().st_mtime
        return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    except OSError as exc:
        logger.error("Could not write memory file for %s: %s", name, exc)
        raise


# ---------------------------------------------------------------------------
# Public API used by main.py
# ---------------------------------------------------------------------------

def get_team() -> list[dict[str, Any]]:
    """Return the full roster with filesystem-resolved memory_path fields.

    last_active and invocations_total are stubbed (null / 0).
    Scanning subagent JSONL files per-request is too expensive; a future
    background task can populate these.  TODO: wire up agent_tracker data.
    """
    result: list[dict[str, Any]] = []
    for agent in ROSTER:
        name = agent["name"]
        mem_path = _memory_path(name)
        has_memory = mem_path.exists() and not mem_path.is_symlink()
        result.append(
            {
                "name": name,
                "role": agent["role"],
                "lean": agent["lean"],
                "model": agent["model"],
                "tier": agent["tier"],
                "has_memory": has_memory,
                "last_active": None,
                "invocations_total": 0,
                "description": agent["description"],
            }
        )
    return result


def get_agent_memory(name: str) -> dict[str, Any]:
    """Return memory content for a named agent.  Raises ValueError if unknown name."""
    if name.lower() not in _ROSTER_BY_NAME:
        raise ValueError(f"Unknown agent: {name!r}")
    content, updated_at = _read_memory_file(name)
    return {
        "name": name,
        "content": content,
        "updated_at": updated_at or datetime.now(timezone.utc).isoformat(),
    }


def put_agent_memory(name: str, content: str) -> dict[str, Any]:
    """Write memory content for a named agent.  Raises ValueError if unknown name."""
    if name.lower() not in _ROSTER_BY_NAME:
        raise ValueError(f"Unknown agent: {name!r}")
    updated_at = _write_memory_file(name, content)
    return {
        "name": name,
        "content": content,
        "updated_at": updated_at,
    }
