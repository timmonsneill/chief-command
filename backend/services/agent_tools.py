"""Provider-neutral agent tool definitions for the Gemini-brained Chief.

Defines the four tools the brain can call, with:
  * Provider-neutral schemas (name, description, JSON-schema parameters)
  * In-process executors for Read / Bash / Grep that reuse the cc_session
    sandbox helpers (path containment + secret-pattern denial + bash leader
    allowlist + read-only-git verbs)
  * dispatch_agent — async wrapper around cc_session.get_pool() so heavy
    multi-tool work goes through the existing read-only CC subprocess pool

The Gemini-flavored ``google.genai.types.FunctionDeclaration`` exporter lives
in this module too so ``gemini_brain`` doesn't have to know about either the
sandbox or the CC pool — it just gets a list of FunctionDeclarations and a
dispatch function it can call when the model emits a function_call.

Security posture (Phase 2 — read-only):
  * Read: file_path under cwd, not a secrets pattern, returns up to 200KB.
  * Bash: leader in allowlist, segment splits checked, path args validated,
    redirects rejected. 5s timeout. 50KB stdout cap.
  * Grep: pattern + optional path, both path-fenced. Falls back to grep -rni
    if rg isn't on PATH. 5s timeout. 50KB stdout cap.
  * dispatch_agent: spawns CC subprocess via cc_session pool, same
    can_use_tool callback, ~10s typical turn — used when Chief needs a
    multi-step agent loop (subagents, MCP, etc.) instead of a single shell.

Cancellation:
  * The four executors here are best-effort interruptible — Read is fast
    enough to ignore, Bash/Grep have asyncio timeouts that are pre-empted by
    asyncio.CancelledError on the outer cancel path. dispatch_agent forwards
    cancellation to ``cc_session.interrupt(...)`` so the CC subprocess stops.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from services.cc_session import (
    _BASH_ALLOWED_LEADING,
    _BASH_FORBIDDEN_CHARS,
    _BASH_PATH_VALIDATING_LEADERS,
    _FORBIDDEN_PATH_RE,
    _GIT_READ_ONLY_VERBS,
    _bash_segment_ok,
    _bash_split_segments,
    _path_forbidden,
    _path_inside_cwd,
    _validate_bash_paths,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------
READ_MAX_BYTES: int = 200 * 1024            # 200KB cap on file content returned
BASH_MAX_BYTES: int = 50 * 1024             # 50KB stdout cap
GREP_MAX_BYTES: int = 50 * 1024             # 50KB stdout cap
TOOL_TIMEOUT_S: float = 5.0                  # Bash + Grep timeout
DISPATCH_AGENT_TIMEOUT_S: float = 90.0       # Outer cap on a CC dispatch turn


# ---------------------------------------------------------------------------
# Provider-neutral tool schema
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ToolSchema:
    """Provider-neutral tool description.

    ``parameters`` is JSON-Schema. Each provider's adapter rewrites this into
    its own function-declaration format (see ``to_gemini_declarations`` below).
    """
    name: str
    description: str
    parameters: dict[str, Any]


READ_TOOL = ToolSchema(
    name="Read",
    description=(
        "Read a UTF-8 text file from the project repository. The path must "
        "be inside the active scope's repo cwd. Returns the file contents "
        "(up to 200KB; truncated past that). Use this to inspect source "
        "files, configs, READMEs, etc."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file. May be relative to cwd or absolute under cwd.",
            },
        },
        "required": ["path"],
    },
)


BASH_TOOL = ToolSchema(
    name="Bash",
    description=(
        "Run a read-only shell command in the project repository. Allowed "
        "commands: git (read-only verbs only — status/log/diff/branch/show/"
        "rev-parse/etc), ls, cat, head, tail, wc, find (no -exec/-delete), "
        "grep, echo, pwd. Output redirects, pipes producing files, and "
        "process substitution are rejected. 5s timeout. Returns up to 50KB "
        "of stdout."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to run, e.g. 'git log --oneline -5'.",
            },
        },
        "required": ["command"],
    },
)


GREP_TOOL = ToolSchema(
    name="Grep",
    description=(
        "Search for a regex pattern across files in the project repository. "
        "Uses ripgrep when available (case-insensitive, recursive); falls "
        "back to grep -rni. The optional 'path' restricts the search to a "
        "subdirectory under cwd. 5s timeout. Returns up to 50KB of matches."
    ),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regex pattern to search for.",
            },
            "path": {
                "type": "string",
                "description": "Optional subdirectory under cwd to limit the search. Defaults to '.'.",
            },
        },
        "required": ["pattern"],
    },
)


DISPATCH_AGENT_TOOL = ToolSchema(
    name="dispatch_agent",
    description=(
        "Spawn a Claude Code subprocess to handle a complex multi-step task "
        "that needs subagents, MCP servers, or a full agent loop. Slower "
        "(typically ~10 seconds) than a direct Read/Bash/Grep call. Use "
        "ONLY when the task genuinely needs an agent — e.g. 'audit the auth "
        "module' or 'find all places we mutate this state'. For single-file "
        "reads or simple greps, use the direct tools instead."
    ),
    parameters={
        "type": "object",
        "properties": {
            "spec": {
                "type": "string",
                "description": "Plain-English description of what the agent should do.",
            },
            "scope": {
                "type": "string",
                "description": (
                    "Optional project scope to dispatch into. Defaults to "
                    "the current Chief scope."
                ),
            },
        },
        "required": ["spec"],
    },
)


ALL_TOOLS: tuple[ToolSchema, ...] = (
    READ_TOOL,
    BASH_TOOL,
    GREP_TOOL,
    DISPATCH_AGENT_TOOL,
)


# ---------------------------------------------------------------------------
# Tool execution result
# ---------------------------------------------------------------------------
@dataclass
class ToolResult:
    """Outcome of one tool execution.

    ``output`` is what gets fed back to the model as a function_response.
    ``error`` is True on a sandbox-deny / timeout / exec failure — same
    shape, but useful for logging/telemetry. The model still sees the
    ``output`` text and can reason about the failure.
    """
    output: str
    error: bool = False
    truncated: bool = False


def _truncate(data: bytes, limit: int) -> tuple[str, bool]:
    """Decode + truncate stdout bytes to ``limit`` chars. Returns (text, truncated)."""
    if len(data) <= limit:
        return data.decode("utf-8", errors="replace"), False
    head = data[:limit].decode("utf-8", errors="replace")
    return head + f"\n…[truncated at {limit} bytes]", True


# ---------------------------------------------------------------------------
# Read tool
# ---------------------------------------------------------------------------
async def execute_read(path: str, cwd: Path) -> ToolResult:
    """Read a file under ``cwd`` with the same fences cc_session enforces.

    Returns a ToolResult; never raises. The model sees error messages as
    ordinary tool output so it can react / re-route.
    """
    if not isinstance(path, str) or not path.strip():
        return ToolResult(output="error: path is required", error=True)
    cwd_resolved = cwd.resolve()
    resolved = _path_inside_cwd(path, cwd_resolved)
    if resolved is None:
        return ToolResult(
            output=f"error: path is outside the project cwd: {path!r}",
            error=True,
        )
    if _path_forbidden(resolved):
        return ToolResult(
            output=f"error: path matches a forbidden pattern (env/credential/secret): {path!r}",
            error=True,
        )
    try:
        # File reads are blocking I/O; use to_thread so we don't stall the
        # event loop on a large or slow-disk read.
        def _read():
            with open(resolved, "rb") as fh:
                return fh.read(READ_MAX_BYTES + 1)

        data = await asyncio.to_thread(_read)
    except FileNotFoundError:
        return ToolResult(output=f"error: file not found: {path!r}", error=True)
    except IsADirectoryError:
        return ToolResult(output=f"error: path is a directory: {path!r}", error=True)
    except PermissionError:
        return ToolResult(output=f"error: permission denied: {path!r}", error=True)
    except OSError as exc:
        return ToolResult(output=f"error: read failed: {exc}", error=True)

    text, truncated = _truncate(data, READ_MAX_BYTES)
    return ToolResult(output=text, truncated=truncated)


# ---------------------------------------------------------------------------
# Bash tool
# ---------------------------------------------------------------------------
async def execute_bash(command: str, cwd: Path) -> ToolResult:
    """Run a single read-only bash command under ``cwd`` with sandbox checks.

    Reuses cc_session's ``_bash_segment_ok`` — every shell separator-segment
    must pass the leader/forbidden-chars/git-verb/path-arg checks before the
    subprocess is spawned. Then runs with a 5s timeout and a 50KB stdout cap.
    """
    if not isinstance(command, str) or not command.strip():
        return ToolResult(output="error: command is required", error=True)
    cwd_resolved = cwd.resolve()
    segments = _bash_split_segments(command)
    if not segments:
        return ToolResult(output="error: command parsed to zero segments", error=True)
    for seg in segments:
        ok, reason = _bash_segment_ok(seg, cwd=cwd_resolved)
        if not ok:
            logger.warning(
                "agent_tools.Bash DENIED: %s — segment=%r command=%r",
                reason, seg, command[:200],
            )
            return ToolResult(
                output=f"error: command rejected by sandbox ({reason}): {seg!r}",
                error=True,
            )

    try:
        # Run via asyncio.create_subprocess_exec on a shell so the ``|`` /
        # ``&&`` etc. that survived our segment-split (and were validated)
        # behave as expected. We pass the command verbatim to /bin/sh -c.
        proc = await asyncio.create_subprocess_exec(
            "/bin/sh",
            "-c",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd_resolved),
            env=_clean_subprocess_env(),
        )
    except OSError as exc:
        return ToolResult(output=f"error: failed to spawn shell: {exc}", error=True)

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=TOOL_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await proc.wait()
        except Exception:
            pass
        return ToolResult(
            output=f"error: command timed out after {TOOL_TIMEOUT_S:.0f}s",
            error=True,
        )
    except asyncio.CancelledError:
        # Outer turn was cancelled (barge-in). Kill the child fast and
        # propagate so the outer task tears down cleanly.
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise

    text, truncated = _truncate(stdout, BASH_MAX_BYTES)
    if proc.returncode != 0:
        # Append stderr (truncated) so the model can see the failure mode.
        err_text, _ = _truncate(stderr, BASH_MAX_BYTES // 2)
        text = (
            f"{text}\n[exit={proc.returncode}]\n"
            f"{err_text}".rstrip()
        )
        return ToolResult(output=text, truncated=truncated, error=True)
    return ToolResult(output=text, truncated=truncated)


# ---------------------------------------------------------------------------
# Grep tool
# ---------------------------------------------------------------------------
async def execute_grep(pattern: str, cwd: Path, path: str = ".") -> ToolResult:
    """Run a recursive case-insensitive search via ripgrep (or grep fallback).

    Both the search root path and any positional args are fenced inside cwd
    via ``_path_inside_cwd`` + ``_path_forbidden``. The caller's ``path``
    argument is validated; an absolute or escaping path is rejected.
    """
    if not isinstance(pattern, str) or not pattern.strip():
        return ToolResult(output="error: pattern is required", error=True)
    if not isinstance(path, str) or not path.strip():
        path = "."
    cwd_resolved = cwd.resolve()
    resolved = _path_inside_cwd(path, cwd_resolved)
    if resolved is None:
        return ToolResult(
            output=f"error: path is outside the project cwd: {path!r}",
            error=True,
        )
    if _path_forbidden(resolved):
        return ToolResult(
            output=f"error: path matches a forbidden pattern: {path!r}",
            error=True,
        )

    rg = shutil.which("rg")
    if rg:
        argv = [rg, "-i", "--no-heading", "--line-number", "--", pattern, str(resolved)]
    else:
        argv = ["grep", "-rni", "--", pattern, str(resolved)]

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd_resolved),
            env=_clean_subprocess_env(),
        )
    except OSError as exc:
        return ToolResult(output=f"error: failed to spawn grep: {exc}", error=True)

    try:
        stdout, _stderr = await asyncio.wait_for(
            proc.communicate(), timeout=TOOL_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await proc.wait()
        except Exception:
            pass
        return ToolResult(
            output=f"error: search timed out after {TOOL_TIMEOUT_S:.0f}s",
            error=True,
        )
    except asyncio.CancelledError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise

    # rg / grep return 1 when there are no matches — that's not an error.
    text, truncated = _truncate(stdout, GREP_MAX_BYTES)
    if proc.returncode not in (0, 1):
        return ToolResult(
            output=f"error: search exited code={proc.returncode}\n{text}",
            error=True,
        )
    if not text.strip():
        text = "(no matches)"
    return ToolResult(output=text, truncated=truncated)


# ---------------------------------------------------------------------------
# dispatch_agent — async CC subprocess spawn via cc_session pool
# ---------------------------------------------------------------------------
async def execute_dispatch_agent(
    spec: str,
    cwd: Path,
    *,
    subject: str,
    scope: str,
    system_prompt_append: str,
) -> ToolResult:
    """Dispatch a CC subprocess for a complex multi-step task.

    Reuses ``services.cc_session`` — same can_use_tool sandbox, same
    read-only allowlist, same cwd containment. Sessions are pooled per
    (subject, scope) so a follow-up dispatch into the same scope reuses
    the warm subprocess.

    Returns a ToolResult whose ``output`` is the CC's final assistant text
    (concatenation of ``TextDelta.text`` events between SessionInit and
    TurnComplete). On any error or timeout, output is the error message
    and ``error=True``.

    Cancellation: if the outer coroutine is cancelled mid-dispatch we
    issue ``cc_session.interrupt(...)`` so the CC subprocess stops talking,
    then re-raise CancelledError. The pool's send() also handles its own
    interrupt+drain on CancelledError, so this is belt-and-suspenders.
    """
    if not isinstance(spec, str) or not spec.strip():
        return ToolResult(output="error: spec is required", error=True)

    # Lazy import — keeps the test surface narrow when callers stub
    # cc_session at module level.
    from services.cc_session import get_pool
    from services.cc_output_parser import (
        ParsedError,
        TextDelta,
        TurnComplete,
    )

    pool = get_pool()
    text_chunks: list[str] = []
    saw_error: Optional[str] = None
    completed = False

    started = time.monotonic()

    try:
        agen = pool.send(
            subject=subject,
            scope=scope,
            cwd=cwd,
            system_prompt_append=system_prompt_append,
            prompt=spec,
        )
        try:
            async with asyncio.timeout(DISPATCH_AGENT_TIMEOUT_S):
                async for ev in agen:
                    if isinstance(ev, TextDelta):
                        text_chunks.append(ev.text)
                    elif isinstance(ev, ParsedError):
                        saw_error = ev.message
                    elif isinstance(ev, TurnComplete):
                        completed = True
        finally:
            # Defensive: if we exit the async-for without TurnComplete (timeout,
            # CancelledError, exception), close the generator so the pool
            # cleans up the underlying iterator.
            try:
                await agen.aclose()
            except Exception:
                pass
    except asyncio.TimeoutError:
        # Forward an interrupt to the CC subprocess so the next turn lands
        # clean. The pool's drain logic handles the rest.
        try:
            await pool.interrupt(subject, scope)
        except Exception:
            pass
        elapsed = time.monotonic() - started
        return ToolResult(
            output=(
                f"error: dispatch_agent timed out after {elapsed:.1f}s. "
                f"Partial output:\n{''.join(text_chunks)[-2000:]}"
            ),
            error=True,
        )
    except asyncio.CancelledError:
        # Outer turn cancelled (barge-in). Tell CC to stop, then re-raise.
        try:
            await pool.interrupt(subject, scope)
        except Exception:
            pass
        raise
    except Exception as exc:
        logger.exception("dispatch_agent: pool.send raised: %s", exc)
        return ToolResult(
            output=f"error: dispatch_agent failed: {exc}",
            error=True,
        )

    if saw_error and not text_chunks:
        return ToolResult(output=f"error: {saw_error}", error=True)

    final_text = "".join(text_chunks).strip()
    if not final_text:
        if completed:
            final_text = "(agent completed with no output)"
        else:
            final_text = "(agent produced no output)"
    return ToolResult(output=final_text)


# ---------------------------------------------------------------------------
# Generic dispatch — provider adapters call this with the parsed tool name.
# ---------------------------------------------------------------------------
async def dispatch_tool(
    name: str,
    args: dict[str, Any],
    *,
    cwd: Path,
    subject: str,
    scope: str,
    system_prompt_append: str,
) -> ToolResult:
    """Dispatch a tool by name. Returns ToolResult; never raises (except
    asyncio.CancelledError, which propagates per cancellation contract)."""
    if name == "Read":
        return await execute_read(args.get("path") or args.get("file_path") or "", cwd)
    if name == "Bash":
        return await execute_bash(args.get("command") or "", cwd)
    if name == "Grep":
        return await execute_grep(
            args.get("pattern") or "",
            cwd,
            path=args.get("path") or ".",
        )
    if name == "dispatch_agent":
        # Allow per-call scope override but require it to be string-typed and
        # non-empty; fall back to the calling Chief scope otherwise.
        target_scope = args.get("scope")
        if not isinstance(target_scope, str) or not target_scope.strip():
            target_scope = scope
        return await execute_dispatch_agent(
            spec=args.get("spec") or "",
            cwd=cwd,
            subject=subject,
            scope=target_scope,
            system_prompt_append=system_prompt_append,
        )
    return ToolResult(output=f"error: unknown tool {name!r}", error=True)


# ---------------------------------------------------------------------------
# Gemini-flavored adapter — converts ALL_TOOLS into FunctionDeclarations.
# Imports the SDK lazily so this module loads in environments without it.
# ---------------------------------------------------------------------------
def to_gemini_declarations() -> list[Any]:
    """Return the list of google.genai FunctionDeclarations for ALL_TOOLS."""
    from google.genai import types

    decls: list[Any] = []
    for tool in ALL_TOOLS:
        decls.append(
            types.FunctionDeclaration(
                name=tool.name,
                description=tool.description,
                parameters_json_schema=tool.parameters,
            )
        )
    return decls


def to_gemini_tool() -> Any:
    """Return a single google.genai.Tool wrapping all four function declarations.

    Gemini accepts a list of Tool objects; each Tool can hold multiple
    function_declarations. Bundling all four into one Tool keeps the request
    simple — there's no semantic reason to split them.
    """
    from google.genai import types
    return types.Tool(function_declarations=to_gemini_declarations())


# ---------------------------------------------------------------------------
# Subprocess env scrubbing — same allowlist cc_session uses, copied here so
# Bash/Grep don't inherit ANTHROPIC_API_KEY / GITHUB_TOKEN / AWS_* from the
# parent. Read tool runs in-process so env doesn't apply.
# ---------------------------------------------------------------------------
_ENV_ALLOWLIST: frozenset[str] = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL",
    "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TMPDIR", "PWD",
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
})


def _clean_subprocess_env() -> dict[str, str]:
    """Strip every parent env var not in the allowlist."""
    return {k: v for k, v in os.environ.items() if k in _ENV_ALLOWLIST}


__all__ = [
    "ALL_TOOLS",
    "BASH_TOOL",
    "DISPATCH_AGENT_TOOL",
    "GREP_TOOL",
    "READ_TOOL",
    "ToolResult",
    "ToolSchema",
    "dispatch_tool",
    "execute_bash",
    "execute_dispatch_agent",
    "execute_grep",
    "execute_read",
    "to_gemini_declarations",
    "to_gemini_tool",
]
