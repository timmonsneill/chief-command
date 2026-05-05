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
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from services.cc_session import (
    _bash_segment_ok,
    _bash_split_segments,
    _path_forbidden,
    _path_inside_cwd,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# think_deep — direct Anthropic API escalation (Stage 3 of Live pivot)
# ---------------------------------------------------------------------------
# When the owner asks for spec walkthrough, planning, tradeoff analysis, or
# "think this through carefully," the Live brain (Flash native-audio, fast
# but conversational) escalates to Sonnet (or Opus for the hardest asks)
# via the direct Anthropic API. Forge measured Sonnet TTFT ~0.56s and Opus
# ~0.79s on the prior Anthropic-streamed pipeline — meaningfully faster
# than Pro on Vertex (1-3s warm, 12-14s cold).
#
# Pricing rows already exist in usage_tracker.PRICING_PER_MTOK for both
# Sonnet and Opus; the executor records the turn so the dashboard sees
# escalation cost alongside Live audio cost.
THINK_DEEP_DEFAULT_MODEL: str = "claude-sonnet-4-6"
THINK_DEEP_OPUS_MODEL: str = "claude-opus-4-7"
THINK_DEEP_MAX_TOKENS: int = 2048
THINK_DEEP_TIMEOUT_S: float = 30.0
# Allowlist guards against the Live brain emitting an arbitrary string —
# the schema's enum prevents it server-side, but a defense-in-depth check
# in the executor stops a misbehaving model from spending against an
# unintended pricing row.
_THINK_DEEP_ALLOWED_MODELS: frozenset[str] = frozenset({
    THINK_DEEP_DEFAULT_MODEL,
    THINK_DEEP_OPUS_MODEL,
})


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


THINK_DEEP_TOOL = ToolSchema(
    name="think_deep",
    description=(
        "Escalate to a deeper thinking model when the user asks for spec "
        "walkthrough, planning, tradeoff analysis, architecture, or 'think "
        "this through.' Use this when the user wants careful reasoning, "
        "NOT for quick chat. The output is read back to the user as Chief's "
        "reply. Sonnet is the default (faster); pick Opus only for the "
        "hardest asks. While this runs (~1-2s), say something brief like "
        "'thinking on it' so the silence isn't dead."
    ),
    parameters={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": (
                    "The full question or spec text to think about. Pass "
                    "the owner's words verbatim — don't paraphrase down."
                ),
            },
            "model": {
                "type": "string",
                "enum": [THINK_DEEP_DEFAULT_MODEL, THINK_DEEP_OPUS_MODEL],
                "description": (
                    "Sonnet for most asks (faster); Opus for the hardest "
                    "ones (architecture choices, tradeoff tournaments)."
                ),
            },
        },
        "required": ["prompt"],
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
        # ``scope`` is intentionally NOT exposed to the model. Cwd resolution
        # for the dispatched CC happens at the caller (the Chief WS handler)
        # using the active scope's repo path; if the model could pick a
        # different scope, the cwd → scope pairing would desync and the
        # dispatched CC would land in the wrong sandbox. Caller-provided
        # scope is authoritative.
        "type": "object",
        "properties": {
            "spec": {
                "type": "string",
                "description": "Plain-English description of what the agent should do.",
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
    THINK_DEEP_TOOL,
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
        # Outer turn was cancelled (barge-in). Kill the child fast, then
        # best-effort wait so the OS can reap the process — bounded so a
        # stuck child can't hold the cancel path. Wrapped in shield so
        # cancellation propagating into wait_for() can't leave the kill
        # mid-flight.
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.shield(asyncio.wait_for(proc.wait(), timeout=0.5))
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
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
        # Same shape as Bash cancel: kill the child, wait briefly so the
        # OS can reap, never let the cleanup outlast the cancel path.
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.shield(asyncio.wait_for(proc.wait(), timeout=0.5))
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
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
# think_deep — direct Anthropic API escalation
# ---------------------------------------------------------------------------
async def execute_think_deep(
    prompt: str,
    *,
    scope: str,
    model: str = THINK_DEEP_DEFAULT_MODEL,
) -> ToolResult:
    """Escalate a hard question to Sonnet/Opus via the direct Anthropic API.

    Returns a ``ToolResult`` whose ``output`` is the assistant's reply text
    (the Live brain reads it back to the owner as Chief's spoken reply).
    Never raises — sandbox/auth/timeout errors come back as ``error=True``
    on the ToolResult so the brain can react in a human voice instead of
    crashing the turn.

    The Chief system prompt for the active scope is injected so escalation
    answers stay in-persona (no "I'm Claude" fourth-wall break). We use the
    flat-string variant (``build_chief_system_string``) because the
    Anthropic single-message API takes one ``system`` string, not the cached
    block list — this call is a one-shot, not a streaming conversation, so
    the cache_control optimization wouldn't fire anyway.

    Cost: ``usage_tracker.record_turn`` is invoked with the active session's
    id when ``session_id`` is plumbed by the caller (websockets layer). For
    the unit-test path where no session is open, the executor still returns
    the right output but skips the billing write — escalations only count
    against the daily cap when they happen in a real WS session.

    Cancellation: ``asyncio.timeout(30s)`` caps the wall-clock; outer
    CancelledError (e.g. from a barge-in) propagates without retry.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        return ToolResult(output="error: prompt is required", error=True)

    chosen_model = model if model in _THINK_DEEP_ALLOWED_MODELS else THINK_DEEP_DEFAULT_MODEL
    if chosen_model != model:
        logger.warning(
            "think_deep: requested model=%r not in allowlist; falling back to %s",
            model, chosen_model,
        )

    # Load the API key from settings — pydantic Settings reads it from .env
    # at startup. Failing closed here (rather than raising) keeps a missing
    # key from spilling into the user's voice as a stack trace.
    from config.settings import settings
    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        logger.warning(
            "think_deep: ANTHROPIC_API_KEY not configured; refusing escalation"
        )
        return ToolResult(
            output="error: think_deep unavailable (no Anthropic API key)",
            error=True,
        )

    # Build the Chief system prompt for the active scope. Same builder the
    # Live brain uses, so escalations stay in voice.
    try:
        from services.chief_context import build_chief_system_string
        system_prompt = build_chief_system_string(scope)
    except Exception as exc:
        # Fall back to a minimal Chief-shaped system if the memory load
        # blows up — better to ship an in-character reply than to refuse.
        logger.warning(
            "think_deep: chief_context build failed for scope=%s: %s",
            scope, exc,
        )
        system_prompt = (
            "You are Chief, the owner's AI orchestrator. Be concise, "
            "direct, and useful. The user is escalating to you for "
            "careful reasoning."
        )

    # Lazy SDK import — keeps the module import-safe in environments
    # without ``anthropic`` installed (e.g. unit tests that stub the
    # executor out before it's reached).
    try:
        from anthropic import AsyncAnthropic
    except ImportError as exc:
        logger.exception("think_deep: anthropic SDK import failed: %s", exc)
        return ToolResult(
            output="error: think_deep unavailable (anthropic SDK missing)",
            error=True,
        )

    started = time.monotonic()
    try:
        async with asyncio.timeout(THINK_DEEP_TIMEOUT_S):
            client = AsyncAnthropic(api_key=api_key)
            response = await client.messages.create(
                model=chosen_model,
                max_tokens=THINK_DEEP_MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": prompt}],
            )
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - started
        logger.warning(
            "think_deep: timed out after %.1fs (model=%s prompt_len=%d)",
            elapsed, chosen_model, len(prompt),
        )
        return ToolResult(
            output=f"error: think_deep timed out after {elapsed:.0f}s",
            error=True,
        )
    except asyncio.CancelledError:
        # Outer turn cancelled (barge-in). Don't try to clean up the inflight
        # request — the SDK handles its own teardown via the timeout context
        # manager; just propagate.
        raise
    except Exception as exc:
        # Authentication / rate-limit / 5xx all funnel here. Detail in the
        # log; user-facing text is generic so we don't leak provider error
        # internals into the voice channel.
        logger.exception("think_deep: anthropic call failed: %s", exc)
        return ToolResult(
            output="error: think_deep failed",
            error=True,
        )

    # Pull the text content out of the response. Anthropic 0.39+ returns
    # ``Message.content`` as a list of blocks; the first ``TextBlock``
    # carries the answer for non-streaming single-turn calls.
    text_out = ""
    try:
        for block in response.content:
            block_text = getattr(block, "text", None)
            if isinstance(block_text, str) and block_text:
                text_out = block_text
                break
    except Exception as exc:
        logger.warning("think_deep: response parsing failed: %s", exc)

    if not text_out.strip():
        return ToolResult(
            output="error: think_deep returned empty text",
            error=True,
        )

    # Record the escalation cost so it lands in the daily cap + dashboard.
    # Best-effort: record_turn requires an active session_id, which we
    # don't have in the executor's local frame. The caller (websockets
    # tool-call dispatch) is responsible for surfacing the cost via the
    # standard turn-recording path; here we ALSO write a standalone
    # bookkeeping row tagged with a synthetic session derived from the
    # subject so daily-cap math sums it. If the import / write fails we
    # log + continue — the user-facing reply is the priority.
    try:
        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        cache_creation = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        from services.usage_tracker import record_think_deep_cost
        await record_think_deep_cost(
            model=chosen_model,
            scope=scope,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
            prompt=prompt,
            assistant_text=text_out,
        )
    except Exception as exc:
        logger.warning("think_deep: cost recording failed: %s", exc)

    return ToolResult(output=text_out)


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
    asyncio.CancelledError, which propagates per cancellation contract).

    Deny-by-default scope guard: if ``cwd`` resolves to the user's $HOME
    (the fallback ``llm._resolve_cwd`` returns when no scope-repo is
    configured), refuse tool dispatch entirely. The path-fence machinery
    anchored on cwd would otherwise treat anything under $HOME as in-scope —
    far too permissive. The brain still answers from memory; tools are
    simply unavailable until a real repo is wired into the active scope.

    ``think_deep`` is exempt from the cwd guard: it never touches the
    filesystem, only the Anthropic API + the system-prompt builder. A
    scope without a repo can still escalate to Sonnet for spec-walkthrough
    work (which is exactly when the owner needs it most — in early
    project-bootstrap, before a repo is even wired in).
    """
    if name != "think_deep" and _cwd_is_unsafe_fallback(cwd):
        logger.warning(
            "agent_tools: tool dispatch refused — cwd is $HOME fallback "
            "(name=%s subject=%s scope=%s)",
            name, subject, scope,
        )
        return ToolResult(
            output="error: no project scope set; tool dispatch refused",
            error=True,
        )

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
        # ``scope`` is intentionally not on the schema (see DISPATCH_AGENT_TOOL).
        # If a future model still emits it we ignore the arg — the caller's
        # scope/cwd pairing is authoritative; letting the model pick scope
        # while cwd stays pinned to the current repo is a sandbox-bypass
        # vector.
        return await execute_dispatch_agent(
            spec=args.get("spec") or "",
            cwd=cwd,
            subject=subject,
            scope=scope,
            system_prompt_append=system_prompt_append,
        )
    if name == "think_deep":
        # think_deep doesn't touch the filesystem so cwd containment isn't
        # relevant — but scope IS, because the Chief system prompt loaded
        # for the escalation must match the active scope or the answer
        # comes back in the wrong voice. Caller-supplied scope is
        # authoritative for the same sandbox-bypass reason as dispatch_agent.
        return await execute_think_deep(
            prompt=args.get("prompt") or "",
            scope=scope,
            model=args.get("model") or THINK_DEEP_DEFAULT_MODEL,
        )
    return ToolResult(output=f"error: unknown tool {name!r}", error=True)


def _cwd_is_unsafe_fallback(cwd: Path) -> bool:
    """True when ``cwd`` is the user's $HOME — the unsafe deny-by-default
    fallback used by ``llm._resolve_cwd`` when no scope repo is configured.

    Compared on resolved paths so a relative ``Path.home()`` and an absolute
    ``/Users/foo`` both match. The check is conservative: we treat any
    cwd that resolves to exactly $HOME as the fallback. Any actual scope
    repo lives at ``$HOME/Desktop/<project>`` or similar, which is strictly
    deeper than $HOME and passes through.
    """
    try:
        return cwd.resolve() == Path.home().resolve()
    except (OSError, RuntimeError):
        # If resolve fails (e.g. cwd doesn't exist), fail safe — deny.
        return True


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
    "THINK_DEEP_TOOL",
    "THINK_DEEP_DEFAULT_MODEL",
    "THINK_DEEP_OPUS_MODEL",
    "THINK_DEEP_TIMEOUT_S",
    "ToolResult",
    "ToolSchema",
    "dispatch_tool",
    "execute_bash",
    "execute_dispatch_agent",
    "execute_grep",
    "execute_read",
    "execute_think_deep",
    "to_gemini_declarations",
    "to_gemini_tool",
]
