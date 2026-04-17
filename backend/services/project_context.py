"""Per-session project context store.

State is module-level (in-process memory), keyed by JWT subject.
Resets on server restart — acceptable for v3.
"""

import logging
from typing import Final

logger = logging.getLogger(__name__)

AVAILABLE_PROJECTS: Final[list[str]] = ["All", "Arch", "Chief Command", "Butler", "Archie"]
DEFAULT_PROJECT: Final[str] = "All"

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
