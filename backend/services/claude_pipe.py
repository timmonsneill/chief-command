"""Manages a subprocess connection to the Claude Code CLI."""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import AsyncGenerator, Optional

from config.settings import settings

logger = logging.getLogger(__name__)

# Patterns that indicate agent lifecycle events
AGENT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("spawn", re.compile(r"Spawning\s+(Builder|Reviewer|Agent)\b", re.IGNORECASE)),
    ("spawn", re.compile(r"Spawning\s+\w+[\- ]?(builder|reviewer)", re.IGNORECASE)),
    ("complete", re.compile(r"Agent\s+.*?complete", re.IGNORECASE)),
    ("review_start", re.compile(r"Spawning.*?Reviewer", re.IGNORECASE)),
    ("review_done", re.compile(r"Review(er)?\s+.*?(complete|done|finished)", re.IGNORECASE)),
    ("error", re.compile(r"(?:Error|Failed|Exception)\b.*", re.IGNORECASE)),
]


@dataclass
class AgentStatus:
    """Represents the current state of an observed agent."""

    name: str
    role: str  # builder, reviewer, researcher, etc.
    status: str  # running, complete, error
    last_output: str = ""


@dataclass
class ClaudePipe:
    """Async wrapper around a Claude Code CLI subprocess.

    Uses ``claude --print`` for non-interactive one-shot commands and
    falls back to ``claude`` with stdin piping for interactive sessions.
    """

    process: Optional[asyncio.subprocess.Process] = field(default=None, init=False)
    agents: dict[str, AgentStatus] = field(default_factory=dict, init=False)
    _output_queue: asyncio.Queue[str] = field(default_factory=asyncio.Queue, init=False)
    _running: bool = field(default=False, init=False)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, project_dir: Optional[str] = None) -> None:
        """Launch a persistent Claude Code process in interactive mode."""
        cmd = [settings.CLAUDE_CODE_PATH]
        if project_dir:
            cmd += ["--project-dir", project_dir]
        # Use --verbose to get richer output for agent tracking
        cmd += ["--verbose"]

        logger.info("Starting Claude Code: %s", " ".join(cmd))
        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._running = True
        # Start background readers
        asyncio.create_task(self._read_stdout())
        asyncio.create_task(self._read_stderr())

    async def stop(self) -> None:
        """Terminate the Claude Code process."""
        self._running = False
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self.process.kill()
            logger.info("Claude Code process stopped")

    # ------------------------------------------------------------------
    # Sending messages
    # ------------------------------------------------------------------

    async def send_message(self, text: str) -> str:
        """Send a one-shot message using ``claude --print`` and return full output."""
        cmd = [settings.CLAUDE_CODE_PATH, "--print", text]
        logger.info("claude --print: %.120s...", text)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        output = stdout.decode(errors="replace")
        if stderr:
            logger.warning("claude stderr: %s", stderr.decode(errors="replace")[:500])
        self._parse_agent_events(output)
        return output

    async def send_to_interactive(self, text: str) -> None:
        """Write a line into the running interactive process stdin."""
        if not self.process or not self.process.stdin:
            raise RuntimeError("Interactive Claude Code process is not running")
        self.process.stdin.write((text + "\n").encode())
        await self.process.stdin.drain()

    # ------------------------------------------------------------------
    # Streaming output
    # ------------------------------------------------------------------

    async def get_output_stream(self) -> AsyncGenerator[str, None]:
        """Yield output chunks as they arrive from the running process."""
        while self._running or not self._output_queue.empty():
            try:
                chunk = await asyncio.wait_for(self._output_queue.get(), timeout=0.5)
                yield chunk
            except asyncio.TimeoutError:
                continue

    async def send_message_stream(self, text: str) -> AsyncGenerator[str, None]:
        """Send a message via ``claude --print`` and stream output line-by-line."""
        cmd = [settings.CLAUDE_CODE_PATH, "--print", text]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            decoded = line.decode(errors="replace")
            self._parse_agent_events(decoded)
            yield decoded
        await proc.wait()

    # ------------------------------------------------------------------
    # Background readers for interactive mode
    # ------------------------------------------------------------------

    async def _read_stdout(self) -> None:
        if not self.process or not self.process.stdout:
            return
        while self._running:
            line = await self.process.stdout.readline()
            if not line:
                break
            decoded = line.decode(errors="replace")
            self._parse_agent_events(decoded)
            await self._output_queue.put(decoded)
            # Auto-accept permission prompts
            if self._is_permission_prompt(decoded):
                await self._auto_accept()

    async def _read_stderr(self) -> None:
        if not self.process or not self.process.stderr:
            return
        while self._running:
            line = await self.process.stderr.readline()
            if not line:
                break
            decoded = line.decode(errors="replace")
            logger.debug("stderr: %s", decoded.rstrip())
            await self._output_queue.put(decoded)

    # ------------------------------------------------------------------
    # Agent event parsing
    # ------------------------------------------------------------------

    def _parse_agent_events(self, text: str) -> None:
        """Scan output for agent spawn / completion patterns."""
        for event_type, pattern in AGENT_PATTERNS:
            match = pattern.search(text)
            if match:
                agent_name = match.group(1) if match.lastindex else "unknown"
                key = agent_name.lower()
                if event_type in ("spawn", "review_start"):
                    self.agents[key] = AgentStatus(
                        name=agent_name,
                        role=agent_name.lower(),
                        status="running",
                        last_output=text.strip(),
                    )
                    logger.info("Agent spawned: %s", agent_name)
                elif event_type in ("complete", "review_done"):
                    if key in self.agents:
                        self.agents[key].status = "complete"
                        self.agents[key].last_output = text.strip()
                    logger.info("Agent complete: %s", agent_name)
                elif event_type == "error":
                    if key in self.agents:
                        self.agents[key].status = "error"
                        self.agents[key].last_output = text.strip()

    def get_agents(self) -> list[dict[str, str]]:
        """Return serialisable list of tracked agents."""
        return [
            {
                "name": a.name,
                "role": a.role,
                "status": a.status,
                "last_output": a.last_output,
            }
            for a in self.agents.values()
        ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_permission_prompt(text: str) -> bool:
        """Detect Claude Code permission prompts that need auto-accept."""
        lower = text.lower()
        return any(
            phrase in lower
            for phrase in [
                "allow this action",
                "do you want to",
                "press y to",
                "(y/n)",
            ]
        )

    async def _auto_accept(self) -> None:
        """Send 'y' to auto-accept a permission prompt."""
        if self.process and self.process.stdin:
            logger.info("Auto-accepting permission prompt")
            self.process.stdin.write(b"y\n")
            await self.process.stdin.drain()

    async def is_reachable(self) -> bool:
        """Quick check — can we invoke Claude Code at all?"""
        try:
            proc = await asyncio.create_subprocess_exec(
                settings.CLAUDE_CODE_PATH, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            return proc.returncode == 0 and len(stdout) > 0
        except Exception:
            return False


# Module-level singleton
claude_pipe = ClaudePipe()
