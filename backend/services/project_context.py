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
#
# NOTE (2026-04-20): "Archie" is NOT a selectable scope — Archie is the AI brain
# layer inside the Arch app, same project. The "archie" voice/text alias still
# canonicalizes to "Arch" (see _canonicalize below).
AVAILABLE_PROJECTS: Final[list[str]] = ["Chief Command", "Arch", "Personal Assist"]
DEFAULT_PROJECT: Final[str] = "Chief Command"


# ---------------------------------------------------------------------------
# Switch-intent detection
# ---------------------------------------------------------------------------
# Matches user utterances like:
#   "switch to Arch"                -> "Arch"
#   "show me Archie"                -> "Arch" (archie is an Arch alias)
#   "switch to Jess"                -> "Personal Assist" (voice alias)
#   "switch to Chief"               -> "Chief Command" (short alias)
# Requires the project name to be followed by end-of-utterance, punctuation,
# or a whitespace + common terminator word. This rejects false positives like:
#   "show me all the files"                 (no "all" in the project list now)
#   "switch to all hands on deck"           ("all" is not a project)
#   "show me arch of the design"            ("arch of" — not a terminator)
#   "switch the arch to bcrypt"             (no "to" between switch/arch)
#
# Pattern ordering matters: multi-word / more-specific forms must come BEFORE
# shorter prefixes so "chief command" isn't swallowed by the bare "chief"
# branch. Same applies for "personal assist" vs "personal(?=assist)".
# ---------------------------------------------------------------------------
_PROJECT_PATTERN = (
    r"(archie|arch|chief[\s-]?command|chiefcommand|chief|"
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


# ---------------------------------------------------------------------------
# Bare-name switch detection
# ---------------------------------------------------------------------------
# Owner wants: if the ENTIRE utterance (trimmed whitespace + stripped trailing
# punctuation from the set .!?,;:) is exactly one of the recognized names —
# case-insensitive — treat it as a switch. Anything else (even a single extra
# word) does NOT qualify and must fall through to the verb-phrase patterns.
#
#   "Jess."          -> "Personal Assist"   (bare)
#   "Arch"           -> "Arch"              (bare)
#   "Chief!"         -> "Chief Command"     (bare with punctuation)
#   "archie"         -> "Arch"              (bare alias)
#   "jess,"          -> "Personal Assist"   (trailing comma still bare)
#   "Jess is great"  -> None                (extra words — NOT bare)
#   "let's go jess"  -> None                (extra words — NOT bare)
# ---------------------------------------------------------------------------
_TRAILING_PUNCT = ".!?,;:"
_BARE_NAMES: Final[dict[str, str]] = {
    "jess": "Personal Assist",
    "chief": "Chief Command",
    "chief command": "Chief Command",
    "chiefcommand": "Chief Command",
    "arch": "Arch",
    "archie": "Arch",
    "personal assist": "Personal Assist",
    "personalassist": "Personal Assist",
}


def _detect_bare_name(text: str) -> Optional[str]:
    """Return the canonical scope if ``text`` (after trim + trailing-punct strip)
    is EXACTLY a recognized name. Otherwise None.

    Only whitespace is collapsed on the outside; internal whitespace beyond a
    single space is normalized so "  chief    command  " still matches. Any
    characters beyond the name and its trailing punctuation disqualify.
    """
    if not text:
        return None
    stripped = text.strip().rstrip(_TRAILING_PUNCT).strip()
    if not stripped:
        return None
    # Collapse any internal whitespace run to a single space so "chief  command"
    # still matches. This does NOT turn "jess is great" into a match because
    # "jess is great" won't be a key in the map.
    normalized = re.sub(r"\s+", " ", stripped).lower()
    return _BARE_NAMES.get(normalized)


def _canonicalize(raw: str) -> Optional[str]:
    """Map a raw regex match like 'chief-command' → 'Chief Command'.

    Case-insensitive. "archie" canonicalizes to "Arch" (archie is the AI brain
    inside the Arch app — same project, different layer). "chief" (bare)
    canonicalizes to "Chief Command".
    """
    if not raw:
        return None
    normalized = re.sub(r"[\s-]+", "", raw).lower()  # collapse whitespace + hyphens
    mapping = {
        "arch": "Arch",
        "archie": "Arch",
        "chief": "Chief Command",
        "chiefcommand": "Chief Command",
        "personalassist": "Personal Assist",
        "jess": "Personal Assist",
    }
    return mapping.get(normalized)


def detect_project_switch(text: str) -> Optional[str]:
    """If the user text contains a switch intent, return the canonical project name.

    Returns None if no clear switch intent is detected. Examples that MATCH:
      "switch to arch"          -> "Arch"
      "show me archie"          -> "Arch"  (archie -> Arch)
      "switch to arch, please"  -> "Arch"
      "Jess"                    -> "Personal Assist"  (bare name)
      "Chief."                  -> "Chief Command"     (bare name w/ punct)
      "archie"                  -> "Arch"              (bare alias)

    Examples that do NOT match (false-positive guards):
      "show me all the files"         -> None  ("all" not a project)
      "switch to all hands on deck"   -> None  ("all" not a project)
      "show me arch of the design"    -> None  ("of" is not a terminator word)
      "switch the arch to bcrypt"     -> None  (no "to" between switch+arch)
      "Jess is great"                 -> None  (extra words — not bare)
      "chief of staff"                -> None  (extra words — not bare)
    """
    if not text or not text.strip():
        return None

    # Bare-name detection runs FIRST — owner wants "Jess." alone to switch.
    bare = _detect_bare_name(text)
    if bare and bare in AVAILABLE_PROJECTS:
        return bare

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
