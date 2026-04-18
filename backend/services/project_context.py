"""Per-session project context store.

State is module-level (in-process memory), keyed by JWT subject.
Resets on server restart — acceptable for v3.
"""

import logging
import re
from typing import Final, Optional

logger = logging.getLogger(__name__)

AVAILABLE_PROJECTS: Final[list[str]] = ["All", "Arch", "Chief Command", "Butler", "Archie"]
DEFAULT_PROJECT: Final[str] = "All"


# ---------------------------------------------------------------------------
# Switch-intent detection — matches user utterances like
#   "switch to Arch", "let's talk about Butler", "show me Archie".
# The second capture group is the raw project spoken by the user; we normalize
# it via _canonicalize() below before returning.
# Only MATCHES that clearly signal a project-scope change should fire; do NOT
# match things like "switch the arch to bcrypt" — that's an unrelated use of
# the word "arch".
# ---------------------------------------------------------------------------
_PROJECT_PATTERN = r"(arch|chief[\s-]?command|chiefcommand|butler|archie|all)"

SWITCH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        rf"\b(?:switch|change|move)\s+(?:to|over to)\s+{_PROJECT_PATTERN}\b",
        re.I,
    ),
    re.compile(
        rf"\b(?:let'?s\s+)?(?:talk|focus|work)\s+(?:on|about)\s+{_PROJECT_PATTERN}\b",
        re.I,
    ),
    re.compile(
        rf"\b(?:show|give)\s+me\s+{_PROJECT_PATTERN}\b",
        re.I,
    ),
]


def _canonicalize(raw: str) -> Optional[str]:
    """Map a raw match like 'chief-command' → 'Chief Command'. Case-insensitive."""
    if not raw:
        return None
    normalized = re.sub(r"[\s-]+", "", raw).lower()  # collapse whitespace + hyphens
    mapping = {
        "arch": "Arch",
        "chiefcommand": "Chief Command",
        "butler": "Butler",
        "archie": "Archie",
        "all": "All",
    }
    return mapping.get(normalized)


def detect_project_switch(text: str) -> Optional[str]:
    """If the user text contains a switch intent, return the canonical project name.

    Returns None if no clear switch intent is detected. Examples:
      "switch to arch"          -> "Arch"
      "let's talk about butler" -> "Butler"
      "show me archie"          -> "Archie"
      "switch the arch to bcrypt" -> None   (no "to" between switch+arch)
    """
    if not text or not text.strip():
        return None
    for pattern in SWITCH_PATTERNS:
        m = pattern.search(text)
        if m:
            raw = m.group(1)
            canonical = _canonicalize(raw)
            if canonical and canonical in AVAILABLE_PROJECTS:
                return canonical
    return None

# Module-level dict: subject -> current project name
_context_store: dict[str, str] = {}


def get_context(subject: str) -> dict:
    """Return the current project context for a given JWT subject.

    Returns a dict with ``current`` and ``available`` keys.
    """
    current = _context_store.get(subject, DEFAULT_PROJECT)
    return {
        "current": current,
        "available": AVAILABLE_PROJECTS,
    }


def set_context(subject: str, project: str) -> dict:
    """Set the current project context for a given JWT subject.

    Raises ValueError if ``project`` is not in AVAILABLE_PROJECTS.
    Returns a dict with the ``current`` key.
    """
    if project not in AVAILABLE_PROJECTS:
        raise ValueError(
            f"Unknown project '{project}'. Must be one of: {AVAILABLE_PROJECTS}"
        )
    _context_store[subject] = project
    logger.info("Context set to '%s' for subject '%s'", project, subject)
    return {"current": project}
