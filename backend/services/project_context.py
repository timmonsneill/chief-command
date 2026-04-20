"""Per-session project context store.

State is module-level (in-process memory), keyed by JWT subject.
Resets on server restart — acceptable for v3.

Scope is ALWAYS a concrete single project (per owner design). "All" is not a
valid scope — the project switcher is the only way to change focus.
"""

import logging
import re
from typing import Final, Optional

logger = logging.getLogger(__name__)

# Canonical project names. The order here drives the default switcher UI order;
# default scope is the first entry.
AVAILABLE_PROJECTS: Final[list[str]] = ["Chief Command", "Arch", "Archie", "Personal Assist"]
DEFAULT_PROJECT: Final[str] = "Chief Command"


# ---------------------------------------------------------------------------
# Switch-intent detection
# ---------------------------------------------------------------------------
# Matches user utterances like:
#   "switch to Arch"                -> "Arch"
#   "show me Archie"                -> "Archie"
#   "switch to Jess"                -> "Personal Assist" (voice alias)
# Requires the project name to be followed by end-of-utterance, punctuation,
# or a whitespace + common terminator word. This rejects false positives like:
#   "show me all the files"                 (no "all" in the project list now)
#   "switch to all hands on deck"           ("all" is not a project)
#   "show me arch of the design"            ("arch of" — not a terminator)
#   "switch the arch to bcrypt"             (no "to" between switch/arch)
# ---------------------------------------------------------------------------
_PROJECT_PATTERN = (
    r"(arch|chief[\s-]?command|chiefcommand|archie|"
    r"personal[\s-]?assist|personalassist|jess)"
)
_TERMINATOR = (
    r"(?=\b(?:\s*[.,!?;:]|\s*$|\s+(?:please|now|today|tomorrow|instead|then|and|but|okay|ok)\b))"
)

SWITCH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        rf"\b(?:switch|change|move)\s+(?:to|over\s+to)\s+{_PROJECT_PATTERN}{_TERMINATOR}",
        re.I,
    ),
    re.compile(
        rf"\b(?:let'?s\s+)?(?:talk|focus|work)\s+(?:on|about)\s+{_PROJECT_PATTERN}{_TERMINATOR}",
        re.I,
    ),
    re.compile(
        rf"\b(?:show|give)\s+me\s+{_PROJECT_PATTERN}{_TERMINATOR}",
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
        "archie": "Archie",
        "personalassist": "Personal Assist",
        "jess": "Personal Assist",
    }
    return mapping.get(normalized)


def detect_project_switch(text: str) -> Optional[str]:
    """If the user text contains a switch intent, return the canonical project name.

    Returns None if no clear switch intent is detected. Examples that MATCH:
      "switch to arch"          -> "Arch"
      "show me archie"          -> "Archie"
      "switch to arch, please"  -> "Arch"

    Examples that do NOT match (false-positive guards):
      "show me all the files"         -> None  ("all" not a project, "files" not a terminator)
      "switch to all hands on deck"   -> None  ("all" not a project)
      "show me arch of the design"    -> None  ("of" is not a terminator word)
      "switch the arch to bcrypt"     -> None  (no "to" between switch+arch)
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
