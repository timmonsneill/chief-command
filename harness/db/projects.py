"""The REAL project list — and each project's own memory.

═══════════════════════════════════════════════════════════════════════════════
WHY THIS EXISTS (owner, 2026-07-20):

    Asked what he's working on, Chief called the medical-records project "a little
    medical records project." It wasn't reading anything — it was improvising. Being
    confidently wrong is the one thing that makes Neill stop trusting it.

The fix is not a smarter model. It's giving Chief the FACTS: read the `projects` table
and hand it the real list, in plain English, on every path he talks through.

MEMORY READABILITY (owner, 2026-07-20):

    "arch has a fuck ton of memory files... the most important ones should be readable
     inside Chief Command."

Each project can point at a `memory_dir` — the folder where that project's agents keep
their accumulated notes. The curated `MEMORY.md` index in that folder IS the
most-important list (the agents ⭐-mark what matters). We surface that, and let a named
file be read on demand. We read NOTES here, not the project's live data — Arch's patient
database is not, and must never be, reachable from this harness (Decision C, PHI).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


class ProjectRepoUnavailable(RuntimeError):
    """Why a job can't get a working copy of this project's code — always plain
    English, this string is shown to Neill and written into the job's own record."""


def _projects_root() -> Path:
    """The one folder real project code is allowed to live under (seats.toml
    `[projects].root`) — a live wiring review found nothing stopped `resolve_repo`
    from happily returning a path OUTSIDE it (a mistyped `repo_path`, or a test that
    forgot to repoint a project row before pointing it at a real one). This is a
    plain function, not a constant, so tests can `monkeypatch.setattr` it straight
    at the tmp directory they're using and get the SAME guard production relies on —
    which is what makes forgetting to repoint a loud refusal instead of a silent
    build against the wrong repo.
    """
    try:
        import dispatch   # deferred: dispatch never imports this module, so this
                          # is one-directional and safe, but importing it at our
                          # own module level would still be a needless coupling
        cfg = dispatch.load_config()
        root = cfg.get("projects", {}).get("root") or "~/code-projects"
    except Exception:
        root = "~/code-projects"
    return Path(root).expanduser()


def resolve_repo(conn, project_id: str) -> tuple[Path, str]:
    """The actual git repo directory + its default branch name for a project.

    Never guesses: a project with no repo_path (Arch, Decision C — the fleet reads
    its notes but must never touch its code or patient data), a repo_path that isn't
    on this machine, a repo_path OUTSIDE the configured projects root, a folder
    that isn't really a git repo, or a repo whose default branch can't be pinned
    down — all raise ProjectRepoUnavailable with a plain sentence rather than
    silently falling back to some other repo or branch. Falling back would be worse
    than failing: it would build the wrong project's code and nobody would notice
    until review.
    """
    row = conn.execute(
        "SELECT repo_path FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if row is None:
        raise ProjectRepoUnavailable("That project isn't set up here.")
    if not row["repo_path"]:
        raise ProjectRepoUnavailable(
            "That project is kept at arm's length — the team can read its notes "
            "but not touch its code."
        )
    path = Path(row["repo_path"]).expanduser()
    if not path.is_dir():
        raise ProjectRepoUnavailable(
            "That project's code isn't where it's supposed to be on this machine."
        )
    root = _projects_root().resolve()
    resolved = path.resolve()
    if root != resolved and root not in resolved.parents:
        raise ProjectRepoUnavailable(
            "That project's code isn't kept where projects are supposed to live "
            "on this machine."
        )
    check = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"], cwd=path,
        capture_output=True, text=True, timeout=10,
    )
    if check.returncode != 0 or check.stdout.strip() != "true":
        raise ProjectRepoUnavailable(
            "That project's folder isn't set up as a real copy of the code."
        )
    return path, _default_branch(path)


def _default_branch(repo: Path) -> str:
    """This repo's own main line — NEVER 'whatever happens to be checked out'.

    A real wiring review caught the earlier version doing exactly that (falling
    back to `rev-parse --abbrev-ref HEAD`): if the source repo's working copy
    happened to be sitting on a feature branch at the moment this ran, THAT became
    every job's 'main line' — silently. The only signals trusted now are ones that
    mean something regardless of what's currently checked out: the remote's own
    HEAD pointer (what a real clone of a real project has), then an actual local
    branch literally named 'main' or 'master'. Anything else is a repo we can't
    honestly name a default branch for, so we refuse rather than guess.
    """
    out = subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD"], cwd=repo,
        capture_output=True, text=True, timeout=10,
    )
    if out.returncode == 0 and out.stdout.strip():
        return out.stdout.strip().rsplit("/", 1)[-1]
    for candidate in ("main", "master"):
        check = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{candidate}"],
            cwd=repo, capture_output=True, text=True, timeout=10,
        )
        if check.returncode == 0:
            return candidate
    raise ProjectRepoUnavailable(
        "couldn't tell which branch is the main line of that project"
    )


def project_name(conn, project_id: str) -> str:
    """Plain-English display name for a project id, falling back to the id itself
    so a caller building a sentence never has to special-case 'not found'."""
    row = conn.execute(
        "SELECT name FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    return row["name"] if row and row["name"] else project_id


def real_projects(conn) -> list[dict[str, Any]]:
    """Every live project, straight from the table. No improvising."""
    return [dict(r) for r in conn.execute(
        "SELECT id, name, repo_path, memory_dir, description, color "
        "FROM projects WHERE archived = 0 ORDER BY rowid"
    )]


def projects_context(conn) -> str:
    """The real project list, as a plain-English block for Chief's context.

    Deliberately jargon-free and short — this is read by a model that speaks to a man in
    a car. It states what each project IS, not where its code lives.
    """
    projects = real_projects(conn)
    if not projects:
        return ""
    lines = ["THE REAL PROJECTS (these are the only ones — do not invent others):"]
    for p in projects:
        desc = (p["description"] or "").strip()
        lines.append(f"- {p['name']}: {desc}" if desc else f"- {p['name']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# A project's own memory — the curated index, and named files on demand.
# ---------------------------------------------------------------------------
def _memory_root(conn, project_id: str) -> Path | None:
    row = conn.execute(
        "SELECT memory_dir FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not row or not row["memory_dir"]:
        return None
    root = Path(row["memory_dir"]).expanduser()
    return root if root.is_dir() else None


def memory_index(conn, project_id: str) -> str | None:
    """The project's MEMORY.md — the agents' own curated 'most important' list.

    Returns None if the project has no memory folder or no index in it, so callers can
    tell 'nothing to show' apart from an empty string.
    """
    root = _memory_root(conn, project_id)
    if root is None:
        return None
    index = root / "MEMORY.md"
    if not index.is_file():
        return None
    return index.read_text(errors="replace")


def memory_file(conn, project_id: str, name: str) -> str | None:
    """Read ONE named memory file from a project's memory folder.

    `name` is a bare filename from the index (e.g. 'feedback_names_mean_roles.md'). We
    resolve it INSIDE the memory folder and refuse anything that escapes it — a memory
    name is never allowed to reach out to ~/.ssh or a sibling project. Returns None if
    it isn't a real file safely inside the folder.
    """
    # Notes are markdown, and that's ALL we serve — a memory folder could sit next to
    # other files, and "read any file in there" is a wider door than intended. Enforcing
    # the suffix keeps the door the size of the feature.
    if not name.endswith(".md"):
        return None
    root = _memory_root(conn, project_id)
    if root is None:
        return None
    root = root.resolve()
    target = (root / name).resolve()
    # Containment check: the resolved target must live under the memory folder. Blocks
    # '../', absolute paths, and symlink escapes.
    if root != target and root not in target.parents:
        return None
    if not target.is_file():
        return None
    return target.read_text(errors="replace")
