"""WebSocket endpoints for voice and terminal streaming.

Stage 4 of the Gemini Live pivot (2026-05-05). Voice WS uses native
Live API audio I/O end-to-end:

  Browser ──16kHz Int16 PCM──► voice_ws ──► LiveSession.send_audio
  Browser ◄──24kHz Int16 PCM── voice_ws ◄── on_audio_chunk
  Browser ◄──json frames─────  voice_ws ◄── on_*_transcript /
                                            on_interrupted /
                                            on_turn_complete / etc.

Stage 4 hardening over Stages 1-3:
  * Receive-pump crash → rebuild the LiveSession with the cached
    session-resumption handle (≤2hr per Live API spec). Capped at
    ``LIVE_RECONNECT_MAX_RETRIES`` retries per WS connection.
  * GoAway → proactive parallel reconnect before the underlying
    transport closes (~10min cap). Old session is closed only after
    the new one is open and receiving.
  * Mid-turn scope flip → cancel the in-flight turn before
    close-and-reopen, so the new scope starts clean.
  * Soft cost cap → emit ``cost_warning`` once per WS connection
    when daily spend crosses 80% of the hard cap.

Server-side VAD on the Live API handles barge-in; the manual UI cancel
button still triggers ``LiveSession.cancel_current_turn``.
"""

import asyncio
import json
import logging
import signal
import uuid
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services.auth import verify_token
from services import cc_session
from services.agent_tools import dispatch_tool, display_name_for, to_gemini_tool
from services.chief_context import build_chief_system_string
from services.dispatcher import TaskDispatcher
from services.gemini_live import LIVE_MODEL, LiveSession
from services.history_store import append_turn
from services.memory_rollup import load_persistent_memory, maybe_rollup
from services.project_context import (
    AVAILABLE_PROJECTS,
    DEFAULT_PROJECT,
    _context_store,
)
from services.repo_map import get_repo_path
from services.usage_tracker import (
    check_daily_cap,
    check_soft_cap,
    close_session,
    create_session,
    get_session_totals,
    record_turn,
)
from config.settings import settings

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
# concurrent writes from separate tasks. In the Live world the voice WS
# has several concurrent producers:
#   - main receive loop (forwarding mic frames to LiveSession.send_audio)
#   - LiveSession callbacks (audio chunks, transcripts, tool calls)
#   - dispatcher stdout pump (task_output frames)
#   - dispatcher completion callback (task_complete frames)
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
    """Voice WebSocket endpoint — Stage 2 (Gemini Live native audio).

    Inbound frames:
      binary  16kHz mono Int16 little-endian PCM (~640 bytes per 20ms frame)
      text    {"type":"text","content":"..."}        — typed input
      text    {"type":"context","project":"..."}     — scope flip
      text    {"type":"interrupt"}                   — manual barge-in
      text    {"type":"speed","value":1.25}          — backward-compat no-op
      text    {"type":"cancel","action":"task"}      — dispatch cancel button

    Outbound frames:
      binary  24kHz mono Int16 little-endian PCM
      text    {"type":"input_transcript","text":"...","is_final":bool}
      text    {"type":"output_transcript","text":"...","is_final":bool}
      text    {"type":"interrupted"}
      text    {"type":"generation_complete"}
      text    {"type":"context_switched","project":"..."}
      text    {"type":"usage", ...}
      text    {"type":"error","message":"..."}
      text    {"type":"session_resumed","handle":"..."}
      text    {"type":"go_away","time_left":N}
      text    {"type":"reconnecting"}                — Stage 4 swap start
      text    {"type":"reconnected"}                 — Stage 4 swap done
      text    {"type":"cost_warning","current_today":N,"cap":15}
      text    {"type":"quota_exceeded", ...}
      text    {"type":"speed", ...}                  — speed echo (no-op)
      text    {"type":"tool_call", ...}

    Stage 4: a receive-pump crash triggers a session-resumption rebuild
    (cap LIVE_RECONNECT_MAX_RETRIES per WS). A GoAway frame triggers a
    proactive parallel reconnect before the underlying transport closes.
    A 3rd consecutive failure or a reconnect that itself fails surfaces
    an error frame and closes the WS.
    """
    await ws.accept()
    client_id = await _authenticate_ws(ws)
    if client_id is None:
        await ws_send_json(ws, {"type": "error", "message": "Unauthorized"})
        await ws.close(code=4001)
        return

    # Stage 3 daily cap. Check at WS open and refuse if today's spend has
    # already hit the cap — opening a new Live session burns ~$3-5/hour
    # in audio + escalation cost, so a hot WS in a runaway loop must not
    # be allowed to start. Re-checked periodically inside the receive
    # loop too (see DAILY_CAP_RECHECK_INTERVAL_S below).
    try:
        over_cap, current_today = await check_daily_cap(client_id)
    except Exception as exc:
        # Fail open — better to allow voice than to brick on a sqlite
        # hiccup. The cap will catch up on the next per-turn recheck.
        logger.warning("voice_ws daily cap precheck failed: %s", exc)
        over_cap, current_today = False, 0.0
    if over_cap:
        from services.usage_tracker import _daily_cost_cap_dollars
        await ws_send_json(ws, {
            "type": "quota_exceeded",
            "current_today_dollars": round(current_today, 4),
            "cap_dollars": _daily_cost_cap_dollars(),
        })
        await ws.close(code=4003)
        logger.warning(
            "voice_ws refused — daily cap exceeded subject=%s today=$%.4f",
            client_id, current_today,
        )
        return

    session_id: Optional[str] = None
    history: list[dict] = []
    # Phase 3: rolling cross-session memory. Loaded alongside ``history`` at
    # WS open and refreshed on scope flip. Embedded into the LiveSession's
    # ``system_prompt`` so Chief picks up cross-session context the raw
    # 20-turn window misses.
    current_summary: Optional[str] = None

    # Per-subject scope keying. ``client_id`` is the JWT subject; the
    # in-memory ``_context_store`` is keyed by it so per-tab/per-device WS
    # connections don't stomp on each other's scope.
    context_key = client_id
    initial = _migrate_dissolved_scope(_context_store.get(context_key)) or DEFAULT_PROJECT
    if initial not in AVAILABLE_PROJECTS:
        initial = DEFAULT_PROJECT
    current_project: str = initial
    _context_store[context_key] = current_project

    # Hydrate persistent memory for the initial scope BEFORE we open the
    # LiveSession — the system prompt depends on the rolling summary, and
    # ``LiveSession`` doesn't (yet) support runtime system-prompt updates,
    # so we pay the load cost up-front and rebuild on scope flip.
    try:
        current_summary, hydrated = await load_persistent_memory(
            current_project, raw_limit=20,
        )
        history = hydrated
        logger.info(
            "voice_ws (Live) hydrated scope=%s history_turns=%d summary=%s",
            current_project, len(history),
            "yes" if current_summary else "no",
        )
    except Exception as exc:
        logger.warning("voice_ws (Live) history rehydrate failed: %s", exc)
        history = []
        current_summary = None

    # Per-turn transcript accumulators. Live emits transcription events
    # incrementally; we concatenate until ``generation_complete`` fires,
    # then drain into history persistence + reset for the next turn. Both
    # sides are tracked so we capture both halves of the conversation —
    # the user's recognised speech AND Chief's spoken reply.
    input_transcript_buf: list[str] = []
    output_transcript_buf: list[str] = []

    # ``LiveSession`` is replaced wholesale on scope flip (different scope
    # → different system prompt). Hold a mutable reference so callbacks
    # bound to the *old* session don't trip on stale state — we read
    # ``live_session`` indirectly through this slot inside callbacks.
    live_session_box: list[Optional[LiveSession]] = [None]

    # ----- Stage 4: reconnect / GoAway / soft-cap state -----
    # Cached resumption handle survives across LiveSession instances within
    # the same WS connection. Updated on every ``session_resumption_update``
    # frame (LiveSession writes self.resumption_handle); we mirror it onto
    # this local so the reconnect path can read it after the old session
    # is torn down. Stamped with a monotonic timestamp so we can drop a
    # stale handle older than ``LIVE_RESUMPTION_HANDLE_MAX_AGE_S``.
    cached_resumption_handle: list[Optional[str]] = [None]
    cached_resumption_handle_at: list[float] = [0.0]

    # Reconnect attempt counter for THIS WS connection. Reset to 0 on a
    # successful turn-complete (so a single rocky stretch followed by
    # recovery doesn't push the counter to the cap). Capped at
    # ``settings.LIVE_RECONNECT_MAX_RETRIES`` — past that we surface an
    # error and close cleanly.
    reconnect_attempts: list[int] = [0]
    # Latch so a reconnect attempt already in flight isn't restarted by a
    # second pump-crash callback firing before the first reconnect lands.
    reconnect_in_progress: list[bool] = [False]

    # Soft-cost warning fires once per WS connection. Multi-fire would just
    # spam the FE; the threshold is a "heads-up" signal, not a continuous
    # update.
    soft_cap_warned: list[bool] = [False]

    async def ensure_session_id() -> str:
        """Lazy-create the usage-tracker session row on first turn."""
        nonlocal session_id
        if session_id is None:
            session_id = str(uuid.uuid4())
            await create_session(session_id, project=current_project)
            logger.info(
                "Voice WS (Live) session started session=%s project=%s",
                session_id, current_project,
            )
        return session_id

    # ----- Stage 3: tool list (constructed once per WS connection) -----
    # ``to_gemini_tool()`` walks ``agent_tools.ALL_TOOLS`` and returns a
    # single Gemini ``Tool`` carrying every function declaration. Built
    # once here (not per-LiveSession) because the schemas don't change
    # mid-connection — only the system prompt does on a scope flip, and
    # that's already handled by close-and-reopen of the LiveSession.
    try:
        live_tool_list: list = [to_gemini_tool()]
    except Exception as exc:
        # If tool construction fails (SDK import error in a degraded env),
        # fall back to no-tools so voice still works in chat-only mode.
        logger.warning("voice_ws tool list build failed: %s", exc)
        live_tool_list = []

    # ----- LiveSession callbacks -----
    # Each callback funnels its event onto a WS frame (or accumulates state)
    # and is registered by reference at session-open time. We wrap WS sends
    # in try/except because the receive pump runs as a background task and
    # a closed WS shouldn't propagate as an unhandled exception in the pump.
    async def _on_audio_chunk(pcm: bytes) -> None:
        try:
            await ws_send_bytes(ws, pcm)
        except Exception as exc:
            logger.debug("voice_ws (Live) on_audio_chunk send failed: %s", exc)

    async def _on_input_transcript(text: str, is_final: bool) -> None:
        if text:
            input_transcript_buf.append(text)
        try:
            await ws_send_json(ws, {
                "type": "input_transcript",
                "text": text,
                "is_final": is_final,
            })
        except Exception as exc:
            logger.debug("voice_ws (Live) input_transcript send failed: %s", exc)

    async def _on_output_transcript(text: str, is_final: bool) -> None:
        if text:
            output_transcript_buf.append(text)
        try:
            await ws_send_json(ws, {
                "type": "output_transcript",
                "text": text,
                "is_final": is_final,
            })
        except Exception as exc:
            logger.debug("voice_ws (Live) output_transcript send failed: %s", exc)

    async def _on_interrupted() -> None:
        try:
            await ws_send_json(ws, {"type": "interrupted"})
        except Exception as exc:
            logger.debug("voice_ws (Live) interrupted send failed: %s", exc)

    async def _on_session_resumed(handle: str) -> None:
        """Cache the resumption handle for cross-reconnect rebuilds.

        Stage 4 actively uses the cached handle on pump-crash and GoAway
        reconnects (see ``_reconnect_with_handle`` + ``_handle_go_away``).
        Echoing the frame to the FE keeps the existing debug surface so
        owner can see "session resumed" pings in the Voice page console.
        """
        import time
        cached_resumption_handle[0] = handle
        cached_resumption_handle_at[0] = time.monotonic()
        logger.info("voice_ws (Live) session_resumed handle=%s", handle[:24])
        try:
            await ws_send_json(ws, {"type": "session_resumed", "handle": handle})
        except Exception as exc:
            logger.debug("voice_ws (Live) session_resumed send failed: %s", exc)

    async def _on_go_away(time_left: float) -> None:
        """Server is closing the underlying transport — rebuild proactively.

        Live API sends GoAway ~30s before forced disconnect (typically at
        the ~10min mark). We use the time_left window to spin up a parallel
        LiveSession with the cached resumption handle and swap once it's
        open, so the user's audio gap is sub-second (vs. ~5-10s if we
        waited for the inevitable transport close + pump-crash retry).

        Latch set SYNCHRONOUSLY here too — a GoAway followed quickly by a
        crash (or vice-versa) shouldn't spawn two parallel rebuilds.
        """
        logger.info("voice_ws (Live) go_away time_left=%.2f", time_left)
        try:
            await ws_send_json(ws, {"type": "go_away", "time_left": time_left})
        except Exception as exc:
            logger.debug("voice_ws (Live) go_away send failed: %s", exc)
        # Kick off proactive reconnect — schedule rather than await so the
        # callback returns to the receive pump quickly and doesn't block
        # other server messages riding on the same dispatch.
        if reconnect_in_progress[0]:
            logger.info(
                "voice_ws (Live) reconnect already in progress — "
                "GoAway will ride on the in-flight rebuild",
            )
            return
        reconnect_in_progress[0] = True
        try:
            asyncio.create_task(_handle_go_away_reconnect())
        except Exception as exc:
            reconnect_in_progress[0] = False
            logger.warning("voice_ws (Live) go_away reconnect spawn failed: %s", exc)

    async def _on_pump_crash(exc: BaseException) -> None:
        """Receive-pump exited on an exception — drive a resumption rebuild.

        Distinct from ``_on_interrupted`` (which fires on user-driven
        barge-in too). When the SDK's WS transport drops mid-session, the
        pump catches the exception, signals on_interrupted (so any queued
        playback flushes locally) AND fires this callback. We use the
        cached resumption handle to rebuild a new LiveSession server-side
        with full context preserved.

        Latch set SYNCHRONOUSLY before ``create_task`` so a second crash
        callback firing in the same loop tick doesn't slip past the check
        inside ``_reconnect_after_crash`` and spawn a parallel rebuild.
        """
        logger.warning(
            "voice_ws (Live) pump crashed: %s — attempting resumption rebuild",
            exc,
        )
        if reconnect_in_progress[0]:
            logger.info(
                "voice_ws (Live) reconnect already in progress — "
                "ignoring duplicate pump-crash signal",
            )
            return
        reconnect_in_progress[0] = True
        try:
            asyncio.create_task(_reconnect_after_crash())
        except Exception as spawn_exc:
            # Release the latch so a follow-up crash can try again.
            reconnect_in_progress[0] = False
            logger.warning(
                "voice_ws (Live) reconnect spawn failed: %s", spawn_exc,
            )

    async def _on_tool_call(tool_call_event: any) -> None:
        """Stage 3: execute Live's tool calls and reply via send_tool_response.

        Live emits a single ``tool_call`` server event carrying one or
        more ``FunctionCall`` parts. We:
          1. Resolve cwd/scope/subject from the WS connection's state.
          2. For each FunctionCall: emit a ``tool_call`` WS frame with
             ``status: "running"`` so the FE chip lights up.
          3. Dispatch via ``agent_tools.dispatch_tool``.
          4. Emit a terminal ``tool_call`` WS frame
             (status complete | error | cancelled).
          5. Build a ``FunctionResponse`` per call and reply via
             ``LiveSession.send_tool_response`` so the model can
             continue the turn with the tool output in context.
          6. Append a synthetic ``[tool: ...]`` note to history so cross-
             reconnect tool memory survives — same shape as the text-mode
             gemini_brain path.

        Cancellation: if the receive pump is being torn down (close on WS
        drop / scope flip), individual dispatches may raise
        CancelledError mid-flight. We catch it per-call so one cancel
        doesn't silence the rest of the round, then ALWAYS attempt a
        send_tool_response — Live's protocol requires a response for
        every call ID; a missing response wedges the model on the
        server side until the session times out.
        """
        # Read the live session reference at handler entry. We don't
        # early-return if it's None — the callback can fire during the
        # close-and-reopen window of a scope flip, and we still want to
        # execute the tool (so the model can see its result on the new
        # session) even if we can't send a tool_response on the closed
        # session. The send_tool_response call below is wrapped in
        # try/except for that case.
        sess = live_session_box[0]
        fcalls = getattr(tool_call_event, "function_calls", None) or []
        if not fcalls:
            return
        logger.info("voice_ws tool_call: dispatching %d call(s)", len(fcalls))

        # Resolve sandbox parameters from the active scope. ``get_repo_path``
        # returns None for scopes without a repo (e.g. PA before Phase 0
        # foundation); ``dispatch_tool`` then refuses Read/Bash/Grep/dispatch
        # but allows think_deep through (see agent_tools.dispatch_tool).
        from pathlib import Path
        repo_cwd = get_repo_path(current_project)
        cwd = repo_cwd if repo_cwd is not None else Path.home()

        # Lazy SDK import for FunctionResponse construction.
        try:
            from google.genai import types
        except Exception as exc:
            logger.warning("voice_ws tool_call: genai import failed: %s", exc)
            return

        function_responses: list = []
        for fc in fcalls:
            tool_name = getattr(fc, "name", "") or ""
            raw_args = getattr(fc, "args", None) or {}
            try:
                args_dict = dict(raw_args)
            except Exception:
                args_dict = {}

            # Args summary (truncated) for the WS frame so the chip doesn't
            # carry a 20KB Bash command verbatim. Same shape gemini_brain
            # emits to keep the FE renderer single-purpose.
            args_summary: dict = {}
            for k, v in args_dict.items():
                if isinstance(v, str) and len(v) > 200:
                    args_summary[k] = v[:200] + "...<truncated>"
                else:
                    args_summary[k] = v

            # Emit the "running" chip frame. ``display_name`` carries the
            # persona alias (Glass for code_review, etc.) so the FE chip
            # renders "Glass · reviewing …" instead of the raw tool ID.
            # Omitted entirely for tools without a persona — frontend falls
            # back to ``name``.
            persona = display_name_for(tool_name)
            running_frame: dict = {
                "type": "tool_call",
                "name": tool_name,
                "args": args_summary,
                "status": "running",
            }
            if persona:
                running_frame["display_name"] = persona
            try:
                await ws_send_json(ws, running_frame)
            except Exception as exc:
                logger.debug("voice_ws tool_call running emit failed: %s", exc)

            import time as _time
            started = _time.monotonic()
            cancelled_in_tool = False
            result = None
            try:
                result = await dispatch_tool(
                    tool_name,
                    args_dict,
                    cwd=cwd,
                    subject=client_id,
                    scope=current_project,
                    system_prompt_append="",
                )
            except asyncio.CancelledError:
                cancelled_in_tool = True
                # Don't re-raise — we still need to flush a function_response
                # for THIS call (Live wedges otherwise). Mark a synthetic
                # error result so the model sees the cancel as a recoverable
                # tool failure instead of a missing response.
                from services.agent_tools import ToolResult
                result = ToolResult(output="error: tool cancelled", error=True)
            except Exception as exc:
                # Don't leak raw exception text to the model (or by
                # extension, the user's voice). Detail stays in the log.
                logger.exception(
                    "voice_ws tool_call: %r raised: %s", tool_name, exc,
                )
                from services.agent_tools import ToolResult
                result = ToolResult(output="error: tool execution failed", error=True)

            # Terminal chip frame.
            try:
                duration_ms = int((_time.monotonic() - started) * 1000)
                if cancelled_in_tool:
                    status = "cancelled"
                elif getattr(result, "error", False):
                    status = "error"
                else:
                    status = "complete"
                preview_text = getattr(result, "output", "") or ""
                if isinstance(preview_text, str) and len(preview_text) > 240:
                    preview_text = preview_text[:240] + "..."
                terminal_frame: dict = {
                    "type": "tool_call",
                    "name": tool_name,
                    "args": args_summary,
                    "status": status,
                    "duration_ms": duration_ms,
                    "preview": preview_text,
                }
                if persona:
                    terminal_frame["display_name"] = persona
                await ws_send_json(ws, terminal_frame)
            except Exception as exc:
                logger.debug("voice_ws tool_call terminal emit failed: %s", exc)

            # Build the FunctionResponse for Live. ``id`` matches the
            # FunctionCall's id so Live correlates response → call when
            # parallel calls of the same name happen in one round.
            response_payload = {
                "output": getattr(result, "output", "") or "",
                "error": bool(getattr(result, "error", False)),
            }
            try:
                fr = types.FunctionResponse(
                    id=getattr(fc, "id", None),
                    name=tool_name,
                    response=response_payload,
                )
                function_responses.append(fr)
            except Exception as exc:
                logger.warning(
                    "voice_ws tool_call: FunctionResponse build failed: %s", exc,
                )

            # Synthetic tool note for cross-session memory. Same builder
            # the text-mode path uses; the WS layer's history-append path
            # is the dedupe of `on_tool_round_complete` from gemini_brain.
            try:
                from services.gemini_brain import _build_tool_note
                note = _build_tool_note(
                    tool_name=tool_name,
                    args=args_dict,
                    output=getattr(result, "output", "") or "",
                    error=bool(getattr(result, "error", False)),
                )
                if note:
                    history.append({"role": "assistant", "content": note})
                    sid = await ensure_session_id()
                    try:
                        await append_turn(sid, current_project, "assistant", note)
                    except Exception as exc:
                        logger.warning(
                            "voice_ws tool note persist failed: %s", exc,
                        )
            except Exception as exc:
                logger.debug("voice_ws tool note build failed: %s", exc)

        # Reply to Live with all function_responses in one call. The
        # protocol expects a single send_tool_response per server
        # tool_call event — sending one per FunctionResponse would be a
        # protocol error.
        if function_responses and sess is not None:
            try:
                await sess.send_tool_response(function_responses=function_responses)
            except Exception as exc:
                logger.warning("voice_ws send_tool_response failed: %s", exc)
        elif function_responses:
            logger.warning(
                "voice_ws tool_call: session closed before send_tool_response "
                "(scope flip / WS drop mid-tool); responses dropped",
            )

    async def _on_turn_complete(usage: dict) -> None:
        """Persist the just-finished turn + record billing.

        Steps (in order):
          1. Drain the transcript accumulators into ``history`` and the
             ``voice_turns`` table — both halves so reload sees the full
             exchange.
          2. Fire-and-forget ``maybe_rollup`` so cross-session summary
             stays fresh without blocking the next turn.
          3. Record cost via ``record_turn`` against the LIVE_MODEL.
             Stage 2 passes through whatever Live's ``usage_metadata``
             reported; Stage 3 will plumb audio_input_tokens /
             audio_output_tokens into ``compute_cost_cents`` for accurate
             audio billing.
          4. Emit a ``usage`` frame to the FE so the cost chip updates.

        Wrapped in try/except per leg — a billing-record failure must not
        prevent the FE from seeing ``generation_complete``.
        """
        user_text = "".join(input_transcript_buf).strip()
        assistant_text = "".join(output_transcript_buf).strip()
        # Reset accumulators IMMEDIATELY so a fast-following turn doesn't
        # pick up stale text. The FE will see ``generation_complete``
        # below and finalize the message bubble.
        input_transcript_buf.clear()
        output_transcript_buf.clear()

        sid = await ensure_session_id()

        # Persist user turn (best-effort).
        if user_text:
            history.append({"role": "user", "content": user_text})
            try:
                await append_turn(sid, current_project, "user", user_text)
            except Exception as exc:
                logger.warning(
                    "voice_ws (Live) history persist (user) failed session=%s: %s",
                    sid, exc,
                )

        # Persist assistant turn (best-effort).
        if assistant_text:
            history.append({"role": "assistant", "content": assistant_text})
            try:
                await append_turn(sid, current_project, "assistant", assistant_text)
            except Exception as exc:
                logger.warning(
                    "voice_ws (Live) history persist (assistant) failed session=%s: %s",
                    sid, exc,
                )

            # Fire-and-forget rolling summary trigger.
            try:
                asyncio.create_task(maybe_rollup(current_project))
            except Exception as exc:
                logger.warning(
                    "voice_ws (Live) memory_rollup spawn failed scope=%s: %s",
                    current_project, exc,
                )

        # Record billing. Map Live's usage_metadata into the shape
        # ``record_turn`` expects. Stage 3 plumbs audio_input_tokens /
        # audio_output_tokens through so ``compute_cost_cents`` bills
        # them at the Live native-audio rates ($3/M in, $12/M out)
        # instead of dropping them on the floor.
        try:
            audio_in = int(usage.get("audio_input_tokens", 0) or 0)
            audio_out = int(usage.get("audio_output_tokens", 0) or 0)
            # Live's prompt_token_count is the SUM of audio + text input
            # tokens. To bill text-input at $0.50/M (vs audio at $3/M)
            # without double-counting, use ``text_input_tokens`` if the
            # session pump captured it; otherwise back it out from the
            # total. Same logic for output. ``LiveSession._accumulate_usage``
            # populates these fields when modality breakdowns arrive.
            text_in = int(usage.get("text_input_tokens", 0) or 0)
            text_out = int(usage.get("text_output_tokens", 0) or 0)
            if text_in == 0 and audio_in > 0:
                # Modality breakdown didn't land — back it out from the
                # cumulative total so we don't double-bill audio at the
                # text rate.
                total_in = int(usage.get("prompt_token_count", 0) or 0)
                text_in = max(0, total_in - audio_in)
            if text_out == 0 and audio_out > 0:
                total_out = int(usage.get("response_token_count", 0) or 0)
                text_out = max(0, total_out - audio_out)
            # When audio counts are zero (e.g. a typed-only turn), fall
            # back to the cumulative scalars so text-only turns continue
            # to bill exactly as before.
            if audio_in == 0 and text_in == 0:
                text_in = int(usage.get("prompt_token_count", 0) or 0)
            if audio_out == 0 and text_out == 0:
                text_out = int(usage.get("response_token_count", 0) or 0)
            usage_dict = {
                "input_tokens": text_in,
                "output_tokens": text_out,
                "cache_read_input_tokens": int(usage.get("cached_content_token_count", 0) or 0),
                "cache_creation_input_tokens": 0,
                "audio_input_tokens": audio_in,
                "audio_output_tokens": audio_out,
            }
            turn = await record_turn(
                session_id=sid,
                model=LIVE_MODEL,
                usage_dict=usage_dict,
                user_text=user_text,
                assistant_text=assistant_text,
            )
            totals = await get_session_totals(sid)
            await ws_send_json(ws, {
                "type": "usage",
                "session_id": sid,
                "model": LIVE_MODEL,
                "input_tokens": usage_dict["input_tokens"],
                "output_tokens": usage_dict["output_tokens"],
                "cached_tokens": usage_dict["cache_read_input_tokens"],
                "audio_input_tokens": int(usage.get("audio_input_tokens", 0) or 0),
                "audio_output_tokens": int(usage.get("audio_output_tokens", 0) or 0),
                "turn_cost_cents": turn["cost_cents"],
                "session_total_cents": totals.get("cost_cents", 0),
            })
        except Exception as exc:
            logger.warning(
                "voice_ws (Live) record_turn failed session=%s: %s",
                sid, exc,
            )

        # Emit the FE's "turn boundary" signal LAST so it lands after the
        # transcript + usage frames the FE needs to finalize the bubble.
        try:
            await ws_send_json(ws, {"type": "generation_complete"})
        except Exception as exc:
            logger.debug("voice_ws (Live) generation_complete send failed: %s", exc)

        # Stage 4: clear the reconnect-attempt counter on any clean turn
        # completion. A single rocky stretch followed by a healthy turn
        # shouldn't permanently consume the WS's retry budget.
        if reconnect_attempts[0] > 0:
            logger.info(
                "voice_ws (Live) clearing reconnect attempts (was %d) — turn ok",
                reconnect_attempts[0],
            )
            reconnect_attempts[0] = 0

        # Stage 3 daily cap recheck after each turn. We do this here
        # (rather than on a fixed timer) because turn boundaries are the
        # natural granularity for "is this session getting expensive" —
        # a single Live turn can run $0.30+ on a long reply, so checking
        # post-turn means we close before the NEXT turn begins instead of
        # mid-utterance. The close path emits ``quota_exceeded`` with the
        # current spend so the FE can render an explanation.
        try:
            over_cap_post, current_post = await check_daily_cap(client_id)
        except Exception as exc:
            logger.warning("voice_ws daily cap recheck failed: %s", exc)
            over_cap_post = False
            current_post = 0.0
        if over_cap_post:
            from services.usage_tracker import _daily_cost_cap_dollars
            try:
                await ws_send_json(ws, {
                    "type": "quota_exceeded",
                    "current_today_dollars": round(current_post, 4),
                    "cap_dollars": _daily_cost_cap_dollars(),
                })
            except Exception as exc:
                logger.debug(
                    "voice_ws quota_exceeded emit failed: %s", exc,
                )
            logger.warning(
                "voice_ws closing — daily cap exceeded subject=%s today=$%.4f",
                client_id, current_post,
            )
            try:
                await ws.close(code=4003)
            except Exception:
                pass
            return

        # Stage 4 soft-cost warning: emit ``cost_warning`` ONCE per WS
        # connection when daily spend crosses 80% of the hard cap. Owner
        # then sees a subtle banner before the hard close lands. We skip
        # this when the hard cap has already fired (would be redundant
        # after ``quota_exceeded`` and the WS is closing anyway).
        if not soft_cap_warned[0]:
            try:
                over_soft, current_soft = await check_soft_cap(client_id)
            except Exception as exc:
                logger.warning("voice_ws soft cap recheck failed: %s", exc)
                over_soft = False
                current_soft = 0.0
            if over_soft:
                from services.usage_tracker import _daily_cost_cap_dollars
                soft_cap_warned[0] = True
                try:
                    await ws_send_json(ws, {
                        "type": "cost_warning",
                        "current_today": round(current_soft, 4),
                        "cap": _daily_cost_cap_dollars(),
                    })
                except Exception as exc:
                    logger.debug(
                        "voice_ws cost_warning emit failed: %s", exc,
                    )

    # ----- LiveSession lifecycle helpers -----
    def _build_live_session(*, resumption_handle: Optional[str] = None) -> LiveSession:
        """Construct a LiveSession against the current scope.

        Pure constructor — no async work. ``open()`` happens at the call
        site so reconnect paths can drive open + swap atomically (or
        backstop a failed open without leaving a half-built instance in
        the slot).
        """
        system_prompt = build_chief_system_string(
            current_project, prior_summary=current_summary,
        )
        return LiveSession(
            model=LIVE_MODEL,
            system_prompt=system_prompt,
            on_audio_chunk=_on_audio_chunk,
            on_input_transcript=_on_input_transcript,
            on_output_transcript=_on_output_transcript,
            on_interrupted=_on_interrupted,
            on_turn_complete=_on_turn_complete,
            on_tool_call=_on_tool_call,
            on_session_resumed=_on_session_resumed,
            on_go_away=_on_go_away,
            on_pump_crash=_on_pump_crash,
            extra_tools=live_tool_list if live_tool_list else None,
            resumption_handle=resumption_handle,
        )

    async def _open_live_session() -> LiveSession:
        """Build + open a LiveSession with the current scope's system prompt.

        Initial open path. Reconnect paths use ``_build_live_session`` +
        ``open()`` directly so they can stage a parallel session before
        swapping the slot.
        """
        sess = _build_live_session()
        await sess.open()
        live_session_box[0] = sess
        logger.info(
            "voice_ws (Live) opened session model=%s scope=%s subject=%s",
            LIVE_MODEL, current_project, client_id,
        )
        return sess

    async def _close_live_session() -> None:
        """Close the current LiveSession + clear the slot. Idempotent."""
        sess = live_session_box[0]
        if sess is None:
            return
        live_session_box[0] = None
        try:
            await sess.close()
        except Exception as exc:
            logger.warning("voice_ws (Live) close failed: %s", exc)

    def _resumption_handle_if_fresh() -> Optional[str]:
        """Return the cached handle iff it's within the 2hr TTL, else None.

        Live API handles are documented as valid for 2hr from issue. Past
        that the server rejects the handle and we'd have to rebuild from
        scratch anyway — surface ``None`` here so the caller can fall back
        to a fresh session without paying the round-trip on a guaranteed
        rejection.
        """
        import time
        handle = cached_resumption_handle[0]
        if not handle:
            return None
        age = time.monotonic() - cached_resumption_handle_at[0]
        if age >= settings.LIVE_RESUMPTION_HANDLE_MAX_AGE_S:
            logger.info(
                "voice_ws (Live) resumption handle expired (age=%.1fs); "
                "dropping and rebuilding fresh",
                age,
            )
            cached_resumption_handle[0] = None
            cached_resumption_handle_at[0] = 0.0
            return None
        return handle

    async def _reconnect_after_crash() -> None:
        """Rebuild the LiveSession after a receive-pump crash.

        Caller (``_on_pump_crash``) has already set ``reconnect_in_progress``
        synchronously. We just need to clear it on exit. Bumped attempt
        counter is checked against ``settings.LIVE_RECONNECT_MAX_RETRIES``;
        past the cap we surface an error frame and leave the WS for the
        FE to close.
        """
        try:
            attempt = reconnect_attempts[0] + 1
            if attempt > settings.LIVE_RECONNECT_MAX_RETRIES:
                logger.warning(
                    "voice_ws (Live) reconnect cap reached (%d) — giving up",
                    settings.LIVE_RECONNECT_MAX_RETRIES,
                )
                try:
                    await ws_send_json(ws, {
                        "type": "error",
                        "message": "voice connection lost — please refresh",
                    })
                except Exception:
                    pass
                # Don't proactively close the WS — receive loop will exit
                # next time the FE sends a frame and saw no live session.
                return
            reconnect_attempts[0] = attempt
            handle = _resumption_handle_if_fresh()
            await _swap_to_new_session(
                resumption_handle=handle,
                reason=f"pump-crash-retry-{attempt}",
            )
        finally:
            reconnect_in_progress[0] = False

    async def _handle_go_away_reconnect() -> None:
        """Proactive reconnect triggered by a server-side GoAway notice.

        Live emits GoAway ~30s before the underlying transport closes
        (typically at the ~10min cap). We open a fresh LiveSession with
        the cached handle in parallel and atomically swap the slot once
        the new one is ready, so the audio gap is bounded by the swap
        time (sub-second) rather than the time it takes to detect the
        forced close (multi-second).

        Doesn't bump ``reconnect_attempts`` — GoAway is a healthy server
        rotation, not a fault. We DON'T want owner's 11th proactive
        rotation to fail just because they had two crashes earlier.

        Caller (``_on_go_away``) has already set ``reconnect_in_progress``
        synchronously. We just need to clear it on exit.
        """
        try:
            handle = _resumption_handle_if_fresh()
            await _swap_to_new_session(
                resumption_handle=handle,
                reason="go-away-rotation",
            )
        finally:
            reconnect_in_progress[0] = False

    async def _swap_to_new_session(
        *,
        resumption_handle: Optional[str],
        reason: str,
    ) -> None:
        """Open a new LiveSession and atomically swap it into the slot.

        Old session is closed only AFTER the new one is open so the user
        experience is bounded by the open latency (typically <500ms),
        not the close latency. If the new open fails, the old session
        stays in place (it's already dead post-pump-crash, but at least
        the receive loop won't be sending audio to None).
        """
        try:
            new_sess = _build_live_session(resumption_handle=resumption_handle)
        except Exception as exc:
            logger.exception(
                "voice_ws (Live) reconnect build failed reason=%s: %s",
                reason, exc,
            )
            try:
                await ws_send_json(ws, {
                    "type": "error",
                    "message": "voice connection failed to rebuild",
                })
            except Exception:
                pass
            return

        # Tell the FE we're swapping BEFORE we touch the slot so a
        # subtle "reconnecting" indicator can render. The FE clears it
        # on the matching ``reconnected`` frame.
        try:
            await ws_send_json(ws, {"type": "reconnecting", "reason": reason})
        except Exception:
            pass

        try:
            await new_sess.open()
        except Exception as exc:
            logger.exception(
                "voice_ws (Live) reconnect open failed reason=%s: %s",
                reason, exc,
            )
            # Clean up the half-built session so we don't leak its async
            # context manager. ``close()`` is a safe no-op if open never
            # actually attached the AsyncSession.
            try:
                await new_sess.close()
            except Exception:
                pass
            try:
                await ws_send_json(ws, {
                    "type": "error",
                    "message": "voice connection failed to rebuild",
                })
            except Exception:
                pass
            return

        # Swap into the slot. The old session is closed AFTER the swap so
        # any callbacks already queued for the old pump (which is dead)
        # find a None reference and skip cleanly.
        old_sess = live_session_box[0]
        live_session_box[0] = new_sess
        if old_sess is not None:
            try:
                await old_sess.close()
            except Exception as exc:
                logger.debug(
                    "voice_ws (Live) old session close on swap failed: %s", exc,
                )
        # Clear in-flight transcript accumulators — server-side context is
        # preserved by the resumption handle, but the IN-FLIGHT turn (if
        # any) was lost when the pump died, so any partial transcript we
        # had cached belongs to nothing.
        input_transcript_buf.clear()
        output_transcript_buf.clear()
        logger.info(
            "voice_ws (Live) reconnected reason=%s handle=%s",
            reason, "yes" if resumption_handle else "no",
        )
        try:
            await ws_send_json(ws, {"type": "reconnected", "reason": reason})
        except Exception:
            pass

    async def _handle_scope_flip(old_project: str, new_project: str) -> None:
        """Cancel any in-flight turn, close, and reopen against the new scope.

        Different scope means a different system prompt (different Chief
        identity, different project memory, different repo binding). We
        do NOT hot-swap — closed-and-reopen is the only honest way to
        get a fresh ``system_instruction`` into Live.

        Stage 4 (mid-turn responsiveness): if a turn is in flight we cancel
        it BEFORE closing the session. Owner pressing the scope switcher
        mid-utterance expects the new scope NOW, not after the half-formed
        reply finishes. Cost is the in-flight reply (already partially
        billed); benefit is responsive scope switching.

        Steps:
          1. Cancel any in-flight turn so the FE flushes pending audio.
          2. Tear down CC pool entries for *other* scopes (frees memory,
             drops crash-loop state). Best-effort.
          3. Drop the cached resumption handle — it's bound to the OLD
             scope's system prompt, so reusing it on the new scope would
             rehydrate Chief with the wrong identity.
          4. Reset reconnect attempt counter — new scope, fresh budget.
          5. Close the current LiveSession.
          6. Reload persistent memory for the new scope so the new
             system prompt carries the right rolling summary.
          7. Open a new LiveSession against the new scope (with fresh
             tools — see below).
        """
        nonlocal current_summary

        # (1) Mid-turn cancel. Cheap if no turn is in flight (cancel_current_turn
        # is idempotent + safe on a closed session). Synthesizes an
        # ``interrupted`` frame so the FE flushes any audio queued for
        # the old scope's reply.
        old_sess = live_session_box[0]
        if old_sess is not None:
            try:
                await old_sess.cancel_current_turn()
            except Exception as exc:
                logger.debug(
                    "voice_ws (Live) scope-flip pre-cancel failed: %s", exc,
                )

        try:
            await cc_session.get_pool().teardown_other_scopes(
                subject=client_id,
                keep_scope=new_project,
                reason="scope-switch",
            )
        except Exception as exc:
            logger.warning(
                "teardown_other_scopes failed during scope flip "
                "subject=%s old=%s new=%s: %s",
                client_id, old_project, new_project, exc,
            )

        # Drop the in-flight turn's accumulators — old scope's data must
        # not bleed into the new scope's first turn.
        input_transcript_buf.clear()
        output_transcript_buf.clear()

        # The cached resumption handle is bound to the old scope's
        # system_instruction. Reusing it on the new scope rehydrates Chief
        # with the wrong identity, so drop it. Reconnect-attempt counter
        # also resets so the new scope gets a clean retry budget.
        cached_resumption_handle[0] = None
        cached_resumption_handle_at[0] = 0.0
        reconnect_attempts[0] = 0

        await _close_live_session()

        try:
            new_summary, new_turns = await load_persistent_memory(
                new_project, raw_limit=20,
            )
            history.clear()
            history.extend(new_turns)
            current_summary = new_summary
            logger.info(
                "voice_ws (Live) scope flip rehydrated subject=%s old=%s new=%s "
                "history_turns=%d summary=%s",
                client_id, old_project, new_project, len(history),
                "yes" if current_summary else "no",
            )
        except Exception as exc:
            logger.warning(
                "voice_ws (Live) scope flip rehydrate failed subject=%s new=%s: %s",
                client_id, new_project, exc,
            )
            history.clear()
            current_summary = None

        try:
            await _open_live_session()
        except Exception as exc:
            logger.exception(
                "voice_ws (Live) reopen-after-scope-flip failed scope=%s: %s",
                new_project, exc,
            )
            try:
                await ws_send_json(ws, {
                    "type": "error",
                    "message": "voice connection failed to reopen on scope switch",
                })
            except Exception:
                pass

    # Open the initial LiveSession. If this fails the WS is unusable —
    # surface the error and close cleanly with code 4002 so the FE can
    # tell auth-failure (4001) apart from session-open failure.
    try:
        await _open_live_session()
    except Exception as exc:
        logger.exception("voice_ws (Live) initial open failed: %s", exc)
        try:
            await ws_send_json(ws, {
                "type": "error",
                "message": "voice connection failed to open",
            })
        except Exception:
            pass
        try:
            await ws.close(code=4002)
        except Exception:
            pass
        return

    # ----- Main receive loop -----
    try:
        while True:
            message = await ws.receive()

            if message.get("type") == "websocket.disconnect":
                break

            sess = live_session_box[0]

            if "bytes" in message:
                # 16kHz Int16 PCM frame from the browser. Forward straight
                # to Live; server-side VAD handles turn boundaries +
                # barge-in. Drop frames that arrive between scope-flip
                # close + reopen so we don't crash on a None session.
                if sess is None:
                    continue
                try:
                    await sess.send_audio(message["bytes"])
                except Exception as exc:
                    logger.warning("voice_ws (Live) send_audio failed: %s", exc)

            elif "text" in message:
                raw = message["text"]
                try:
                    data = json.loads(raw)
                    msg_type = data.get("type")
                except json.JSONDecodeError:
                    logger.info("voice_ws (Live) ignoring non-JSON text frame: %r", raw[:120])
                    continue

                if msg_type == "context":
                    # Scope flip from the picker. Validate, persist, and
                    # close-and-reopen the LiveSession against the new
                    # scope. Initial frame on WS open often echoes the
                    # rehydrated scope — no-op in that case.
                    raw_proj = _migrate_dissolved_scope(data.get("project") or None)
                    new_proj = raw_proj if raw_proj in AVAILABLE_PROJECTS else DEFAULT_PROJECT
                    old_project = current_project
                    if new_proj != old_project:
                        current_project = new_proj
                        _context_store[context_key] = current_project
                        await _handle_scope_flip(old_project, current_project)
                    else:
                        # Echo-only context frame — keep the persisted
                        # value canonical (handles Archie -> Arch
                        # migration on first WS open).
                        _context_store[context_key] = current_project
                    try:
                        await ws_send_json(ws, {
                            "type": MSG_CONTEXT_SWITCHED,
                            "project": current_project,
                        })
                    except Exception as exc:
                        logger.warning(
                            "voice_ws (Live) context_switched send failed: %s", exc,
                        )

                elif msg_type == "text":
                    # Typed input on iPad keyboard. Routes through the
                    # text-turn channel so transcript bubbles still
                    # populate AND the Live model can answer with audio.
                    content = data.get("content", "")
                    if content and content.strip() and sess is not None:
                        # Mirror typed text into input_transcript_buf so
                        # ``on_turn_complete`` persists it as the user
                        # turn — without this, typed input would land in
                        # history with an empty user_text.
                        input_transcript_buf.append(content)
                        try:
                            await sess.send_text(content)
                        except Exception as exc:
                            logger.warning("voice_ws (Live) send_text failed: %s", exc)

                elif msg_type == "interrupt":
                    # Manual barge-in (UI cancel button without speaking).
                    if sess is not None:
                        await sess.cancel_current_turn()

                elif msg_type == "speed":
                    # Backward-compat — Live API has no speed control.
                    # Echo the value back so FE state reconciles cleanly
                    # without a one-way "did the server accept it?"
                    # ambiguity.
                    try:
                        echo_speed = float(data.get("value", 1.0))
                    except (TypeError, ValueError):
                        echo_speed = 1.0
                    try:
                        await ws_send_json(ws, {"type": "speed", "value": echo_speed})
                    except Exception:
                        pass

                elif msg_type == "cancel":
                    # Dispatch task cancel — handled by ``_dispatcher``,
                    # NOT the Live session. The FE's TaskBubble red-X
                    # button hits this path; we kill the subprocess and
                    # let the dispatcher's on_complete callback
                    # surface the result.
                    sid = await ensure_session_id()
                    try:
                        await _dispatcher.cancel(sid)
                    except Exception as exc:
                        logger.warning("voice_ws (Live) dispatcher.cancel failed: %s", exc)

                else:
                    logger.info("voice_ws (Live) ignoring unknown text type: %s", msg_type)

            else:
                logger.warning("voice_ws (Live) unknown message shape keys=%s", list(message.keys()))

    except WebSocketDisconnect:
        logger.info("voice_ws (Live) disconnected session=%s", session_id)
    except Exception as exc:
        # Receive-pump or send-side fault. Surface to FE if WS is still
        # alive, then fall through to cleanup. Don't try to reopen — that's
        # Stage 4.
        logger.exception("voice_ws (Live) error session=%s: %s", session_id, exc)
        try:
            await ws_send_json(ws, {
                "type": "error",
                "message": "voice connection dropped",
            })
        except Exception:
            pass
    finally:
        # Cleanup. Order matters: kill any dispatcher subprocess first
        # (so the close on the LiveSession doesn't block on a runaway
        # claude CLI's stdout drain), then close Live, then close the
        # usage-tracker session row.
        try:
            if session_id is not None:
                await _dispatcher.cancel(session_id)
        except Exception:
            pass
        await _close_live_session()
        if session_id is not None:
            try:
                await close_session(session_id)
            except Exception as exc:
                logger.warning("voice_ws (Live) close_session failed: %s", exc)


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
