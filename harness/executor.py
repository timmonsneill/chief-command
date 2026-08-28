"""The worker. This is the thing that was missing — the reason nothing ran.

Dispatch (dispatch.py) decides a job MAY run and records it. This module is what
actually DOES it: it takes a queued job, runs the work on the assigned seat's real
model, writes down every step as it goes, and lands a real result. Before this file
existed, Chief said "putting Riggs on it" and no worker ever started.

Two hard rules, both structural rather than polite:

  1. THE WORK HAPPENS IN AN ISOLATED WORKTREE, never the live project. A builder gets
     its own private copy of the repo (a git worktree). It physically cannot scribble
     on main while it works — main is untouched until something is reviewed and merged
     by the one service allowed to merge. "One worktree per agent" (owner + Sol).

  2. LOCAL OUTPUT CANNOT COMPLETE ON ITS OWN. When the free local model finishes, the
     job parks at 'review'. The database guards (schema.sql) refuse to let it reach
     'done' without a higher-tier reviewer passing THIS version. We do not route
     around that here; we feed it.

Runs in a background thread so the app never blocks — the whole point of the
architecture. sessions come and go; the job row is the durable truth.
"""

from __future__ import annotations

import hashlib
import os
import json
import subprocess
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import git_policy

from db.jobs import (
    connect,
    seat,
    set_head_version,
    set_status,
)

HARNESS = Path(__file__).resolve().parent
DB_PATH = HARNESS / "db" / "chief.db"
WORKTREES = HARNESS / ".worktrees"       # gitignored; one subdir per job
CLONES = HARNESS.parent.parent / f"{HARNESS.parent.name}-clones"  # sibling to the
                          # repo, e.g. chief-command-clones next to chief-command —
                          # NEVER inside the repo tree. See _make_clone for why.
BUILD_TIMEOUT_S = 900
OUTPUT_DIRNAME = "chief_output"          # where a builder drops standalone work

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"


# ---------------------------------------------------------------------------
# Event trail — this is what the app's activity view reads. Every step, live.
# ---------------------------------------------------------------------------
def _event(conn, job_id: int, seat_row, kind: str, detail: str = "", target: str = "") -> None:
    conn.execute(
        "INSERT INTO events (job_id, seat_id, lane, model, family, kind, target, detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, seat_row["id"], seat_row["id"], seat_row["model"],
         seat_row["family"], kind, target or None, detail or None),
    )


def _commit_in_worktree(wt: Path, job_id: int, branch: str, summary: str) -> str | None:
    """Commit the job's output on its own branch and return the commit id.

    This is what makes the gatekeeper's merge possible at all: it refuses unless the
    branch tip IS the reviewed version, and a version has to be a commit for that to be
    provable. If anything here fails, the caller falls back to a content hash — the job
    is still reviewable, it just can never be merged by the gatekeeper, which is the
    honest outcome for work that isn't in git.
    """
    env = {**os.environ,
           "GIT_AUTHOR_NAME": "chief-worker", "GIT_AUTHOR_EMAIL": "worker@chief.local",
           "GIT_COMMITTER_NAME": "chief-worker", "GIT_COMMITTER_EMAIL": "worker@chief.local"}
    def git(*args):
        return subprocess.run(["git", *args], cwd=wt, capture_output=True, text=True,
                              timeout=60, env=env)
    try:
        if git("rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
            return None
        if git("checkout", "-B", branch).returncode != 0:
            return None
        if git("add", "-A", OUTPUT_DIRNAME).returncode != 0:
            return None
        if git("commit", "-q", "-m", f"job {job_id}: {summary[:60]}").returncode != 0:
            return None
        sha = git("rev-parse", "HEAD").stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return None          # a hung git is "not in git", not a dead worker thread
    return sha[:16] if len(sha) >= 16 else None


def _version_of(text: str) -> str:
    """The exact content being put forward, as a short hash. This is what the whole
    review-to-version chain binds to: change the output, change the version, and every
    earlier approval silently stops counting (schema.sql, gate 3)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Worktree isolation — a builder never touches the live project.
# ---------------------------------------------------------------------------
def _make_worktree(job_id: int, branch: str,
                   source_repo: Path | None = None) -> tuple[Path | None, str]:
    """Give this job its own private copy of the repo. Returns (path, note).

    If git worktrees aren't usable for any reason, fall back to a plain isolated
    directory and SAY SO — a silent fallback that quietly drops isolation would be
    exactly the kind of unspoken gap this project keeps getting bitten by.
    """
    WORKTREES.mkdir(exist_ok=True)
    dest = WORKTREES / f"job-{job_id}"
    if dest.exists():
        return dest, "reused"
    source_repo = source_repo if source_repo is not None else HARNESS.parent
    try:
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(dest)],
            cwd=source_repo, capture_output=True, text=True, timeout=60, check=True,
        )
        return dest, "worktree"
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        # Isolation still holds (separate dir, outside the repo tree) — it just isn't a
        # git worktree, so we record that honestly.
        dest.mkdir(parents=True, exist_ok=True)
        detail = getattr(exc, "stderr", "") or str(exc)
        return dest, f"plain-dir ({str(detail)[:60].strip()})"


def _make_clone(job_id: int, branch: str, source_repo: Path | None = None,
                source_branch: str = "main") -> tuple[Path | None, str]:
    """A standalone clone for code builders — the isolation a linked worktree
    cannot provide.

    `git worktree add` shares $GIT_COMMON_DIR with the live repo: .git/config,
    hooks, refs, the object store. A builder holding real Edit/Write tools inside a
    worktree could, in principle, move refs/heads/main or rewrite .git/config —
    Sol's design gate named this explicitly as a hard STOP. A clone has its OWN
    .git; nothing written inside it can touch the source repo's refs or config.

    NO FALLBACK to a plain directory here (unlike _make_worktree): a code build
    that can't be isolated must not run at all. The local text builder can fall
    back honestly because it has no shell and writes one known file; a code
    builder with edit tools cannot make that same trade.
    """
    CLONES.mkdir(exist_ok=True)
    dest = CLONES / f"job-{job_id}"
    if dest.exists():
        # Sol's design gate: an existing job directory accepted without checking
        # its cleanliness/HEAD is a way to redirect a builder. Refuse to reuse.
        return None, "an isolated copy for this job already exists"
    source_repo = source_repo if source_repo is not None else HARNESS.parent
    try:
        subprocess.run(
            ["git", "clone", "--quiet", "--no-hardlinks", "--branch", source_branch,
             str(source_repo), str(dest)],
            capture_output=True, text=True, timeout=120, check=True,
        )
        subprocess.run(
            ["git", "checkout", "-q", "-B", branch], cwd=dest,
            capture_output=True, text=True, timeout=30, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        if dest.exists():
            subprocess.run(["rm", "-rf", str(dest)], capture_output=True, text=True)
        detail = getattr(exc, "stderr", "") or str(exc)
        return None, f"could not create an isolated copy ({str(detail)[:80].strip()})"
    return dest, "clone"


def cleanup_clone(job_id: int) -> None:
    """Remove a job's clone AND its settings file.

    Called from two places with two different meanings: (a) here in this module,
    on every FAILED claude-cli build — a failed job has nothing left worth reading,
    and leaving the clone behind would block a retry (_make_clone refuses to reuse
    an existing job directory). (b) NOT called automatically when a diff job
    reaches 'done' — Neill reads the candidate first; see gauntlet._ask_to_ship's
    diff-kind branch, which deliberately never calls this on the success path.
    """
    dest = CLONES / f"job-{job_id}"
    settings = dest.parent / f"{dest.name}.settings.json"
    if dest.exists():
        subprocess.run(["rm", "-rf", str(dest)], capture_output=True, text=True)
    if settings.exists():
        try:
            settings.unlink()
        except OSError:
            pass


def cleanup_worktree(job_id: int) -> None:
    """Remove a job's worktree once its work is merged or abandoned."""
    dest = WORKTREES / f"job-{job_id}"
    if not dest.exists():
        return
    subprocess.run(["git", "worktree", "remove", "--force", str(dest)],
                   cwd=HARNESS.parent, capture_output=True, text=True)
    if dest.exists():
        subprocess.run(["rm", "-rf", str(dest)], capture_output=True, text=True)


# ---------------------------------------------------------------------------
# The actual builders, one per provider. Add a provider = add a function.
# ---------------------------------------------------------------------------
# The claude-cli builder — locked down the same way gauntlet.py's reviewer CLI
# calls are (see that file's `_claude_cmd` / `assert_reviewers_locked_down`, which
# this mirrors). Checked against `claude --help` on this machine, 2026-08-27:
#   --append-system-prompt-file does NOT exist on this build (only
#   --append-system-prompt <text>, which is an argv value, not a file, and this
#   project's rule is prompt-on-stdin only — variadic flags swallow a positional
#   argument). So project-aware conventions and notes travel on stdin, ahead of the
#   actual request, clearly labelled as background context rather than an instruction
#   to follow over the task (see _compose_builder_prompt).
# ---------------------------------------------------------------------------
def _claude_build_argv(model: str, clone: Path, settings_path: Path) -> list[str]:
    return [
        "claude", "-p",
        "--setting-sources", "",       # no user/project/local settings load at all
        "--strict-mcp-config",         # ...so no MCP server the caller happens to have
        "--restricted",                # ...no Bash/shell tools, file tools confined
        "--safe-mode",                 #    to the working dirs, no hooks/skills/plugins
        "--permission-mode", "acceptEdits",   # headless: apply edits, never prompt
        "--tools", "Read Edit Write Glob Grep",   # no Bash — deliberately, tonight
        "--add-dir", str(clone),
        "--settings", str(settings_path),
        "--max-budget-usd", "3",
        "--model", model,
        "--output-format", "json",
    ]


def _flag_value(argv: list[str], flag: str) -> str | None:
    try:
        i = argv.index(flag)
    except ValueError:
        return None
    return argv[i + 1] if i + 1 < len(argv) else None


def assert_builders_locked_down() -> None:
    """Inspect the argv a build call would actually use and refuse to build if a
    lockdown flag is missing. Builds argv only — never spawns a process — so it's
    cheap enough to call before every build. Mirrors
    gauntlet.assert_reviewers_locked_down(); a refactor that drops --restricted or
    swaps --tools for something wider fails the FIRST build loudly instead of
    quietly running every build after it wide open.
    """
    argv = _claude_build_argv(
        "probe-model", Path("/tmp/chief-build-probe"),
        Path("/tmp/chief-build-probe/settings.json"),
    )
    failures: list[str] = []
    if _flag_value(argv, "--setting-sources") != "":
        failures.append("claude: --setting-sources must be present and empty")
    if "--strict-mcp-config" not in argv:
        failures.append("claude: --strict-mcp-config is missing")
    if "--restricted" not in argv:
        failures.append("claude: --restricted is missing")
    if "--safe-mode" not in argv:
        failures.append("claude: --safe-mode is missing")
    if _flag_value(argv, "--permission-mode") != "acceptEdits":
        failures.append("claude: --permission-mode acceptEdits is missing")
    if _flag_value(argv, "--tools") != "Read Edit Write Glob Grep":
        failures.append("claude: --tools must be exactly 'Read Edit Write Glob Grep' (no Bash)")
    if not _flag_value(argv, "--add-dir"):
        failures.append("claude: --add-dir is missing")
    if not _flag_value(argv, "--settings"):
        failures.append("claude: --settings is missing")
    if _flag_value(argv, "--max-budget-usd") != "3":
        failures.append("claude: --max-budget-usd 3 is missing")
    if _flag_value(argv, "--output-format") != "json":
        failures.append("claude: --output-format json is missing")
    if not _flag_value(argv, "--model"):
        failures.append("claude: --model is missing")
    if failures:
        raise RuntimeError(
            "builder lockdown check failed — refusing to build: " + "; ".join(failures)
        )


MEMORY_INDEX_MAX_CHARS = 8000   # a project's MEMORY.md can be large; cap what rides
                                # into every single build prompt


def _lane_memory_text(project_id: str) -> str:
    """This PROJECT's own conventions — never a different project's (Sol's design
    gate flagged prepending the wrong project's memory as a real bug, 2026-07-xx).
    Looks for a project-specific file first, falls back to the shared one."""
    for path in (
        HARNESS / "config" / "lanes" / project_id / "riggs.md",
        HARNESS / "config" / "lanes" / "riggs.md",
    ):
        try:
            text = path.read_text()
        except OSError:
            continue
        if text.strip():
            return text
    return ""


def _compose_builder_prompt(request: str, lane_text: str,
                            memory_text: str | None) -> str:
    """Everything that rides into the builder ahead of the actual task, each part
    clearly labelled as background so the model never mistakes a project's own notes
    for an instruction to follow over the real request."""
    sections: list[str] = []
    if lane_text.strip():
        sections.append(
            "Background — this project's own conventions (read-only context, not "
            f"part of the task):\n\n{lane_text.strip()}"
        )
    if memory_text and memory_text.strip():
        sections.append(
            "Background — this project's own accumulated notes (read-only context, "
            f"not part of the task):\n\n{memory_text.strip()[:MEMORY_INDEX_MAX_CHARS]}"
        )
    sections.append(f"The task:\n\n{request}")
    return "\n\n---\n\n".join(sections)


def _settings_path_for(clone: Path) -> Path:
    """Where a clone's --settings file lives: alongside the clone, in CLONES
    itself (never inside the clone — see the per-job --settings contract in
    _claude_build_argv), named after the clone directory."""
    return clone.parent / f"{clone.name}.settings.json"


def _plain_builder_error(raw: str) -> str:
    """Turn CLI/vendor text into something Neill could read without flinching.
    Only translates the ONE pattern we know shows up in practice (an unauthenticated
    session); anything else stays as truncated raw text — this is a translation of
    a specific known phrase, not a general jargon filter."""
    if "not logged in" in raw.lower():
        return "the builder isn't signed in on this machine"
    return raw.strip()[:300]


def _claude_cli_build(seat_row, request: str, model: str, clone: Path) -> str:
    """Run the builder INSIDE the clone with the already-composed full prompt.

    Returns the CLI's raw stdout (a JSON envelope) for logging only — the actual work
    product is whatever changed on disk, which the CALLER commits. This function never
    reads or trusts the model's own narration of what it did.
    """
    assert_builders_locked_down()
    settings_path = _settings_path_for(clone)
    settings_path.write_text(json.dumps({"permissions": {"additionalDirectories": []}}))
    argv = _claude_build_argv(model, clone, settings_path)

    # MINIMAL ENV. PATH so the CLI's own subprocess calls (git) resolve; HOME so it
    # finds its OAuth session — nothing else. No XAI_API_KEY, no OPENAI_API_KEY: a
    # diff a builder writes must never be able to read a sibling provider's key out
    # of its own process environment.
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
        "HOME": os.environ.get("HOME", str(Path.home())),
        # USER, not just HOME: verified live that with only PATH+HOME the CLI
        # exits "Not logged in" — the macOS Keychain lookup for the OAuth session
        # keys off USER as well. Still no XAI_API_KEY/OPENAI_API_KEY — a sibling
        # provider's key must never reach this process.
        "USER": os.environ.get("USER", ""),
    }
    proc = subprocess.run(
        argv, input=request, cwd=clone, env=env,
        capture_output=True, text=True, timeout=BUILD_TIMEOUT_S,
    )
    if proc.returncode != 0:
        raise RuntimeError(_plain_builder_error(proc.stderr or proc.stdout))

    # The exit code alone isn't enough — the CLI can exit 0 with an envelope that
    # says the run didn't actually finish (mirrors gauntlet.py's _claude_review,
    # which does the same is_error check for the review path). NOTHING gets
    # committed on any of these: the caller only calls _commit_diff_in_clone after
    # this function returns successfully.
    try:
        envelope = json.loads(proc.stdout)
    except ValueError as exc:
        raise RuntimeError("the builder's reply wasn't readable JSON") from exc
    if not isinstance(envelope, dict):
        raise RuntimeError("the builder's reply wasn't the JSON envelope we expected")
    if envelope.get("is_error"):
        raise RuntimeError(_plain_builder_error(str(envelope.get("result", ""))))
    subtype = envelope.get("subtype")
    if subtype and subtype != "success":
        raise RuntimeError(f"the builder didn't finish cleanly ({subtype})")
    return proc.stdout


def _ollama_build(seat_row, request: str, model: str) -> str:
    """The free local coder actually writes the code."""
    payload = json.dumps({"model": model, "prompt": request, "stream": False}).encode()
    req = urllib.request.Request(
        OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())["response"].strip()


_BUILDERS = {
    "ollama": _ollama_build,
}


@dataclass
class ClonedCommit:
    sha: str | None
    reason: str = ""          # why sha is None, in plain words (the job's error text)


def _disallowed_files_staged(clone: Path) -> list[str]:
    try:
        raw = subprocess.run(["git", "diff", "--cached", "--raw"], cwd=clone,
                             capture_output=True, text=True, timeout=30)
        numstat = subprocess.run(["git", "diff", "--cached", "--numstat"], cwd=clone,
                                 capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError):
        return ["(could not check what changed — refusing to guess)"]
    if raw.returncode != 0 or numstat.returncode != 0:
        # An empty result here used to mean "nothing to flag" — indistinguishable
        # from git having FAILED to tell us anything at all. A check that can't
        # run is not a clean bill of health.
        return ["(could not check what changed — refusing to guess)"]
    return git_policy.disallowed_paths(raw.stdout.splitlines(), numstat.stdout.splitlines())


def _commit_diff_in_clone(clone: Path, job_id: int, branch: str, summary: str) -> ClonedCommit:
    """Commit whatever the builder changed — and refuse to commit anything that
    isn't a plain text file. This is the FIRST of the two places that list is
    enforced (gatekeeper.merge re-applies it at merge time on the committed range;
    see git_policy.py's docstring) — together they mean no path a builder can take
    lands something the panel never saw the shape of, in main.
    """
    env = {**os.environ,
           "GIT_AUTHOR_NAME": "chief-worker", "GIT_AUTHOR_EMAIL": "worker@chief.local",
           "GIT_COMMITTER_NAME": "chief-worker", "GIT_COMMITTER_EMAIL": "worker@chief.local"}

    def git(*args, timeout=60):
        return subprocess.run(["git", *args], cwd=clone, capture_output=True, text=True,
                              timeout=timeout, env=env)

    try:
        if git("rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
            return ClonedCommit(None, "the isolated copy isn't a real git checkout")
        if git("add", "-A").returncode != 0:
            return ClonedCommit(None, "could not stage the change")
        staged = git("diff", "--cached", "--name-only").stdout.strip()
        if not staged:
            return ClonedCommit(None, "nothing changed — there's no work to review")
        bad = _disallowed_files_staged(clone)
        if bad:
            git("reset")          # unstage — leave nothing half-prepared behind
            return ClonedCommit(
                None,
                "the change touches things that can't be reviewed as plain code "
                f"({', '.join(bad)[:200]})",
            )
        if git("commit", "-q", "-m", f"job {job_id}: {summary[:60]}").returncode != 0:
            return ClonedCommit(None, "the change couldn't be committed")
        sha = git("rev-parse", "HEAD").stdout.strip()
    except (subprocess.TimeoutExpired, OSError) as exc:
        return ClonedCommit(None, f"git didn't respond ({str(exc)[:80]})")
    if len(sha) != 40:
        return ClonedCommit(None, "git didn't return a real commit id")
    return ClonedCommit(sha)


def _merge_base_with_main(clone: Path, branch: str = "main") -> str | None:
    try:
        out = subprocess.run(["git", "merge-base", branch, "HEAD"], cwd=clone,
                             capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return (out.stdout.strip() or None) if out.returncode == 0 else None


def _diff_against_main(clone: Path, base: str, tip: str) -> str | None:
    try:
        out = subprocess.run(["git", "diff", f"{base}..{tip}"], cwd=clone,
                             capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return out.stdout if out.returncode == 0 else None


# Reviewing lives in gauntlet.py (task #10). This module builds; that one judges.
# It used to hold a single hard-wired Claude reviewer, which was enough to close the
# loop and was never the design — one reviewer cannot show that a DIFFERENT MIND looked.


# ---------------------------------------------------------------------------
# The run loop — everything above, in order, for one job.
# ---------------------------------------------------------------------------
def run_job(job_id: int, *, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Take one recorded job and actually do it. Safe to call in a thread.

    Opens its own database connection (sqlite + threads). WAL mode lets this write
    while the web server reads. Never raises to the caller — a worker that dies must
    leave a legible 'failed' row, not a stack trace nobody sees.
    """
    conn = connect(DB_PATH)
    try:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            return {"job_id": job_id, "status": "missing"}
        seat_row = seat(conn, job["builder_seat"])
        if seat_row is None:
            set_status(conn, job_id, "failed", error=f"unknown seat {job['builder_seat']}")
            return {"job_id": job_id, "status": "failed"}

        provider = seat_row["provider"]
        is_claude_builder = provider == "claude-cli"
        builder = _BUILDERS.get(provider)
        if builder is None and not is_claude_builder:
            # A provider we don't have a local runner for (codex/xai builds go
            # through OpenClaw, not this in-process worker). Say so plainly.
            set_status(conn, job_id, "failed",
                       error=f"no in-process builder for provider '{provider}' "
                             "— this seat dispatches through OpenClaw")
            _event(conn, job_id, seat_row, "error",
                   "This worker only runs the local model or the claude-cli builder directly.")
            return {"job_id": job_id, "status": "failed"}

        # The job's bundle_kind was frozen at dispatch from the seat's provider AT
        # THAT TIME (dispatch.BUNDLE_KIND_BY_PROVIDER). Seats are re-upserted from
        # config on every server start, so a seat's LIVE provider can drift after a
        # job is queued (e.g. reconfigured between dispatch and this worker actually
        # running). If the frozen stamp and the live provider now disagree about
        # what shape this job's review should be, refuse rather than guess which
        # one is right — running it either way would produce a bundle that doesn't
        # match what the record says it should be.
        expected_kind = "diff" if is_claude_builder else "text"
        actual_kind = job["bundle_kind"] or "text"
        if actual_kind != expected_kind:
            set_status(conn, job_id, "failed",
                       error="this job's review shape no longer matches its builder "
                             "— refusing to run it")
            _event(conn, job_id, seat_row, "error",
                   "This job's setup changed since it was queued and can't safely continue.")
            return {"job_id": job_id, "status": "failed"}

        project_id = job["project_id"]
        effective_project_id = project_id or "chief"
        project_display = None
        source_repo, source_branch = None, "main"
        if project_id:
            from db.projects import ProjectRepoUnavailable, project_name, resolve_repo
            try:
                source_repo, source_branch = resolve_repo(conn, project_id)
            except ProjectRepoUnavailable as exc:
                set_status(conn, job_id, "failed", error=str(exc))
                _event(conn, job_id, seat_row, "error", str(exc))
                return {"job_id": job_id, "status": "failed"}
            project_display = project_name(conn, project_id)
        else:
            from db.projects import project_name
            project_display = project_name(conn, "chief")

        branch = job["branch"] or f"job/{job_id}"

        # 1. Isolate. Code builders get a standalone clone (no shared .git with
        #    main); the local text builder keeps the lighter-weight worktree — it
        #    never runs a shell and only ever writes one known file.
        if is_claude_builder:
            wt, wt_note = _make_clone(job_id, branch, source_repo, source_branch)
            if wt is None:
                set_status(conn, job_id, "failed",
                           error=f"could not isolate the build: {wt_note}")
                _event(conn, job_id, seat_row, "error",
                       "Could not set up an isolated copy to build in.")
                cleanup_clone(job_id)
                return {"job_id": job_id, "status": "failed"}
        else:
            wt, wt_note = _make_worktree(job_id, branch, source_repo)
        conn.execute("UPDATE jobs SET worktree = ? WHERE id = ?", (str(wt), job_id))
        _event(conn, job_id, seat_row, "dispatched",
               f"Working in {project_display}'s code, in its own copy ({wt_note}).")

        from db.projects import memory_index
        lane_text = _lane_memory_text(effective_project_id)
        try:
            memory_text = memory_index(conn, effective_project_id)
        except Exception:
            memory_text = None
        built_prompt = _compose_builder_prompt(job["request"], lane_text, memory_text)

        # 2. Build.
        model = job["tier"] and _model_for(seat_row, job["tier"]) or seat_row["model"]
        _event(conn, job_id, seat_row, "thinking", "Working out how to do it.")

        if is_claude_builder:
            try:
                _claude_cli_build(seat_row, built_prompt, model, wt)
            except Exception as exc:  # noqa: BLE001 — model, timeout, lockdown check
                set_status(conn, job_id, "failed", error=f"builder error: {exc}")
                _event(conn, job_id, seat_row, "error", "The worker hit a problem.")
                cleanup_clone(job_id)
                return {"job_id": job_id, "status": "failed"}

            # 3. Commit what changed, refusing anything that isn't plain text —
            #    and bind the FULL 40-char sha, never a truncated prefix (the
            #    gatekeeper's own version check requires the full length once one
            #    is stored — see gatekeeper._same_commit).
            commit = _commit_diff_in_clone(wt, job_id, branch, job["request"])
            if commit.sha is None:
                set_status(conn, job_id, "failed", error=commit.reason)
                _event(conn, job_id, seat_row, "error", commit.reason)
                cleanup_clone(job_id)
                return {"job_id": job_id, "status": "failed"}
            version = commit.sha
            base = _merge_base_with_main(wt, source_branch)
            bundle = _diff_against_main(wt, base, version) if base else None
            if not base or bundle is None:
                set_status(conn, job_id, "failed",
                           error="could not compute the change against the main line")
                _event(conn, job_id, seat_row, "error",
                       "Could not compare the work against the main line of code.")
                cleanup_clone(job_id)
                return {"job_id": job_id, "status": "failed"}
            set_head_version(conn, job_id, version)
            result = bundle
            _event(conn, job_id, seat_row, "write",
                   f"Wrote a change of {len(result)} characters.", target=str(wt))
        else:
            try:
                result = builder(seat_row, built_prompt, model)
            except Exception as exc:  # noqa: BLE001 — network, model, timeout
                set_status(conn, job_id, "failed", error=f"builder error: {exc}")
                _event(conn, job_id, seat_row, "error", "The worker hit a problem.")
                return {"job_id": job_id, "status": "failed"}

            out_dir = wt / OUTPUT_DIRNAME
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"job_{job_id}.txt").write_text(result)
            version = _commit_in_worktree(wt, job_id, branch, job["request"]) or _version_of(result)
            set_head_version(conn, job_id, version)
            _event(conn, job_id, seat_row, "write",
                   f"Wrote {len(result)} characters of work.", target=str(out_dir))

        # 4. Park for review. The guards will hold it here until a real reviewer
        #    passes THIS version — we never force it past them.
        set_status(conn, job_id, "review", result=result)

        # 5. Hand the frozen bundle to the review panel — unchanged shape. The one
        #    new condition: never run the harness's own test suite on builder-
        #    written code (task #9's GO version stops at a reviewed candidate).
        if cfg is not None:
            import gauntlet
            reviewer_request = (
                f"{job['request']}\n\nThis work is for the project '{project_display}'."
            )
            panel = gauntlet.run_panel(conn, job_id, reviewer_request, result, version,
                                       cfg, db_path=DB_PATH)
            if panel.certified and not is_claude_builder:
                import tester
                try:
                    tester.record_smoke_check(conn, job_id)
                except Exception:  # noqa: BLE001 — purely diagnostic; never worth
                    pass            # losing the worker thread over

        final = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()["status"]
        return {"job_id": job_id, "status": final, "version": version}
    finally:
        conn.close()


def _model_for(seat_row, tier: str) -> str:
    try:
        from tiering import resolve_model
        return resolve_model(seat_row, tier)[0]
    except Exception:
        return seat_row["model"]


# ---------------------------------------------------------------------------
# Fire-and-forget: start a worker in the background so the app stays responsive.
# ---------------------------------------------------------------------------
def start_in_background(job_id: int, *, cfg: dict[str, Any] | None = None) -> threading.Thread:
    t = threading.Thread(
        target=run_job, args=(job_id,), kwargs={"cfg": cfg},
        name=f"job-{job_id}", daemon=True,
    )
    t.start()
    return t
