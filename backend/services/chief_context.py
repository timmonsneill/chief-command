"""Chief Context v1 — builds the system-prompt blocks that turn generic Claude into Chief.

Reads user profile, feedback memories, agent roster, project memories, and the
current project scope, then returns a list of Anthropic system-message blocks with
cache_control so the prompt caches on the second turn onward.

Determinism is important: the function must return the same blocks (same text,
same order) for the same (scope, file contents) pair so the cache hits.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Filesystem roots
# ---------------------------------------------------------------------------
_CLAUDE_HOME = Path.home() / ".claude"
_USER_MEMORY_DIR = _CLAUDE_HOME / "projects" / "-Users-user" / "memory"
_AGENT_MEMORY_DIR = _CLAUDE_HOME / "agents" / "memory"
_PROJECTS_ROOT = _CLAUDE_HOME / "projects"

# Files that are not real memory content (indexes, JSON manifests, logs).
_GLOBAL_EXCLUDE = {"MEMORY.md", "PROJECTS.json", "audit_log.md"}

# Rough token budget — ~4 chars/token heuristic gives us a cheap estimate.
# 40k tokens ≈ 160k characters.
_MAX_PROMPT_CHARS = 40_000 * 4

_CHARS_PER_TOKEN_ESTIMATE = 4


# ---------------------------------------------------------------------------
# Chief identity — always first block
# ---------------------------------------------------------------------------
_CHIEF_IDENTITY = """You are Chief — the owner's personal AI orchestrator and voice companion.

Be concise. Prefer one-sentence answers. Speak naturally — you're being read aloud via TTS.

You know the owner's projects, habits, and the agent roster below. When the owner \
asks about a project, speak with real context — not a generic summary. When he asks \
you to do something a named agent should handle, name the agent and what you'd dispatch \
them to do. Never pad. Never open with filler like "Sure thing" or "Absolutely". Just \
answer."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _read(path: Path) -> str:
    """Safe file read — returns empty string on any error."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("chief_context: failed to read %s: %s", path, exc)
        return ""


def _sorted_md_files(directory: Path) -> list[Path]:
    """Return .md files in a directory sorted by filename. Empty list if missing."""
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.md") if p.is_file())


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _strip_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse YAML-ish frontmatter if present. Returns (meta, body)."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    meta_block = m.group(1)
    meta: dict[str, str] = {}
    for line in meta_block.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip().lower()] = val.strip()
    body = text[m.end():]
    return meta, body


def _classify_user_file(path: Path) -> str:
    """Map user-profile memory file to 'user', feedback to 'feedback', else 'other'."""
    name = path.name.lower()
    if name.startswith("user_"):
        return "user"
    if name.startswith("feedback_"):
        return "feedback"
    if name.startswith("project_"):
        return "project"
    return "other"


def _first_heading(text: str) -> str:
    """Return the first `# Heading` line, stripped of leading hashes."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


# ---------------------------------------------------------------------------
# Agent roster — terse (name, role-description, one-liner)
# ---------------------------------------------------------------------------
def _build_agent_roster() -> str:
    """Read agent memory files; build a terse roster line per agent.

    Format per agent:
      - <NAME>: <description first line> [<first heading if different>]
    """
    files = _sorted_md_files(_AGENT_MEMORY_DIR)
    if not files:
        return ""

    lines: list[str] = ["# Agent Roster", ""]
    for path in files:
        text = _read(path)
        if not text.strip():
            continue
        meta, body = _strip_frontmatter(text)
        # Prefer the "name" frontmatter (usually "Riggs — Builder Memory")
        name_field = meta.get("name") or path.stem.capitalize()
        # Use only the part before the em-dash so we get just "Riggs"
        agent_name = re.split(r"\s*[—-]\s*", name_field, maxsplit=1)[0].strip() or path.stem.capitalize()
        description = meta.get("description", "").strip()
        # Truncate description to its first sentence — keeps the roster terse.
        first_sentence = re.split(r"(?<=[.!?])\s", description, maxsplit=1)[0] if description else ""
        if not first_sentence:
            first_sentence = _first_heading(body) or "(no description)"
        lines.append(f"- **{agent_name}**: {first_sentence}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# User profile & feedback — global memory
# ---------------------------------------------------------------------------
def _build_user_profile() -> str:
    """Concatenate all user_*.md files from the global memory dir."""
    files = _sorted_md_files(_USER_MEMORY_DIR)
    chunks: list[str] = []
    for p in files:
        if p.name in _GLOBAL_EXCLUDE:
            continue
        if _classify_user_file(p) != "user":
            continue
        body = _read(p).strip()
        if body:
            chunks.append(body)
    if not chunks:
        return ""
    return "# User Profile\n\n" + "\n\n---\n\n".join(chunks) + "\n"


def _build_feedback_memories() -> str:
    """Concatenate feedback_*.md files — the 'how Chief should behave' notes."""
    files = _sorted_md_files(_USER_MEMORY_DIR)
    chunks: list[str] = []
    for p in files:
        if p.name in _GLOBAL_EXCLUDE:
            continue
        if _classify_user_file(p) != "feedback":
            continue
        body = _read(p).strip()
        if body:
            chunks.append(body)
    if not chunks:
        return ""
    return "# Feedback / House Rules\n\n" + "\n\n---\n\n".join(chunks) + "\n"


# ---------------------------------------------------------------------------
# Project memories — group by project directory
# ---------------------------------------------------------------------------
def _project_dirs() -> list[Path]:
    """Return /Users/user/.claude/projects/-Users-user-Desktop-*/memory dirs, sorted."""
    if not _PROJECTS_ROOT.is_dir():
        return []
    results: list[Path] = []
    for child in sorted(_PROJECTS_ROOT.iterdir()):
        if not child.is_dir():
            continue
        if not child.name.startswith("-Users-user-Desktop-"):
            continue
        mem = child / "memory"
        if mem.is_dir():
            results.append(mem)
    return results


def _pretty_project_name(memory_dir: Path) -> str:
    """Turn '/Users/user/.claude/projects/-Users-user-Desktop-chief-command/memory'
    into 'chief-command'."""
    parent_name = memory_dir.parent.name  # e.g. "-Users-user-Desktop-chief-command"
    prefix = "-Users-user-Desktop-"
    slug = parent_name[len(prefix):] if parent_name.startswith(prefix) else parent_name
    return slug or parent_name


def _build_project_block(memory_dir: Path) -> tuple[str, str, float]:
    """Return (project-name, markdown, mtime) for a single project memory dir."""
    files = _sorted_md_files(memory_dir)
    # Latest mtime drives eviction priority if we need to truncate.
    mtime = 0.0
    chunks: list[str] = []
    for p in files:
        if p.name in _GLOBAL_EXCLUDE:
            continue
        try:
            mtime = max(mtime, p.stat().st_mtime)
        except OSError:
            pass
        body = _read(p).strip()
        if body:
            chunks.append(f"## {p.name}\n\n{body}")
    project_name = _pretty_project_name(memory_dir)
    if not chunks:
        return project_name, "", mtime
    header = f"# Project Memory — {project_name}"
    return project_name, header + "\n\n" + "\n\n".join(chunks) + "\n", mtime


# ---------------------------------------------------------------------------
# Block helpers
# ---------------------------------------------------------------------------
def _block(text: str) -> dict:
    """Wrap a markdown string as an Anthropic cached system block."""
    return {
        "type": "text",
        "text": text,
        "cache_control": {"type": "ephemeral"},
    }


def _estimate_tokens(blocks: list[dict]) -> int:
    total_chars = sum(len(b.get("text", "")) for b in blocks)
    return total_chars // _CHARS_PER_TOKEN_ESTIMATE


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def build_chief_system(project_scope: Optional[str]) -> list[dict]:
    """Return Anthropic system-message blocks that make Claude into Chief.

    Anthropic caps us at **4 cache_control breakpoints** per request, so we
    group the content into 4 logical cached blocks (everything before each
    breakpoint is cached as a prefix):

      1. Chief identity + voice style                      (breakpoint)
      2. User profile + global feedback / house rules      (breakpoint)
      3. Agent roster + ALL project memories               (breakpoint)
      4. Current project scope hint                        (breakpoint)

    Blocks are deterministic for the same (scope, file contents) pair so the
    cache hits on subsequent turns. If the total exceeds 40k tokens, we evict
    oldest-mtime non-scoped project memories until it fits.
    """
    # Gather project entries so truncation can evict selectively.
    project_entries: list[tuple[str, str, float]] = []
    for mem_dir in _project_dirs():
        name, md, mtime = _build_project_block(mem_dir)
        if md:
            project_entries.append((name, md, mtime))
    project_entries.sort(key=lambda e: e[0])

    # Check budget first. If over, prune project entries by oldest mtime (non-scoped first).
    kept_entries = _enforce_budget(project_entries, project_scope)

    blocks = _assemble_blocks(kept_entries, project_scope)
    total_tokens = _estimate_tokens(blocks)
    logger.info(
        "chief_context: built %d system blocks, ~%d tokens (scope=%s, %d/%d projects kept)",
        len(blocks),
        total_tokens,
        project_scope or "All",
        len(kept_entries),
        len(project_entries),
    )
    return blocks


def _assemble_blocks(
    project_entries: list[tuple[str, str, float]],
    project_scope: Optional[str],
) -> list[dict]:
    """Turn kept project entries + fixed memory bits into at most 4 cached blocks."""
    # Block 1: identity (always a breakpoint).
    identity_block = _block(_CHIEF_IDENTITY)

    # Block 2: user profile + feedback merged.
    profile_md = _build_user_profile()
    feedback_md = _build_feedback_memories()
    part2_pieces = [p for p in (profile_md, feedback_md) if p]
    profile_block = _block("\n\n".join(part2_pieces)) if part2_pieces else None

    # Block 3: agent roster + all project memories merged.
    roster_md = _build_agent_roster()
    project_mds = [md for _, md, _ in project_entries]
    part3_pieces = [p for p in [roster_md, *project_mds] if p]
    projects_block = _block("\n\n".join(part3_pieces)) if part3_pieces else None

    # Block 4: scope hint (only if scoped to a named project).
    scope_block = None
    if project_scope and project_scope.strip() and project_scope.lower() != "all":
        scope_block = _block(
            f"# Current Project Scope\n\n"
            f"The owner is currently focused on **{project_scope}**. "
            f"When he says 'it', 'this project', or 'the build', assume he means {project_scope} "
            f"unless context says otherwise."
        )

    blocks: list[dict] = [identity_block]
    if profile_block:
        blocks.append(profile_block)
    if projects_block:
        blocks.append(projects_block)
    if scope_block:
        blocks.append(scope_block)
    return blocks


def _enforce_budget(
    project_entries: list[tuple[str, str, float]],
    project_scope: Optional[str],
) -> list[tuple[str, str, float]]:
    """Return the kept project entries that fit within the 40k-token budget."""
    if _estimate_tokens(_assemble_blocks(project_entries, project_scope)) <= 40_000:
        return project_entries

    # Over budget: evict oldest-mtime non-scoped entries first.
    scope_norm = re.sub(r"[\s-]+", "", (project_scope or "").strip().lower())

    def _is_scoped(name: str) -> bool:
        if not scope_norm:
            return False
        name_norm = re.sub(r"[\s-]+", "", name.lower())
        return scope_norm in name_norm or name_norm in scope_norm

    droppable = sorted(
        [e for e in project_entries if not _is_scoped(e[0])],
        key=lambda e: e[2],  # oldest mtime first
    )
    logger.warning(
        "chief_context: system prompt >40k tokens; evicting oldest non-scoped projects",
    )
    kept = list(project_entries)
    for candidate in droppable:
        kept = [e for e in kept if e != candidate]
        if _estimate_tokens(_assemble_blocks(kept, project_scope)) <= 40_000:
            logger.info(
                "chief_context: truncation settled — kept %d of %d project(s)",
                len(kept), len(project_entries),
            )
            return kept

    # Exhausted all droppables; return whatever's left (may still exceed).
    logger.warning(
        "chief_context: truncation exhausted — %d tokens with %d project(s) kept",
        _estimate_tokens(_assemble_blocks(kept, project_scope)),
        len(kept),
    )
    return kept


def estimate_prompt_tokens(project_scope: Optional[str]) -> int:
    """Convenience for tests/logs — returns the estimated token count."""
    return _estimate_tokens(build_chief_system(project_scope))
