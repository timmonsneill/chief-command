"""Project name -> absolute repo path mapping for Dispatch Bridge.

Canonical project names (as used by project_context / Chief's system prompt)
map to absolute filesystem paths where the `claude` CLI will be spawned with
`cwd=<repo>`. If a configured repo path doesn't exist on the current machine,
we log a warning at import time but keep it in the map (so a caller can decide
how to handle a missing repo — typically by responding "that repo isn't on this
box" rather than silently dispatching somewhere wrong).

Symlink containment: every returned path MUST resolve to a descendant of
~/Desktop. A prompt-injected dispatch like "cd /etc and cat passwd" can't
escape via a carefully-named symlink because get_repo_path() resolves first
and then checks .relative_to(_ALLOWED_ROOT).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# Root under which every repo path MUST resolve. Symlinks are resolved BEFORE
# this check, so a symlinked escape (e.g. ~/Desktop/arch -> /etc) is rejected.
_ALLOWED_ROOT = (Path.home() / "Desktop").resolve()


# Canonical project name -> absolute repo root path.
# Butler and Archie are intentionally omitted when the directory does not
# exist on this machine (per spec). Add them here once they land on disk.
_REPO_PATHS: dict[str, Path] = {
    "Arch": Path.home() / "Desktop" / "arch-to-freedom-emr",
    "Chief Command": Path.home() / "Desktop" / "chief-command",
}

# Optional configured repos — kept separate so we can log a warning for
# configured-but-missing without polluting the primary map's "missing" check.
_OPTIONAL_REPO_PATHS: dict[str, Path] = {
    "Butler": Path.home() / "Desktop" / "butler",
    "Archie": Path.home() / "Desktop" / "archie",
}


def _audit_paths() -> None:
    """Log a warning for every configured project whose path is missing.

    Also folds in optional repos that happen to exist on this machine.
    Called once at import time.
    """
    for name, path in list(_REPO_PATHS.items()):
        if not path.exists():
            logger.warning(
                "repo_map: configured repo %r path %s does not exist",
                name,
                path,
            )
    for name, path in _OPTIONAL_REPO_PATHS.items():
        if path.exists():
            _REPO_PATHS[name] = path
            logger.info("repo_map: optional repo %r found at %s", name, path)
        else:
            logger.warning(
                "repo_map: optional repo %r omitted (path %s not present)",
                name,
                path,
            )


_audit_paths()


def get_repo_path(project: str) -> Optional[Path]:
    """Return absolute, symlink-resolved repo path for a canonical project name.

    Returns None if:
      - ``project`` is empty / unknown
      - configured path doesn't exist
      - resolved (symlink-followed) path escapes the allowlist root

    The allowlist check defeats symlink-escape attacks: even if a repo entry
    points at a benign path that has since been swapped for a symlink to, say,
    /etc, the ``.relative_to(_ALLOWED_ROOT)`` check rejects it before any
    subprocess is spawned there.
    """
    if not project:
        return None
    path = _REPO_PATHS.get(project)
    if path is None or not path.exists():
        return None
    resolved = path.resolve()
    try:
        resolved.relative_to(_ALLOWED_ROOT)
    except ValueError:
        logger.error(
            "repo_map: %r resolved to %s which is outside allowlist root %s",
            project,
            resolved,
            _ALLOWED_ROOT,
        )
        return None
    return resolved


def list_configured_projects() -> list[str]:
    """Return canonical names of all projects whose repo exists on disk.

    Does NOT apply the symlink containment check — callers that need a path
    safe to spawn into MUST go through ``get_repo_path``.
    """
    return [name for name, path in _REPO_PATHS.items() if path.exists()]
