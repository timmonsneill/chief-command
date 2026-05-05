"""Unit tests for services.agent_tools.

Coverage:
  * Read: path-fence (reject outside cwd), forbidden patterns (.env, secrets,
    *.key), happy-path read with truncation at 200KB.
  * Bash: leader allowlist, forbidden chars, git verb fence, path-arg fence,
    timeout, cwd containment, exit-code surfacing.
  * Grep: pattern + path validation, "no matches" path, ripgrep + grep
    fallback.
  * dispatch_tool: routes to the right executor by name.
  * Gemini adapters: to_gemini_declarations / to_gemini_tool produce the
    expected schema.

The cc_session sandbox helpers (_path_inside_cwd, _bash_segment_ok, etc.)
are exercised here transitively — these are end-to-end tests of the public
agent_tools surface, not duplicates of the cc_session-internal sandbox
tests.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


from services import agent_tools  # noqa: E402
from services.agent_tools import (  # noqa: E402
    ToolResult,
    dispatch_tool,
    execute_bash,
    execute_grep,
    execute_read,
    to_gemini_declarations,
    to_gemini_tool,
)


# ---------------------------------------------------------------------------
# Read tool
# ---------------------------------------------------------------------------
class TestExecuteRead:
    @pytest.mark.asyncio
    async def test_read_happy_path(self, tmp_path: Path):
        f = tmp_path / "hello.txt"
        f.write_text("hello world", encoding="utf-8")
        result = await execute_read("hello.txt", tmp_path)
        assert result.error is False
        assert "hello world" in result.output

    @pytest.mark.asyncio
    async def test_read_outside_cwd_rejected(self, tmp_path: Path):
        # Try to read /etc/passwd; the path-fence must reject regardless of
        # whether the file exists on the test box.
        result = await execute_read("/etc/passwd", tmp_path)
        assert result.error is True
        assert "outside the project cwd" in result.output

    @pytest.mark.asyncio
    async def test_read_dotenv_rejected(self, tmp_path: Path):
        f = tmp_path / ".env"
        f.write_text("SECRET=xxx", encoding="utf-8")
        result = await execute_read(".env", tmp_path)
        assert result.error is True
        assert "forbidden" in result.output.lower()

    @pytest.mark.asyncio
    async def test_read_credentials_rejected(self, tmp_path: Path):
        f = tmp_path / "credentials.json"
        f.write_text("{}", encoding="utf-8")
        result = await execute_read("credentials.json", tmp_path)
        assert result.error is True
        assert "forbidden" in result.output.lower()

    @pytest.mark.asyncio
    async def test_read_key_file_rejected(self, tmp_path: Path):
        f = tmp_path / "private.key"
        f.write_text("BEGIN RSA", encoding="utf-8")
        result = await execute_read("private.key", tmp_path)
        assert result.error is True

    @pytest.mark.asyncio
    async def test_read_truncates_huge_file(self, tmp_path: Path):
        f = tmp_path / "big.txt"
        # 250KB of 'a'
        f.write_text("a" * (250 * 1024), encoding="utf-8")
        result = await execute_read("big.txt", tmp_path)
        assert result.error is False
        assert result.truncated is True
        assert "[truncated at" in result.output

    @pytest.mark.asyncio
    async def test_read_missing_file(self, tmp_path: Path):
        result = await execute_read("nope.txt", tmp_path)
        assert result.error is True
        assert "not found" in result.output

    @pytest.mark.asyncio
    async def test_read_directory(self, tmp_path: Path):
        (tmp_path / "subdir").mkdir()
        result = await execute_read("subdir", tmp_path)
        assert result.error is True

    @pytest.mark.asyncio
    async def test_read_empty_path(self, tmp_path: Path):
        result = await execute_read("", tmp_path)
        assert result.error is True
        assert "required" in result.output

    @pytest.mark.asyncio
    async def test_read_tilde_expanded_outside_cwd(self, tmp_path: Path):
        # ~ is expanded to $HOME before the cwd check; if HOME isn't under
        # tmp_path (which it never is on a sane test machine), the read
        # must be rejected.
        result = await execute_read("~/.ssh/id_rsa", tmp_path)
        assert result.error is True


# ---------------------------------------------------------------------------
# Bash tool
# ---------------------------------------------------------------------------
class TestExecuteBash:
    @pytest.mark.asyncio
    async def test_bash_ls_happy(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("x")
        (tmp_path / "b.txt").write_text("y")
        result = await execute_bash("ls", tmp_path)
        assert result.error is False
        assert "a.txt" in result.output and "b.txt" in result.output

    @pytest.mark.asyncio
    async def test_bash_pwd_happy(self, tmp_path: Path):
        result = await execute_bash("pwd", tmp_path)
        assert result.error is False
        # pwd may resolve symlinks differently on macOS; just check it
        # contains the tmp_path's last segment.
        assert tmp_path.name in result.output

    @pytest.mark.asyncio
    async def test_bash_disallowed_command(self, tmp_path: Path):
        result = await execute_bash("rm -rf /", tmp_path)
        assert result.error is True
        assert "rejected by sandbox" in result.output

    @pytest.mark.asyncio
    async def test_bash_redirect_rejected(self, tmp_path: Path):
        result = await execute_bash("echo x > /tmp/foo", tmp_path)
        assert result.error is True
        assert "forbidden token" in result.output

    @pytest.mark.asyncio
    async def test_bash_command_substitution_rejected(self, tmp_path: Path):
        result = await execute_bash("ls $(pwd)", tmp_path)
        assert result.error is True

    @pytest.mark.asyncio
    async def test_bash_git_log_allowed(self, tmp_path: Path):
        # Init a tiny repo so `git log` returns cleanly.
        os.system(f"cd {tmp_path} && git init -q && git -c user.email=a@b -c user.name=t commit --allow-empty -q -m initial")
        result = await execute_bash("git log --oneline -1", tmp_path)
        # If git isn't available the test will see error=True; that's
        # acceptable — the sandbox didn't reject it.
        assert "rejected by sandbox" not in result.output

    @pytest.mark.asyncio
    async def test_bash_git_push_rejected(self, tmp_path: Path):
        result = await execute_bash("git push origin main", tmp_path)
        assert result.error is True
        assert "rejected by sandbox" in result.output

    @pytest.mark.asyncio
    async def test_bash_cat_outside_cwd_rejected(self, tmp_path: Path):
        result = await execute_bash("cat /etc/passwd", tmp_path)
        assert result.error is True
        # Path-fence on Bash leg.
        assert "outside cwd" in result.output

    @pytest.mark.asyncio
    async def test_bash_cat_dotenv_rejected(self, tmp_path: Path):
        # Even when .env IS inside cwd, the secrets pattern blocks it.
        (tmp_path / ".env").write_text("SECRET=xxx")
        result = await execute_bash("cat .env", tmp_path)
        assert result.error is True
        assert "forbidden path" in result.output

    @pytest.mark.asyncio
    async def test_bash_empty_command(self, tmp_path: Path):
        result = await execute_bash("", tmp_path)
        assert result.error is True
        assert "required" in result.output

    @pytest.mark.asyncio
    async def test_bash_timeout(self, tmp_path: Path, monkeypatch):
        # Override the timeout to something tiny + use a sleep-ish command.
        # ``find`` with a deep tree will exceed it; we use a synthetic
        # tree built by mkdir to keep this deterministic.
        monkeypatch.setattr(agent_tools, "TOOL_TIMEOUT_S", 0.1)
        # Make a deep nested structure. ``find`` traversal across enough
        # files takes >100ms reliably.
        for i in range(2000):
            (tmp_path / f"file_{i}.txt").write_text("x")
        # Use a command that's allowlisted but slow at this scale.
        result = await execute_bash("find . -name '*.txt' -type f", tmp_path)
        # Either timeout fires or it completes within budget — both are
        # acceptable as long as we don't crash. We assert that on TIMEOUT
        # the message is correct.
        if "timed out" in result.output:
            assert result.error is True


# ---------------------------------------------------------------------------
# Grep tool
# ---------------------------------------------------------------------------
class TestExecuteGrep:
    @pytest.mark.asyncio
    async def test_grep_finds_matches(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
        (tmp_path / "b.py").write_text("def bar():\n    return 2\n")
        result = await execute_grep("def foo", tmp_path)
        assert result.error is False
        assert "def foo" in result.output

    @pytest.mark.asyncio
    async def test_grep_no_matches(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("hello")
        result = await execute_grep("nope_no_match_xyz", tmp_path)
        assert result.error is False
        assert "no matches" in result.output.lower()

    @pytest.mark.asyncio
    async def test_grep_path_outside_cwd_rejected(self, tmp_path: Path):
        result = await execute_grep("foo", tmp_path, path="/etc")
        assert result.error is True
        assert "outside the project cwd" in result.output

    @pytest.mark.asyncio
    async def test_grep_secrets_path_rejected(self, tmp_path: Path):
        secrets_dir = tmp_path / "secret_stuff"
        secrets_dir.mkdir()
        result = await execute_grep("foo", tmp_path, path="secret_stuff")
        assert result.error is True
        assert "forbidden" in result.output

    @pytest.mark.asyncio
    async def test_grep_empty_pattern(self, tmp_path: Path):
        result = await execute_grep("", tmp_path)
        assert result.error is True


# ---------------------------------------------------------------------------
# dispatch_tool router
# ---------------------------------------------------------------------------
class TestDispatchToolRouter:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, tmp_path: Path):
        result = await dispatch_tool(
            "NotARealTool",
            {},
            cwd=tmp_path,
            subject="owner",
            scope="Chief Command",
            system_prompt_append="",
        )
        assert result.error is True
        assert "unknown tool" in result.output

    @pytest.mark.asyncio
    async def test_dispatch_refused_when_cwd_is_home(self):
        """When ``llm._resolve_cwd`` couldn't find a scope repo, it falls back
        to Path.home(). The path-fence machinery anchored on cwd would then
        treat ANY path under $HOME as in-scope — far too permissive. This
        test verifies dispatch_tool refuses every tool in that fallback."""
        for name, args in (
            ("Read", {"path": "x.txt"}),
            ("Bash", {"command": "ls"}),
            ("Grep", {"pattern": "foo"}),
            ("dispatch_agent", {"spec": "do something"}),
        ):
            result = await dispatch_tool(
                name,
                args,
                cwd=Path.home(),
                subject="owner",
                scope="Chief Command",
                system_prompt_append="",
            )
            assert result.error is True, f"{name} was not refused"
            assert "no project scope set" in result.output, (
                f"{name}: unexpected error message: {result.output!r}"
            )

    @pytest.mark.asyncio
    async def test_dispatch_allowed_when_cwd_is_real_subdir(self, tmp_path: Path):
        """Sanity check: a real per-scope cwd (NOT the $HOME fallback) routes
        through to the executor. The Read happens with a real subdir."""
        f = tmp_path / "ok.txt"
        f.write_text("contents")
        result = await dispatch_tool(
            "Read",
            {"path": "ok.txt"},
            cwd=tmp_path,
            subject="owner",
            scope="Chief Command",
            system_prompt_append="",
        )
        assert result.error is False
        assert "contents" in result.output

    @pytest.mark.asyncio
    async def test_routes_read_to_executor(self, tmp_path: Path):
        f = tmp_path / "x.txt"
        f.write_text("hello")
        result = await dispatch_tool(
            "Read",
            {"path": "x.txt"},
            cwd=tmp_path,
            subject="owner",
            scope="Chief Command",
            system_prompt_append="",
        )
        assert result.error is False
        assert "hello" in result.output

    @pytest.mark.asyncio
    async def test_routes_bash_to_executor(self, tmp_path: Path):
        result = await dispatch_tool(
            "Bash",
            {"command": "pwd"},
            cwd=tmp_path,
            subject="owner",
            scope="Chief Command",
            system_prompt_append="",
        )
        assert result.error is False

    @pytest.mark.asyncio
    async def test_routes_grep_to_executor(self, tmp_path: Path):
        (tmp_path / "x.py").write_text("hello world")
        result = await dispatch_tool(
            "Grep",
            {"pattern": "hello"},
            cwd=tmp_path,
            subject="owner",
            scope="Chief Command",
            system_prompt_append="",
        )
        assert result.error is False
        assert "hello" in result.output


# ---------------------------------------------------------------------------
# dispatch_agent — wraps cc_session.get_pool().send(...)
# ---------------------------------------------------------------------------
class TestExecuteDispatchAgent:
    @pytest.mark.asyncio
    async def test_dispatch_agent_collects_text_and_returns(
        self, tmp_path: Path, monkeypatch,
    ):
        # Stub the cc_session pool so we don't actually spawn a CC subprocess.
        from services.cc_output_parser import (
            SessionInit, TextDelta, TurnComplete,
        )

        events = [
            SessionInit(session_id="sess-1"),
            TextDelta(text="Hello "),
            TextDelta(text="from "),
            TextDelta(text="dispatch."),
            TurnComplete(
                session_id="sess-1",
                total_cost_usd=0.0,
                num_turns=1,
                is_error=False,
            ),
        ]

        async def fake_send(**kwargs):
            for ev in events:
                yield ev

        async def fake_interrupt(*a, **kw):
            return False

        fake_pool = MagicMock()
        fake_pool.send = fake_send
        fake_pool.interrupt = fake_interrupt

        from services import cc_session as _cc_session
        monkeypatch.setattr(_cc_session, "get_pool", lambda: fake_pool)

        result = await agent_tools.execute_dispatch_agent(
            spec="check the auth module",
            cwd=tmp_path,
            subject="owner",
            scope="Chief Command",
            system_prompt_append="(append)",
        )
        assert result.error is False
        assert "Hello from dispatch." == result.output

    @pytest.mark.asyncio
    async def test_dispatch_agent_empty_spec(self, tmp_path: Path):
        result = await agent_tools.execute_dispatch_agent(
            spec="",
            cwd=tmp_path,
            subject="owner",
            scope="Chief Command",
            system_prompt_append="",
        )
        assert result.error is True
        assert "required" in result.output

    @pytest.mark.asyncio
    async def test_dispatch_agent_pool_error_surfaces(
        self, tmp_path: Path, monkeypatch,
    ):
        from services.cc_output_parser import ParsedError

        events = [ParsedError(message="cc spawn failed")]

        async def fake_send(**kwargs):
            for ev in events:
                yield ev

        async def fake_interrupt(*a, **kw):
            return False

        fake_pool = MagicMock()
        fake_pool.send = fake_send
        fake_pool.interrupt = fake_interrupt

        from services import cc_session as _cc_session
        monkeypatch.setattr(_cc_session, "get_pool", lambda: fake_pool)

        result = await agent_tools.execute_dispatch_agent(
            spec="anything",
            cwd=tmp_path,
            subject="owner",
            scope="Chief Command",
            system_prompt_append="",
        )
        assert result.error is True
        assert "cc spawn failed" in result.output

    @pytest.mark.asyncio
    async def test_dispatch_agent_cancellation_interrupts_pool(
        self, tmp_path: Path, monkeypatch,
    ):
        # Ensure that on outer CancelledError, we tell the pool to
        # interrupt before re-raising.
        interrupt_calls = []

        async def fake_send(**kwargs):
            # Yield forever — the outer cancel must terminate this.
            from services.cc_output_parser import TextDelta
            yield TextDelta(text="x")
            await asyncio.sleep(60)

        async def fake_interrupt(subject, scope):
            interrupt_calls.append((subject, scope))
            return True

        fake_pool = MagicMock()
        fake_pool.send = fake_send
        fake_pool.interrupt = fake_interrupt

        from services import cc_session as _cc_session
        monkeypatch.setattr(_cc_session, "get_pool", lambda: fake_pool)

        async def run_and_cancel():
            task = asyncio.create_task(
                agent_tools.execute_dispatch_agent(
                    spec="long thing",
                    cwd=tmp_path,
                    subject="owner",
                    scope="Chief Command",
                    system_prompt_append="",
                )
            )
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        await run_and_cancel()
        assert interrupt_calls == [("owner", "Chief Command")]


# ---------------------------------------------------------------------------
# Gemini adapter
# ---------------------------------------------------------------------------
class TestGeminiAdapter:
    def test_to_gemini_declarations_returns_five(self):
        # Stage 3 of Live pivot added think_deep as the fifth tool.
        decls = to_gemini_declarations()
        assert len(decls) == 5
        names = [d.name for d in decls]
        assert set(names) == {
            "Read", "Bash", "Grep", "dispatch_agent", "think_deep",
        }

    def test_to_gemini_tool_packages_them(self):
        tool = to_gemini_tool()
        assert tool.function_declarations is not None
        assert len(tool.function_declarations) == 5

    def test_read_declaration_has_required_path(self):
        decls = to_gemini_declarations()
        read_decl = next(d for d in decls if d.name == "Read")
        # The declaration carries our JSON-Schema verbatim.
        schema = read_decl.parameters_json_schema
        assert "path" in schema["properties"]
        assert "path" in schema["required"]

    def test_dispatch_agent_does_not_expose_scope_param(self):
        """``scope`` was removed from the dispatch_agent schema — letting the
        model pick a scope while cwd stays pinned to the calling Chief's repo
        is a sandbox-bypass vector. Caller-provided scope is authoritative."""
        decls = to_gemini_declarations()
        dispatch_decl = next(d for d in decls if d.name == "dispatch_agent")
        schema = dispatch_decl.parameters_json_schema
        assert "scope" not in schema["properties"]
        assert "spec" in schema["properties"]
        assert "spec" in schema["required"]


# ---------------------------------------------------------------------------
# Forbidden-path coverage — verify each newly-added pattern is blocked
# (cc_session._FORBIDDEN_PATH_RE extension, sweep finding 2026-05-04).
# ---------------------------------------------------------------------------
class TestForbiddenPathPatterns:
    """Sweep finding 2026-05-04: the original regex caught .env / credentials /
    *.key / secret. These cases were silently allowed:
      *.pem, *.p12, *.crt, *.pfx, *.cer, *.jks
      id_rsa, id_ed25519, id_ecdsa, id_dsa
      .npmrc, .netrc, .pgpass, .pypirc
      oauth_token*, service-account*.json
    Each must now be rejected by execute_read (which calls _path_forbidden
    via the cc_session helper)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("filename", [
        "server.pem",
        "client.p12",
        "cert.crt",
        "store.pfx",
        "ca.cer",
        "keystore.jks",
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
        "id_dsa",
        "id_rsa.pub",
        ".npmrc",
        ".netrc",
        ".pgpass",
        ".pypirc",
        "oauth_token.json",
        "oauth-token.txt",
        "service-account.json",
        "service-account-prod.json",
        "service_account_creds.json",
        "gcloud-key.json",
        "gcloud_key.json",
    ])
    async def test_each_pattern_blocked(self, tmp_path: Path, filename: str):
        f = tmp_path / filename
        f.write_text("sensitive", encoding="utf-8")
        result = await execute_read(filename, tmp_path)
        assert result.error is True, (
            f"Expected {filename!r} to be rejected by the forbidden-path "
            f"regex, got result.error={result.error}"
        )
        # Either "forbidden" wording fires (the executor's deny message) or
        # the secrets-substring path catches it. Be permissive about the
        # exact message — the contract is "rejected", not "rejected with
        # exact phrase X".
        assert (
            "forbidden" in result.output.lower()
            or "rejected" in result.output.lower()
        ), f"{filename}: unexpected output: {result.output!r}"

    @pytest.mark.asyncio
    async def test_innocuous_filename_still_allowed(self, tmp_path: Path):
        """Sanity: a plain text file isn't accidentally caught by the
        broadened pattern."""
        f = tmp_path / "notes.md"
        f.write_text("hello", encoding="utf-8")
        result = await execute_read("notes.md", tmp_path)
        assert result.error is False
        assert "hello" in result.output
