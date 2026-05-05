"""Per-(subject, scope) persistent ClaudeSDKClient pool — Chief's brain.

Each (subject, scope) pair gets ONE long-lived ClaudeSDKClient connection that
sticks around between user turns. The first turn after a scope switch or a
process restart pays the spawn tax (~1s); every follow-up runs against the
already-warm subprocess.

What this module owns:
  * Spawn / health-check / SIGINT (interrupt) / SIGTERM (teardown) lifecycle
  * Idle-timeout teardown (default 30 min)
  * Crash detection + auto-respawn-with-resume on the next turn
  * Crash-loop guard: 3 spawns within 10 s = stop respawning, surface error
  * Subprocess env scrubbing via the same allowlist the dispatcher uses
  * cwd containment via repo_map.get_repo_path
  * Per-tool-call sandboxing via the SDK's ``can_use_tool`` callback
    (Vera 2026-05-04 CRIT — strict allow/deny by inspecting actual tool input,
    NOT by the static allowed_tools/disallowed_tools pattern lists)

What this module does NOT own:
  * WS frames — the caller maps ParsedEvent → WS frames
  * TTS sentence flushing — caller does that on TextDelta
  * Cost tracking — caller writes turn rows; Chief's brain LLM cost is $0
    on Max plan so we report 0 token counts and let usage_tracker zero out

Cancellation contract (preserves the legacy ``stream_turn`` invariant):
  * The outer task can be cancelled at any time (barge-in, supersede, WS drop)
  * On cancel: call client.interrupt() so SDK stops generating, then drain
    receive_response() until ResultMessage so the next turn starts clean
  * No tokens emitted after the cancel point — the caller's send_token
    callback sees the cancellation BEFORE it can leak the next delta
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from services import cc_output_parser
from services.cc_output_parser import (
    ParsedError,
    ParsedEvent,
    SessionInit,
    TurnComplete,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tuning knobs — module constants so tests can monkeypatch.
# ---------------------------------------------------------------------------
IDLE_TIMEOUT_S: float = 30 * 60.0          # 30 min — teardown after this long without a turn
CC_IDLE_SWEEP_INTERVAL_S: float = 60.0     # Period between background idle-sweep runs
CRASH_LOOP_WINDOW_S: float = 10.0          # Spawns within this window count toward the loop guard
CRASH_LOOP_MAX_SPAWNS: int = 3             # 3 spawns in 10s → stop respawning
INTERRUPT_DRAIN_TIMEOUT_S: float = 3.0     # Wait at most this long for ResultMessage after interrupt
TEARDOWN_TIMEOUT_S: float = 5.0            # Wait at most this long for clean SDK disconnect
SPAWN_TIMEOUT_S: float = 15.0              # Hard ceiling on initial connect — surfaces a hung CC fast


# Read-only Bash subcommand allowlist. Mirror what the dispatch spec requires.
# Subcommand patterns use the SDK's "Bash(<prefix>:*)" syntax — wildcard suffix
# matches any argv tail starting with that prefix.
_READ_ONLY_TOOLS: tuple[str, ...] = (
    "Read",
    "Grep",
    "Glob",
    "Bash(git status:*)",
    "Bash(git log:*)",
    "Bash(git diff:*)",
    "Bash(git branch:*)",
    "Bash(git worktree:*)",
    "Bash(git show:*)",
    "Bash(git rev-parse:*)",
    "Bash(git rev-list:*)",
    "Bash(ls:*)",
    "Bash(cat:*)",
    "Bash(head:*)",
    "Bash(tail:*)",
    "Bash(wc:*)",
    "Bash(find:*)",
)

# Tools we explicitly DENY even if a future config tries to allow them — the
# dispatch is read-only Phase 1+; Edit/Write/MultiEdit/NotebookEdit must stay
# locked.
#
# IMPORTANT (Forge 2026-05-04): we learned the hard way that
# ``permission_mode="dontAsk"`` + a ``Bash(<prefix>:*)`` allowlist is NOT a
# strict per-subcommand check. The CLI honors the disallowed_tools list but
# treats ANY ``Bash(...)`` entry in allowed_tools as essentially "Bash" — so
# without explicit deny patterns, the chat brain happily executes
# ``Bash(echo 'x' >> file)`` and similar redirects. The list below is the
# enforced backstop: any common write/network/exec subcommand gets denied
# even though it isn't in the allow list.
#
# Every entry here was added because we either OBSERVED the chat brain take
# this action (echo + redirect → file write — modified the prod README on
# 2026-05-04) or judged it close enough to obvious to deny preemptively.
_DISALLOWED_TOOLS: tuple[str, ...] = (
    "Edit",
    "Write",
    "MultiEdit",
    "NotebookEdit",
    # Common file-mutation subcommands — block by command prefix so the chat
    # brain can't append-via-redirect or truncate via tee.
    "Bash(echo:*)",
    "Bash(printf:*)",
    "Bash(tee:*)",
    "Bash(sed:*)",
    "Bash(awk:*)",
    "Bash(perl:*)",
    "Bash(python:*)",
    "Bash(python3:*)",
    "Bash(ruby:*)",
    "Bash(node:*)",
    "Bash(rm:*)",
    "Bash(mv:*)",
    "Bash(cp:*)",
    "Bash(ln:*)",
    "Bash(touch:*)",
    "Bash(mkdir:*)",
    "Bash(rmdir:*)",
    "Bash(chmod:*)",
    "Bash(chown:*)",
    "Bash(dd:*)",
    # Network / exec — chat brain has no business making outbound calls.
    "Bash(curl:*)",
    "Bash(wget:*)",
    "Bash(ssh:*)",
    "Bash(scp:*)",
    "Bash(rsync:*)",
    "Bash(nc:*)",
    "Bash(netcat:*)",
    # Mutating git — only the specific read-only git subcommands in the
    # allowlist should be reachable. These deny any "destructive" git verbs
    # we can think of.
    "Bash(git push:*)",
    "Bash(git commit:*)",
    "Bash(git checkout:*)",
    "Bash(git reset:*)",
    "Bash(git rebase:*)",
    "Bash(git merge:*)",
    "Bash(git pull:*)",
    "Bash(git fetch:*)",
    "Bash(git stash:*)",
    "Bash(git tag:*)",
    "Bash(git add:*)",
    "Bash(git rm:*)",
    "Bash(git mv:*)",
    "Bash(git clean:*)",
    # Package managers — never legit from chat brain.
    "Bash(brew:*)",
    "Bash(pip:*)",
    "Bash(pip3:*)",
    "Bash(npm:*)",
    "Bash(yarn:*)",
    "Bash(pnpm:*)",
    "Bash(gem:*)",
    "Bash(cargo:*)",
    "Bash(go:*)",
    # Long-running / exec helpers.
    "Bash(make:*)",
    "Bash(sh:*)",
    "Bash(bash:*)",
    "Bash(zsh:*)",
    "Bash(env:*)",
    "Bash(eval:*)",
    "Bash(exec:*)",
    "Bash(xargs:*)",
)

# Env allowlist — same shape as dispatcher._ENV_ALLOWLIST. Importing it would
# reach into a private name; duplicating ~15 lines is cheaper than the coupling.
_ENV_ALLOWLIST: frozenset[str] = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL",
    "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TMPDIR", "PWD",
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
})


def _clean_env() -> dict[str, str]:
    """Strip every parent env var not in the allowlist.

    Stops a prompt-injected CC tool call from exfiltrating ANTHROPIC_API_KEY,
    GITHUB_TOKEN, AWS_*, etc. The CC subprocess inherits ONLY locale + path
    + home — same posture as the existing ``TaskDispatcher``.
    """
    return {k: v for k, v in os.environ.items() if k in _ENV_ALLOWLIST}


# ---------------------------------------------------------------------------
# Tool sandbox — ``can_use_tool`` callback (Vera 2026-05-04 CRIT-2)
#
# The bundled ``claude`` CLI doesn't strictly enforce ``Bash(<prefix>:*)`` allow
# patterns — it only reliably honours the deny list. Forge ended up layering
# 40+ deny patterns just to plug the holes we observed (echo + redirect, etc.).
#
# The real fix is the SDK's ``can_use_tool`` callback: every tool invocation
# is inspected mid-turn, the actual ``command`` / ``path`` strings are
# matched against allow/deny rules, and we explicitly approve or deny.
#
# The deny list (``_DISALLOWED_TOOLS`` below) stays as defense-in-depth. The
# read-only allow list is downgraded to an advertised hint (``allowed_tools``)
# rather than the enforcement boundary.
# ---------------------------------------------------------------------------

# Path patterns that must be denied even when resolved INSIDE cwd (secrets,
# env files). Match against ``Path.name`` AND the path-string with a
# fnmatch-style glob test.
_FORBIDDEN_PATH_RE = re.compile(
    r"(^|/)\.env($|\.|/)|"           # .env, .env.local, .env/, etc
    r"(^|/)credentials(\.|$|/)|"     # credentials, credentials.json
    r"\.key($|/)|"                   # *.key
    r"secret",                        # any "secret" substring
    re.IGNORECASE,
)

# Bash subcommand allowlist — the FIRST argv token of each segment. We split
# the command on shell separators (`;` `&&` `||` `|`) and require each segment
# to start with one of these names. Bare names; we further narrow git below.
_BASH_ALLOWED_LEADING: frozenset[str] = frozenset({
    "git",       # narrowed to read-only verbs in _bash_segment_ok
    "ls",
    "cat",
    "head",
    "tail",
    "wc",
    "find",      # narrowed: no -exec / -delete / -ok
    "grep",
    "echo",      # standalone echo (no redirects — see below) is fine for "echo $PATH"
    "pwd",
    "true",
    "false",
})

# Read-only git verbs. Anything not in this set is rejected (push/commit/etc).
_GIT_READ_ONLY_VERBS: frozenset[str] = frozenset({
    "status",
    "log",
    "diff",
    "branch",
    "worktree",
    "show",
    "rev-parse",
    "rev-list",
    "blame",
    "config",     # read-only via --get; we additionally reject --add/--unset below
    "remote",     # read-only verbs only — narrowed below
    "ls-files",
    "ls-tree",
    "cat-file",
    "describe",
    "tag",        # read-only listing only ("git tag" with no args lists)
})

# Shell metacharacters that imply redirection or process substitution. ANY
# of these in a Bash command segment → reject (we don't try to parse them).
_BASH_FORBIDDEN_CHARS: tuple[str, ...] = (">", "<", "$(", "`", "&>", ">>", "<<")


def _bash_split_segments(command: str) -> list[str]:
    """Split a bash command on the top-level pipe / and-or / list separators.

    Naive split — does NOT handle quoted separators. The callback already
    rejects backticks and ``$(...)``; the residual risk is something like
    ``ls "a;b"`` which would incorrectly split. That command's leading token
    is still ``ls`` so it would pass per-segment validation; we accept that
    minor over-permissiveness given splitting is purely an enforcement aid.
    """
    # Replace multi-char tokens with a single sentinel so a regex split is sane.
    s = command
    for sep in ("&&", "||", ";", "|"):
        s = s.replace(sep, "\x01")
    return [seg.strip() for seg in s.split("\x01") if seg.strip()]


# Bash leading commands whose positional arguments are filesystem paths that
# MUST be validated against cwd. ``ls`` and ``find`` accept zero-or-more paths
# (default to ".") so an empty positional list is fine. ``cat``/``head``/
# ``tail``/``wc`` require at least one positional and every positional must be
# a path under cwd. ``grep`` is special-cased: the FIRST positional after the
# flag scan is the pattern (not a path), the rest are paths.
#
# Forge 2026-05-04 CRIT — without this layer, an attacker who got ``Bash``
# could run ``cat ~/.ssh/id_rsa`` or ``find / -name '*.key'`` even though
# Read/Grep/Glob were path-fenced. The Bash leg was missing the same fence.
_BASH_PATH_VALIDATING_LEADERS: frozenset[str] = frozenset({
    "cat", "head", "tail", "wc", "ls", "find", "grep",
})


def _is_path_like(token: str) -> bool:
    """Heuristic: token is a positional path arg (not a flag).

    Flags start with '-'. Numeric arguments to ``head -100`` / ``tail -n 50``
    look like flags. We accept anything else as a path candidate. The caller
    feeds candidates into ``_path_inside_cwd`` which is the actual fence; this
    function just filters obvious non-paths to keep deny messages readable.
    """
    if not token:
        return False
    if token.startswith("-"):
        return False
    return True


def _validate_bash_paths(parts: list[str], cwd: Path) -> tuple[bool, str]:
    """For path-taking leaders, ensure every positional path is inside cwd
    and not a forbidden secrets pattern.

    Returns (ok, reason). ok=True on no path args (e.g. ``ls`` with no args
    defaults to cwd) or every path resolved-and-inside-cwd-and-not-forbidden.
    """
    leading = parts[0]
    args = parts[1:]

    # Strip flags. For ``head -n 100 file.txt`` we want just ['file.txt'].
    # We don't fully parse each tool's flag spec; we approximate by skipping
    # any token starting with '-' and the immediately-following token if the
    # flag is one of the well-known "takes-a-value" forms. Over-skipping is
    # safe (we'd just miss a path that follows a flag-taking-value, which the
    # tool would error on anyway).
    flags_taking_value = {
        "-n",          # head/tail/grep -n
        "-c",          # head/tail/wc -c
        "--bytes",     # head/tail/wc
        "--lines",
        "-A", "-B", "-C",  # grep context
        "--max-count", "-m",
        "--include", "--exclude", "--exclude-dir",
        "--name",      # find -name
        "--iname",
        "-name", "-iname", "-path", "-ipath", "-type", "-size",
        "-mtime", "-mmin", "-newer", "-not", "-and", "-or", "-o", "-a",
        "-maxdepth", "-mindepth", "-regex", "-iregex",
    }

    positionals: list[str] = []
    skip_next = False
    for tok in args:
        if skip_next:
            skip_next = False
            continue
        if tok in flags_taking_value:
            skip_next = True
            continue
        if tok.startswith("-") or tok.startswith("!") or tok.startswith("("):
            # bare flag or find logical operator — skip
            continue
        if not _is_path_like(tok):
            continue
        positionals.append(tok)

    # ``grep`` first positional is the pattern, not a path.
    if leading == "grep" and positionals:
        positionals = positionals[1:]

    # ``ls`` and ``find`` are fine with zero positionals (default cwd / ".").
    # Other path-taking leaders REQUIRE at least one positional path that
    # lands inside cwd — otherwise they'd default to stdin (cat) or error.
    # We don't enforce a minimum count; if zero positionals slip past us the
    # subprocess just hangs or errors, no security risk.

    for path_arg in positionals:
        # Vera 2026-05-04: ``$`` in a path arg is a shell variable. Bash will
        # expand ``$HOME``, ``${HOME}``, ``$TMPDIR`` etc. in the subprocess,
        # but our static cwd check sees the literal string and treats it as
        # a relative subdir of cwd — same threat class as the tilde bypass.
        # ``$(`` (command substitution) is already caught by the segment-level
        # forbidden-char list; this catches the bare-variable form.
        # Pure-deny on ``$`` is cleaner than expand-then-resolve, which would
        # introduce env-allowlist ordering bugs.
        if "$" in path_arg:
            return False, f"shell variable in path: {path_arg!r}"
        resolved = _path_inside_cwd(path_arg, cwd)
        if resolved is None:
            return False, f"path outside cwd: {path_arg!r}"
        if _path_forbidden(resolved):
            return False, f"forbidden path: {path_arg!r}"

    return True, ""


def _bash_segment_ok(segment: str, cwd: Optional[Path] = None) -> tuple[bool, str]:
    """Validate one bash segment. Returns (ok, reason).

    Reason is empty on ok; populated with a short human-readable message on
    failure so the callback can include it in the deny payload (used for
    debugging / unit-test assertions).

    ``cwd`` is required for full validation of path-taking leaders (cat,
    head, tail, wc, ls, find, grep). If None (legacy callers / tests of
    pure-syntax rejections), path validation is skipped — but the leading
    command + forbidden-char + git-verb checks all still run.
    """
    # Reject any forbidden char anywhere in the segment.
    for bad in _BASH_FORBIDDEN_CHARS:
        if bad in segment:
            return False, f"forbidden token {bad!r}"

    try:
        parts = shlex.split(segment, posix=True)
    except ValueError as exc:
        return False, f"unparseable: {exc}"
    if not parts:
        return False, "empty segment"

    leading = parts[0]
    if leading not in _BASH_ALLOWED_LEADING:
        return False, f"command {leading!r} not in allowlist"

    # Tighten leaders that need it.
    if leading == "git":
        if len(parts) < 2:
            return False, "bare 'git' not allowed"
        verb = parts[1]
        if verb not in _GIT_READ_ONLY_VERBS:
            return False, f"git verb {verb!r} not in read-only allowlist"
        # config: only --get / --get-all / --list / --show-origin
        if verb == "config":
            sub = parts[2:]
            allowed = {"--get", "--get-all", "--list", "--show-origin", "-l"}
            if not any(s in allowed for s in sub):
                return False, "git config requires read-only flag"
            if any(s in {"--add", "--unset", "--unset-all", "--replace-all"} for s in sub):
                return False, "git config write flag not allowed"
        # remote: 'show', '-v', or 'get-url' only
        if verb == "remote":
            sub = parts[2:]
            if sub and sub[0] not in {"-v", "show", "get-url"}:
                return False, f"git remote subverb {sub[0]!r} not allowed"

    if leading == "find":
        # Reject any flag that runs an external program or mutates the FS.
        for tok in parts[1:]:
            if tok in {"-exec", "-execdir", "-delete", "-ok", "-okdir", "-fprint", "-fprintf"}:
                return False, f"find flag {tok!r} not allowed"

    if leading == "echo":
        # Standalone echo is harmless; we already reject any segment with `>`,
        # `>>`, `|` (handled by the segment split + forbidden chars). So an
        # echo that lands here can only be ``echo something``.
        pass

    # Forge CRIT (2026-05-04 round 3): every path argument to a path-taking
    # leader must resolve inside cwd and not match the secrets pattern. The
    # Read/Grep/Glob path is fenced at the SDK level, but Bash arguments are
    # raw filesystem paths and were previously unrestricted — letting Chief
    # ``cat ~/.ssh/id_rsa`` even though the Read tool blocked it.
    if cwd is not None and leading in _BASH_PATH_VALIDATING_LEADERS:
        ok, reason = _validate_bash_paths(parts, cwd)
        if not ok:
            return False, reason

    return True, ""


def _path_inside_cwd(path_arg: str, cwd: Path) -> Optional[Path]:
    """Resolve ``path_arg`` and return it iff it's inside ``cwd``.

    Returns the resolved Path on success, None on rejection. ``path_arg``
    can be relative (resolved against cwd) or absolute. Symlinks are
    followed by ``Path.resolve()`` — the SDK already had its own cwd fence,
    we layer on top.

    Forge 2026-05-04: ``~`` in argv to a Bash subprocess is expanded by the
    shell to ``$HOME``, so ``cat ~/.ssh/id_rsa`` reads ``/Users/$USER/.ssh/
    id_rsa`` in the subprocess. We must expand the tilde before the cwd
    containment check or the comparison runs on the literal ``~/...`` string,
    which trivially "lives under" cwd because Python treats ``~`` as a normal
    relative-path component.
    """
    try:
        # os.path.expanduser handles ~/foo and ~user/foo. Pure-Path equivalents
        # (Path.expanduser) would do the same; we use os.path for symmetry
        # with the bash subprocess's own expansion.
        expanded = os.path.expanduser(path_arg)
        p = Path(expanded)
        if not p.is_absolute():
            p = (cwd / p)
        resolved = p.resolve()
        cwd_resolved = cwd.resolve()
        # ``relative_to`` raises ValueError if not a subpath. We keep cwd
        # itself acceptable (relative_to("/x", "/x") returns ``Path('.')``).
        resolved.relative_to(cwd_resolved)
        return resolved
    except (ValueError, OSError):
        return None


def _path_forbidden(resolved: Path) -> bool:
    """True iff the resolved path looks like a secret/credential file."""
    return bool(_FORBIDDEN_PATH_RE.search(str(resolved)))


def make_can_use_tool(cwd: Path):
    """Build a ``can_use_tool`` callback bound to a specific cwd.

    Async per the SDK's contract. Returns ``PermissionResultAllow`` /
    ``PermissionResultDeny``. We import the SDK types lazily so unit tests
    can swap a fake without dragging the SDK module in.
    """
    cwd_resolved = cwd.resolve()

    async def can_use_tool(tool_name: str, tool_input: dict[str, Any], _ctx):
        from claude_agent_sdk.types import (
            PermissionResultAllow,
            PermissionResultDeny,
        )

        def deny(msg: str):
            logger.warning(
                "cc_session: tool DENIED tool=%s reason=%s input=%r",
                tool_name, msg, _summarize_input(tool_input),
            )
            return PermissionResultDeny(behavior="deny", message=msg)

        # Path-based tools — Read / Grep / Glob — require a path under cwd
        # AND the path must not match the forbidden pattern set.
        if tool_name in ("Read", "Grep", "Glob"):
            # The SDK passes "file_path" for Read, "path" for Glob/Grep, and
            # "pattern" plus "path" for Grep. Be liberal in what we accept.
            for arg_key in ("file_path", "path"):
                arg = tool_input.get(arg_key)
                if not arg:
                    continue
                resolved = _path_inside_cwd(str(arg), cwd_resolved)
                if resolved is None:
                    return deny(f"path outside cwd: {arg!r}")
                if _path_forbidden(resolved):
                    return deny(f"forbidden path: {arg!r}")
            # If neither key was set we let the SDK default behaviour apply
            # (the call must specify SOMETHING, and the SDK will reject a
            # missing required arg before we get here).
            return PermissionResultAllow(behavior="allow")

        if tool_name == "Bash":
            command = tool_input.get("command", "")
            if not isinstance(command, str) or not command.strip():
                return deny("empty Bash command")
            segments = _bash_split_segments(command)
            if not segments:
                return deny("Bash command parsed to zero segments")
            for seg in segments:
                ok, reason = _bash_segment_ok(seg, cwd=cwd_resolved)
                if not ok:
                    return deny(f"Bash segment rejected ({reason}): {seg!r}")
            return PermissionResultAllow(behavior="allow")

        # Anything else (Edit, Write, MultiEdit, NotebookEdit, WebFetch,
        # WebSearch, mcp__*, Task, etc.) — deny. Phase 1 is read-only.
        return deny(f"tool {tool_name!r} not allowed in read-only sandbox")

    return can_use_tool


def _summarize_input(tool_input: dict[str, Any]) -> dict[str, Any]:
    """Truncate string fields in a tool-input dict for log noise control."""
    out: dict[str, Any] = {}
    for k, v in tool_input.items():
        if isinstance(v, str) and len(v) > 200:
            out[k] = v[:200] + "...<truncated>"
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Session bookkeeping
# ---------------------------------------------------------------------------
@dataclass
class CCSession:
    """One persistent Chief brain — a (subject, scope) pair."""
    subject: str
    scope: str
    cwd: Path
    system_prompt_append: str
    client: object  # ClaudeSDKClient — typed loosely so tests can stub
    session_id: Optional[str] = None
    last_activity: float = field(default_factory=time.monotonic)
    spawn_history: list[float] = field(default_factory=list)
    # An asyncio.Lock — only one turn at a time per scope. The receive_response
    # loop is single-consumer; concurrent turns on the same client would
    # interleave outputs.
    turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Set when the session has been disconnected (idle-timeout, scope switch,
    # crash). A torn-down session must be respawned before send().
    closed: bool = False
    # Last reason the session was torn down — surfaced to the user on the
    # respawn turn so a "Chief restarted" note can be specific.
    teardown_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# SDK adapter — one indirection so unit tests can swap the client class.
# ---------------------------------------------------------------------------
def _make_client(options):
    """Construct a ClaudeSDKClient.

    Imported lazily so this module loads cleanly in environments where the
    SDK isn't installed (tests that monkeypatch ``_make_client`` directly).
    """
    from claude_agent_sdk import ClaudeSDKClient
    return ClaudeSDKClient(options=options)


def _make_options(
    cwd: Path,
    system_prompt_append: str,
    resume_session_id: Optional[str] = None,
    *,
    setting_sources: Optional[list[str]] = None,
    can_use_tool: Optional[Callable] = None,
):
    """Build a ClaudeAgentOptions for Chief's read-only persistent session.

    The Chief identity + scope memory is appended to CC's stock ``claude_code``
    preset so tool use, tool results, file context etc. all behave like
    interactive CC — only the assistant's voice + read-only enforcement
    are ours.

    SECURITY (Vera 2026-05-04 CRIT-1):
    By default we pass ``setting_sources=[]`` to the SDK so it does NOT load
    ``~/.claude/settings.json`` — owner's interactive settings grant
    unconstrained Bash/Edit/Write and define ``additionalDirectories`` that
    would widen reach beyond cwd. Tests can override via ``setting_sources``
    kwarg; production callers must NOT.

    Similarly, ``add_dirs=[]`` prevents the SDK from extending the readable
    workspace beyond ``cwd``.
    """
    from claude_agent_sdk import ClaudeAgentOptions

    kwargs = dict(
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": system_prompt_append,
        },
        allowed_tools=list(_READ_ONLY_TOOLS),
        disallowed_tools=list(_DISALLOWED_TOOLS),
        # ``dontAsk`` would silently deny anything not in allowed_tools. With
        # the can_use_tool callback wired, the SDK routes EVERY tool call to
        # us first; we explicitly approve or deny per call. Keep dontAsk as
        # belt-and-suspenders so a future SDK change can't make the CLI
        # interactive.
        permission_mode="dontAsk",
        cwd=str(cwd),
        env=_clean_env(),
        # Token-by-token text deltas via StreamEvent — required for our TTS
        # sentence-flush pipeline. Without this, AssistantMessage arrives only
        # at block-close and TTS would queue the whole reply at once.
        include_partial_messages=True,
        # Vera CRIT-1: ignore ALL filesystem settings (~/.claude/settings.json,
        # project .claude/settings.json, .local). Tests may override.
        setting_sources=[] if setting_sources is None else setting_sources,
        # Vera CRIT-1 (secondary): no additional readable directories beyond
        # cwd. Combined with setting_sources=[] this prevents owner's
        # ``additionalDirectories`` from widening reach.
        add_dirs=[],
    )
    if resume_session_id:
        kwargs["resume"] = resume_session_id
    if can_use_tool is not None:
        kwargs["can_use_tool"] = can_use_tool
    return ClaudeAgentOptions(**kwargs)


# ---------------------------------------------------------------------------
# Pool
# ---------------------------------------------------------------------------
class CCSpawnLoopError(RuntimeError):
    """Raised when a session has crashed too many times in a short window.

    Surface this to the user as a hard error rather than fork-bombing on a
    broken CC install.
    """


class CCSessionPool:
    """Manages the (subject, scope) → CCSession map.

    Public API:
      * ``get_or_spawn(subject, scope, ...)`` — return a ready session
      * ``send(subject, scope, prompt, ...)`` → AsyncIterator[ParsedEvent]
      * ``interrupt(subject, scope)`` — barge-in (SIGINT semantics)
      * ``teardown(subject, scope, reason)`` — clean shutdown
      * ``teardown_all()`` — for app shutdown
      * ``check_idle()`` — to be called periodically; tears down idle sessions

    All methods are async-safe across concurrent turns on different scopes.
    Same-scope concurrency is serialized via ``CCSession.turn_lock``.
    """

    def __init__(
        self,
        *,
        idle_timeout_s: float = IDLE_TIMEOUT_S,
        crash_loop_window_s: float = CRASH_LOOP_WINDOW_S,
        crash_loop_max_spawns: int = CRASH_LOOP_MAX_SPAWNS,
        client_factory: Optional[Callable] = None,
    ) -> None:
        self._sessions: dict[tuple[str, str], CCSession] = {}
        self._lock = asyncio.Lock()
        self._idle_timeout_s = idle_timeout_s
        self._crash_loop_window_s = crash_loop_window_s
        self._crash_loop_max_spawns = crash_loop_max_spawns
        # Factory lets tests inject a fake. Defaults to the real SDK.
        self._client_factory = client_factory or _make_client

    # -------------------------------------------------------- lifecycle
    async def get_or_spawn(
        self,
        subject: str,
        scope: str,
        cwd: Path,
        system_prompt_append: str,
    ) -> CCSession:
        """Return a ready CCSession for this (subject, scope), spawning if needed.

        If a session exists but has been torn down (idle-timeout, scope switch,
        crash), respawn with the captured ``session_id`` for context resume
        when available.
        """
        key = (subject, scope)
        # Hawke CRIT (C3): if a warm session exists but was spawned with a
        # different cwd or system_prompt_append, the live subprocess is now
        # stale. Tear it down and respawn with the new config so the brain
        # can't reply with prior-scope memory baked in.
        sess = self._sessions.get(key)
        if (
            sess is not None
            and not sess.closed
            and (sess.cwd != cwd or sess.system_prompt_append != system_prompt_append)
        ):
            logger.info(
                "cc_session: warm-reuse mismatch subject=%s scope=%s — "
                "tearing down (old_cwd=%s new_cwd=%s prompt_changed=%s)",
                subject, scope, sess.cwd, cwd,
                sess.system_prompt_append != system_prompt_append,
            )
            await self.teardown(subject, scope, reason="config-changed")
            # teardown marked closed=True; the lookup below returns the same
            # entry but won't reuse it (closed branch).

        async with self._lock:
            sess = self._sessions.get(key)
            if sess is not None and not sess.closed:
                return sess

            # Crash-loop guard — track spawn timestamps within the window.
            resume_id: Optional[str] = sess.session_id if sess is not None else None
            spawn_history = list(sess.spawn_history) if sess is not None else []
            now = time.monotonic()
            spawn_history = [t for t in spawn_history if now - t < self._crash_loop_window_s]
            if len(spawn_history) >= self._crash_loop_max_spawns:
                logger.error(
                    "cc_session: crash-loop detected subject=%s scope=%s "
                    "spawns_in_window=%d (window=%.0fs) — refusing to respawn",
                    subject, scope, len(spawn_history), self._crash_loop_window_s,
                )
                raise CCSpawnLoopError(
                    f"Chief crashed {len(spawn_history)} times in "
                    f"{self._crash_loop_window_s:.0f}s — refusing to respawn"
                )
            spawn_history.append(now)

            try:
                new_sess = await self._spawn_locked(
                    subject=subject,
                    scope=scope,
                    cwd=cwd,
                    system_prompt_append=system_prompt_append,
                    resume_session_id=resume_id,
                    spawn_history=spawn_history,
                )
            except Exception:
                # H1: connect-failed — preserve the spawn-history counter so
                # the next attempt sees the prior failure(s) and the crash-
                # loop guard fires correctly. Fork-bomb risk on broken CC
                # install otherwise.
                stub = CCSession(
                    subject=subject,
                    scope=scope,
                    cwd=cwd,
                    system_prompt_append=system_prompt_append,
                    client=None,  # no live client; closed=True signals dead
                    session_id=resume_id,
                    last_activity=time.monotonic(),
                    spawn_history=spawn_history,
                    closed=True,
                    teardown_reason="connect failed",
                )
                self._sessions[key] = stub
                raise
            self._sessions[key] = new_sess
            return new_sess

    async def _spawn_locked(
        self,
        *,
        subject: str,
        scope: str,
        cwd: Path,
        system_prompt_append: str,
        resume_session_id: Optional[str],
        spawn_history: list[float],
    ) -> CCSession:
        """Construct + connect a new ClaudeSDKClient. Caller holds ``self._lock``."""
        options = _make_options(
            cwd=cwd,
            system_prompt_append=system_prompt_append,
            resume_session_id=resume_session_id,
            can_use_tool=make_can_use_tool(cwd),
        )
        client = self._client_factory(options)

        try:
            await asyncio.wait_for(client.connect(), timeout=SPAWN_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.error(
                "cc_session: connect() timed out after %.0fs subject=%s scope=%s",
                SPAWN_TIMEOUT_S, subject, scope,
            )
            try:
                await client.disconnect()
            except Exception:
                pass
            raise
        except Exception as exc:
            logger.exception(
                "cc_session: connect() failed subject=%s scope=%s: %s",
                subject, scope, exc,
            )
            raise

        sess = CCSession(
            subject=subject,
            scope=scope,
            cwd=cwd,
            system_prompt_append=system_prompt_append,
            client=client,
            session_id=resume_session_id,  # SystemMessage init may overwrite
            last_activity=time.monotonic(),
            spawn_history=spawn_history,
        )
        logger.info(
            "cc_session: spawned subject=%s scope=%s cwd=%s resume=%s",
            subject, scope, cwd, "yes" if resume_session_id else "no",
        )
        return sess

    # -------------------------------------------------------- send (one turn)
    async def send(
        self,
        subject: str,
        scope: str,
        cwd: Path,
        system_prompt_append: str,
        prompt: str,
        *,
        on_crash_note: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> AsyncIterator[ParsedEvent]:
        """Send a user prompt and yield ParsedEvent stream until TurnComplete.

        ``on_crash_note`` — optional callback invoked exactly once if we had
        to respawn the session (idle-timeout, crash). The caller can use this
        to push a one-line "Chief restarted — context preserved" / "context
        lost" frame to the user.

        On asyncio.CancelledError mid-stream:
          1. Call ``self.interrupt(...)`` so the SDK signals CC to stop
          2. Drain the iterator until ResultMessage (with a short timeout)
          3. Re-raise CancelledError so the outer task tears down cleanly
        """
        # First attempt. If the subprocess died between turns (SIGKILL, OOM,
        # external crash) the SDK does NOT proactively mark its client as
        # disconnected — we only learn about the death when query() raises
        # ProcessError("Cannot write to terminated process"). On that failure
        # we mark the session closed, respawn-with-resume via _send_prepare
        # (which fires on_crash_note exactly once with "Chief restarted"),
        # and retry the turn a single time on the fresh subprocess. If the
        # retry also fails we surface a hard error — at that point CC is
        # genuinely broken on the box.
        for attempt in (1, 2):
            sess = await self._send_prepare(
                subject=subject,
                scope=scope,
                cwd=cwd,
                system_prompt_append=system_prompt_append,
                on_crash_note=on_crash_note,
            )

            # Use an async generator helper so we can detect a query() failure
            # WITHOUT releasing the per-session turn_lock between the failure
            # and the retry of _send_prepare. The helper yields one of two
            # signals up front (a sentinel ParsedError on query failure, or
            # nothing if query succeeded), then yields the live event stream.
            async with sess.turn_lock:
                sess.last_activity = time.monotonic()
                try:
                    await sess.client.query(prompt)
                except Exception as exc:
                    logger.warning(
                        "cc_session: query() raised subject=%s scope=%s "
                        "(attempt %d/2): %s",
                        subject, scope, attempt, exc,
                    )
                    sess.closed = True
                    sess.teardown_reason = f"query failed: {exc}"
                    if attempt == 1:
                        # Drop the lock and retry — the session is dead so
                        # nobody else should be waiting on this lock anyway.
                        continue
                    yield ParsedError(message=f"Chief failed to send: {exc}")
                    return

                # Bookkeeping shared across all exit paths. ``saw_turn_complete``
                # tells the finally block whether to attempt an interrupt+drain
                # (we only do that when the consumer cancelled or aclose()'d
                # the generator BEFORE the SDK emitted the final ResultMessage).
                saw_turn_complete = False
                stream_error: Optional[BaseException] = None
                # Stash the receive iterator on the session so _drain_until_result
                # can RESUME the same iterator instead of opening a second one
                # against a half-closed channel. Vera C5.
                receive_iter = sess.client.receive_response().__aiter__()
                sess._receive_iter = receive_iter  # type: ignore[attr-defined]

                try:
                    while True:
                        try:
                            message = await receive_iter.__anext__()
                        except StopAsyncIteration:
                            break
                        parsed = cc_output_parser.parse_message(message)
                        for ev in parsed:
                            # Capture session_id eagerly so a mid-turn crash
                            # still leaves us with a valid resume target.
                            if isinstance(ev, SessionInit):
                                sess.session_id = ev.session_id
                            elif isinstance(ev, TurnComplete):
                                if ev.session_id:
                                    sess.session_id = ev.session_id
                                saw_turn_complete = True
                            yield ev
                        sess.last_activity = time.monotonic()
                except Exception as exc:
                    # Non-cancel stream error. CancelledError + GeneratorExit
                    # bypass this block (they're BaseException, not Exception)
                    # and fall through to ``finally``.
                    stream_error = exc
                    logger.exception(
                        "cc_session: receive_response error subject=%s "
                        "scope=%s: %s",
                        subject, scope, exc,
                    )
                    sess.closed = True
                    sess.teardown_reason = f"stream error: {exc}"
                    yield ParsedError(message=f"Chief stream error: {exc}")
                finally:
                    # C4: ANY early exit before TurnComplete (CancelledError,
                    # GeneratorExit on consumer aclose(), unhandled exception)
                    # must run interrupt() + drain so the next turn lands on
                    # a clean slate. The previous code only handled
                    # CancelledError; aclose() throws GeneratorExit which
                    # was silently skipping the cleanup and leaving stragglers
                    # in receive_response for the next query().
                    if not saw_turn_complete and stream_error is None:
                        try:
                            await asyncio.wait_for(
                                sess.client.interrupt(),
                                timeout=INTERRUPT_DRAIN_TIMEOUT_S,
                            )
                        except (asyncio.TimeoutError, Exception):
                            pass
                        # Drain the SAME iterator we were reading from (C5).
                        await self._drain_until_result(sess)
                    sess.last_activity = time.monotonic()
                    # Drop the iterator handle — no longer in use.
                    sess._receive_iter = None  # type: ignore[attr-defined]
                # Successful turn (or surfaced stream error) — no retry.
                return

    async def _send_prepare(
        self,
        *,
        subject: str,
        scope: str,
        cwd: Path,
        system_prompt_append: str,
        on_crash_note: Optional[Callable[[str], Awaitable[None]]],
    ) -> CCSession:
        """Resolve the session for this turn, surfacing a crash note if we
        had to respawn.

        We look up the existing session first; if it was torn down for any
        reason (idle, scope switch, crash) we capture the reason BEFORE
        get_or_spawn replaces the entry, then fire ``on_crash_note`` once
        the new session is ready.
        """
        key = (subject, scope)
        prior_reason: Optional[str] = None
        prior_session_id: Optional[str] = None
        async with self._lock:
            prior = self._sessions.get(key)
            if prior is not None and prior.closed:
                prior_reason = prior.teardown_reason or "restarted"
                prior_session_id = prior.session_id

        sess = await self.get_or_spawn(
            subject=subject,
            scope=scope,
            cwd=cwd,
            system_prompt_append=system_prompt_append,
        )

        if prior_reason is not None and on_crash_note is not None:
            # H2: distinguish "first-turn-failed-cold" from "session-was-running-
            # then-crashed". If we have a prior session_id we DID have prior
            # context that we're attempting to resume; otherwise the very first
            # turn failed and "context lost" is misleading (there was no
            # context).
            if prior_session_id is None:
                note = "Chief had trouble starting — try again."
            elif sess.session_id:
                note = "Chief restarted — context preserved."
            else:
                note = "Chief restarted — context lost, please rephrase."
            try:
                await on_crash_note(note)
            except Exception as exc:
                logger.warning("cc_session: on_crash_note raised: %s", exc)
            logger.info(
                "cc_session: respawn after %s subject=%s scope=%s resumed=%s",
                prior_reason, subject, scope,
                "yes" if sess.session_id else "no",
            )

        return sess

    async def _drain_until_result(self, sess: CCSession) -> None:
        """Drain the live ``receive_response`` iterator until ResultMessage.

        Bounded by ``INTERRUPT_DRAIN_TIMEOUT_S`` — if the SDK never emits a
        ResultMessage, we mark the session closed and force a respawn next
        turn rather than leaving the stream in a half-state.

        Vera C5: we reuse the iterator stashed on the session by ``send``
        rather than opening a fresh one. The SDK's ``receive_response()`` is
        a single multiplexed channel; spinning a second iterator while the
        first is half-closed is undefined behaviour (best case: hangs the
        full drain timeout; worst case: deadlock or stale data on next turn).
        If no iterator is stashed (caller invoked us out-of-band), we fall
        back to opening a new one and document that in the comment.
        """
        existing_iter = getattr(sess, "_receive_iter", None)

        async def _drain_existing():
            assert existing_iter is not None
            while True:
                try:
                    message = await existing_iter.__anext__()
                except StopAsyncIteration:
                    return
                parsed = cc_output_parser.parse_message(message)
                for ev in parsed:
                    if isinstance(ev, TurnComplete):
                        return

        async def _drain_fresh():
            # Fallback path — no stashed iterator. This SHOULD only fire when
            # an out-of-band caller invoked _drain_until_result directly
            # (currently no such caller; defensive only).
            async for message in sess.client.receive_response():
                parsed = cc_output_parser.parse_message(message)
                for ev in parsed:
                    if isinstance(ev, TurnComplete):
                        return

        coro = _drain_existing() if existing_iter is not None else _drain_fresh()
        try:
            await asyncio.wait_for(coro, timeout=INTERRUPT_DRAIN_TIMEOUT_S)
        except (asyncio.TimeoutError, Exception):
            sess.closed = True
            sess.teardown_reason = "drain timeout after interrupt"
            logger.warning(
                "cc_session: drain after interrupt timed out subject=%s scope=%s — "
                "forcing respawn next turn",
                sess.subject, sess.scope,
            )

    # -------------------------------------------------------- interrupt
    async def interrupt(self, subject: str, scope: str) -> bool:
        """Signal CC to stop generating the current turn.

        Returns True if a session was found and interrupted, False otherwise.
        Caller still owns the cancel of the outer asyncio task — interrupt()
        is the SDK-level "stop talking" only.
        """
        key = (subject, scope)
        async with self._lock:
            sess = self._sessions.get(key)
            if sess is None or sess.closed:
                return False
        try:
            await sess.client.interrupt()
            logger.info("cc_session: interrupt sent subject=%s scope=%s", subject, scope)
            return True
        except Exception as exc:
            logger.warning(
                "cc_session: interrupt raised subject=%s scope=%s: %s",
                subject, scope, exc,
            )
            return False

    # -------------------------------------------------------- teardown
    async def teardown(
        self,
        subject: str,
        scope: str,
        reason: str = "explicit",
    ) -> bool:
        """Clean SIGTERM-equivalent shutdown. Keeps session_id for later resume.

        Returns True if a session was found and torn down, False otherwise.
        """
        key = (subject, scope)
        async with self._lock:
            sess = self._sessions.get(key)
            if sess is None:
                return False
            if sess.closed:
                # Already torn down — leave the entry in place so the next
                # get_or_spawn can read the saved session_id for resume.
                sess.teardown_reason = sess.teardown_reason or reason
                return False
            sess.closed = True
            sess.teardown_reason = reason

        try:
            await asyncio.wait_for(
                sess.client.disconnect(), timeout=TEARDOWN_TIMEOUT_S,
            )
            # Vera 2026-05-04: session_id is a resume token; emit a presence
            # boolean at INFO and keep the raw id at DEBUG.
            logger.info(
                "cc_session: torn down subject=%s scope=%s reason=%s session_id_present=%s",
                subject, scope, reason, bool(sess.session_id),
            )
            logger.debug(
                "cc_session: torn down subject=%s scope=%s session_id=%s",
                subject, scope, sess.session_id or "none",
            )
            return True
        except (asyncio.TimeoutError, Exception) as exc:
            logger.warning(
                "cc_session: disconnect raised/timed out subject=%s scope=%s: %s",
                subject, scope, exc,
            )
            return True

    async def teardown_all(self, reason: str = "shutdown") -> None:
        """Tear down every session — for app shutdown."""
        async with self._lock:
            keys = list(self._sessions.keys())
        for subject, scope in keys:
            await self.teardown(subject, scope, reason=reason)

    async def teardown_other_scopes(
        self,
        subject: str,
        keep_scope: str,
        reason: str = "scope-switch",
    ) -> None:
        """Tear down every session for ``subject`` except the one for ``keep_scope``.

        Called on context_switched. Frees memory + drops crash-loop state for
        the scopes we won't be talking to anymore. The retained scope's
        session_id is preserved for later resume.
        """
        async with self._lock:
            stale = [
                (s, sc) for (s, sc) in self._sessions.keys()
                if s == subject and sc != keep_scope
            ]
        for s, sc in stale:
            await self.teardown(s, sc, reason=reason)

    # -------------------------------------------------------- idle timeout
    async def check_idle(self) -> int:
        """Tear down sessions idle longer than ``self._idle_timeout_s``.

        Returns the number torn down. Intended to be called from a periodic
        background task or piggy-backed on each turn.

        H3: To avoid racing an in-flight turn (sweep finds idle entries,
        releases lock, concurrent turn picks up a session, sweep then tears
        it down mid-stream → BrokenPipeError), we acquire each candidate's
        ``turn_lock`` and re-verify idleness AFTER the lock — bailing if the
        session was reused while we waited.
        """
        now = time.monotonic()
        async with self._lock:
            candidates = [
                (key, sess) for key, sess in self._sessions.items()
                if not sess.closed
                and (now - sess.last_activity) > self._idle_timeout_s
            ]

        torn = 0
        for (subject, scope), sess in candidates:
            # Acquire turn_lock so we wait for any in-flight turn to finish
            # before tearing down. Use a short timeout — if a turn is genuinely
            # in flight we can sweep this entry on the next pass.
            try:
                await asyncio.wait_for(sess.turn_lock.acquire(), timeout=0.05)
            except asyncio.TimeoutError:
                logger.debug(
                    "cc_session: idle sweep skipping busy session subject=%s scope=%s",
                    subject, scope,
                )
                continue
            try:
                # Re-check idleness now that we own the turn lock — a turn
                # that ran between our snapshot and lock acquisition would
                # have bumped last_activity.
                if sess.closed:
                    continue
                if (time.monotonic() - sess.last_activity) <= self._idle_timeout_s:
                    logger.debug(
                        "cc_session: idle sweep raced; session no longer idle "
                        "subject=%s scope=%s",
                        subject, scope,
                    )
                    continue
                # teardown grabs self._lock briefly then awaits disconnect; we
                # still hold turn_lock so no turn can run on this client during
                # disconnect.
                if await self.teardown(subject, scope, reason="idle-timeout"):
                    torn += 1
            finally:
                sess.turn_lock.release()

        if torn:
            logger.info("cc_session: idle teardown count=%d", torn)
        return torn

    # -------------------------------------------------------- introspection
    def has_session(self, subject: str, scope: str) -> bool:
        sess = self._sessions.get((subject, scope))
        return sess is not None and not sess.closed

    def get_session_id(self, subject: str, scope: str) -> Optional[str]:
        sess = self._sessions.get((subject, scope))
        if sess is None:
            return None
        return sess.session_id


# Module-level singleton — one pool shared across all WS connections.
_pool = CCSessionPool()


def get_pool() -> CCSessionPool:
    return _pool


__all__ = [
    "CCSession",
    "CCSessionPool",
    "CCSpawnLoopError",
    "get_pool",
    "make_can_use_tool",
    "IDLE_TIMEOUT_S",
    "CC_IDLE_SWEEP_INTERVAL_S",
    "CRASH_LOOP_WINDOW_S",
    "CRASH_LOOP_MAX_SPAWNS",
]
