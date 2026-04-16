"""Parse Claude Code project memory files into structured JSON."""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class TodoItem:
    text: str
    done: bool


@dataclass
class Phase:
    name: str
    description: str = ""
    complete: bool = False
    items: list[TodoItem] = field(default_factory=list)


@dataclass
class Milestone:
    date: str
    label: str


@dataclass
class ProjectInfo:
    slug: str
    name: str
    description: str
    file_path: str
    category: str = "project"  # project, feedback, memory
    status: str = "active"
    phases: list[Phase] = field(default_factory=list)
    todos: list[TodoItem] = field(default_factory=list)
    milestones: list[Milestone] = field(default_factory=list)
    raw_content: str = ""

    def to_dict(self) -> dict[str, Any]:
        total_todos = len(self.todos)
        done_todos = sum(1 for t in self.todos if t.done)
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "file_path": self.file_path,
            "category": self.category,
            "status": self.status,
            "phases": [
                {
                    "name": p.name,
                    "description": p.description,
                    "complete": p.complete,
                    "items": [{"text": i.text, "done": i.done} for i in p.items],
                }
                for p in self.phases
            ],
            "todos": [{"text": t.text, "done": t.done} for t in self.todos],
            "todo_progress": {
                "total": total_todos,
                "done": done_todos,
                "percent": round(done_todos / total_todos * 100) if total_todos else 0,
            },
            "milestones": [{"date": m.date, "label": m.label} for m in self.milestones],
        }


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_CHECKBOX_RE = re.compile(r"^\s*[-*]\s*\[([ xX])\]\s*(.+)$", re.MULTILINE)
_PHASE_HEADER_RE = re.compile(
    r"^#{1,3}\s+(?:Phase\s+\d+[:\s]*|Step\s+\d+[:\s]*)(.+?)(?:\s*[✅✓])?$",
    re.MULTILINE,
)
_DATE_RE = re.compile(
    r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})\s*[-:—]\s*(.+)"
)
_MEMORY_ENTRY_RE = re.compile(
    r"^\s*-\s*\[(.+?)\]\((.+?)\)\s*—\s*(.+)$", re.MULTILINE
)


def _parse_checkboxes(text: str) -> list[TodoItem]:
    """Extract all markdown checkbox items."""
    return [
        TodoItem(text=m.group(2).strip(), done=m.group(1).lower() == "x")
        for m in _CHECKBOX_RE.finditer(text)
    ]


def _parse_phases(text: str) -> list[Phase]:
    """Extract phase headers and their child checkboxes."""
    phases: list[Phase] = []
    headers = list(_PHASE_HEADER_RE.finditer(text))
    for idx, hdr in enumerate(headers):
        start = hdr.end()
        end = headers[idx + 1].start() if idx + 1 < len(headers) else len(text)
        section = text[start:end]
        items = _parse_checkboxes(section)
        all_done = bool(items) and all(i.done for i in items)
        complete = all_done or "✅" in hdr.group(0) or "✓" in hdr.group(0)
        phases.append(
            Phase(
                name=hdr.group(1).strip(),
                complete=complete,
                items=items,
            )
        )
    return phases


def _parse_milestones(text: str) -> list[Milestone]:
    """Extract date-prefixed lines as milestones."""
    return [
        Milestone(date=m.group(1), label=m.group(2).strip())
        for m in _DATE_RE.finditer(text)
    ]


def _slug_from_filename(filename: str) -> str:
    return filename.removesuffix(".md")


def _name_from_slug(slug: str) -> str:
    """Convert a slug like project_master_todo to 'Master Todo'."""
    parts = slug.split("_")
    # Drop category prefix
    if parts and parts[0] in ("project", "feedback"):
        parts = parts[1:]
    return " ".join(p.capitalize() for p in parts)


def _detect_category(filename: str) -> str:
    if filename.startswith("project_"):
        return "project"
    if filename.startswith("feedback_"):
        return "feedback"
    if filename == "MEMORY.md":
        return "memory"
    return "other"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_memory_index() -> list[dict[str, str]]:
    """Parse MEMORY.md and return the list of linked project entries."""
    memory_file = settings.memory_dir / "MEMORY.md"
    if not memory_file.exists():
        logger.warning("MEMORY.md not found at %s", memory_file)
        return []

    text = memory_file.read_text(encoding="utf-8")
    entries: list[dict[str, str]] = []
    for m in _MEMORY_ENTRY_RE.finditer(text):
        entries.append(
            {
                "name": m.group(1).strip(),
                "file": m.group(2).strip(),
                "description": m.group(3).strip(),
            }
        )
    return entries


def list_projects() -> list[dict[str, Any]]:
    """Return lightweight summaries for every memory file."""
    mem_dir = settings.memory_dir
    if not mem_dir.exists():
        logger.warning("Memory dir does not exist: %s", mem_dir)
        return []

    projects: list[dict[str, Any]] = []
    for md_file in sorted(mem_dir.glob("*.md")):
        if md_file.name == "MEMORY.md":
            continue
        slug = _slug_from_filename(md_file.name)
        text = md_file.read_text(encoding="utf-8", errors="replace")
        todos = _parse_checkboxes(text)
        total = len(todos)
        done = sum(1 for t in todos if t.done)
        projects.append(
            {
                "slug": slug,
                "name": _name_from_slug(slug),
                "category": _detect_category(md_file.name),
                "file": md_file.name,
                "todo_total": total,
                "todo_done": done,
                "todo_percent": round(done / total * 100) if total else 0,
            }
        )
    return projects


def get_project(slug: str) -> dict[str, Any] | None:
    """Parse a single project file into a full dashboard payload."""
    mem_dir = settings.memory_dir
    # Try with .md extension
    candidates = [
        mem_dir / f"{slug}.md",
        mem_dir / f"project_{slug}.md",
        mem_dir / f"feedback_{slug}.md",
    ]
    md_file: Path | None = None
    for c in candidates:
        if c.exists():
            md_file = c
            break

    if md_file is None:
        return None

    text = md_file.read_text(encoding="utf-8", errors="replace")

    info = ProjectInfo(
        slug=slug,
        name=_name_from_slug(slug),
        description=_extract_description(text),
        file_path=str(md_file),
        category=_detect_category(md_file.name),
        todos=_parse_checkboxes(text),
        phases=_parse_phases(text),
        milestones=_parse_milestones(text),
        raw_content=text,
    )
    return info.to_dict()


def _extract_description(text: str) -> str:
    """Pull the first non-heading, non-blank line as a description."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and not stripped.startswith("-"):
            return stripped[:200]
    return ""
