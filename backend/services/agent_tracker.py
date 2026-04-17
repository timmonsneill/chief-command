"""Track active and recent Claude Code subagents from /private/tmp/claude-501."""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CLAUDE_TMP_BASE = Path("/private/tmp/claude-501")
_CLAUDE_PROJECTS_BASE = Path.home() / ".claude" / "projects"
_CACHE_TTL = 2.0

_cache: dict[str, Any] = {"ts": 0.0, "data": []}


def _parse_agent_jsonl(jsonl_path: Path) -> dict[str, Any]:
    """Extract name, started_at, completed_at, summary from a subagent JSONL."""
    started_at: str | None = None
    completed_at: str | None = None
    summary: str = ""
    agent_id: str = jsonl_path.stem.removeprefix("agent-")

    try:
        with jsonl_path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw_line in fh:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    rec = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue

                ts = rec.get("timestamp")
                msg = rec.get("message", {})

                if not started_at and ts:
                    started_at = ts

                if ts:
                    completed_at = ts

                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    content = msg.get("content", [])
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text = block.get("text", "").strip()
                                if text and len(text) > len(summary):
                                    summary = text
                    elif isinstance(content, str) and len(content) > len(summary):
                        summary = content

    except OSError:
        pass

    if len(summary) > 200:
        summary = summary[:197] + "..."

    return {
        "agent_id": agent_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "summary": summary,
    }


def _scan_project_subagents(session_dir: Path) -> list[dict[str, Any]]:
    """Scan a session directory for subagent .jsonl files + .meta.json."""
    subagents_dir = session_dir / "subagents"
    if not subagents_dir.exists():
        return []

    agents: list[dict[str, Any]] = []
    meta_map: dict[str, dict[str, Any]] = {}

    for meta_file in subagents_dir.glob("*.meta.json"):
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
            agent_stem = meta_file.stem.removesuffix(".meta")
            agent_id = agent_stem.removeprefix("agent-")
            meta_map[agent_id] = data
        except Exception:
            pass

    for jsonl_file in subagents_dir.glob("agent-*.jsonl"):
        parsed = _parse_agent_jsonl(jsonl_file)
        agent_id = parsed["agent_id"]
        meta = meta_map.get(agent_id, {})

        agent_type = meta.get("agentType", "Builder")
        description = meta.get("description", "")
        worktree_path = meta.get("worktreePath", "")

        mtime = jsonl_file.stat().st_mtime
        now = time.time()
        age_seconds = now - mtime
        status = "running" if age_seconds < 30 else "completed"

        elapsed: float | None = None
        if parsed["started_at"] and parsed["completed_at"]:
            try:
                t0 = datetime.fromisoformat(parsed["started_at"].replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(parsed["completed_at"].replace("Z", "+00:00"))
                elapsed = (t1 - t0).total_seconds()
            except Exception:
                pass

        agents.append({
            "id": agent_id,
            "name": description or agent_type,
            "subagent_type": agent_type,
            "status": status,
            "started_at": parsed["started_at"],
            "completed_at": parsed["completed_at"],
            "elapsed_seconds": elapsed,
            "summary": parsed["summary"],
            "worktree_path": worktree_path,
            "last_active": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
        })

    return agents


def _scan_tmp_agents() -> list[dict[str, Any]]:
    """Read /private/tmp/claude-501 tasks/*.output symlinks."""
    agents: list[dict[str, Any]] = []
    if not _CLAUDE_TMP_BASE.exists():
        return agents

    for project_dir in _CLAUDE_TMP_BASE.iterdir():
        if not project_dir.is_dir():
            continue
        for session_dir in project_dir.iterdir():
            if not session_dir.is_dir():
                continue
            tasks_dir = session_dir / "tasks"
            if not tasks_dir.exists():
                continue
            for output_file in tasks_dir.glob("*.output"):
                try:
                    mtime = output_file.stat().st_mtime
                    now = time.time()
                    age_seconds = now - mtime
                    status = "running" if age_seconds < 30 else "completed"
                    agent_id = output_file.stem
                    agents.append({
                        "id": agent_id,
                        "name": agent_id,
                        "subagent_type": "Task",
                        "status": status,
                        "started_at": None,
                        "completed_at": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                        "elapsed_seconds": None,
                        "summary": "",
                        "worktree_path": "",
                        "last_active": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                    })
                except OSError:
                    pass

    return agents


def get_agents() -> list[dict[str, Any]]:
    """Return last 20 agents (running + recent). Cached for 2 seconds."""
    global _cache
    now = time.time()
    if now - _cache["ts"] < _CACHE_TTL:
        return _cache["data"]

    agents: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    if _CLAUDE_PROJECTS_BASE.exists():
        for project_dir in _CLAUDE_PROJECTS_BASE.iterdir():
            if not project_dir.is_dir():
                continue
            for session_dir in project_dir.iterdir():
                if not session_dir.is_dir():
                    continue
                for agent in _scan_project_subagents(session_dir):
                    if agent["id"] not in seen_ids:
                        seen_ids.add(agent["id"])
                        agents.append(agent)

    for agent in _scan_tmp_agents():
        if agent["id"] not in seen_ids:
            seen_ids.add(agent["id"])
            agents.append(agent)

    agents.sort(key=lambda a: a.get("last_active") or "", reverse=True)
    agents = agents[:20]

    _cache = {"ts": now, "data": agents}
    return agents
