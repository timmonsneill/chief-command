"""Shared memory-filesystem constants and helpers.

Extracted from ``memory_service`` and ``chief_context`` so both share a single
source of truth. Do not duplicate these constants elsewhere — import from here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

# ---------------------------------------------------------------------------
# Filesystem roots (single source of truth)
# ---------------------------------------------------------------------------
CLAUDE_HOME: Final[Path] = Path.home() / ".claude"
USER_MEMORY_DIR: Final[Path] = CLAUDE_HOME / "projects" / "-Users-user" / "memory"
AGENT_MEMORY_DIR: Final[Path] = CLAUDE_HOME / "agents" / "memory"
PROJECTS_ROOT: Final[Path] = CLAUDE_HOME / "projects"
AUDIT_LOG_PATH: Final[Path] = USER_MEMORY_DIR / "audit_log.md"

# Per-project memory dirs live under
#   PROJECTS_ROOT / <PROJECT_DIR_PREFIX>-<slug> / memory
# Project dirs that do not have this prefix are skipped by Chief context.
PROJECT_DIR_PREFIX: Final[str] = "-Users-user-Desktop-"

# Files that are not real memory content (indexes, JSON manifests, logs).
GLOBAL_EXCLUDE: Final[frozenset[str]] = frozenset(
    {"MEMORY.md", "PROJECTS.json", "audit_log.md"}
)

# ---------------------------------------------------------------------------
# Filename classification
# ---------------------------------------------------------------------------
_TYPE_RULES: Final[list[tuple[str, str]]] = [
    ("feedback_", "feedback"),
    ("project_", "project"),
    ("user_", "user"),
]


def classify_type(filename: str) -> str:
    """Map a memory filename to its type by prefix. Falls back to 'reference'."""
    for prefix, ttype in _TYPE_RULES:
        if filename.startswith(prefix):
            return ttype
    return "reference"


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------
def parse_frontmatter(content: str) -> dict[str, str]:
    """Extract YAML-ish frontmatter fields from a markdown file.

    Supports the simple ``key: value`` block between ``---`` delimiters.
    Returns an empty dict if no frontmatter is present.
    """
    stripped = content.lstrip()
    if not stripped.startswith("---"):
        return {}
    end = stripped.find("\n---", 3)
    if end == -1:
        return {}
    block = stripped[3:end].strip()
    fields: dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields


def strip_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Return (meta, body) — body has the frontmatter removed if present."""
    stripped = content.lstrip()
    if not stripped.startswith("---"):
        return {}, content
    end = stripped.find("\n---", 3)
    if end == -1:
        return {}, content
    meta = parse_frontmatter(content)
    # Skip past closing ``---\n``.
    leading_skip = len(content) - len(stripped)
    # end is measured from stripped; closing marker is "\n---" (4 chars) + optional newline.
    after = stripped[end + 4:]
    if after.startswith("\n"):
        after = after[1:]
    return meta, content[:leading_skip] + after if leading_skip else after


# ---------------------------------------------------------------------------
# Symlink-safe directory iteration
# ---------------------------------------------------------------------------
def safe_md_files(directory: Path) -> list[Path]:
    """Return *.md files in ``directory`` that are real files and not symlinks.

    - Rejects the directory itself if it is a symlink.
    - Skips files that are symlinks or that resolve outside ``directory``.
    - Sorted by filename for deterministic output.
    """
    if not directory.is_dir() or directory.is_symlink():
        return []
    try:
        root_real = directory.resolve(strict=False)
    except OSError:
        return []

    results: list[Path] = []
    for p in directory.glob("*.md"):
        try:
            if not p.is_file():
                continue
            if p.is_symlink():
                continue
            real = p.resolve(strict=False)
            # Confirm the resolved file stays inside the directory tree.
            real.relative_to(root_real)
        except (OSError, ValueError):
            continue
        results.append(p)
    results.sort()
    return results
