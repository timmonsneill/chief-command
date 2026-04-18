# Dispatch Bridge — Glue Hook Spec

**Purpose:** This is the ~50-line integration that goes into
`backend/app/websockets.py` AFTER both Riggs (Chief Memory v1.1) and Nova
(Dispatch Bridge v1) merge. Written in advance so the merge step is mechanical.

**Author:** Chief, 2026-04-18

**Who pastes this:** Chief, post-merge, after the 5-reviewer sweep on the
merged state. Not a builder task.

---

## What the hook does

Before every user turn hits `_handle_text_turn` (Riggs's memory-loaded LLM
path), the hook:

1. Runs `detect_project_switch` (Riggs's regex) — scope changes don't need
   a classifier call.
2. Runs `classify_intent` (Nova's classifier) → returns one of
   `chat | task | status | cancel`.
3. Routes:
   - `chat` → Riggs's `_handle_text_turn` (memory-equipped API response)
   - `task` → `dispatcher.dispatch(...)` with narration
   - `status` → summarize running task stdout via Haiku + TTS
   - `cancel` → `dispatcher.cancel(...)`

## Concurrency model

- **One dispatched task per WS session.** If a task is running and the
  classifier returns `task` again, Chief voice-replies
  *"Still working on the previous task. Say 'status' for an update, or 'stop'
  to cancel and start over."*
- Chat turns are NOT blocked while a task runs — user can ask questions
  mid-build. Those stay on Anthropic API.
- Voice narration of task progress is fire-and-forget; does not block the
  WS message loop.

## Exact code to paste

Replace the current (post-Riggs-merge) routing block inside
`_handle_text_turn` / the WS message loop with:

```python
from services.classifier import classify_intent
from services.dispatcher import TaskDispatcher, TaskAlreadyRunning
from services.repo_map import get_repo_path

# Module-level singleton — one dispatcher instance shared across WS sessions,
# state keyed by session_id
_dispatcher = TaskDispatcher()


async def _route_user_turn(
    ws: WebSocket,
    session_id: str,
    history: list[dict],
    user_text: str,
    current_project: str,
) -> None:
    """Single entry point that classifies then routes to chat/task/status/cancel."""
    # Switch intent has already run upstream. Now classify.
    result = await classify_intent(user_text, current_project)
    intent = result["intent"]

    if intent == "chat":
        await _handle_text_turn(ws, session_id, history, user_text, current_project)
        return

    if intent == "task":
        await _route_task(ws, session_id, history, result.get("task_spec") or user_text, current_project)
        return

    if intent == "status":
        await _route_status(ws, session_id, history, current_project)
        return

    if intent == "cancel":
        await _route_cancel(ws, session_id)
        return

    # Should not reach — classifier is typed
    await _handle_text_turn(ws, session_id, history, user_text, current_project)


async def _route_task(
    ws: WebSocket, session_id: str, history: list[dict],
    task_spec: str, current_project: str,
) -> None:
    repo = get_repo_path(current_project)
    if repo is None or not repo.exists():
        # No local repo configured for this scope. Fall back to chat so Chief
        # can explain rather than silently fail.
        await ws.send_json({
            "type": "token",
            "text": f"I can't dispatch — no local repo configured for {current_project}. ",
        })
        await _handle_text_turn(ws, session_id, history, task_spec, current_project)
        return

    async def on_output(text: str, stream: str) -> None:
        await ws.send_json({"type": "task_output", "text": text, "stream": stream})

    async def on_complete(exit_code: int, summary: str) -> None:
        await ws.send_json({
            "type": "task_complete",
            "exit_code": exit_code,
            "duration_seconds": int((datetime.now(timezone.utc) - handle.started_at).total_seconds()),
            "summary": summary,
        })
        # Voice narration of completion — single TTS sentence
        await _narrate(ws, f"Task complete. Exit code {exit_code}. {summary[:160]}")

    try:
        handle = await _dispatcher.dispatch(
            session_id=session_id,
            task_spec=task_spec,
            repo=repo,
            on_output=on_output,
            on_complete=on_complete,
        )
    except TaskAlreadyRunning:
        await _narrate(ws, "Still working on the previous task. Say status for an update, or stop to cancel.")
        return

    # Initial narration: immediate, deterministic (no LLM call needed)
    await ws.send_json({
        "type": "task_started",
        "task_spec": task_spec,
        "repo": str(repo),
        "started_at": handle.started_at.isoformat(),
    })
    await _narrate(ws, f"Dispatching to Claude Code on your Mac. Working in {current_project}. I'll let you know when it's done.")


async def _route_status(
    ws: WebSocket, session_id: str, history: list[dict], current_project: str,
) -> None:
    handle = _dispatcher.get_handle(session_id)
    if handle is None:
        await _narrate(ws, "No task running right now. Ask me something or give me a build task.")
        return

    # Summarize live stdout via Haiku
    tail = _dispatcher.summarize(handle, max_lines=50)
    summary_prompt = (
        "You summarize what a coding agent is currently doing in ONE short spoken sentence. "
        "Input is the last 50 stdout lines. Output the sentence only, no quotes, no preamble."
    )
    try:
        from services.classifier import _get_client  # reuse the Haiku client
        resp = await _get_client().messages.create(
            model="claude-haiku-4-5",
            max_tokens=80,
            system=[{"type": "text", "text": summary_prompt}],
            messages=[{"role": "user", "content": tail[-4000:]}],
        )
        sentence = "".join(
            getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text"
        ).strip() or "Working."
    except Exception:
        sentence = "Working — no new output to summarize."

    elapsed = int((datetime.now(timezone.utc) - handle.started_at).total_seconds())
    await _narrate(ws, f"{sentence} Running for {elapsed // 60} minutes {elapsed % 60} seconds.")


async def _route_cancel(ws: WebSocket, session_id: str) -> None:
    killed = await _dispatcher.cancel(session_id)
    if killed:
        await ws.send_json({"type": "task_cancelled", "reason": "owner-requested"})
        await _narrate(ws, "Cancelled. Task killed.")
    else:
        await _narrate(ws, "Nothing to cancel — no task running.")


async def _narrate(ws: WebSocket, text: str) -> None:
    """Send a short line of text as a token + trigger TTS via the existing tts_worker.

    Uses the same tts_queue + tts_worker pattern as _run_llm_turn. Keeps voice
    consistent between chat narration and task narration.
    """
    # NOTE: this reuses the existing TTS sentence-queue infrastructure from
    # _run_llm_turn. Concrete wiring lives in the merged code; conceptually:
    # - emit {"type": "token", "text": text + " "} so the assistant bubble updates
    # - queue `text` to the TTS worker
    # - when worker flushes, the existing tts_start/tts_end frames fire
    await ws.send_json({"type": "token", "text": text + " "})
    await ws.send_json({"type": "message_done"})
    # TTS synthesis: wire to tts_service.synthesize_stream and send_bytes inline
    # (see tts_worker in _run_llm_turn for the pattern)
    try:
        from services.tts import tts_service
        async for chunk in tts_service.synthesize_stream(text):
            await ws.send_bytes(chunk)
    except Exception as exc:
        logger.warning("narration TTS failed: %s", exc)
    await ws.send_json({"type": "tts_end"})
```

## Call-site change

Two things to wire in the WS message loop inside `voice_ws()`:

### 1. Add a direct `{type: "cancel"}` handler BEFORE classifier routing

The TaskBubble's Cancel button sends `{type: "cancel"}` inbound. Handle it
directly — don't route through the classifier, that's a waste of a Haiku call
for a known intent with a known action. Add this branch in the text-message
handling block, alongside `msg_type == "interrupt"` and `msg_type == "context"`:

```python
if msg_type == "cancel":
    # Direct from the TaskBubble Cancel button. Bypass the classifier.
    await _route_cancel(ws, session_id)
    continue
```

### 2. Replace the turn-routing call

Riggs's post-respin code calls `_handle_text_turn` directly. Swap the call site
so classifier routing happens first:

```python
# Before:
await _maybe_switch_project(ws, user_text)
current_turn_task = asyncio.create_task(
    _handle_text_turn(ws, sid, history, text_content, current_project)
)

# After:
await _maybe_switch_project(ws, user_text)
current_turn_task = asyncio.create_task(
    _route_user_turn(ws, sid, history, text_content, current_project)
)
```

Same wrapping in a `create_task` so barge-in cancellation still works on the
chat path. Task-mode dispatched subprocesses run independently — they aren't
cancelled by barge-in (user can still speak mid-dispatch).

## Barge-in interaction

- Barge-in cancels the current `current_turn_task` — that cancels a
  chat-path LLM stream OR a narration TTS, whichever is active.
- Barge-in does NOT kill a dispatched subprocess. That's what the "cancel"
  intent is for. This is intentional — the user might mumble over a narration
  ("oh wait") without wanting to kill an hour-long build.

## WS disconnect cleanup (Hawke)

Hawke flagged: if the browser disconnects mid-dispatch, the `claude` subprocess
is orphaned and keeps running. Add to the `finally:` block of `voice_ws`:

```python
finally:
    # Kill any dispatched subprocess tied to this session.
    try:
        await _dispatcher.cancel(session_id)
    except Exception:
        pass
    # ...existing session close...
```

## Scope safety

- `get_repo_path(current_project)` is the gate. If the scope has no mapped
  repo (Butler/Archie don't today), `_route_task` falls back to chat with
  a clarifying message. No subprocess spawned → no risk of wrong-repo write.
- `cwd=str(repo)` on `create_subprocess_exec` means the `claude` CLI starts
  in the correct directory. Any files it writes land in that repo.
- `env` strips `ANTHROPIC_API_KEY` so Max subscription is used.

## Narration prompts — summary Haiku call

System prompt for the "status" summarizer (inline in `_route_status`):

> *"You summarize what a coding agent is currently doing in ONE short spoken sentence. Input is the last 50 stdout lines. Output the sentence only, no quotes, no preamble."*

- Keep responses <20 words — voice output is slow.
- Max tokens 80.
- Truncate input stdout tail to last 4KB to stay cheap.

## Sanity tests (after glue lands)

Forge should verify:

1. `"What's Chief Command?"` → chat → memory-equipped LLM reply (Riggs's path).
2. `"Build a smoke test for the login page."` → task → subprocess spawns in
   `~/Desktop/chief-command`, stdout streams, voice narrates dispatch.
3. Mid-dispatch: `"status?"` → Haiku summarizes current stdout, voice reads it.
4. Mid-dispatch: `"stop"` → subprocess killed via SIGTERM → SIGKILL after 5s.
5. No local repo for a scope (switch to Butler): `"Build X"` → chat fallback
   with clarifying message, no subprocess.
6. `ANTHROPIC_API_KEY` verified absent from subprocess env via `ps eww <pid>`.
7. User types while task runs: chat path works, task unaffected.
8. User barge-in during narration: TTS cuts, task survives.

## Open questions (resolve post-merge if they bite)

- **Session ID stability across WS reconnects.** If the iPhone's WS drops and
  reconnects mid-dispatch, does the new connection's `session_id` still match
  the running task's handle? Likely not — we'll want to key task state by a
  stable user identifier rather than ephemeral session_id. Flag for v2.
- **`claude --print` stdout vs stderr semantics.** The CLI might emit progress
  to stderr and the final result to stdout, or mixed. Test during Forge's
  integration run and adjust `on_output` routing if needed.
- **Max concurrent dispatches across multiple WS sessions.** Right now one
  dispatcher instance holds state per session. If Neill opens voice on two
  devices, two tasks can run in parallel. Probably fine. Flag for v2.
