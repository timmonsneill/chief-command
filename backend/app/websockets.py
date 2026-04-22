"""WebSocket endpoints for voice and terminal streaming."""

import asyncio
import json
import logging
import signal
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services.auth import verify_token
from services import stt_service, tts_service
from services.audio_utils import convert_webm_to_wav
from services.chief_context import build_chief_system
from services.classifier import classify_intent
from services.dispatcher import TaskDispatcher, TaskAlreadyRunning
from services.history_store import append_turn, load_recent_for_project
from services.llm import stream_turn
from services.project_context import (
    AVAILABLE_PROJECTS,
    DEFAULT_PROJECT,
    _context_store,
    detect_project_switch,
)
from services.repo_map import get_repo_path
from services.router import classify_and_route, random_thinking_phrase
from services.usage_tracker import create_session, close_session, record_turn, get_session_totals

logger = logging.getLogger(__name__)

router = APIRouter()

# Module-level singleton — one dispatcher instance shared across WS sessions,
# state keyed by session_id.
_dispatcher = TaskDispatcher()


# ---------------------------------------------------------------------------
# Outbound WS message-type tags
#
# Keep string tags centralized so emit sites + tests can't drift apart.
# Mirrors the discriminant literals on `WsEvent` in frontend/src/lib/api.ts.
# ---------------------------------------------------------------------------
MSG_CONTEXT_SWITCHED = "context_switched"


# ---------------------------------------------------------------------------
# Dissolved-scope migration: "Archie" -> "Arch"
#
# Archie was a separate scope prior to 2026-04-20; it's since been folded into
# Arch (same project, Archie is just the brain layer). Any persisted client
# state or in-memory _context_store value that still reads "Archie" must be
# remapped to "Arch" on read. Helper is idempotent — values already canonical
# pass through unchanged.
# ---------------------------------------------------------------------------
def _migrate_dissolved_scope(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if value == "Archie":
        return "Arch"
    return value


# ---------------------------------------------------------------------------
# Serialized WS send helpers (Hawke CRITICAL — concurrent WS writes)
#
# Starlette's WebSocket.send_{json,bytes} is NOT guaranteed to serialize
# concurrent writes from separate tasks. In the dispatch-glue world the
# voice WS has several concurrent producers:
#   - main receive loop (sends transcript / token / etc.)
#   - turn task (_run_llm_turn) streaming tokens + TTS bytes
#   - dispatcher stdout pump (_route_task on_output)
#   - dispatcher completion callback (_route_task on_complete)
#   - narration (_narrate) emitting tts_start / bytes / tts_end / message_done
#
# Without explicit serialization two of these can interleave mid-frame on
# the underlying transport and corrupt bytes on the wire. Per-connection
# asyncio.Lock funnels every write through a single critical section; the
# lock lives on the `ws` object as an attribute so all module-level helpers
# share it across call sites.
# ---------------------------------------------------------------------------


def _get_send_lock(ws: WebSocket) -> asyncio.Lock:
    lock: Optional[asyncio.Lock] = getattr(ws, "_send_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        ws._send_lock = lock  # type: ignore[attr-defined]
    return lock


async def ws_send_json(ws: WebSocket, payload: dict) -> None:
    async with _get_send_lock(ws):
        await ws.send_json(payload)


async def ws_send_bytes(ws: WebSocket, data: bytes) -> None:
    async with _get_send_lock(ws):
        await ws.send_bytes(data)


def _drain_queue(queue: asyncio.Queue) -> int:
    """Empty an asyncio.Queue synchronously. Returns the count drained.

    Track B #6: before cancelling a tts worker, drain anything buffered so
    the next ``queue.get()`` after the cancel flag can't pull one more
    sentence and synthesize it before the flag is seen. Ordering matters:
    drain first, THEN cancel the task.
    """
    drained = 0
    while not queue.empty():
        try:
            queue.get_nowait()
            drained += 1
        except asyncio.QueueEmpty:
            break
    return drained


async def _authenticate_ws(ws: WebSocket) -> Optional[str]:
    """Validate the connecting client's JWT.

    Returns the JWT subject (e.g. ``"owner"``) on success, ``None`` on
    failure. Callers should treat a non-None return as authenticated;
    the subject doubles as a stable ``client_id`` for history resume.
    """
    token = ws.query_params.get("token")
    if token:
        sub = verify_token(token)
        if sub:
            return sub
    try:
        first = await asyncio.wait_for(ws.receive_text(), timeout=5.0)
        try:
            data = json.loads(first)
            token = data.get("token")
        except json.JSONDecodeError:
            token = first.strip()
        if token:
            sub = verify_token(token)
            if sub:
                return sub
    except (asyncio.TimeoutError, WebSocketDisconnect):
        pass
    return None


@router.websocket("/ws/voice")
async def voice_ws(ws: WebSocket) -> None:
    """Voice WebSocket endpoint.

    Inbound frames:
      text  {"type": "text", "content": "..."}
      text  {"type": "interrupt"}
      text  {"type": "context", "project": "..."}
      binary  raw audio (WebM/Opus)

    Outbound frames:
      {"type": "transcript", "content": "..."}       — STT result
      {"type": "active_model", "model": "...", "is_deep": bool} — routing decision
      {"type": "bridge_phrase", "text": "..."}        — spoken while Opus thinks
      {"type": "token", "text": "..."}                — streaming token
      {"type": "tts_start"}                           — TTS about to begin
      binary                                          — WAV audio chunk
      {"type": "tts_end"}                             — TTS done
      {"type": "message_done"}                        — full turn complete
      {"type": "usage", ...}                          — token/cost summary
      {"type": "context_switched", "project": "..."}  — owner switched scope
      {"type": "turn_cancelled", "reason": "..."}     — prior turn aborted
      {"type": "error", "message": "..."}
    """
    await ws.accept()
    client_id = await _authenticate_ws(ws)
    if client_id is None:
        await ws_send_json(ws, {"type": "error", "message": "Unauthorized"})
        await ws.close(code=4001)
        return

    session_id: Optional[str] = None
    history: list[dict] = []

    # Per-subject scope keying. The in-memory ``_context_store`` is a
    # module-level dict; before this fix it was keyed by the hardcoded literal
    # ``"ws"``, meaning every open WS (second tab, second device, reload after
    # a JWT re-auth, uvicorn restart) stomped on or read someone else's scope.
    # Key by the JWT subject so each authenticated owner session has its own
    # slot. ``client_id`` is guaranteed non-None past the auth gate above; we
    # snapshot into a clearer name for readability.
    context_key = client_id
    # Default scope: Chief Command. Per owner: scope is ALWAYS a concrete single project.
    # If an earlier session persisted the dissolved "Archie" scope into the
    # in-memory store, migrate it before we use it as the initial value.
    initial = _migrate_dissolved_scope(_context_store.get(context_key)) or DEFAULT_PROJECT
    if initial not in AVAILABLE_PROJECTS:
        initial = DEFAULT_PROJECT
    current_project: str = initial
    _context_store[context_key] = current_project  # persist the possibly-migrated value

    # Context-frame gate. The frontend sends a ``{type: "context", project: ...}``
    # frame immediately after WS open, but that frame arrives asynchronously.
    # If the owner sends a turn (audio or text) before we process it, the turn
    # runs with whatever ``current_project`` was rehydrated from the store,
    # which may be stale from a prior session. Defer any non-context inbound
    # frames until we either (a) receive a context frame, or (b) hit the
    # timeout and accept the rehydrated scope as authoritative.
    #
    # The timeout is absolute — tracked against the WS accept() moment — so
    # slow clients that send a context frame ~900ms in still get their scope
    # applied before the gate opens.
    #
    # Implementation is an ``asyncio.Event`` set either by the context-frame
    # handler below OR by the deadline path in ``_await_context_gate``. User
    # turns are spawned as child tasks that ``await`` the event; the main
    # receive loop keeps running so the context frame CAN land mid-deferral.
    CONTEXT_GATE_TIMEOUT_S = 1.0
    ws_accepted_at = asyncio.get_event_loop().time()

    context_gate_event = asyncio.Event()

    async def _await_context_gate() -> None:
        """Block (in the turn task — NOT the receive loop) until we've seen a
        context frame, or fall through on timeout.

        Called at the head of every user-turn path (text + audio) inside the
        spawned ``_route_user_turn`` wrapper task. Because the turn runs in a
        child task, the main receive loop keeps servicing ``ws.receive()`` —
        that's how a context frame sent ~50ms after a user's first utterance
        can still flip the gate open and let the deferred turn proceed with
        the correct scope.

        Timeout: ``CONTEXT_GATE_TIMEOUT_S`` measured against WS accept() time.
        After that we fall back to the rehydrated subject-keyed scope and log
        WARNING so the fallback is never silent. The rehydrated scope is
        already safer than the pre-fix global store — each JWT subject has
        its own slot in ``_context_store`` now.
        """
        if context_gate_event.is_set():
            return
        loop = asyncio.get_event_loop()
        remaining = (ws_accepted_at + CONTEXT_GATE_TIMEOUT_S) - loop.time()
        if remaining <= 0:
            if not context_gate_event.is_set():
                logger.warning(
                    "voice_ws context-frame gate already past deadline subject=%s "
                    "falling back to rehydrated scope=%s",
                    context_key, current_project,
                )
                context_gate_event.set()
            return
        try:
            await asyncio.wait_for(context_gate_event.wait(), timeout=remaining)
        except asyncio.TimeoutError:
            logger.warning(
                "voice_ws context-frame gate timed out subject=%s "
                "falling back to rehydrated scope=%s",
                context_key, current_project,
            )
            context_gate_event.set()

    # Rehydrate recent conversation context for the current project scope so
    # reconnects (and uvicorn --reload restarts) don't feel amnesiac. We
    # deliberately do NOT resume the prior session_id — a fresh uuid is
    # allocated on the first turn (via ensure_session) so usage tracking
    # doesn't attribute turns to a ghost session row. Hawke CRITICAL 2026-04-20.
    try:
        history = await load_recent_for_project(current_project, limit=20)
        logger.info(
            "voice_ws hydrated scope=%s history_turns=%d (fresh session)",
            current_project, len(history),
        )
    except Exception as exc:
        # Best-effort: a broken DB shouldn't 500 the WS.
        logger.warning("voice_ws history rehydrate failed: %s", exc)
        history = []
    # TTS speed multiplier. Applied server-side via Google's `speaking_rate` so
    # the audio is time-stretched without pitch shift. Frontend must NOT apply
    # playbackRate on top — that would re-introduce the chipmunk effect.
    current_speed: float = 1.0
    current_turn_task: Optional[asyncio.Task] = None

    async def ensure_session() -> str:
        """Lazy-create session on first real turn to avoid ghost rows from
        status-only WS connections (e.g. Layout's connection-status indicator)."""
        nonlocal session_id
        if session_id is None:
            session_id = str(uuid.uuid4())
            await create_session(session_id, project=current_project)
            logger.info("Voice WS session started session=%s project=%s", session_id, current_project)
        return session_id

    async def cancel_current_turn(reason: str) -> None:
        """Cancel an in-flight turn and notify the client. Awaits full teardown
        so sends are serialized on the WS — no concurrent writes with the turn task.

        Track B #5/#6 cancel order:
          1. Set the per-turn TTS cancel_event (if attached) so synthesize_stream
             stops at the next chunk boundary.
          2. Drain the tts_queue so the worker can't grab one more sentence
             between the cancel flag check and the queue.get() call.
          3. Cancel the outer turn task (which cascades into the LLM stream +
             TTS worker CancelledError paths).
          4. Await full teardown so subsequent WS writes (turn_cancelled +
             later narrations) don't race with the turn's final writes.
        """
        nonlocal current_turn_task
        if current_turn_task and not current_turn_task.done():
            logger.info("Voice WS cancelling turn session=%s reason=%s", session_id, reason)
            # Pull the per-turn event + queue off the task if they were attached.
            tts_event = getattr(current_turn_task, "_tts_cancel_event", None)
            tts_queue = getattr(current_turn_task, "_tts_queue", None)
            if tts_event is not None:
                tts_event.set()
            if tts_queue is not None:
                drained = _drain_queue(tts_queue)
                if drained:
                    logger.info(
                        "Voice WS drained %d buffered TTS sentences on cancel "
                        "session=%s",
                        drained, session_id,
                    )
            current_turn_task.cancel()
            try:
                await current_turn_task
            except (asyncio.CancelledError, Exception):
                pass
            try:
                await ws_send_json(ws, {"type": "turn_cancelled", "reason": reason})
            except Exception:
                pass
        current_turn_task = None

    async def _maybe_switch_project(user_text: str) -> None:
        """Run switch-intent detection on user text; update scope + notify client.

        Scope is ALWAYS a concrete project. If detection returns a value, we
        switch to it; if the new scope matches the current one, we no-op.

        On a successful switch we:
          1. Run the dissolved-scope migration (Archie -> Arch) so a stale
             persisted value doesn't leak into the server-side store.
          2. Persist the canonical value into ``_context_store`` so subsequent
             reads by any other caller (HTTP /context GET, status summary, etc.)
             see the same scope the voice path just applied.
          3. Push a ``context_switched`` frame to the client so the UI
             ``ProjectContextProvider`` can update the picker in real time —
             the UI was stale before this was wired up.
        """
        nonlocal current_project
        detected = _migrate_dissolved_scope(detect_project_switch(user_text))
        if detected is None:
            return
        if detected == current_project:
            return
        current_project = detected
        _context_store[context_key] = current_project
        logger.info(
            "Voice WS project-switch intent detected text=%r -> project=%s",
            user_text[:80], current_project,
        )
        try:
            await ws_send_json(ws, {
                "type": MSG_CONTEXT_SWITCHED,
                "project": detected,
            })
        except Exception as exc:  # client gone, swallow
            logger.warning("Failed to send context_switched frame: %s", exc)

    try:
        while True:
            message = await ws.receive()

            if message.get("type") == "websocket.disconnect":
                break

            if "text" in message:
                raw = message["text"]
                logger.info("Voice WS TEXT inbound: %s", raw[:200])
                try:
                    data = json.loads(raw)
                    msg_type = data.get("type")
                    text_content = data.get("content", "")
                except json.JSONDecodeError:
                    msg_type = None
                    text_content = raw

                if msg_type == "context":
                    # Project context frame — validate against allowlist, no LLM turn.
                    # Scope is always concrete; unknown values fall back to default.
                    raw_proj = _migrate_dissolved_scope(data.get("project") or None)
                    if raw_proj in AVAILABLE_PROJECTS:
                        current_project = raw_proj
                    else:
                        current_project = DEFAULT_PROJECT
                    # Persist keyed by JWT subject so per-session scope doesn't
                    # stomp on other tabs / devices / restarts. See context_key
                    # note above.
                    _context_store[context_key] = current_project
                    context_gate_event.set()  # unblock any deferred user turn
                    logger.info(
                        "Voice WS context updated subject=%s project=%s",
                        context_key, current_project,
                    )
                    # Confirm the (possibly migrated) scope to the client so the
                    # Provider pill reflects the authoritative server value.
                    try:
                        await ws_send_json(ws, {
                            "type": MSG_CONTEXT_SWITCHED,
                            "project": current_project,
                        })
                    except Exception as exc:
                        logger.warning("Failed to echo context_switched frame: %s", exc)
                    continue

                if msg_type == "speed":
                    # TTS speed preference. Clamp to Google's supported range
                    # (0.25-4.0) and fall back to 1.0 on bad input. No LLM turn.
                    raw_speed = data.get("value")
                    try:
                        new_speed = float(raw_speed)
                        if not (0.25 <= new_speed <= 4.0):
                            new_speed = 1.0
                    except (TypeError, ValueError):
                        new_speed = 1.0
                    current_speed = new_speed
                    logger.info("Voice WS speed updated speed=%.2f", current_speed)
                    continue

                if msg_type == "interrupt":
                    await cancel_current_turn("barge-in")
                    continue

                if msg_type == "cancel":
                    # Direct from the TaskBubble Cancel button. Bypass the
                    # classifier — known intent with a known action, no need
                    # to spend a Haiku call on it.
                    #
                    # Hawke CRITICAL: cancel must supersede any in-flight turn
                    # (chat or dispatched narration) BEFORE we issue the
                    # dispatcher cancel + cancel-narration. If we don't, the
                    # prior turn's TTS worker can still be emitting frames
                    # while _route_cancel emits turn_cancelled + narration ->
                    # two concurrent writers on the same WS.
                    await cancel_current_turn("user-cancelled")
                    sid = await ensure_session()
                    await _route_cancel(ws, sid)
                    continue

                if msg_type and msg_type != "text":
                    logger.info("Voice WS ignoring non-text message type: %s", msg_type)
                    continue

                if not text_content or not text_content.strip():
                    logger.info("Voice WS empty text — skipping")
                    continue

                await cancel_current_turn("superseded")
                sid = await ensure_session()
                # Check for switch intent BEFORE the LLM call. If the owner said
                # "switch to Arch", we update scope and still continue the turn
                # with the new scope so Chief replies already-oriented.
                await _maybe_switch_project(text_content)
                # Gate + dispatch happens in a child task so the receive loop
                # keeps servicing ``ws.receive()`` — a context frame racing
                # with this turn can still land and flip the gate before we
                # run the LLM. See _await_context_gate() docstring.
                async def _gated_text_turn(
                    sid=sid,
                    text_content=text_content,
                    speed=current_speed,
                ) -> None:
                    await _await_context_gate()
                    scope = current_project  # re-read AFTER gate opens
                    logger.info(
                        "Voice WS handling text turn session=%s len=%d scope=%s",
                        sid, len(text_content), scope,
                    )
                    await _route_user_turn(ws, sid, history, text_content, scope, speed)
                current_turn_task = asyncio.create_task(_gated_text_turn())

            elif "bytes" in message:
                audio_data: bytes = message["bytes"]
                logger.info("Voice WS AUDIO inbound: %d bytes", len(audio_data))
                await cancel_current_turn("superseded")
                sid = await ensure_session()
                # Transcribe inline (not in a background task) so we can run
                # switch detection on the utterance before starting the turn.
                try:
                    wav_data = await convert_webm_to_wav(audio_data)
                    transcript = await stt_service.transcribe(wav_data)
                except Exception as exc:
                    logger.exception("Audio conversion/transcription failed: %s", exc)
                    await ws_send_json(ws, {"type": "error", "message": "Could not transcribe audio"})
                    continue

                if not transcript:
                    await ws_send_json(ws, {"type": "error", "message": "Could not transcribe audio"})
                    continue

                await ws_send_json(ws, {"type": "transcript", "content": transcript})
                await _maybe_switch_project(transcript)
                # See the text-path commentary above — spawn a child task that
                # awaits the context gate before running the turn, so a context
                # frame racing with the first utterance still lands first.
                async def _gated_audio_turn(
                    sid=sid,
                    transcript=transcript,
                    speed=current_speed,
                ) -> None:
                    await _await_context_gate()
                    scope = current_project  # re-read AFTER gate opens
                    logger.info(
                        "Voice WS handling audio turn session=%s len=%d scope=%s",
                        sid, len(transcript), scope,
                    )
                    await _route_user_turn(ws, sid, history, transcript, scope, speed)
                current_turn_task = asyncio.create_task(_gated_audio_turn())

            else:
                logger.warning("Voice WS unknown message shape keys=%s", list(message.keys()))

    except WebSocketDisconnect:
        logger.info("Voice WS disconnected session=%s", session_id)
    except Exception as exc:
        logger.exception("Voice WS error session=%s: %s", session_id, exc)
        try:
            await ws_send_json(ws, {"type": "error", "message": "Internal error"})
        except Exception:
            pass
    finally:
        # Hawke: kill any dispatched subprocess tied to this session so a
        # browser disconnect doesn't orphan the `claude` CLI child.
        try:
            if session_id is not None:
                await _dispatcher.cancel(session_id)
        except Exception:
            pass
        if current_turn_task and not current_turn_task.done():
            current_turn_task.cancel()
            try:
                await current_turn_task
            except (asyncio.CancelledError, Exception):
                pass
        if session_id is not None:
            await close_session(session_id)


async def _run_llm_turn(
    ws: WebSocket,
    session_id: str,
    history: list[dict],
    user_text: str,
    project_scope: str,
    current_speed: float = 1.0,
) -> None:
    """Core LLM streaming loop: route → stream tokens → TTS → record."""
    model, is_deep = classify_and_route(user_text)
    await ws_send_json(ws, {"type": "active_model", "model": model, "is_deep": is_deep})

    tts_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
    # Per-turn TTS cancellation event (Track B #5/#6). Passed into
    # synthesize_stream so the TTS worker stops emitting audio bytes at the
    # next chunk boundary when cancel_current_turn flips it. Storing on the
    # current task lets the outer cancel path find it without a third callback
    # parameter.
    tts_cancel_event = asyncio.Event()
    current_task = asyncio.current_task()
    if current_task is not None:
        # Attach to the task so cancel_current_turn (which has the task handle
        # but not this local) can drain+cancel cleanly. getattr/setattr is the
        # canonical way to pin arbitrary state on a Task — no class gymnastics.
        current_task._tts_cancel_event = tts_cancel_event  # type: ignore[attr-defined]
        current_task._tts_queue = tts_queue  # type: ignore[attr-defined]

    if is_deep:
        bridge = random_thinking_phrase()
        await ws_send_json(ws, {"type": "bridge_phrase", "text": bridge})
        await tts_queue.put(bridge)

    async def send_token(text: str) -> None:
        await ws_send_json(ws, {"type": "token", "text": text})

    async def send_tts_sentence(sentence: str) -> None:
        await tts_queue.put(sentence)

    async def tts_worker() -> None:
        try:
            await ws_send_json(ws, {"type": "tts_start"})
            while True:
                if tts_cancel_event.is_set():
                    break
                sentence = await tts_queue.get()
                if sentence is None:
                    break
                if tts_cancel_event.is_set():
                    break
                try:
                    async for chunk in tts_service.synthesize_stream(
                        sentence,
                        speed=current_speed,
                        cancel_event=tts_cancel_event,
                    ):
                        if tts_cancel_event.is_set():
                            break
                        await ws_send_bytes(ws, chunk)
                except TypeError:
                    # Fallback for a TTS provider that hasn't been updated
                    # with the cancel_event kwarg yet. Less responsive, but
                    # won't kill the turn if a custom provider is slotted in.
                    async for chunk in tts_service.synthesize_stream(
                        sentence, speed=current_speed,
                    ):
                        if tts_cancel_event.is_set():
                            break
                        await ws_send_bytes(ws, chunk)
                except Exception as tts_err:
                    logger.warning("TTS failed for sentence: %s", tts_err)
        finally:
            try:
                await ws_send_json(ws, {"type": "tts_end"})
            except Exception:
                # WS may already be torn down on cancel — don't let the
                # tts_end failure mask the primary cancel.
                pass

    history.append({"role": "user", "content": user_text})
    # Persist the user turn before we kick off the LLM stream. If the stream
    # fails or gets cancelled mid-flight, the user utterance is still on
    # disk — reconnect will show it in the rehydrated history.
    try:
        await append_turn(session_id, project_scope, "user", user_text)
    except Exception as exc:
        logger.warning("history persist (user) failed session=%s: %s", session_id, exc)

    tts_task = asyncio.create_task(tts_worker())

    # Build Chief system prompt blocks — identity + memory + roster + project scope.
    # Deterministic for (scope, file-contents) so Anthropic prompt caching works.
    # File reads are blocking I/O — wrap in to_thread to avoid stalling the loop.
    system_blocks = await asyncio.to_thread(build_chief_system, project_scope)

    try:
        usage = await stream_turn(
            history=history,
            model=model,
            send_token=send_token,
            send_tts_sentence=send_tts_sentence,
            project_scope=project_scope,
            system_blocks=system_blocks,
        )

        await tts_queue.put(None)
        await tts_task

        assistant_text = usage.get("assistant_text", "")
        history.append({"role": "assistant", "content": assistant_text})
        # Persist the assistant reply so resume rebuilds both sides of the
        # turn. Best-effort: if the DB write fails we still finish the turn.
        try:
            await append_turn(session_id, project_scope, "assistant", assistant_text)
        except Exception as exc:
            logger.warning(
                "history persist (assistant) failed session=%s: %s", session_id, exc
            )

        await ws_send_json(ws, {"type": "message_done"})

        turn = await record_turn(
            session_id=session_id,
            model=model,
            usage_dict=usage,
            user_text=user_text,
            assistant_text=assistant_text,
        )
        totals = await get_session_totals(session_id)

        await ws_send_json(ws, {
            "type": "usage",
            "session_id": session_id,
            "model": model,
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cached_tokens": usage.get("cache_read_input_tokens", 0),
            "turn_cost_cents": turn["cost_cents"],
            "session_total_cents": totals.get("cost_cents", 0),
        })

    except (WebSocketDisconnect, asyncio.CancelledError):
        # Track B #5/#6: signal the TTS worker to stop at the next chunk,
        # drain any buffered sentences (so the worker doesn't grab one more
        # before seeing the cancel flag), then cancel and await teardown.
        tts_cancel_event.set()
        _drain_queue(tts_queue)
        tts_task.cancel()
        try:
            await tts_task
        except (asyncio.CancelledError, Exception):
            pass
        raise
    except Exception:
        # Non-cancel error path: let the worker drain naturally so any
        # buffered sentences finish speaking before we close out. Keep the
        # 2s guard so a hung worker can't hold the turn forever.
        await tts_queue.put(None)
        try:
            await asyncio.wait_for(tts_task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            tts_task.cancel()
        raise


async def _handle_text_turn(
    ws: WebSocket,
    session_id: str,
    history: list[dict],
    text: str,
    project_scope: str,
    current_speed: float = 1.0,
) -> None:
    try:
        await _run_llm_turn(ws, session_id, history, text, project_scope, current_speed)
    except asyncio.CancelledError:
        # Turn was cancelled (barge-in / superseded) — don't emit a user-facing
        # error. The caller already sent turn_cancelled.
        raise
    except Exception as exc:
        logger.exception("Error processing text turn session=%s: %s", session_id, exc)
        try:
            await ws_send_json(ws, {"type": "error", "message": str(exc)})
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Dispatch bridge glue: classify every user turn, then route to chat / task /
# status / cancel. See docs/dispatch-bridge-glue-spec.md for the full spec.
# ---------------------------------------------------------------------------


async def _route_user_turn(
    ws: WebSocket,
    session_id: str,
    history: list[dict],
    user_text: str,
    current_project: str,
    current_speed: float = 1.0,
) -> None:
    """Single entry point that classifies then routes to chat/task/status/cancel."""
    # Switch intent has already run upstream. Now classify.
    result = await classify_intent(user_text, current_project)
    intent = result["intent"]

    if intent == "chat":
        await _handle_text_turn(ws, session_id, history, user_text, current_project, current_speed)
        return

    if intent == "task":
        await _route_task(
            ws, session_id, history,
            result.get("task_spec") or user_text, current_project,
            current_speed,
        )
        return

    if intent == "status":
        await _route_status(ws, session_id, history, current_project, current_speed)
        return

    if intent == "cancel":
        await _route_cancel(ws, session_id, current_speed)
        return

    # Should not reach — classifier is typed.
    await _handle_text_turn(ws, session_id, history, user_text, current_project, current_speed)


async def _route_task(
    ws: WebSocket,
    session_id: str,
    history: list[dict],
    task_spec: str,
    current_project: str,
    current_speed: float = 1.0,
) -> None:
    # Hawke HIGH: wrap the entire body so FileNotFoundError (claude missing
    # on PATH), OSError (exec failures), ValueError (task_spec length cap),
    # and anything else that isn't TaskAlreadyRunning surfaces to the user
    # instead of dying silently in a background task.
    try:
        # Defense-in-depth alongside the "--" end-of-options marker in
        # dispatcher.dispatch(). Even if the downstream CLI argv parser didn't
        # honor "--", a task_spec whose first non-whitespace char is "-" is
        # almost never a legitimate Claude Code prompt — it's either a prompt
        # injection attempt or a classifier misfire. Reject and clarify rather
        # than spawn. Vera HIGH finding.
        if task_spec.lstrip().startswith("-"):
            logger.warning(
                "route_task: rejecting task_spec with leading dash (session=%s spec=%r)",
                session_id,
                task_spec[:120],
            )
            await _narrate(
                ws,
                "That looks like a command flag, not a task. Can you rephrase what you'd like me to do?",
                speed=current_speed,
            )
            return

        repo = get_repo_path(current_project)
        if repo is None or not repo.exists():
            # No local repo configured for this scope. Fall back to chat so
            # Chief can explain rather than silently fail.
            await ws_send_json(ws, {
                "type": "token",
                "text": f"I can't dispatch — no local repo configured for {current_project}. ",
            })
            await _handle_text_turn(ws, session_id, history, task_spec, current_project)
            return

        # `task_id` must appear on every task_* frame or the frontend silently
        # drops them (routes by id, not by most-recently-active ref). The id is
        # `handle.started_at.isoformat()` which is only set after dispatch
        # spawns. Use a one-element box so callbacks can close over it and see
        # the id assigned on the line after dispatch returns.
        tid_box: list[str] = [""]

        async def on_output(text: str, stream: str) -> None:
            await ws_send_json(ws, {
                "type": "task_output",
                "task_id": tid_box[0],
                "text": text,
                "stream": stream,
            })

        async def on_complete(exit_code: int, summary: str) -> None:
            await ws_send_json(ws, {
                "type": "task_complete",
                "task_id": tid_box[0],
                "exit_code": exit_code,
                "duration_seconds": int(
                    (datetime.now(timezone.utc) - handle.started_at).total_seconds()
                ),
                "summary": summary,
            })
            # Terminal narration: the conversational unit is fully done.
            await _narrate(
                ws,
                f"Task complete. Exit code {exit_code}. {summary[:160]}",
                speed=current_speed,
            )

        try:
            handle = await _dispatcher.dispatch(
                session_id=session_id,
                task_spec=task_spec,
                repo=repo,
                on_output=on_output,
                on_complete=on_complete,
            )
        except TaskAlreadyRunning:
            await _narrate(
                ws,
                "Still working on the previous task. Say status for an update, or stop to cancel.",
                speed=current_speed,
            )
            return

        tid_box[0] = handle.task_id  # closures above now see the real id

        # Initial narration: immediate, deterministic (no LLM call needed).
        await ws_send_json(ws, {
            "type": "task_started",
            "task_id": handle.task_id,
            "task_spec": task_spec,
            "repo": str(repo),
            "started_at": handle.started_at.isoformat(),
        })
        # NOT terminal — the task is still running. Hawke HIGH: emitting
        # message_done here would tell the frontend the assistant is done
        # speaking before the task actually completes, which races against
        # the later task_complete + terminal narration.
        await _narrate(
            ws,
            f"Dispatching to Claude Code on your Mac. Working in {current_project}. "
            "I'll let you know when it's done.",
            terminal=False,
            speed=current_speed,
        )
    except asyncio.CancelledError:
        # Turn was cancelled (barge-in / superseded) — propagate so the
        # caller can clean up. Don't emit a user-facing error.
        raise
    except Exception as exc:
        logger.exception(
            "route_task: dispatch failed session=%s task_spec=%r: %s",
            session_id,
            task_spec[:120],
            exc,
        )
        await _narrate(
            ws,
            f"Task dispatch failed: {exc}. Staying on chat.",
            speed=current_speed,
        )
        # Fall back to chat so the user still gets a response.
        try:
            await _handle_text_turn(ws, session_id, history, task_spec, current_project)
        except Exception:
            logger.exception(
                "route_task: chat fallback also failed session=%s", session_id,
            )


async def _route_status(
    ws: WebSocket,
    session_id: str,
    history: list[dict],
    current_project: str,
    current_speed: float = 1.0,
) -> None:
    handle = _dispatcher.get_handle(session_id)
    if handle is None:
        await _narrate(
            ws,
            "No task running right now. Ask me something or give me a build task.",
            speed=current_speed,
        )
        return

    # Summarize live stdout via Haiku.
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
            getattr(b, "text", "") for b in resp.content
            if getattr(b, "type", None) == "text"
        ).strip() or "Working."
    except Exception as exc:
        logger.warning("status summarizer failed: %s", exc)
        sentence = "Working — no new output to summarize."

    elapsed = int((datetime.now(timezone.utc) - handle.started_at).total_seconds())
    await _narrate(
        ws,
        f"{sentence} Running for {elapsed // 60} minutes {elapsed % 60} seconds.",
        speed=current_speed,
    )


async def _route_cancel(
    ws: WebSocket,
    session_id: str,
    current_speed: float = 1.0,
) -> None:
    # Fetch the handle BEFORE cancel so we still have access to task_id for
    # the frame (cancel() may evict the handle).
    handle = _dispatcher.get_handle(session_id)
    killed = await _dispatcher.cancel(session_id)
    if killed and handle is not None:
        await ws_send_json(ws, {
            "type": "task_cancelled",
            "task_id": handle.task_id,
            "reason": "owner-requested",
        })
        await _narrate(ws, "Cancelled. Task killed.", speed=current_speed)
    else:
        await _narrate(ws, "Nothing to cancel — no task running.", speed=current_speed)


async def _narrate(
    ws: WebSocket,
    text: str,
    *,
    terminal: bool = True,
    speed: float = 1.0,
    cancel_event: Optional[asyncio.Event] = None,
) -> None:
    """Send a short line of text as a token + trigger TTS.

    ``speed`` matches the chat-path TTS speed (Google's `speaking_rate`).
    Without this plumbed through, task/status/cancel narrations ignored the
    owner's speed preference and always played at 1.0x.

    ``cancel_event`` is checked between TTS chunks so a barge-in stops
    narration at the next chunk boundary instead of waiting for
    CancelledError to reach the next await point. Defaults to the running
    task's ``_tts_cancel_event`` attribute if set (same pattern
    ``_run_llm_turn`` and ``cancel_current_turn`` use).
    """
    if cancel_event is None:
        task = asyncio.current_task()
        if task is not None:
            cancel_event = getattr(task, "_tts_cancel_event", None)
    try:
        await ws_send_json(ws, {"type": "token", "text": text + " "})
        await ws_send_json(ws, {"type": "tts_start"})
        try:
            async for chunk in tts_service.synthesize_stream(
                text, speed=speed, cancel_event=cancel_event,
            ):
                await ws_send_bytes(ws, chunk)
        except Exception as exc:
            logger.warning("narration TTS failed: %s", exc)
        await ws_send_json(ws, {"type": "tts_end"})
        if terminal:
            await ws_send_json(ws, {"type": "message_done"})
    except Exception as exc:
        # WS may be gone mid-narration — swallow so the caller (callback or
        # router) doesn't propagate a disconnect as a turn error.
        logger.warning("narration send failed: %s", exc)


@router.websocket("/ws/terminal")
async def terminal_ws(ws: WebSocket) -> None:
    """Terminal WebSocket endpoint.

    Inbound frames:
      {"type": "command", "content": "ls -la"}
      {"type": "signal", "signal": "SIGINT"}
      {"type": "resize", "cols": 80, "rows": 24}

    Outbound frames:
      {"type": "stdout", "content": "..."}
      {"type": "stderr", "content": "..."}
      {"type": "exit", "code": 0}
      {"type": "error", "message": "..."}
    """
    await ws.accept()
    if not await _authenticate_ws(ws):
        await ws.send_json({"type": "error", "message": "Unauthorized"})
        await ws.close(code=4001)
        return

    logger.info("Terminal WebSocket connected")

    current_process: Optional[asyncio.subprocess.Process] = None

    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {"type": "command", "content": raw}

            msg_type = data.get("type", "command")

            if msg_type == "command":
                cmd = data.get("content", "").strip()
                if not cmd:
                    continue

                current_process = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    preexec_fn=None,
                )

                async def _stream_pipe(
                    pipe: asyncio.StreamReader, stream_type: str
                ) -> None:
                    while True:
                        line = await pipe.readline()
                        if not line:
                            break
                        await ws.send_json(
                            {"type": stream_type, "content": line.decode(errors="replace")}
                        )

                tasks = []
                if current_process.stdout:
                    tasks.append(asyncio.create_task(_stream_pipe(current_process.stdout, "stdout")))
                if current_process.stderr:
                    tasks.append(asyncio.create_task(_stream_pipe(current_process.stderr, "stderr")))

                if tasks:
                    await asyncio.gather(*tasks)

                exit_code = await current_process.wait()
                await ws.send_json({"type": "exit", "code": exit_code})
                current_process = None

            elif msg_type in ("signal", "kill"):
                sig_name = data.get("signal", "SIGINT")
                allowed_signals = {"SIGINT", "SIGTERM"}
                if sig_name not in allowed_signals:
                    sig_name = "SIGINT"
                if current_process and current_process.returncode is None:
                    sig = getattr(signal, sig_name, signal.SIGINT)
                    current_process.send_signal(sig)
                    logger.info("Sent %s to running process", sig_name)

    except WebSocketDisconnect:
        logger.info("Terminal WebSocket disconnected")
    except Exception as exc:
        logger.exception("Terminal WebSocket error: %s", exc)
    finally:
        if current_process and current_process.returncode is None:
            current_process.terminate()
