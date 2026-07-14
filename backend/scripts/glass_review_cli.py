"""Glass — CLI wrapper around services.agent_tools.execute_code_review.

Exists so /review's reviewer sweep can include Glass (Pro on Vertex) alongside
the Claude-Sonnet reviewers. Claude Code subagents only run Claude models, so
the actual cross-family analysis can't happen inside a subagent — instead, the
``glass.md`` subagent shells out to this script via Bash and returns the
script's stdout verbatim. Pro does the reviewing; Claude is the tunnel.

Contract:
  --target  required — file path / git range / inline text (auto-detected by
            ``execute_code_review``'s resolver, same posture as the Live tool).
  --focus   optional, default 'general'. One of: general, security, performance,
            spec, architecture. Off-enum values fall back to 'general' inside
            the executor (defense-in-depth).
  --scope   optional, default 'chief-command'. Maps to the canonical project
            name via ``services.repo_map`` to resolve cwd. Two short forms
            ('chief-command' / 'arch') because the /review sweep doesn't want
            to type the canonical "Chief Command" / "Arch" with spaces.

Output discipline:
  * STDOUT carries ONLY the review text. The Claude tunnel relays stdout back
    to the user verbatim, so any diagnostic chatter on stdout would corrupt
    the review.
  * STDERR carries diagnostics (which scope, which focus, elapsed time) for
    operator debugging. The tunnel surfaces stderr only on non-zero exit.

Exit codes:
  0  — review printed to stdout
  1  — argument or scope-resolution failure (no project / unknown scope)
  2  — execute_code_review returned an error result
  3  — uncaught exception

Run from anywhere:
    python /Users/user/code-projects/chief-command/backend/scripts/glass_review_cli.py \\
        --target backend/services/agent_tools.py \\
        --focus security \\
        --scope chief-command
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Optional

# Make ``backend/`` the import root so ``services.*`` / ``config.*`` resolve
# when this script is invoked as a top-level entry-point from any cwd.
# Same pattern live_smoke.py uses.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


# Short-form scope names → canonical project names used by repo_map.
# Keep this map narrow: `chief-command` and `arch` are the only sweep-eligible
# scopes today. Adding more should be an intentional edit, not config soup.
_SCOPE_ALIASES: dict[str, str] = {
    "chief-command": "Chief Command",
    "chief": "Chief Command",
    "arch": "Arch",
    "personal-assist": "Personal Assist",
}


def _resolve_scope(scope_arg: str) -> tuple[Optional[Path], Optional[str], Optional[str]]:
    """Resolve a CLI scope string to (cwd, canonical_name, error).

    Returns (None, None, error_message) on failure. The canonical name is
    surfaced for the executor's cost-recording row + system-prompt builder.
    """
    if not scope_arg:
        return None, None, "scope is required"

    canonical = _SCOPE_ALIASES.get(scope_arg.strip().lower())
    if canonical is None:
        # Allow callers to pass the canonical name directly, too — the lookup
        # above is a convenience, not a fence.
        canonical = scope_arg.strip()

    # Lazy import — keeps argparse failures fast without paying the cost of
    # repo_map's import-time path audit.
    from services.repo_map import get_repo_path

    cwd = get_repo_path(canonical)
    if cwd is None:
        return None, None, (
            f"scope {scope_arg!r} (canonical={canonical!r}) does not resolve "
            f"to an existing project repo"
        )
    return cwd, canonical, None


async def _run_review(target: str, focus: str, scope_arg: str) -> int:
    """Run execute_code_review and stream the result to stdout.

    Returns the process exit code: 0 on success, 1 on scope-resolution
    failure, 2 on executor error, 3 on uncaught exception (caught above
    in main()).
    """
    cwd, canonical_scope, err = _resolve_scope(scope_arg)
    if err is not None or cwd is None or canonical_scope is None:
        print(f"glass_review_cli: {err}", file=sys.stderr)
        return 1

    print(
        f"glass_review_cli: scope={canonical_scope!r} cwd={cwd} focus={focus!r}",
        file=sys.stderr,
    )

    # Lazy import — agent_tools has heavy transitive deps (anthropic SDK,
    # google-genai). Importing inside the async path keeps argparse + scope
    # validation snappy on argument errors.
    from services.agent_tools import execute_code_review

    started = time.monotonic()
    result = await execute_code_review(
        target=target,
        cwd=cwd,
        scope=canonical_scope,
        focus=focus,
    )
    elapsed = time.monotonic() - started
    print(
        f"glass_review_cli: completed in {elapsed:.2f}s (error={result.error}, "
        f"truncated={result.truncated})",
        file=sys.stderr,
    )

    if result.error:
        # Surface the executor's error message on stderr so the Claude tunnel
        # can relay it as the failure mode, and exit non-zero.
        print(result.output, file=sys.stderr)
        return 2

    # Print the review verbatim — STDOUT is reserved for review text only.
    print(result.output)
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Build the CLI argument parser. Lives in its own function for testability."""
    parser = argparse.ArgumentParser(
        prog="glass_review_cli",
        description=(
            "Cross-family code reviewer (Pro on Vertex). "
            "Wraps services.agent_tools.execute_code_review so the /review "
            "sweep can run Glass alongside the Claude reviewers."
        ),
    )
    parser.add_argument(
        "--target",
        required=True,
        help=(
            "What to review. File path (relative to scope's cwd), git range "
            "(e.g. HEAD~3..HEAD or main..feature), or inline code/spec text. "
            "Auto-detected by the executor."
        ),
    )
    parser.add_argument(
        "--focus",
        default="general",
        choices=["general", "security", "performance", "spec", "architecture"],
        help="Review angle. Default: general (correctness / readability / obvious issues).",
    )
    parser.add_argument(
        "--scope",
        default="chief-command",
        help=(
            "Project scope — controls which repo's cwd resolves. Accepts "
            "'chief-command', 'arch', 'personal-assist', or a canonical name. "
            "Default: chief-command."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """Entry-point. argv defaults to sys.argv[1:]."""
    # Diagnostics on stderr at WARNING — keep stdout clean for the review text.
    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    args = _parse_args(argv if argv is not None else sys.argv[1:])

    try:
        return asyncio.run(_run_review(args.target, args.focus, args.scope))
    except KeyboardInterrupt:
        print("glass_review_cli: interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        # Unhandled exception path — don't leak a stack trace to stdout (would
        # corrupt the review channel for the tunnel). Log it on stderr.
        logging.exception("glass_review_cli: unhandled exception")
        print(f"glass_review_cli: failed: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
