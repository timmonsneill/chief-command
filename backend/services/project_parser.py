"""Parse real project definitions from PROJECTS.json + per-project md files.

Reads from ``settings.projects_data_dir`` (the CC dashboard data directory,
at ``backend/data/projects/`` inside this repo), NOT from Claude Code's
memory directory. PROJECTS.json + referenced md files live in-repo so
they're versioned alongside the code that reads them."""

import json
import logging
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from config.settings import settings

logger = logging.getLogger(__name__)

_CHECKBOX_RE = re.compile(r"^\s*[-*]\s*\[([ xX])\]\s*(.+)$", re.MULTILINE)
_PHASE_HEADER_RE = re.compile(
    r"^#{1,4}\s+(?:Phase\s+\d+[:\s]*|Step\s+\d+[:\s]*)(.+?)(?:\s*[✅✓])?$",
    re.MULTILINE,
)
_DATE_RE = re.compile(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})\s*[-:—]\s*(.+)")
_H2_SECTION_RE = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _parse_checkboxes(text: str) -> list[dict[str, Any]]:
    return [
        {
            "id": f"todo-{i}",
            "category": "General",
            "text": m.group(2).strip(),
            "done": m.group(1).lower() == "x",
        }
        for i, m in enumerate(_CHECKBOX_RE.finditer(text))
    ]


def _parse_phases(text: str) -> list[dict[str, Any]]:
    phases: list[dict[str, Any]] = []
    headers = list(_PHASE_HEADER_RE.finditer(text))
    for idx, hdr in enumerate(headers):
        start = hdr.end()
        end = headers[idx + 1].start() if idx + 1 < len(headers) else len(text)
        section = text[start:end]
        items = _parse_checkboxes(section)
        done_count = sum(1 for i in items if i["done"])
        total = len(items)
        complete = bool(items) and done_count == total
        complete = complete or "✅" in hdr.group(0) or "✓" in hdr.group(0)
        phases.append({
            "name": hdr.group(1).strip(),
            "complete": complete,
            "items": items,
            "total": total,
            "completed": done_count,
            "percent": round(done_count / total * 100) if total else (100 if complete else 0),
        })
    return phases


def _parse_milestones(text: str) -> list[dict[str, str]]:
    return [
        {"date": m.group(1), "label": m.group(2).strip()}
        for m in _DATE_RE.finditer(text)
    ]


def _extract_description(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and not stripped.startswith("-") and not stripped.startswith("---"):
            return stripped[:200]
    return ""


def _git_log(repo_path: str, n: int = 10) -> list[dict[str, str]]:
    p = Path(repo_path)
    if not p.exists():
        return []
    try:
        out = subprocess.check_output(
            ["git", "log", f"-{n}", "--pretty=format:%H|%ai|%s", "--no-merges"],
            cwd=str(p),
            timeout=5,
            stderr=subprocess.DEVNULL,
        ).decode("utf-8", errors="replace")
        commits = []
        for line in out.strip().splitlines():
            parts = line.split("|", 2)
            if len(parts) == 3:
                commits.append({"hash": parts[0][:8], "date": parts[1].strip(), "message": parts[2].strip()})
        return commits
    except Exception:
        return []


def _load_projects_json() -> list[dict[str, Any]]:
    projects_file = settings.projects_data_dir / "PROJECTS.json"
    if not projects_file.exists():
        default: list[dict[str, Any]] = [
            {
                "id": "arch",
                "name": "Arch to Freedom EMR",
                "path": str(Path.home() / "Documents" / "GitHub" / "arch-to-freedom-emr"),
                "repo_url": str(Path.home() / "Documents" / "GitHub" / "arch-to-freedom-emr"),
                "memory_files": [],
                "status": "active",
                "description": "Recovery house EMR — clinical notes, medications, billing, tasks, AI assistant for staff.",
            },
            {
                "id": "chief-command",
                "name": "Chief Command Center",
                "path": str(Path.home() / "Desktop" / "chief-command"),
                "repo_url": str(Path.home() / "Desktop" / "chief-command"),
                "memory_files": [],
                "status": "active",
                "description": "Owner-only AI command center — voice interface to Claude, agent orchestration, usage tracking.",
            },
            {
                "id": "personal-assist",
                "name": "Personal Assist",
                "path": str(Path.home() / "Desktop" / "personal-assist"),
                "repo_url": str(Path.home() / "Desktop" / "personal-assist"),
                "memory_files": [],
                "status": "active",
                "description": "Personal AI assistant (voice alias 'Jess') — Google-native brain, dashboard + action layer.",
            },
        ]
        try:
            projects_file.write_text(json.dumps(default, indent=2), encoding="utf-8")
            logger.info("Created default PROJECTS.json at %s", projects_file)
        except OSError as exc:
            logger.warning("Could not write PROJECTS.json: %s", exc)
        return default

    try:
        return json.loads(projects_file.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("Failed to parse PROJECTS.json: %s", exc)
        return []


def _build_summary(entry: dict[str, Any]) -> dict[str, Any]:
    mem_dir = settings.projects_data_dir
    memory_files: list[str] = entry.get("memory_files", [])

    combined_text = ""
    last_modified: float = 0.0
    for fname in memory_files:
        fp = mem_dir / fname
        if fp.exists():
            combined_text += _read_text(fp) + "\n"
            mtime = fp.stat().st_mtime
            if mtime > last_modified:
                last_modified = mtime

    todos = _parse_checkboxes(combined_text)
    total_todos = len(todos)
    done_todos = sum(1 for t in todos if t["done"])

    last_activity = (
        datetime.fromtimestamp(last_modified).isoformat()
        if last_modified
        else None
    )

    return {
        "id": entry["id"],
        "name": entry["name"],
        "status": entry.get("status", "active"),
        "description": entry.get("description", ""),
        "todo_total": total_todos,
        "todo_done": done_todos,
        "todo_percent": round(done_todos / total_todos * 100) if total_todos else 0,
        "last_activity": last_activity,
    }


def get_projects() -> list[dict[str, Any]]:
    """Return lightweight project summaries from PROJECTS.json."""
    entries = _load_projects_json()
    return [_build_summary(e) for e in entries]


def get_project(project_id: str) -> dict[str, Any] | None:
    """Return full dashboard payload for a single project."""
    entries = _load_projects_json()
    entry = next((e for e in entries if e["id"] == project_id), None)
    if entry is None:
        return None

    mem_dir = settings.projects_data_dir
    memory_files: list[str] = entry.get("memory_files", [])

    combined_text = ""
    last_modified: float = 0.0
    for fname in memory_files:
        fp = mem_dir / fname
        if fp.exists():
            combined_text += _read_text(fp) + "\n"
            mtime = fp.stat().st_mtime
            if mtime > last_modified:
                last_modified = mtime

    todos = _parse_checkboxes(combined_text)
    phases = _parse_phases(combined_text)
    milestones = _parse_milestones(combined_text)

    total_todos = len(todos)
    done_todos = sum(1 for t in todos if t["done"])

    repo_path = entry.get("repo_url") or entry.get("path") or ""
    recent_activity = _git_log(repo_path, n=10)

    if not recent_activity and last_modified:
        recent_activity = [{
            "hash": "",
            "date": datetime.fromtimestamp(last_modified).isoformat(),
            "message": "Memory file updated",
        }]

    description = entry.get("description", "") or _extract_description(combined_text)

    return {
        "id": entry["id"],
        "slug": entry["id"],
        "name": entry["name"],
        "status": entry.get("status", "active"),
        "description": description,
        "dashboard_url": entry.get("dashboard_url", ""),
        "phases": phases,
        "todos": todos,
        "todo_progress": {
            "total": total_todos,
            "done": done_todos,
            "percent": round(done_todos / total_todos * 100) if total_todos else 0,
        },
        "milestones": milestones,
        "recent_activity": recent_activity,
        "builds": [],
    }


# ---------------------------------------------------------------------------
# Legacy aliases kept for any import that still uses the old names
# ---------------------------------------------------------------------------

def list_projects() -> list[dict[str, Any]]:
    return get_projects()


def parse_memory_index() -> list[dict[str, str]]:
    return []
