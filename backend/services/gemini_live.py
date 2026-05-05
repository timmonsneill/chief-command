"""Gemini Live API session wrapper — native audio I/O brain (Stage 1).

Stage 1 of the Live API pivot. This module ISOLATES the SDK surface
(`client.aio.live.connect` and the AsyncSession lifecycle) behind a
stable callback-driven facade so the WebSocket layer (Stage 2) and the
tool-routing layer (Stage 3) can be wired in cleanly later.

Architecture:
  Voice WS  ─ pcm chunks (16k mono int16) ─►  LiveSession.send_audio
                                                      │
                                                      ▼
                              client.aio.live.connect → AsyncSession
                                                      │
                                                      ▼
                            background receive pump   ─► dispatches to
                            (LiveSession._pump)          caller-supplied
                                                         coroutines:
                                                           on_audio_chunk
                                                           on_input_transcript
                                                           on_output_transcript
                                                           on_interrupted
                                                           on_turn_complete
                                                           on_tool_call
                                                           on_session_resumed
                                                           on_go_away

Audio formats (per Live API contract):
  * Input:  16 kHz mono 16-bit little-endian PCM, mime_type
            ``audio/pcm;rate=16000``.
  * Output: 24 kHz mono 16-bit little-endian PCM, delivered as raw bytes
            via ``server_content.model_turn.parts[*].inline_data.data``.

What this module does NOT do (intentionally — later stages):
  * Stage 2: integration with ``app/websockets.py``. WS layer will own
    the on_*  callbacks and translate them to outbound WS frames.
  * Stage 3: tools. ``LiveConnectConfig`` accepts a ``tools=`` list, but
    we don't pass any here. ``on_tool_call`` is a placeholder — Stage 3
    will execute via ``services.agent_tools.dispatch_tool`` and reply
    via ``send_tool_response``.
  * Cost computation. ``usage_tracker.compute_cost_cents`` is unchanged.
    The ``gemini-live-2.5-flash-native-audio`` PRICING entry is added so
    Stage 3's audio-token recording is one line.

Module is import-safe in environments without the google-genai SDK
installed — the SDK is imported lazily inside ``LiveSession.open``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Awaitable, Callable, Optional

from config.settings import settings

logger = logging.getLogger(__name__)


# Canonical model id for the Live API native-audio model. GA on Vertex AI
# as of Dec 2025. This is NOT the 3.1-preview path — pinned to the GA
# build until the preview delta justifies the risk surface.
LIVE_MODEL: str = "gemini-live-2.5-flash-native-audio"

# Audio format constants. The Live API REQUIRES these exact rates +
# encoding — a 24k input or stereo PCM is rejected at the SDK layer.
# Output rate is fixed by the server (we just receive what it sends).
INPUT_AUDIO_MIME_TYPE: str = "audio/pcm;rate=16000"
INPUT_SAMPLE_RATE_HZ: int = 16000
OUTPUT_SAMPLE_RATE_HZ: int = 24000

# The receive pump's outermost handler logs unexpected exceptions and
# signals on_interrupted so callers can recover; we cap how long we wait
# for a graceful close on tear-down so a flakey server-side socket
# doesn't wedge ``LiveSession.close`` indefinitely.
RECEIVE_PUMP_CLOSE_TIMEOUT_S: float = 2.0


# Type aliases for the callback surface. All callbacks are async — the
# pump is itself async and would otherwise spin a loop just to route
# events to threads.
AudioCb = Callable[[bytes], Awaitable[None]]
TranscriptCb = Callable[[str], Awaitable[None]]
InterruptedCb = Callable[[], Awaitable[None]]
TurnCompleteCb = Callable[[dict], Awaitable[None]]
ToolCallCb = Callable[[Any], Awaitable[Any]]
SessionResumedCb = Callable[[str], Awaitable[None]]
GoAwayCb = Callable[[float], Awaitable[None]]


_client: Optional[Any] = None


def _get_client() -> Any:
    """Lazily construct + cache the genai client for Live API use.

    Mirrors ``gemini_brain._get_client`` so both modules pick up the
    same auth path (AI Studio key OR Vertex service-account). Cached
    separately because the Live API's WebSocket transport may be served
    from a different sub-client than the streaming HTTP one — keeping
    the cache symmetric with gemini_brain rather than shared also avoids
    cross-contamination if one path's client gets re-initialised.

    Resolution order:
      1. ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY`` (env or settings) →
         AI Studio path. genai handles WebSocket framing.
      2. ``GOOGLE_APPLICATION_CREDENTIALS`` set → Vertex AI path with
         project/location from settings.
      3. Neither → RuntimeError with a clear message.
    """
    global _client
    if _client is not None:
        return _client
    from google import genai

    api_key = (
        getattr(settings, "GEMINI_API_KEY", None)
        or getattr(settings, "GOOGLE_API_KEY", None)
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
    )

    if api_key:
        _client = genai.Client(api_key=api_key)
        logger.info("gemini_live: using AI Studio (api_key)")
        return _client

    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        project = settings.VERTEX_AI_PROJECT
        location = settings.VERTEX_AI_LOCATION
        _client = genai.Client(
            vertexai=True,
            project=project,
            location=location,
        )
        logger.info(
            "gemini_live: using Vertex AI (project=%s location=%s)",
            project, location,
        )
        return _client

    raise RuntimeError(
        "gemini_live: no auth configured. Set GEMINI_API_KEY for the "
        "AI Studio path, or GOOGLE_APPLICATION_CREDENTIALS + Vertex AI "
        "roles for the service-account path."
    )


def _build_live_config(
    *,
    system_prompt: str,
    resumption_handle: Optional[str],
    extra_tools: Optional[list[Any]] = None,
) -> Any:
    """Construct the ``types.LiveConnectConfig`` for ``connect()``.

    Pulled into a helper so tests can inspect / mutate it without
    re-implementing the option matrix. Stage 3 will pass
    ``extra_tools=[...]`` here; Stage 1 callers leave it None.
    """
    from google.genai import types

    config_kwargs: dict[str, Any] = {
        "response_modalities": [types.Modality.AUDIO],
        "system_instruction": types.Content(
            parts=[types.Part(text=system_prompt)]
        ),
        "output_audio_transcription": types.AudioTranscriptionConfig(),
        "input_audio_transcription": types.AudioTranscriptionConfig(),
        "context_window_compression": types.ContextWindowCompressionConfig(
            sliding_window=types.SlidingWindow(),
        ),
        "session_resumption": types.SessionResumptionConfig(
            handle=resumption_handle,
        ),
    }
    if extra_tools:
        config_kwargs["tools"] = extra_tools
    return types.LiveConnectConfig(**config_kwargs)


class LiveSession:
    """Single Gemini Live session. One per voice WebSocket connection.

    Wraps ``client.aio.live.connect`` so the caller doesn't have to
    manage the async-context entry/exit, the receive pump task, or the
    server-message dispatch matrix. The caller registers async callbacks
    at construction (or via the ``on_*`` setters) and the pump invokes
    them as events arrive.

    Lifecycle:
      sess = LiveSession(system_prompt=..., on_audio_chunk=..., ...)
      await sess.open()                    # opens connect, starts pump
      await sess.send_audio(pcm_chunk)     # repeated, real-time
      ...
      await sess.close()                   # cancels pump, closes connect

    Concurrency contract:
      * ``open`` must be awaited before any ``send_*`` call.
      * Multiple coroutines may call ``send_audio`` / ``send_text`` /
        ``send_tool_response`` on the same session from the SAME asyncio
        loop; the underlying ``AsyncSession`` serialises writes.
      * ``close`` is idempotent — calling it twice is safe.
      * The receive pump runs as a background task; it raises only
        ``asyncio.CancelledError`` (re-raised cleanly during tear-down).
        All other exceptions are logged + signal ``on_interrupted`` so
        the caller can decide whether to reopen.
    """

    def __init__(
        self,
        *,
        model: str = LIVE_MODEL,
        system_prompt: str,
        on_audio_chunk: AudioCb,
        on_input_transcript: Optional[TranscriptCb] = None,
        on_output_transcript: Optional[TranscriptCb] = None,
        on_interrupted: Optional[InterruptedCb] = None,
        on_turn_complete: Optional[TurnCompleteCb] = None,
        on_tool_call: Optional[ToolCallCb] = None,
        on_session_resumed: Optional[SessionResumedCb] = None,
        on_go_away: Optional[GoAwayCb] = None,
        resumption_handle: Optional[str] = None,
    ) -> None:
        # Required identity
        self.model = model
        self.system_prompt = system_prompt

        # Callbacks — all stored even if None. The pump tests for None
        # before invoking each one.
        self.on_audio_chunk = on_audio_chunk
        self.on_input_transcript = on_input_transcript
        self.on_output_transcript = on_output_transcript
        self.on_interrupted = on_interrupted
        self.on_turn_complete = on_turn_complete
        self.on_tool_call = on_tool_call
        self.on_session_resumed = on_session_resumed
        self.on_go_away = on_go_away

        # Resumption — we keep the latest handle on the instance so a
        # caller can checkpoint it (for cross-reconnect resumption) by
        # reading ``self.resumption_handle`` whenever convenient.
        self.resumption_handle: Optional[str] = resumption_handle

        # Internal SDK handles. Created in open(); cleared in close().
        self._cm: Optional[Any] = None         # connect() async context mgr
        self._session: Optional[Any] = None    # AsyncSession yielded by it
        self._pump_task: Optional[asyncio.Task[None]] = None
        self._closed: bool = False

        # Per-session usage accumulator. ``UsageMetadata`` arrives on
        # individual frames during a turn; we sum into this dict and
        # snapshot it onto each on_turn_complete dispatch so the caller
        # gets the running totals and can record per-turn billing.
        # Audio token counts come through on
        # ``response_tokens_details`` / ``prompt_tokens_details`` —
        # Stage 3 will plumb those into usage_tracker.compute_cost_cents.
        self._usage: dict[str, int] = {
            "prompt_token_count": 0,
            "response_token_count": 0,
            "cached_content_token_count": 0,
            "total_token_count": 0,
            "audio_input_tokens": 0,
            "audio_output_tokens": 0,
            "text_input_tokens": 0,
            "text_output_tokens": 0,
        }

    # ------------------------------------------------------------------
    # Open / close
    # ------------------------------------------------------------------
    async def open(self) -> None:
        """Open the live session and start the receive pump.

        Raises whatever the SDK raises on connect failure (auth, model
        not enabled in region, etc) — caller is expected to handle and
        decide whether to fall back to the legacy STT/text/TTS path.
        """
        if self._session is not None:
            raise RuntimeError("gemini_live: open() called twice on same session")

        client = _get_client()
        config = _build_live_config(
            system_prompt=self.system_prompt,
            resumption_handle=self.resumption_handle,
        )

        # ``client.aio.live.connect`` returns an async context manager.
        # We hold the CM open for the lifetime of the LiveSession (i.e.
        # we manually __aenter__ here and __aexit__ in close()) — we
        # can't use ``async with`` because the lifetime is owned by the
        # caller, not a single coroutine.
        self._cm = client.aio.live.connect(model=self.model, config=config)
        self._session = await self._cm.__aenter__()
        logger.info(
            "gemini_live: session opened model=%s resumption_handle=%s",
            self.model,
            "<set>" if self.resumption_handle else "<none>",
        )

        # Spawn the receive pump. We name it for debuggability; if it
        # crashes during a turn, ``add_done_callback`` surfaces the
        # exception in the log even when no caller awaits it.
        self._pump_task = asyncio.create_task(
            self._pump(),
            name=f"gemini_live_pump:{id(self):x}",
        )
        self._pump_task.add_done_callback(self._on_pump_done)

    async def close(self) -> None:
        """Cancel the receive pump and close the live session.

        Idempotent — repeated calls are no-ops after the first.
        """
        if self._closed:
            return
        self._closed = True

        # Cancel the pump first so it doesn't race the session close.
        if self._pump_task is not None and not self._pump_task.done():
            self._pump_task.cancel()
            try:
                await asyncio.wait_for(
                    self._pump_task,
                    timeout=RECEIVE_PUMP_CLOSE_TIMEOUT_S,
                )
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("gemini_live: pump exited with exception: %s", exc)
        self._pump_task = None

        # Close the AsyncSession context. We don't pass exception info
        # here — even if a caller's coroutine raised, the session close
        # protocol is the same.
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception as exc:
                logger.warning("gemini_live: connect() __aexit__ raised: %s", exc)
        self._cm = None
        self._session = None
        logger.info("gemini_live: session closed")

    # ------------------------------------------------------------------
    # Send paths
    # ------------------------------------------------------------------
    async def send_audio(self, pcm_chunk: bytes) -> None:
        """Forward a 16kHz mono int16-LE PCM chunk to the live session.

        Caller is responsible for chunking (typical: 20-100ms per call,
        i.e. 640-3200 bytes). Empty chunks are silently dropped — they
        carry no signal and would just spend bandwidth.
        """
        if not pcm_chunk:
            return
        sess = self._require_session()
        from google.genai import types
        await sess.send_realtime_input(
            audio=types.Blob(data=pcm_chunk, mime_type=INPUT_AUDIO_MIME_TYPE),
        )

    async def send_text(self, text: str) -> None:
        """Send a text-only client turn (typed user input).

        Uses ``send_client_content`` with ``turn_complete=True`` so the
        model treats it as a complete user turn (vs. partial dictation).
        Use this for typed messages alongside / interleaved with audio.
        """
        if not text:
            return
        sess = self._require_session()
        from google.genai import types
        await sess.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=text)]),
            turn_complete=True,
        )

    async def send_tool_response(self, function_responses: list) -> None:
        """Reply to a server-side tool call with one or more results.

        Stage 3 wires this — Stage 1 keeps it for the test surface and
        so the caller's mental model is complete.
        """
        sess = self._require_session()
        await sess.send_tool_response(function_responses=function_responses)

    # ------------------------------------------------------------------
    # Receive pump — internals
    # ------------------------------------------------------------------
    async def _pump(self) -> None:
        """Iterate ``session.receive()`` and dispatch each event.

        The pump is the only place where Live server-message → callback
        translation happens. It catches CancelledError cleanly (re-
        raises) and catches all other exceptions at the outermost level,
        signalling on_interrupted (if set) so the caller can decide
        whether to recover. Inner per-callback errors are logged and
        swallowed so one bad caller-supplied coroutine can't kill the
        pump and silently drop subsequent events.
        """
        sess = self._session
        if sess is None:
            return  # close() raced open() — nothing to do

        try:
            async for response in sess.receive():
                await self._dispatch(response)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("gemini_live: receive pump crashed")
            await self._safe_invoke(
                self.on_interrupted,
                None,  # no arg
                tag="on_interrupted (pump-crash)",
            )

    async def _dispatch(self, response: Any) -> None:
        """Route a single LiveServerMessage to the right callback(s).

        We test for each top-level field independently because a single
        server frame can carry multiple fields (e.g. server_content with
        audio bytes AND a transcription update AND generation_complete
        on the same message). Each branch is independent.
        """
        # ----- top-level usage_metadata -----
        # Live API emits usage_metadata on selected frames (typically
        # after a turn completes). Accumulate on every emit so partial
        # mid-turn snapshots also count; on_turn_complete will receive
        # the running total.
        um = getattr(response, "usage_metadata", None)
        if um is not None:
            self._accumulate_usage(um)

        # ----- server_content (audio out, transcripts, turn flags) -----
        sc = getattr(response, "server_content", None)
        if sc is not None:
            await self._dispatch_server_content(sc)

        # ----- session_resumption_update -----
        sru = getattr(response, "session_resumption_update", None)
        if sru is not None:
            new_handle = getattr(sru, "new_handle", None)
            resumable = getattr(sru, "resumable", None)
            if new_handle and resumable is not False:
                self.resumption_handle = new_handle
                await self._safe_invoke(
                    self.on_session_resumed,
                    new_handle,
                    tag="on_session_resumed",
                )

        # ----- go_away (server warning of imminent disconnect) -----
        ga = getattr(response, "go_away", None)
        if ga is not None:
            time_left_raw = getattr(ga, "time_left", None)
            time_left_s = _coerce_time_left_seconds(time_left_raw)
            await self._safe_invoke(
                self.on_go_away,
                time_left_s,
                tag="on_go_away",
            )

        # ----- tool_call (Stage 3 will wire) -----
        tc = getattr(response, "tool_call", None)
        if tc is not None:
            fcalls = getattr(tc, "function_calls", None) or []
            logger.warning(
                "gemini_live: tool_call received (Stage 3 not wired) — "
                "%d function call(s); names=%s",
                len(fcalls),
                [getattr(fc, "name", "?") for fc in fcalls],
            )
            await self._safe_invoke(
                self.on_tool_call,
                tc,
                tag="on_tool_call",
            )

    async def _dispatch_server_content(self, sc: Any) -> None:
        """Inner dispatch for the ``server_content`` field of a message.

        Walks model_turn parts for inline audio, then transcripts, then
        turn-state flags. Order matters only insofar as a downstream
        on_audio_chunk MUST run before on_turn_complete (so the caller
        sees audio frames before the "turn done" signal); everything
        else is independent.
        """
        # Audio chunks ride on ``model_turn.parts[*].inline_data.data``.
        model_turn = getattr(sc, "model_turn", None)
        if model_turn is not None:
            parts = getattr(model_turn, "parts", None) or []
            for part in parts:
                inline = getattr(part, "inline_data", None)
                if inline is None:
                    continue
                audio_bytes = getattr(inline, "data", None)
                if not audio_bytes:
                    continue
                await self._safe_invoke(
                    self.on_audio_chunk,
                    audio_bytes,
                    tag="on_audio_chunk",
                )

        # Input audio transcription (the user's speech, recognised).
        in_tx = getattr(sc, "input_transcription", None)
        if in_tx is not None and self.on_input_transcript is not None:
            text = getattr(in_tx, "text", None)
            if text:
                await self._safe_invoke(
                    self.on_input_transcript,
                    text,
                    tag="on_input_transcript",
                )

        # Output audio transcription (the model's spoken reply).
        out_tx = getattr(sc, "output_transcription", None)
        if out_tx is not None and self.on_output_transcript is not None:
            text = getattr(out_tx, "text", None)
            if text:
                await self._safe_invoke(
                    self.on_output_transcript,
                    text,
                    tag="on_output_transcript",
                )

        # Interrupted — the model was cut off mid-utterance (typically
        # because the user started speaking again). Caller flushes any
        # pending TTS / clears the audio queue.
        if getattr(sc, "interrupted", None) is True:
            await self._safe_invoke(
                self.on_interrupted,
                None,
                tag="on_interrupted",
            )

        # Turn complete — server is done generating for this user turn.
        # We hand a copy of the running usage dict to the callback so
        # the caller can record billing without holding a live reference
        # that we'd later mutate.
        if getattr(sc, "generation_complete", None) is True:
            usage_snapshot = dict(self._usage)
            await self._safe_invoke(
                self.on_turn_complete,
                usage_snapshot,
                tag="on_turn_complete",
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _require_session(self) -> Any:
        """Return the live session, raising if not opened / already closed."""
        if self._session is None:
            raise RuntimeError(
                "gemini_live: session not open — call open() before send_*"
            )
        return self._session

    def _accumulate_usage(self, um: Any) -> None:
        """Sum a UsageMetadata frame into the per-session totals.

        Live emits running totals on each frame, so we OVERWRITE the
        cumulative scalars and ADD modality-detail counts. The
        modality-detail accumulation matters because the SDK emits each
        modality breakdown once per turn rather than as a running total.
        """
        # Top-level scalars are running totals — overwrite, don't sum.
        for src, dst in (
            ("prompt_token_count", "prompt_token_count"),
            ("response_token_count", "response_token_count"),
            ("cached_content_token_count", "cached_content_token_count"),
            ("total_token_count", "total_token_count"),
        ):
            v = getattr(um, src, None)
            if isinstance(v, int):
                self._usage[dst] = v

        # Modality breakdowns. Each is a list of ModalityTokenCount with
        # ``modality`` (enum-like with .name) and ``token_count``. We map
        # AUDIO/TEXT into our flat fields. Some SDK builds emit
        # MODALITY_UNSPECIFIED for tool tokens; we drop those — they're
        # already covered by the top-level total.
        prompt_details = getattr(um, "prompt_tokens_details", None) or []
        response_details = getattr(um, "response_tokens_details", None) or []
        for entry in prompt_details:
            modality = _modality_name(getattr(entry, "modality", None))
            count = int(getattr(entry, "token_count", 0) or 0)
            if modality == "AUDIO":
                self._usage["audio_input_tokens"] = count
            elif modality == "TEXT":
                self._usage["text_input_tokens"] = count
        for entry in response_details:
            modality = _modality_name(getattr(entry, "modality", None))
            count = int(getattr(entry, "token_count", 0) or 0)
            if modality == "AUDIO":
                self._usage["audio_output_tokens"] = count
            elif modality == "TEXT":
                self._usage["text_output_tokens"] = count

    async def _safe_invoke(
        self,
        cb: Optional[Callable[..., Awaitable[Any]]],
        arg: Any,
        *,
        tag: str,
    ) -> None:
        """Invoke a caller-supplied callback, swallowing exceptions.

        CancelledError still propagates — that's the only signal we
        want to honour at the pump level (so close() actually closes).
        Everything else is logged and swallowed so one buggy callback
        can't poison subsequent dispatches in the same turn.
        """
        if cb is None:
            return
        try:
            if arg is None:
                # Distinguish "callback takes no args" from "we have an
                # arg of None". on_interrupted is the only no-arg
                # callback in this surface; we always invoke it with no
                # args by passing arg=None and skipping the positional.
                await cb()
            else:
                await cb(arg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("gemini_live: callback %s raised: %s", tag, exc)

    def _on_pump_done(self, task: asyncio.Task) -> None:
        """Surface unexpected pump exits in the log.

        Called by the asyncio scheduler when the pump task finishes for
        any reason. We log only when the cause was an exception — clean
        cancel during close() is the expected exit path.
        """
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning("gemini_live: pump task ended with exception: %r", exc)


def _modality_name(modality: Any) -> str:
    """Best-effort string for a ``Modality`` value.

    SDK builds vary: some hand back the enum, some hand back the bare
    string. We accept either and normalise to upper-case.
    """
    if modality is None:
        return ""
    name = getattr(modality, "name", None)
    if isinstance(name, str):
        return name.upper()
    if isinstance(modality, str):
        return modality.upper()
    return str(modality).upper()


def _coerce_time_left_seconds(raw: Any) -> float:
    """Convert a GoAway time_left value into a float of seconds.

    Some SDK builds expose the field as ``int`` seconds, others as a
    proto duration string like ``"42s"``, others as ``timedelta``. We
    try the obvious shapes and fall back to 0.0 — a 0 here means "go
    away NOW", which is the same conservative thing we'd do if we
    couldn't parse a real value anyway.
    """
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    total_seconds = getattr(raw, "total_seconds", None)
    if callable(total_seconds):
        try:
            return float(total_seconds())
        except Exception:
            pass
    if isinstance(raw, str):
        s = raw.strip().rstrip("s").rstrip("S")
        try:
            return float(s)
        except ValueError:
            return 0.0
    return 0.0


__all__ = [
    "LIVE_MODEL",
    "INPUT_AUDIO_MIME_TYPE",
    "INPUT_SAMPLE_RATE_HZ",
    "OUTPUT_SAMPLE_RATE_HZ",
    "LiveSession",
]
