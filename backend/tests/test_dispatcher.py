"""Integration tests for services.dispatcher.

Spawns real subprocesses (echo, sh -c) because the whole point of dispatcher
is subprocess orchestration — a pure-mock test wouldn't prove anything useful.
Uses cwd=/tmp for portability.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from services.dispatcher import (
    ENV_ALLOWLIST,
    MAX_TASK_SPEC_BYTES,
    TaskAlreadyRunning,
    TaskDispatcher,
    TaskHandle,
    serialized_sender,
)


TMP = Path("/tmp")


@pytest.mark.asyncio
async def test_echo_roundtrip() -> None:
    """echo 'hello' -> one task_output line, task_complete with exit_code=0."""
    d = TaskDispatcher()
    output_events: list[tuple[str, str]] = []
    complete_events: list[tuple[int, str]] = []
    done = asyncio.Event()

    async def on_output(text: str, stream: str) -> None:
        output_events.append((text, stream))

    async def on_complete(exit_code: int, summary: str) -> None:
        complete_events.append((exit_code, summary))
        done.set()

    handle = await d.dispatch(
        session_id="s1",
        task_spec="echo hello",
        repo=TMP,
        on_output=on_output,
        on_complete=on_complete,
        command=["/bin/echo", "hello"],
    )

    assert handle.pid > 0
    assert handle.task_spec == "echo hello"
    assert handle.repo == TMP
    assert handle.started_at is not None
    # task_id is auto-derived from started_at.isoformat() — must be a string
    # that matches the ISO timestamp exactly.
    assert handle.task_id == handle.started_at.isoformat()
    assert isinstance(handle.task_id, str)

    await asyncio.wait_for(done.wait(), timeout=5.0)

    # One stdout line "hello\n".
    stdout_events = [e for e in output_events if e[1] == "stdout"]
    assert len(stdout_events) == 1
    assert stdout_events[0][0].strip() == "hello"

    assert complete_events == [(0, "hello")]
    assert handle.exit_code == 0
    assert not handle.is_running


@pytest.mark.asyncio
async def test_task_ids_are_unique_per_dispatch() -> None:
    """Two dispatches on distinct sessions get distinct task_ids.

    This is the foundation of the frontend's "route output by task_id" fix —
    if two task_started frames carried the same id, a late output from the
    first could be misattributed to the second.
    """
    d = TaskDispatcher()
    done_a = asyncio.Event()
    done_b = asyncio.Event()

    async def noop(text: str, stream: str) -> None:
        pass

    async def done_a_cb(code: int, summary: str) -> None:
        done_a.set()

    async def done_b_cb(code: int, summary: str) -> None:
        done_b.set()

    handle_a = await d.dispatch(
        session_id="a",
        task_spec="first",
        repo=TMP,
        on_output=noop,
        on_complete=done_a_cb,
        command=["/bin/echo", "a"],
    )
    # Sleep enough to ensure a fresh ISO-8601 timestamp at microsecond
    # resolution.
    await asyncio.sleep(0.01)
    handle_b = await d.dispatch(
        session_id="b",
        task_spec="second",
        repo=TMP,
        on_output=noop,
        on_complete=done_b_cb,
        command=["/bin/echo", "b"],
    )

    assert handle_a.task_id != handle_b.task_id
    await asyncio.wait_for(done_a.wait(), timeout=5.0)
    await asyncio.wait_for(done_b.wait(), timeout=5.0)


@pytest.mark.asyncio
async def test_env_allowlist_contains_path() -> None:
    """The public allowlist constant includes PATH — sanity check so Chief's
    integration can reason about what survives into the subprocess."""
    assert "PATH" in ENV_ALLOWLIST
    assert "HOME" in ENV_ALLOWLIST


@pytest.mark.asyncio
async def test_secrets_stripped_from_subprocess_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env allowlist — every non-whitelisted secret is stripped from the
    subprocess env. Tests ANTHROPIC_*, AWS_*, GITHUB_TOKEN, OPENAI_API_KEY,
    CUSTOM_SECRET all stripped; PATH and HOME survive.

    This is the Vera CRITICAL — previously a blocklist covered only the two
    ANTHROPIC names, leaking everything else from the owner's shell into a
    subprocess that Claude Code could then exfil via a prompt injection.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-should-be-stripped")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "bearer-should-be-stripped")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_should_be_stripped")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "AKIA-test-should-be-stripped")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "aws-id-should-be-stripped")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-should-be-stripped")
    monkeypatch.setenv("CUSTOM_SECRET", "random-secret-should-be-stripped")

    d = TaskDispatcher()
    output: list[str] = []
    done = asyncio.Event()

    async def on_output(text: str, stream: str) -> None:
        if stream == "stdout":
            output.append(text)

    async def on_complete(exit_code: int, summary: str) -> None:
        done.set()

    cmd = [
        "/bin/sh",
        "-c",
        (
            'echo "ANTHROPIC_API_KEY=[${ANTHROPIC_API_KEY}]"; '
            'echo "ANTHROPIC_AUTH_TOKEN=[${ANTHROPIC_AUTH_TOKEN}]"; '
            'echo "GITHUB_TOKEN=[${GITHUB_TOKEN}]"; '
            'echo "AWS_SECRET_ACCESS_KEY=[${AWS_SECRET_ACCESS_KEY}]"; '
            'echo "AWS_ACCESS_KEY_ID=[${AWS_ACCESS_KEY_ID}]"; '
            'echo "OPENAI_API_KEY=[${OPENAI_API_KEY}]"; '
            'echo "CUSTOM_SECRET=[${CUSTOM_SECRET}]"; '
            'echo "PATH_PRESENT=[${PATH:+yes}]"; '
            'echo "HOME_PRESENT=[${HOME:+yes}]"'
        ),
    ]

    await d.dispatch(
        session_id="env-test",
        task_spec="env-check",
        repo=TMP,
        on_output=on_output,
        on_complete=on_complete,
        command=cmd,
    )
    await asyncio.wait_for(done.wait(), timeout=5.0)

    combined = "".join(output)
    for var in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "GITHUB_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_ACCESS_KEY_ID",
        "OPENAI_API_KEY",
        "CUSTOM_SECRET",
    ):
        assert f"{var}=[]" in combined, f"{var} leaked into subprocess: {combined!r}"
    # Allowlist-preserved vars must survive.
    assert "PATH_PRESENT=[yes]" in combined, f"PATH was stripped: {combined!r}"
    assert "HOME_PRESENT=[yes]" in combined, f"HOME was stripped: {combined!r}"


@pytest.mark.asyncio
async def test_one_task_per_session_raises() -> None:
    """A second dispatch for the same session while the first is live raises."""
    d = TaskDispatcher()
    done = asyncio.Event()

    async def on_output(text: str, stream: str) -> None:
        pass

    async def on_complete(exit_code: int, summary: str) -> None:
        done.set()

    # Long-running first task.
    await d.dispatch(
        session_id="dup",
        task_spec="sleep",
        repo=TMP,
        on_output=on_output,
        on_complete=on_complete,
        command=["/bin/sh", "-c", "sleep 2"],
    )

    with pytest.raises(TaskAlreadyRunning):
        await d.dispatch(
            session_id="dup",
            task_spec="sleep-again",
            repo=TMP,
            on_output=on_output,
            on_complete=on_complete,
            command=["/bin/sh", "-c", "sleep 2"],
        )

    # Cleanup.
    await d.cancel("dup")
    await asyncio.wait_for(done.wait(), timeout=5.0)


@pytest.mark.asyncio
async def test_cancel_terminates_running_task() -> None:
    """cancel() terminates a long-running task and returns True."""
    d = TaskDispatcher()
    done = asyncio.Event()
    seen_exit: list[int] = []

    async def on_output(text: str, stream: str) -> None:
        pass

    async def on_complete(exit_code: int, summary: str) -> None:
        seen_exit.append(exit_code)
        done.set()

    handle = await d.dispatch(
        session_id="c1",
        task_spec="long",
        repo=TMP,
        on_output=on_output,
        on_complete=on_complete,
        command=["/bin/sh", "-c", "sleep 30"],
    )
    assert handle.is_running

    killed = await d.cancel("c1")
    assert killed is True

    await asyncio.wait_for(done.wait(), timeout=5.0)
    assert handle.cancelled is True
    assert handle.exit_code is not None
    assert handle.exit_code != 0


@pytest.mark.asyncio
async def test_cancel_returns_false_when_no_task() -> None:
    d = TaskDispatcher()
    assert await d.cancel("nothing-here") is False


@pytest.mark.asyncio
async def test_cancel_lock_does_not_block_concurrent_dispatch() -> None:
    """cancel() releases the lock before awaiting terminate() — so a dispatch
    on a *different* session during the 5s escalation window must still be
    able to spawn."""
    d = TaskDispatcher()
    done_long = asyncio.Event()
    done_fast = asyncio.Event()

    async def noop_out(text: str, stream: str) -> None:
        pass

    async def done_long_cb(code: int, summary: str) -> None:
        done_long.set()

    async def done_fast_cb(code: int, summary: str) -> None:
        done_fast.set()

    # Session A: long-running, will be cancelled (takes full 5s to terminate
    # because it ignores SIGTERM).
    await d.dispatch(
        session_id="A",
        task_spec="long",
        repo=TMP,
        on_output=noop_out,
        on_complete=done_long_cb,
        command=[
            "/bin/sh",
            "-c",
            "trap '' TERM; sleep 30",
        ],
    )

    # Kick off cancel(A) in the background; it will spend up to 5s waiting
    # for terminate() to land before escalating to SIGKILL.
    cancel_task = asyncio.create_task(d.cancel("A"))

    # Give cancel() a moment to acquire/release the lock and start waiting.
    await asyncio.sleep(0.1)

    # While A's cancel is mid-escalation, we must be able to dispatch B.
    t0 = asyncio.get_running_loop().time()
    await d.dispatch(
        session_id="B",
        task_spec="fast",
        repo=TMP,
        on_output=noop_out,
        on_complete=done_fast_cb,
        command=["/bin/echo", "ok"],
    )
    dispatch_latency_s = asyncio.get_running_loop().time() - t0

    # Dispatch B should be fast — if cancel were holding the lock through
    # terminate/wait, this would stall for ~5 seconds.
    assert dispatch_latency_s < 1.5, (
        f"dispatch(B) blocked {dispatch_latency_s:.2f}s — cancel holds lock too long"
    )

    await asyncio.wait_for(done_fast.wait(), timeout=5.0)
    await asyncio.wait_for(cancel_task, timeout=10.0)
    await asyncio.wait_for(done_long.wait(), timeout=10.0)


@pytest.mark.asyncio
async def test_summarize_returns_tail() -> None:
    """summarize() returns the last N stdout lines joined."""
    d = TaskDispatcher()
    done = asyncio.Event()

    async def on_output(text: str, stream: str) -> None:
        pass

    async def on_complete(exit_code: int, summary: str) -> None:
        done.set()

    handle = await d.dispatch(
        session_id="sum",
        task_spec="counter",
        repo=TMP,
        on_output=on_output,
        on_complete=on_complete,
        command=["/bin/sh", "-c", "for i in 1 2 3 4 5; do echo line-$i; done"],
    )
    await asyncio.wait_for(done.wait(), timeout=5.0)

    summary = d.summarize(handle, max_lines=3)
    lines = summary.split("\n")
    assert lines == ["line-3", "line-4", "line-5"]


@pytest.mark.asyncio
async def test_missing_repo_raises() -> None:
    d = TaskDispatcher()

    async def noop_out(text: str, stream: str) -> None:
        pass

    async def noop_done(code: int, summary: str) -> None:
        pass

    with pytest.raises(FileNotFoundError):
        await d.dispatch(
            session_id="x",
            task_spec="t",
            repo=Path("/this/definitely/does/not/exist"),
            on_output=noop_out,
            on_complete=noop_done,
            command=["/bin/echo", "hi"],
        )


@pytest.mark.asyncio
async def test_get_handle_and_active_sessions() -> None:
    d = TaskDispatcher()
    done = asyncio.Event()

    async def on_output(text: str, stream: str) -> None:
        pass

    async def on_complete(exit_code: int, summary: str) -> None:
        done.set()

    handle = await d.dispatch(
        session_id="gh",
        task_spec="t",
        repo=TMP,
        on_output=on_output,
        on_complete=on_complete,
        command=["/bin/echo", "x"],
    )

    assert d.get_handle("gh") is handle

    await asyncio.wait_for(done.wait(), timeout=5.0)
    # After completion, the handle is still retrievable for status but not active.
    assert d.get_handle("gh") is handle
    assert "gh" not in d.active_sessions()


@pytest.mark.asyncio
async def test_task_spec_length_cap_enforced() -> None:
    """task_spec > MAX_TASK_SPEC_BYTES is rejected with ValueError — before
    we grab the lock or spawn anything."""
    d = TaskDispatcher()

    async def noop_out(text: str, stream: str) -> None:
        pass

    async def noop_done(code: int, summary: str) -> None:
        pass

    huge = "x" * (MAX_TASK_SPEC_BYTES + 1)
    with pytest.raises(ValueError) as excinfo:
        await d.dispatch(
            session_id="huge",
            task_spec=huge,
            repo=TMP,
            on_output=noop_out,
            on_complete=noop_done,
            command=["/bin/echo", "never"],
        )
    assert "too long" in str(excinfo.value)
    assert str(MAX_TASK_SPEC_BYTES) in str(excinfo.value)


@pytest.mark.asyncio
async def test_max_runtime_watchdog_kills_long_task() -> None:
    """A task that exceeds max_runtime_s is SIGKILLed and reported complete.

    This is the Vera+Hawke HIGH — without a watchdog, a wedged subprocess
    would hold a session forever (and the second dispatch would fail with
    TaskAlreadyRunning until timeout).
    """
    d = TaskDispatcher()
    done = asyncio.Event()
    exit_codes: list[int] = []

    async def on_output(text: str, stream: str) -> None:
        pass

    async def on_complete(exit_code: int, summary: str) -> None:
        exit_codes.append(exit_code)
        done.set()

    t0 = asyncio.get_running_loop().time()
    handle = await d.dispatch(
        session_id="watchdog",
        task_spec="long-task",
        repo=TMP,
        on_output=on_output,
        on_complete=on_complete,
        command=["/bin/sh", "-c", "sleep 30"],
        max_runtime_s=0.5,
    )

    await asyncio.wait_for(done.wait(), timeout=5.0)
    elapsed = asyncio.get_running_loop().time() - t0

    assert handle.cancelled is True, "watchdog should mark handle cancelled"
    assert handle.exit_code is not None
    assert handle.exit_code != 0, "killed process should not exit 0"
    assert elapsed < 4.0, f"watchdog took {elapsed:.2f}s — should fire near max_runtime_s"
    assert len(exit_codes) == 1


@pytest.mark.asyncio
async def test_serialized_sender_preserves_order() -> None:
    """serialized_sender funnels frames through a single writer so concurrent
    producers (stdout + stderr pumps + task lifecycle) can't corrupt the WS.
    """
    sent: list[dict] = []
    send_calls_in_flight = 0
    max_concurrent = 0
    lock = asyncio.Lock()

    async def fake_send_json(frame: dict) -> None:
        nonlocal send_calls_in_flight, max_concurrent
        async with lock:
            send_calls_in_flight += 1
            max_concurrent = max(max_concurrent, send_calls_in_flight)
        # Simulate network — if two calls could race, we'd observe concurrency.
        await asyncio.sleep(0.005)
        sent.append(frame)
        async with lock:
            send_calls_in_flight -= 1

    enqueue, drain = serialized_sender(fake_send_json)

    # Fan-in from three "pumps" concurrently.
    async def producer(prefix: str) -> None:
        for i in range(10):
            await enqueue({"type": prefix, "i": i})

    await asyncio.gather(producer("a"), producer("b"), producer("c"))
    await drain()

    assert len(sent) == 30
    # Despite three concurrent producers, only one send_json is ever in flight.
    assert max_concurrent == 1, f"expected serial sends, saw {max_concurrent} concurrent"
    # Per-producer frame order is preserved (asyncio.Queue is FIFO per producer).
    for prefix in ("a", "b", "c"):
        got = [f["i"] for f in sent if f["type"] == prefix]
        assert got == list(range(10)), f"producer {prefix} order scrambled: {got}"


def test_task_handle_autoderives_task_id() -> None:
    """TaskHandle.__post_init__ sets task_id from started_at when not passed."""
    # Build a handle with a dummy proc (we won't start it).
    class _FakeProc:
        def __init__(self) -> None:
            self.returncode = None
            self.pid = -1

    when = datetime(2026, 4, 18, 12, 0, 0, tzinfo=timezone.utc)
    h = TaskHandle(
        session_id="s",
        task_spec="t",
        repo=TMP,
        started_at=when,
        proc=_FakeProc(),  # type: ignore[arg-type]
    )
    assert h.task_id == when.isoformat()
