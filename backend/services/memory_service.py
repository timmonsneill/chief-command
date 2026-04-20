"""Memory service — global, per-project, per-agent, and audit-log memory I/O.

File locations
--------------
Global memory : ~/.claude/projects/-Users-user/memory/*.md
                (excluding MEMORY.md and PROJECTS.json)
Per-agent memory: ~/.claude/agents/memory/<name>.md
Audit log     : ~/.claude/projects/-Users-user/memory/audit_log.md
                (return empty list if missing; never crash)

Shared constants + helpers live in ``memory_paths``; this module imports
from there so Chief's prompt builder and the REST memory API agree.
"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.memory_paths import (
    AGENT_MEMORY_DIR,
    AUDIT_LOG_PATH,
    GLOBAL_EXCLUDE,
    USER_MEMORY_DIR as _GLOBAL_MEMORY_DIR,
    classify_type,
    parse_frontmatter,
    safe_md_files,
)

logger = logging.getLogger(__name__)

# Named agents — must match team_service.ROSTER names
_AGENT_NAMES = [
    "Chief", "Atlas", "Forge", "Riggs", "Finn", "Nova",
    "Vera", "Hawke", "Sable", "Pax", "Quill", "Hip",
]

# Project classification: filename keywords -> project label.
# More-specific keywords MUST come before more-general ones because
# _classify_project returns on the first match (substring search).
_PROJECT_KEYWORDS: dict[str, str] = {
    # Archie is the AI brain layer inside Arch (same project, not a separate
    # scope). archie_*-prefixed memory files classify under Arch.
    "archie_cost": "Arch",
    "archie_voice": "Arch",
    "archie": "Arch",
    "arch": "Arch",
    # Chief Command — both the full phrase and the short "chief" prefix.
    # Files like project_chief_ui_design_system.md and
    # feedback_chief_ui_layout_direction.md don't contain "chief_command"
    # so without the bare "chief" keyword they'd leak into every scope.
    "chief_command": "Chief Command",
    "chief": "Chief Command",
    "voice_claude": "Chief Command",
    "agent_framework": "Chief Command",
    "agent_roster": "Chief Command",
    "infrastructure": "Chief Command",
    "api_transition": "Chief Command",
    # Session-handoff notes — owner-written day-end summaries. In practice
    # these are all about Chief Command work sessions; routing them here
    # prevents them from loading globally and contaminating Arch/PA scopes.
    "session_handoff": "Chief Command",
    # Personal Assist ("Jess" — voice alias). All three keywords needed —
    # project_pa_*, *_jess_*, *_personal_assist_* — so a file like
    # project_pa_overview.md or project_pa_tomorrow_pickup_*.md routes
    # correctly and doesn't leak into Chief Command or Arch scopes.
    "pa_": "Personal Assist",
    "personal_assist": "Personal Assist",
    "jess": "Personal Assist",
    # Legacy aliases — "Butler" dissolved into Chief Command Phase 9 on
    # 2026-04-18. Keep these so existing project_butler_*.md archive files
    # still classify under Chief Command instead of going unclassified.
    "butler_orchestration": "Chief Command",
    "butler": "Chief Command",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _mtime_iso(path: Path) -> str:
    """Return file mtime as ISO 8601 UTC string."""
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _classify_project(filename: str) -> str | None:
    """Return a project label for a filename, or None if it doesn't belong to one."""
    stem = filename.lower().replace(".md", "")
    for keyword, project in _PROJECT_KEYWORDS.items():
        if keyword in stem:
            return project
    return None


def _build_entry(path: Path) -> dict[str, Any]:
    """Build a MemoryEntry dict from a file path."""
    content = ""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read memory file %s: %s", path.name, exc)

    fm = parse_frontmatter(content)
    title = fm.get("name") or path.stem
    description = fm.get("description", "")
    ftype = fm.get("type") or classify_type(path.name)

    return {
        "filename": path.name,
        "title": title,
        "type": ftype,
        "description": description,
        "content": content,
        "updated_at": _mtime_iso(path),
    }


# ---------------------------------------------------------------------------
# Audit log parsing
# ---------------------------------------------------------------------------

_AUDIT_LINE_RE = re.compile(
    r"\*\*(?P<ts>[^*]+)\*\*\s*[—\-]\s*(?P<action>\w+)\s*[—\-]\s*`?(?P<target>[^`\n]+?)`?\s*[—\-]\s*(?P<reason>.+)"
)
# Matches section headers like: ## 2026-04-17 — session title
_AUDIT_SECTION_RE = re.compile(r"^##\s+(?P<date>\d{4}-\d{2}-\d{2})\s*[—\-]\s*(?P<title>.+)$")


def _parse_audit_log(content: str) -> list[dict[str, str]]:
    """Parse the audit_log.md into a list of AuditEntry dicts.

    Supports two formats:
    1. Inline: **<timestamp>** — <action> — `<target>` — <reason>
    2. Section header: ## YYYY-MM-DD — <session title>  (with bullet list body)

    Returns entries most-recent-first.
    """
    entries: list[dict[str, str]] = []
    current_section: dict[str, str] | None = None
    current_body_lines: list[str] = []

    def _flush_section() -> None:
        if current_section is not None:
            body = " ".join(current_body_lines).strip()
            entries.append({**current_section, "reason": body[:300] if body else ""})

    for line in content.splitlines():
        stripped = line.strip()

        # Try section-header format first.
        sec_m = _AUDIT_SECTION_RE.match(stripped)
        if sec_m:
            _flush_section()
            current_section = {
                "timestamp": sec_m.group("date"),
                "action": "session",
                "target": sec_m.group("title").strip(),
            }
            current_body_lines = []
            continue

        # Try inline **ts** — action — target — reason format.
        m = _AUDIT_LINE_RE.search(stripped)
        if m:
            _flush_section()
            current_section = None
            current_body_lines = []
            entries.append(
                {
                    "timestamp": m.group("ts").strip(),
                    "action": m.group("action").strip().lower(),
                    "target": m.group("target").strip(),
                    "reason": m.group("reason").strip(),
                }
            )
            continue

        # Accumulate body lines for the current section (bullet points, context lines).
        if current_section is not None and stripped and not stripped.startswith("#"):
            # Strip markdown bullet/bold markers for readability.
            clean = stripped.lstrip("-* ").strip()
            if clean:
                current_body_lines.append(clean)

    _flush_section()

    # Most-recent-first — file has newest entries at the top per audit_log.md convention.
    return entries


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_all_memory() -> dict[str, Any]:
    """Return the full memory payload: global, per_project, per_agent, audit_log."""

    # --- Global entries -------------------------------------------------
    global_entries: list[dict[str, Any]] = []
    project_buckets: dict[str, list[dict[str, Any]]] = {}

    for path in safe_md_files(_GLOBAL_MEMORY_DIR):
        if path.name in GLOBAL_EXCLUDE:
            continue
        entry = _build_entry(path)
        project = _classify_project(path.name)
        if project:
            project_buckets.setdefault(project, []).append(entry)
        else:
            global_entries.append(entry)

    # --- Per-project list -----------------------------------------------
    per_project: list[dict[str, Any]] = []
    for project_name, entries in sorted(project_buckets.items()):
        per_project.append(
            {
                "project": project_name,
                "status": "active",
                "entries": entries,
            }
        )

    # --- Per-agent memory -----------------------------------------------
    per_agent: list[dict[str, Any]] = []
    for name in _AGENT_NAMES:
        path = AGENT_MEMORY_DIR / f"{name.lower()}.md"
        if path.exists() and not path.is_symlink():
            try:
                content = path.read_text(encoding="utf-8")
                updated_at: str | None = _mtime_iso(path)
            except OSError:
                content, updated_at = "", None
        else:
            content, updated_at = "", None
        per_agent.append({"name": name, "content": content, "updated_at": updated_at})

    # --- Audit log ------------------------------------------------------
    audit_entries: list[dict[str, str]] = []
    if AUDIT_LOG_PATH.exists() and not AUDIT_LOG_PATH.is_symlink():
        try:
            audit_content = AUDIT_LOG_PATH.read_text(encoding="utf-8")
            audit_entries = _parse_audit_log(audit_content)
        except OSError as exc:
            logger.warning("Could not read audit log: %s", exc)

    return {
        "global": global_entries,
        "per_project": per_project,
        "per_agent": per_agent,
        "audit_log": audit_entries,
    }


def _safe_memory_path(filename: str) -> Path:
    """Resolve filename inside the memory dir. Rejects path traversal + non-.md."""
    if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
        raise ValueError(f"Invalid filename: {filename!r}")
    if not filename.endswith(".md"):
        raise ValueError("Only .md files permitted")
    dir_real = _GLOBAL_MEMORY_DIR.resolve()
    path = (_GLOBAL_MEMORY_DIR / filename).resolve()
    try:
        path.relative_to(dir_real)
    except ValueError:
        raise ValueError(f"Invalid filename: {filename!r}")
    return path


def get_memory_file(filename: str) -> dict[str, Any]:
    """Return a single MemoryEntry by filename.  Raises FileNotFoundError if missing."""
    try:
        path = _safe_memory_path(filename)
    except ValueError as exc:
        raise FileNotFoundError(str(exc))
    if not path.exists() or path.name in GLOBAL_EXCLUDE:
        raise FileNotFoundError(f"Memory file not found: {filename!r}")
    if path.is_symlink():
        raise FileNotFoundError(f"Memory file not found: {filename!r}")
    return _build_entry(path)


def put_memory_file(filename: str, content: str) -> dict[str, Any]:
    """Write content to a global memory file and return the updated MemoryEntry.

    Rejects traversal, non-.md, and protected filenames.
    """
    if filename in GLOBAL_EXCLUDE:
        raise ValueError(f"Cannot write to protected file: {filename!r}")
    path = _safe_memory_path(filename)
    if path.exists() and path.is_symlink():
        raise ValueError(f"Refusing to overwrite symlink: {filename!r}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        logger.error("Could not write memory file %s: %s", filename, exc)
        raise
    return _build_entry(path)
