"""Task dispatcher — spawn `claude` CLI and stream output back.

Env is an explicit allowlist, not a blocklist: only a small set of process /
locale / path variables survive into the subprocess env. Every other secret
from the parent shell (AWS_*, GITHUB_TOKEN, OPENAI_API_KEY, ANTHROPIC_*, etc.)
is stripped. This shuts the door on a prompt-injected `claude` invocation
exfiltrating the owner's credential belt.

One running task per websocket session. A second dispatch() on the same
session while a task is live raises TaskAlreadyRunning — the caller (Chief) is
expected to surface that to the user.

Each TaskHandle carries a ``max_runtime_s`` watchdog (default 1800s / 30 min)
so a wedged subprocess doesn't hold a session forever.

Outbound WS frames: the dispatcher is a library, not a WS endpoint. Callers
pass two callbacks — ``on_output(text, stream)`` for each line and
``on_complete(exit_code, summary)`` on termination. Both are awaited; a single
writer inside the caller's code path is the usual pattern (see
``serialized_sender`` helper below) since FastAPI's WebSocket does NOT
guarantee concurrent-write safety — stdout/stderr pumps racing on ws.send_json
can corrupt frames. Chief's integration should wrap its ws.send_json in
``serialized_sender`` before handing the result to ``dispatch()``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


# Number of stdout lines to retain in memory per handle for "status" summaries.
# Plenty for a 1-sentence Haiku narration; bounded so long tasks don't OOM.
_STDOUT_BUFFER_LINES = 500

# Default command. Can be overridden per-test via TaskDispatcher(cmd=(..)) but
# production always spawns the real claude CLI. Path resolved at call time so
# env changes (PATH tweaks) are honored without module reload.
_DEFAULT_CLAUDE_BIN = "claude"

# Hard cap on task_spec length. Dispatch() rejects anything longer. 8KB is
# plenty for a natural-language "refactor X and add tests for Y" instruction
# and keeps an abusive / misclassified monster payload out of argv.
_MAX_TASK_SPEC_BYTES = 8000

# Default watchdog — 30 minutes covers every realistic task-dispatch on local
# Claude Code; longer-running work should stream updates and not need a single
# invocation that runs for hours.
_DEFAULT_MAX_RUNTIME_S = 1800.0


# Env allowlist. Everything else is stripped before the subprocess is spawned.
# This is deliberately minimal — PATH for resolvers, HOME / USER for tools
# that need an identity, SHELL for shebang-less invocations, LANG / LC_* for
# unicode, TERM for tools that query it, TMPDIR for intermediate files,
# XDG_* for standard-config-location discovery, PWD because subprocess_exec
# sets it but some shells still read it.
_ENV_ALLOWLIST = {
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TMPDIR",
    "PWD",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "XDG_DATA_HOME",
}


OnOutput = Callable[[str, str], Awaitable[None]]        # (text, stream_name)
OnComplete = Callable[[int, str], Awaitable[None]]      # (exit_code, summary)


class TaskAlreadyRunning(Exception):
    """Raised when dispatch() is called while a task for the same session is live."""


@dataclass
class TaskHandle:
    session_id: str
    task_spec: str
    repo: Path
    started_at: datetime
    proc: asyncio.subprocess.Process
    # task_id is the unique key used in outbound WS frames so a late output from
    # a prior task can't be attributed to a newer one. We use the ISO timestamp
    # of started_at, which is unique per dispatch (monotonic within a session).
    task_id: str = ""
    max_runtime_s: float = _DEFAULT_MAX_RUNTIME_S
    # Bounded deque so a chatty subprocess can't blow memory.
    stdout_lines: deque[str] = field(
        default_factory=lambda: deque(maxlen=_STDOUT_BUFFER_LINES)
    )
    exit_code: Optional[int] = None
    cancelled: bool = False
    # Reader tasks kept so we can await them on completion / cancellation.
    _reader_tasks: list[asyncio.Task] = field(default_factory=list)
    _waiter_task: Optional[asyncio.Task] = field(default=None)

    def __post_init__(self) -> None:
        if not self.task_id:
            self.task_id = self.started_at.isoformat()

    @property
    def pid(self) -> int:
        return self.proc.pid

    @property
    def is_running(self) -> bool:
        return self.exit_code is None and not self.cancelled and (self.proc.returncode is None)


def serialized_sender(
    send_json: Callable[[dict], Awaitable[None]],
) -> tuple[Callable[[dict], Awaitable[None]], Callable[[], Awaitable[None]]]:
    """Wrap a raw WebSocket ``send_json`` in a single-writer queue.

    Returns ``(enqueue, drain)``.

    ``enqueue(frame)`` puts a frame on the queue without awaiting the socket
    directly; a background worker is lazily spawned on the first call and
    performs ordered ``send_json`` calls one at a time. This is how Chief's
    glue should funnel stdout / stderr pumps + status + complete frames so
    they cannot interleave mid-frame on a concurrent WebSocket write.

    ``drain()`` awaits the queue's completion — call before closing the WS.

    The pattern matches the existing ``tts_queue`` in ``_run_llm_turn`` inside
    websockets.py. We expose it here so the dispatcher library is
    self-contained and the pattern doesn't have to be re-implemented per call
    site.
    """
    queue: asyncio.Queue[Optional[dict]] = asyncio.Queue()
    worker_task: dict[str, Optional[asyncio.Task]] = {"task": None}

    async def _worker() -> None:
        while True:
            frame = await queue.get()
            if frame is None:
                return
            try:
                await send_json(frame)
            except Exception:
                logger.exception("serialized_sender: send_json raised — dropping frame")

    async def enqueue(frame: dict) -> None:
        if worker_task["task"] is None:
            worker_task["task"] = asyncio.create_task(_worker())
        await queue.put(frame)

    async def drain() -> None:
        if worker_task["task"] is None:
            return
        await queue.put(None)
        try:
            await worker_task["task"]
        except Exception:
            logger.exception("serialized_sender: drain worker raised")

    return enqueue, drain


class TaskDispatcher:
    """Manages one spawned task per session."""

    def __init__(
        self,
        *,
        claude_bin: Optional[str] = None,
        extra_args: Optional[tuple[str, ...]] = None,
    ) -> None:
        # Resolve the binary now so a caller can override for tests.
        self._claude_bin = claude_bin or _DEFAULT_CLAUDE_BIN
        self._extra_args = extra_args
        self._handles: dict[str, TaskHandle] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ env
    @staticmethod
    def _clean_env() -> dict[str, str]:
        """Build a subprocess env using an explicit allowlist.

        Only PATH / HOME / USER / locale / TERM / TMPDIR / XDG_* survive. Every
        other env var — including ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN,
        AWS_*, GITHUB_TOKEN, OPENAI_API_KEY, and anything else in the owner's
        shell — is stripped. This means:

        1. The spawned `claude` CLI falls back to the Max subscription (no
           API key available, so it uses the Claude Code login).
        2. A prompt-injected sub-invocation (`curl $GITHUB_TOKEN ...`) can't
           exfil credentials — they literally aren't present.
        """
        return {k: v for k, v in os.environ.items() if k in _ENV_ALLOWLIST}

    # ---------------------------------------------------------------- dispatch
    async def dispatch(
        self,
        session_id: str,
        task_spec: str,
        repo: Path,
        on_output: OnOutput,
        on_complete: OnComplete,
        *,
        command: Optional[list[str]] = None,
        max_runtime_s: float = _DEFAULT_MAX_RUNTIME_S,
    ) -> TaskHandle:
        """Spawn a task, start streaming output, return the handle.

        ``command`` is an override for the argv used to spawn the subprocess.
        Production callers should omit it; tests supply a synthetic argv like
        ``["/bin/echo", "hello"]`` to avoid depending on the real CLI.

        ``max_runtime_s`` is a hard wall-clock limit; when exceeded, the
        subprocess is SIGKILLed and the task is marked cancelled.
        """
        # Length cap applied up front — rejects obvious abuse without grabbing
        # the lock or touching any state.
        if len(task_spec) > _MAX_TASK_SPEC_BYTES:
            raise ValueError(
                f"task_spec too long ({len(task_spec)} bytes, "
                f"max {_MAX_TASK_SPEC_BYTES})"
            )

        async with self._lock:
            existing = self._handles.get(session_id)
            if existing is not None and existing.is_running:
                raise TaskAlreadyRunning(
                    f"session {session_id} already has a running task (pid={existing.pid})"
                )
            if not repo.exists():
                raise FileNotFoundError(f"repo path does not exist: {repo}")

            env = self._clean_env()
            argv = command if command is not None else [
                self._claude_bin,
                "--print",
                "--model",
                "claude-opus-4-7",
                # End-of-options marker. Without this, a task_spec starting
                # with "-" or "--" (e.g. a classifier-emitted "--help ...")
                # would be interpreted by commander.js as a CLI flag rather
                # than the prompt positional. Vera HIGH finding.
                "--",
                task_spec,
            ]
            if command is None and self._extra_args:
                # Inject extra_args before the flags so the "--" terminator
                # stays adjacent to task_spec.
                argv = [argv[0], *self._extra_args, *argv[1:]]

            logger.info(
                "dispatcher: spawning session=%s pid-parent argv0=%r cwd=%s max_runtime_s=%.0f",
                session_id,
                argv[0],
                repo,
                max_runtime_s,
            )

            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(repo),
                env=env,
            )

            handle = TaskHandle(
                session_id=session_id,
                task_spec=task_spec,
                repo=repo,
                started_at=datetime.now(timezone.utc),
                proc=proc,
                max_runtime_s=max_runtime_s,
            )
            self._handles[session_id] = handle

            loop = asyncio.get_running_loop()
            stdout_task = loop.create_task(
                self._pump(handle, proc.stdout, "stdout", on_output)
            )
            stderr_task = loop.create_task(
                self._pump(handle, proc.stderr, "stderr", on_output)
            )
            handle._reader_tasks.extend([stdout_task, stderr_task])

            handle._waiter_task = loop.create_task(
                self._wait_and_complete(handle, on_complete)
            )

            return handle

    async def _pump(
        self,
        handle: TaskHandle,
        stream: Optional[asyncio.StreamReader],
        stream_name: str,
        on_output: OnOutput,
    ) -> None:
        if stream is None:
            return
        try:
            while True:
                chunk = await stream.readline()
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                if stream_name == "stdout":
                    handle.stdout_lines.append(text.rstrip("\n"))
                try:
                    await on_output(text, stream_name)
                except Exception:
                    logger.exception(
                        "dispatcher: on_output callback raised for session=%s",
                        handle.session_id,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "dispatcher: pump crashed (session=%s stream=%s)",
                handle.session_id,
                stream_name,
            )

    async def _wait_and_complete(
        self,
        handle: TaskHandle,
        on_complete: OnComplete,
    ) -> None:
        """Wait for the subprocess to exit, enforcing max_runtime_s.

        If the wall clock runs out we SIGKILL the subprocess and surface the
        resulting exit code (typically -9) — on_complete still fires, so the
        caller can narrate a clean "task exceeded limit" message. Handle is
        marked cancelled so a subsequent dispatch() on the same session is
        allowed.
        """
        try:
            try:
                exit_code = await asyncio.wait_for(
                    handle.proc.wait(), timeout=handle.max_runtime_s
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "dispatcher: task exceeded max_runtime_s=%.0f, killing session=%s pid=%s",
                    handle.max_runtime_s,
                    handle.session_id,
                    handle.proc.pid,
                )
                try:
                    handle.proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    exit_code = await asyncio.wait_for(handle.proc.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.error(
                        "dispatcher: SIGKILL wait timed out for session=%s pid=%s",
                        handle.session_id,
                        handle.proc.pid,
                    )
                    exit_code = -9
                handle.cancelled = True
        except asyncio.CancelledError:
            raise
        # Drain readers so any final output lands before on_complete fires.
        for t in handle._reader_tasks:
            try:
                await t
            except Exception:
                logger.debug("dispatcher: reader task ended with exception", exc_info=True)
        handle.exit_code = exit_code
        summary = self.summarize(handle)
        try:
            await on_complete(exit_code, summary)
        except Exception:
            logger.exception(
                "dispatcher: on_complete callback raised for session=%s",
                handle.session_id,
            )

    # ----------------------------------------------------------------- cancel
    async def cancel(self, session_id: str) -> bool:
        """Stop the running task for ``session_id``.

        Returns True if we actually killed something, False otherwise.
        Uses terminate() -> wait 5s -> kill() escalation.

        Lock scope: we acquire the dispatcher lock only long enough to look up
        the handle and flip ``cancelled = True``. The terminate/wait/kill
        escalation takes up to 7 seconds and we do NOT want to block a
        concurrent dispatch() on a *different* session for that long.
        """
        async with self._lock:
            handle = self._handles.get(session_id)
            if handle is None or not handle.is_running:
                return False
            handle.cancelled = True
            proc = handle.proc
        # Lock released — now do the slow terminate/wait/kill escalation.
        try:
            proc.terminate()
        except ProcessLookupError:
            return False
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning(
                "dispatcher: terminate() timeout for session=%s pid=%s — sending SIGKILL",
                session_id,
                proc.pid,
            )
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                logger.error(
                    "dispatcher: SIGKILL wait timed out for session=%s pid=%s",
                    session_id,
                    proc.pid,
                )
        return True

    # ------------------------------------------------------------------ read
    def get_handle(self, session_id: str) -> Optional[TaskHandle]:
        return self._handles.get(session_id)

    def summarize(self, handle: TaskHandle, max_lines: int = 50) -> str:
        """Return last ``max_lines`` of stdout joined with newlines.

        Chief pipes this through Haiku to narrate "how's it going?" in one
        sentence, so the raw text just needs to be representative.
        """
        if not handle.stdout_lines:
            return ""
        if max_lines <= 0:
            lines = list(handle.stdout_lines)
        else:
            # deque has no negative slicing — pull the tail explicitly.
            lines = list(handle.stdout_lines)[-max_lines:]
        return "\n".join(lines).strip()

    # ------------------------------------------------------------------ misc
    def active_sessions(self) -> list[str]:
        return [sid for sid, h in self._handles.items() if h.is_running]

    def claude_bin_available(self) -> bool:
        return shutil.which(self._claude_bin) is not None


# Module-level singleton — Chief's hook in websockets.py should grab this.
dispatcher = TaskDispatcher()


# Re-export so callers don't have to touch private names.
ENV_ALLOWLIST: frozenset[str] = frozenset(_ENV_ALLOWLIST)
MAX_TASK_SPEC_BYTES: int = _MAX_TASK_SPEC_BYTES
DEFAULT_MAX_RUNTIME_S: float = _DEFAULT_MAX_RUNTIME_S


__all__ = [
    "TaskDispatcher",
    "TaskHandle",
    "TaskAlreadyRunning",
    "dispatcher",
    "serialized_sender",
    "ENV_ALLOWLIST",
    "MAX_TASK_SPEC_BYTES",
    "DEFAULT_MAX_RUNTIME_S",
]
