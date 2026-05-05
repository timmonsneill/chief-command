"""Gemini 2.5 Flash via Vertex AI — Chief's brain (Phase 2; flipped from Pro
to Flash on 2026-05-05 for latency).

This is the streaming + function-call adapter that turns the Vertex AI
Gemini SDK into a drop-in for the legacy Anthropic ``stream_turn`` API.

Architecture:
  Voice WS -> STT -> [stream(history, send_token, send_tts_sentence,
                             send_tool_call, ...)] -> TTS -> audio out
                       │
                       └─ tools[]: Read / Bash / Grep / dispatch_agent
                          (executed in-process via agent_tools.dispatch_tool)

Key behaviors:
  * Text-mode streaming via ``client.aio.models.generate_content_stream``.
  * Manual function-call loop: when a chunk's candidate part has a
    ``function_call`` we (a) flush the in-flight TTS sentence buffer, (b)
    execute the tool via ``agent_tools.dispatch_tool``, (c) append both the
    model's call and our function_response to the conversation history, then
    (d) re-issue ``generate_content_stream`` with the updated history. Loops
    until the model emits text without a tool call (or the tool-loop guard
    fires).
  * Cancellation parity with the original ``llm.stream_turn``: we check
    ``asyncio.current_task().cancelling()`` between chunks, never await
    additional state retrieval after a cancel point, and let the SDK's async
    iterator handle its own teardown. If an in-flight tool call (especially
    dispatch_agent) is running on cancel, we let it propagate
    CancelledError — agent_tools.execute_dispatch_agent already calls
    ``cc_session.interrupt()`` on its way out.
  * Cost: Gemini usage_metadata is parsed at end-of-turn. The result dict
    matches the old Anthropic ``usage_dict`` shape so the existing
    record_turn / usage_tracker pipeline keeps working unchanged.

Module is import-safe in environments without ``google-genai`` installed —
the SDK is imported lazily inside ``stream``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from config.settings import settings
from services.agent_tools import dispatch_tool, to_gemini_tool

logger = logging.getLogger(__name__)


# Canonical model id used both as the Vertex AI model name AND as the
# database "model" column for the cost tracker. Keeping a single source of
# truth here avoids drift between the brain call and the cost row.
#
# 2026-05-05: flipped Pro -> Flash after owner hit 12-14s TTFT on Pro in real
# iOS use. Flash measures ~1-2s TTFT for the same prompts and the routing
# quality is good enough for the rule-#9-shaped dispatch surface. Pricing
# entry already in usage_tracker.PRICING_PER_MTOK.
GEMINI_MODEL: str = "gemini-2.5-pro"

# Same sentence-flush regex the legacy llm.py used. Mirrors ``\s+`` after
# sentence-ending punctuation.
SENTENCE_FLUSH_RE = re.compile(r"(?<=[.!?])\s+|(?<=[.!?])$")

# Tool-loop guard. If the model emits 16 consecutive tool-call rounds without
# producing a final text reply, we break the loop and surface the partial
# state. Real-world usage sits at 1-3 rounds; 16 is conservative.
MAX_TOOL_ROUNDS: int = 16

# Per-turn output ceiling — Gemini's ``max_output_tokens`` config knob.
DEFAULT_MAX_OUTPUT_TOKENS: int = 1024

# Truncation caps on tool-call WS frames. Args summaries get truncated at
# ARG_SUMMARY_MAX_CHARS so a "command" string of 20KB doesn't blow up the
# tool-chip frame; tool output previews get TOOL_PREVIEW_MAX_CHARS so the chip
# carries a useful snippet without dragging the whole 50KB stdout to the WS.
ARG_SUMMARY_MAX_CHARS: int = 200
TOOL_PREVIEW_MAX_CHARS: int = 240

# Cap on the synthetic "[tool: ...]" note we emit per tool round so cross-
# session tool memory survives a reconnect (Phase 3). Each note carries:
# the tool name, a one-line arg summary, and a short preview of the
# output. Short by design — these land in voice_turns and the next
# reconnect's history; we want the model to see "I ran git log -5, top
# commit was abc123" not the full 50KB stdout.
TOOL_NOTE_MAX_CHARS: int = 160
TOOL_NOTE_ARG_CHARS: int = 60
TOOL_NOTE_PREVIEW_CHARS: int = 80


UsageRecord = dict


_client: Optional[Any] = None


def _get_client() -> Any:
    """Lazily construct + cache the google-genai client.

    Auto-detects which auth path to use:
      1. **AI Studio (api_key)** — if ``GEMINI_API_KEY`` (or its alias
         ``GOOGLE_API_KEY``) is set in env or settings, build
         ``genai.Client(api_key=...)``. No project/location/IAM needed; the
         key is minted from "Gemini API" / Generative Language API in GCP.
      2. **Vertex AI (service account)** — otherwise, if
         ``GOOGLE_APPLICATION_CREDENTIALS`` points at a JSON key, build
         ``genai.Client(vertexai=True, project=..., location=...)`` using
         the project + location from settings.
      3. If neither is configured, raise a clear ``RuntimeError`` so the
         failure mode shows up plainly in the logs instead of as a Google
         auth 403.

    The genai client is light enough to be created per-process; we cache one
    instance to avoid the constructor's HTTP discovery cost on every turn.
    """
    global _client
    if _client is not None:
        return _client
    from google import genai

    # Resolve API key from settings first, then fall back to raw env so
    # values set after settings was loaded still work. settings.GEMINI_API_KEY
    # takes precedence over GOOGLE_API_KEY when both are set.
    api_key = (
        getattr(settings, "GEMINI_API_KEY", None)
        or getattr(settings, "GOOGLE_API_KEY", None)
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
    )

    if api_key:
        _client = genai.Client(api_key=api_key)
        logger.info("gemini_brain: using AI Studio (api_key)")
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
            "gemini_brain: using Vertex AI (project=%s location=%s)",
            project, location,
        )
        return _client

    raise RuntimeError(
        "gemini_brain: no auth configured. Set GEMINI_API_KEY for the "
        "AI Studio path, or GOOGLE_APPLICATION_CREDENTIALS + Vertex AI "
        "roles for the service-account path."
    )


# NOTE: Cost computation lives in ``services.usage_tracker.compute_cost_cents``
# — single source of truth. Earlier revisions of this module computed Gemini
# 2.5 Pro cost here and stuffed it onto ``usage_dict["cost_cents"]``, but
# ``usage_tracker.record_turn`` always recomputes from token counts using
# ``PRICING_PER_MTOK[model]`` and silently discarded the brain's value.
# Keeping a second pricing table here was just a drift hazard.


# ---------------------------------------------------------------------------
# History adapters — Anthropic-style ``[{"role", "content"}]`` -> Gemini
# ``Content`` list. The legacy WS handler builds history in the
# Anthropic shape (role: user/assistant, content: str). Gemini uses
# ``user`` / ``model`` roles and structured ``Content(parts=[Part])``. We
# convert at the boundary so the WS layer + history persistence don't need
# to change.
# ---------------------------------------------------------------------------
def _history_to_gemini_contents(history: list[dict]) -> list[Any]:
    """Convert the WS-layer history into a list of google.genai Content objects.

    Skips empty / malformed entries. Maps ``assistant`` -> ``model`` per
    Gemini's role vocabulary.
    """
    from google.genai import types

    contents: list[Any] = []
    for entry in history:
        role = entry.get("role")
        text = entry.get("content")
        if not isinstance(text, str) or not text.strip():
            continue
        if role == "user":
            gem_role = "user"
        elif role == "assistant":
            gem_role = "model"
        else:
            # Unknown roles are dropped; tool roles are emitted by us
            # mid-turn (see _build_tool_response_content) and never come from
            # persisted history.
            continue
        contents.append(types.Content(role=gem_role, parts=[types.Part(text=text)]))
    return contents


def _build_function_call_content(function_calls: list[Any]) -> Any:
    """Wrap a list of Gemini FunctionCall parts as a 'model' Content turn."""
    from google.genai import types
    parts = []
    for fc in function_calls:
        parts.append(types.Part(function_call=fc))
    return types.Content(role="model", parts=parts)


def _build_tool_response_content(
    function_calls: list[Any], results: list[Any]
) -> Any:
    """Build a 'tool' (user-role function_response) Content for the next turn.

    Per Gemini's protocol, function responses are sent back as a single
    ``user``-role Content whose parts are ``FunctionResponse`` objects. The
    ``name`` AND ``id`` on each response part must match the corresponding
    ``function_call`` part we're answering — for parallel tool calls of the
    SAME name, Gemini correlates request → response by ``id``, not name.
    Dropping the id silently breaks parallel-tool-call ordering.

    We pair calls and results by index so order matches the execution we
    just did.
    """
    from google.genai import types
    parts = []
    for fc, result in zip(function_calls, results):
        # The SDK accepts ``response`` as a dict; we wrap the tool's stdout
        # in {"output": ...} so the model can reason about both success and
        # error cases uniformly. ``error`` flag is included for explicit
        # signaling.
        response_payload = {
            "output": result.output,
            "error": bool(result.error),
        }
        if getattr(result, "truncated", False):
            response_payload["truncated"] = True
        # Build the FunctionResponse directly (rather than via
        # ``Part.from_function_response``) so we can plumb ``id`` through.
        # ``fc.id`` is None for single-tool-call rounds where the SDK didn't
        # mint one; passing None is fine — the SDK omits the field on the
        # wire and the call/response pairing falls back to name+order, which
        # IS unique when there's only one call per name.
        fr = types.FunctionResponse(
            id=getattr(fc, "id", None),
            name=fc.name,
            response=response_payload,
        )
        parts.append(types.Part(function_response=fr))
    return types.Content(role="user", parts=parts)


# ---------------------------------------------------------------------------
# Public stream entry point — drop-in for the legacy llm.stream_turn.
# ---------------------------------------------------------------------------
async def stream(
    *,
    history: list[dict],
    user_text: str,
    system_prompt: str,
    send_token: Callable[[str], Awaitable[None]],
    send_tts_sentence: Callable[[str], Awaitable[None]],
    send_tool_call: Optional[Callable[[dict], Awaitable[None]]] = None,
    on_tool_round_complete: Optional[Callable[[str], Awaitable[None]]] = None,
    cwd: Path,
    subject: str,
    scope: str,
    system_prompt_append: str,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    cancel_event: Optional[asyncio.Event] = None,
) -> UsageRecord:
    """Run one Gemini turn and stream tokens / TTS sentences / tool-call frames.

    Args:
      history:          WS-layer conversation history (role/content dicts).
                        Does NOT include ``user_text`` — the caller appends
                        the user turn elsewhere; we add it inside.
      user_text:        Latest user utterance.
      system_prompt:    Flattened system instruction (from
                        chief_context.build_chief_system_string).
      send_token:       Coroutine emitting one ``{"type":"token"}`` WS frame.
      send_tts_sentence: Coroutine queuing one sentence onto the TTS worker.
      send_tool_call:   Optional coroutine emitting a tool-chip WS frame
                        ``{"type":"tool_call", "name":..., "args":..., "status":...}``.
                        Called once on tool start and once on tool end.
      on_tool_round_complete: Optional coroutine invoked once after each
                        completed tool round with a single argument: a
                        compact synthetic ``[tool: ...]`` note string.
                        Caller is expected to append this note to the
                        WS-layer history list AND persist it via
                        ``history_store.append_turn(role="assistant", ...)``
                        so cross-reconnect tool memory survives.
      cwd:              Active scope's repo path. Passed to every tool.
      subject:          JWT subject — keys the dispatch_agent CC pool.
      scope:            Active scope — keys the dispatch_agent CC pool.
      system_prompt_append: Suffix appended to the CC subprocess's preset
                            prompt when dispatch_agent fires.
      max_output_tokens: Gemini ``max_output_tokens`` per round.
      cancel_event:     Optional explicit cancel flag (in addition to
                        asyncio.CancelledError). Tested between chunks so a
                        barge-in stops the stream at the next chunk boundary.

    Returns:
      UsageRecord with the same keys legacy callers expect plus ``model``
      pinned to whatever ``GEMINI_MODEL`` is set to (currently
      ``gemini-2.5-pro``).
    """
    from google.genai import types

    client = _get_client()
    full_text: list[str] = []
    sentence_buf: list[str] = []
    stop_reason = "stop"
    cumulative_input_tokens = 0
    cumulative_output_tokens = 0
    cumulative_cached_tokens = 0

    # Build the initial Gemini ``contents`` list from the persisted history,
    # then append the new user turn. We use a fresh list per request so the
    # tool-call loop can mutate it without contaminating the caller's
    # history.
    contents = _history_to_gemini_contents(history)
    contents.append(
        types.Content(role="user", parts=[types.Part(text=user_text)])
    )

    cancelled = False

    async def _flush_remaining_sentence_buf() -> None:
        # Flush whatever's in the sentence buffer to the TTS worker. Called
        # at the boundary where Gemini either stops cleanly OR pauses to
        # emit a tool call — in both cases we want the partial sentence
        # spoken so the TTS feels continuous.
        if not sentence_buf:
            return
        remainder = "".join(sentence_buf).strip()
        sentence_buf.clear()
        if remainder:
            await send_tts_sentence(remainder)

    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        max_output_tokens=max_output_tokens,
        tools=[to_gemini_tool()],
    )

    try:
        for _ in range(MAX_TOOL_ROUNDS):
            pending_function_calls: list[Any] = []

            # ----- one streaming round (text + maybe function_call(s)) -----
            stream_iter = await client.aio.models.generate_content_stream(
                model=GEMINI_MODEL,
                contents=contents,
                config=config,
            )

            # Per-round usage. Vertex emits usage_metadata on the LAST chunk
            # of each round and the counts are PER-REQUEST (not cumulative
            # across the multi-round turn). We capture the last seen value
            # this round and += it into the cumulative totals when the round
            # closes — using max() across rounds silently drops earlier
            # rounds when round 2's prompt is longer than round 1's was.
            round_input_tokens = 0
            round_output_tokens = 0
            round_cached_tokens = 0

            try:
                async for chunk in stream_iter:
                    # Fast-path cancel check between chunks. Mirrors the
                    # cancellation contract from the legacy llm.py — no
                    # extra awaits after a cancel decision.
                    current_task = asyncio.current_task()
                    if (
                        current_task is not None
                        and current_task.cancelling()
                    ) or (cancel_event is not None and cancel_event.is_set()):
                        cancelled = True
                        break

                    # Snapshot this chunk's usage. Gemini emits the same
                    # totals on every chunk that carries usage_metadata; we
                    # overwrite (not add) within a round so we don't
                    # double-count when the SDK emits usage on more than
                    # one chunk in a round.
                    um = getattr(chunk, "usage_metadata", None)
                    if um is not None:
                        round_input_tokens = int(
                            getattr(um, "prompt_token_count", 0) or 0
                        )
                        round_output_tokens = int(
                            getattr(um, "candidates_token_count", 0) or 0
                        )
                        round_cached_tokens = int(
                            getattr(um, "cached_content_token_count", 0) or 0
                        )

                    # Walk all parts on this chunk's first candidate. A chunk
                    # may contain text-only, function-call-only, or both
                    # interleaved — we surface text in real time and queue
                    # function_calls for execution after the round closes.
                    candidates = getattr(chunk, "candidates", None) or []
                    if not candidates:
                        continue
                    cand = candidates[0]
                    cand_finish = getattr(cand, "finish_reason", None)
                    if cand_finish:
                        # Map Gemini finish reasons to a string the legacy
                        # WS frame can carry. Most values come through as
                        # types.FinishReason enum-likes; .name handles both.
                        stop_reason = (
                            getattr(cand_finish, "name", None)
                            or str(cand_finish)
                        ).lower()

                    content = getattr(cand, "content", None)
                    if content is None:
                        continue
                    parts = getattr(content, "parts", None) or []
                    for part in parts:
                        # Skip "thought" parts. When ``thinking_config`` has
                        # ``include_thoughts=True`` the SDK streams internal
                        # reasoning back as parts with ``thought=True``.
                        # Surfacing those to the user (via send_token / TTS)
                        # would leak chain-of-thought to the audio output.
                        # We currently do NOT enable include_thoughts, but
                        # this guard keeps a future config flip from
                        # accidentally broadcasting reasoning.
                        if getattr(part, "thought", False) is True:
                            continue
                        text_piece = getattr(part, "text", None)
                        fcall = getattr(part, "function_call", None)
                        if text_piece:
                            full_text.append(text_piece)
                            try:
                                await send_token(text_piece)
                            except asyncio.CancelledError:
                                cancelled = True
                                raise
                            sentence_buf.append(text_piece)

                            joined = "".join(sentence_buf)
                            split_parts = SENTENCE_FLUSH_RE.split(joined)
                            if len(split_parts) > 1:
                                for sentence in split_parts[:-1]:
                                    sentence = sentence.strip()
                                    if sentence:
                                        await send_tts_sentence(sentence)
                                sentence_buf.clear()
                                if split_parts[-1]:
                                    sentence_buf.append(split_parts[-1])
                        if fcall is not None:
                            # Gather; we run them after the round closes so
                            # the streamed iterator finishes consuming
                            # before we issue another generate_content call.
                            pending_function_calls.append(fcall)

                    if cancelled:
                        break
            except asyncio.CancelledError:
                cancelled = True
                # The SDK's iterator will tear itself down; we don't await
                # any extra completion. Re-raise after the finally below.
                raise
            finally:
                # Best-effort iterator close. Some SDK versions expose
                # .aclose() on the streamed response; others don't (the
                # async-for exit + GC handles it). Either way we don't
                # block here.
                aclose = getattr(stream_iter, "aclose", None)
                if aclose is not None:
                    try:
                        await aclose()
                    except Exception:
                        pass

            # Roll this round's per-request usage into the cumulative turn
            # totals. Each generate_content_stream call bills independently,
            # so a multi-round tool-using turn must SUM the rounds — using
            # max() across rounds would silently drop earlier rounds' input
            # bills (e.g. round 1 sent the full system prompt, round 2's
            # contents added the function_response → both rounds billed
            # separately, both must be counted).
            cumulative_input_tokens += round_input_tokens
            cumulative_output_tokens += round_output_tokens
            cumulative_cached_tokens += round_cached_tokens

            if cancelled:
                break

            if not pending_function_calls:
                # Final round — break out, finalize, return.
                break

            # ----- execute the tool calls + feed responses back -----
            # Flush any partial spoken sentence before the tool runs so the
            # TTS pipeline doesn't stall waiting for text that won't arrive
            # until after the tool completes.
            await _flush_remaining_sentence_buf()

            tool_results = []
            for fcall in pending_function_calls:
                tool_name = fcall.name
                # ``fcall.args`` is a dict-like ``MapComposite``; coerce to
                # a vanilla dict for our dispatcher / WS payload.
                raw_args = getattr(fcall, "args", None) or {}
                try:
                    args_dict = dict(raw_args)
                except Exception:
                    args_dict = {}

                start = time.monotonic()
                cancelled_in_tool = False
                result = None
                try:
                    # Emit the running chip INSIDE the try so a CancelledError
                    # landing on this send (or between this send and the
                    # dispatch_tool below) still routes through the finally
                    # block and produces a terminal "cancelled" chip. Without
                    # this, a cancel during the running send would leave the
                    # UI with a stuck spinner.
                    if send_tool_call is not None:
                        try:
                            await send_tool_call({
                                "name": tool_name,
                                "args": _summarize_args(args_dict),
                                "status": "running",
                            })
                        except asyncio.CancelledError:
                            cancelled = True
                            cancelled_in_tool = True
                            raise
                        except Exception as exc:
                            logger.warning(
                                "gemini_brain: send_tool_call(start) raised: %s", exc
                            )

                    try:
                        result = await dispatch_tool(
                            tool_name,
                            args_dict,
                            cwd=cwd,
                            subject=subject,
                            scope=scope,
                            system_prompt_append=system_prompt_append,
                        )
                    except asyncio.CancelledError:
                        cancelled = True
                        cancelled_in_tool = True
                        raise
                    except Exception as exc:
                        # Don't feed raw exception text back to the model
                        # (or via the brain's reply, to the user) — it can
                        # leak file paths, model IDs, internal stack hints,
                        # etc. The model still sees ``error=True`` so it
                        # can react / reroute. Detail stays in the log.
                        logger.exception(
                            "gemini_brain: tool %r raised: %s", tool_name, exc
                        )
                        from services.agent_tools import ToolResult
                        result = ToolResult(
                            output="error: tool execution failed",
                            error=True,
                        )
                    tool_results.append(result)
                finally:
                    # Always emit a terminal tool-chip frame so the frontend
                    # can transition the chip out of "running" state. The
                    # cancel path used to skip this and leave a stuck spinner
                    # on the UI when a barge-in landed mid-tool.
                    if send_tool_call is not None:
                        if cancelled_in_tool:
                            try:
                                await send_tool_call({
                                    "name": tool_name,
                                    "args": _summarize_args(args_dict),
                                    "status": "cancelled",
                                    "duration_ms": int(
                                        (time.monotonic() - start) * 1000
                                    ),
                                })
                            except Exception as exc:
                                logger.warning(
                                    "gemini_brain: send_tool_call(cancel) raised: %s",
                                    exc,
                                )
                        elif result is not None:
                            try:
                                await send_tool_call({
                                    "name": tool_name,
                                    "args": _summarize_args(args_dict),
                                    "status": "error" if result.error else "complete",
                                    "duration_ms": int(
                                        (time.monotonic() - start) * 1000
                                    ),
                                    "preview": _preview(result.output),
                                })
                            except Exception as exc:
                                logger.warning(
                                    "gemini_brain: send_tool_call(end) raised: %s",
                                    exc,
                                )

            # Append both the model's tool-call turn AND our function_response
            # turn to the conversation. Gemini's protocol requires both.
            contents.append(_build_function_call_content(pending_function_calls))
            contents.append(
                _build_tool_response_content(pending_function_calls, tool_results)
            )

            # Phase 3: synthetic tool-round note. Build one note PER tool
            # call in this round and hand each to the WS layer's
            # on_tool_round_complete callback (if provided). The WS layer
            # is responsible for appending to in-memory history AND
            # persisting via history_store.append_turn so cross-reconnect
            # tool memory survives. Failures are logged + swallowed —
            # missing a note row should never break the turn.
            if on_tool_round_complete is not None:
                for fcall, result in zip(pending_function_calls, tool_results):
                    try:
                        raw_args = getattr(fcall, "args", None) or {}
                        try:
                            args_dict = dict(raw_args)
                        except Exception:
                            args_dict = {}
                        note = _build_tool_note(
                            tool_name=fcall.name,
                            args=args_dict,
                            output=getattr(result, "output", "") or "",
                            error=bool(getattr(result, "error", False)),
                        )
                        await on_tool_round_complete(note)
                    except asyncio.CancelledError:
                        cancelled = True
                        raise
                    except Exception as exc:
                        logger.warning(
                            "gemini_brain: on_tool_round_complete raised: %s", exc
                        )

            # Loop and let Gemini speak its post-tool reply.
        else:
            # Tool-loop guard fired. Surface a one-line message; the partial
            # ``full_text`` content is already on the WS.
            logger.warning(
                "gemini_brain: hit MAX_TOOL_ROUNDS=%d without a final reply",
                MAX_TOOL_ROUNDS,
            )
            await send_tts_sentence(
                "I'm in too many tool rounds — let me stop here."
            )
            full_text.append("\n[truncated: max tool rounds]")

        if cancelled:
            raise asyncio.CancelledError()

        # Flush the final partial sentence on a clean stop.
        await _flush_remaining_sentence_buf()

        usage_dict: UsageRecord = {
            "input_tokens": cumulative_input_tokens,
            "output_tokens": cumulative_output_tokens,
            "cache_read_input_tokens": cumulative_cached_tokens,
            "cache_creation_input_tokens": 0,
            "model": GEMINI_MODEL,
            "stop_reason": stop_reason,
            "assistant_text": "".join(full_text),
        }
        # NOTE: ``cost_cents`` is intentionally NOT populated here. The
        # caller's ``usage_tracker.record_turn`` recomputes it from the
        # token counts above using ``PRICING_PER_MTOK[GEMINI_MODEL]``.
        logger.info(
            "gemini_brain: turn complete model=%s input=%d output=%d cached=%d",
            GEMINI_MODEL,
            usage_dict["input_tokens"],
            usage_dict["output_tokens"],
            usage_dict["cache_read_input_tokens"],
        )
        return usage_dict
    except asyncio.CancelledError:
        logger.info(
            "gemini_brain: cancelled tokens_emitted=%d", len(full_text),
        )
        raise


def _summarize_args(args: dict[str, Any]) -> dict[str, Any]:
    """Truncate long string fields in an args dict for WS / log readability."""
    out: dict[str, Any] = {}
    for k, v in args.items():
        if isinstance(v, str) and len(v) > ARG_SUMMARY_MAX_CHARS:
            out[k] = v[:ARG_SUMMARY_MAX_CHARS] + "...<truncated>"
        else:
            out[k] = v
    return out


def _preview(output: str) -> str:
    """Return a short preview of tool output for the WS chip frame."""
    if not isinstance(output, str):
        return ""
    if len(output) <= TOOL_PREVIEW_MAX_CHARS:
        return output
    return output[:TOOL_PREVIEW_MAX_CHARS] + "..."


def _build_tool_note(
    tool_name: str,
    args: dict[str, Any],
    output: str,
    error: bool,
) -> str:
    """Build a one-line synthetic note describing a tool round.

    Format: ``[tool: <name> · <arg-snippet> · <output-preview>]``

    The note is persisted to ``voice_turns`` as an assistant-role row so
    cross-session tool memory survives a reconnect. We deliberately keep
    it terse (no raw paths, single-line output preview) so it doesn't
    pollute the rolling history with stdout dumps.

    Sensitive paths from raw output are NOT scrubbed beyond the size cap;
    callers (Read/Bash/Grep dispatchers) already path-fence to scope cwd,
    and the brain's previous chip-render path emits the same preview text
    to the user. This note carries the same surface as the chip preview,
    not anything new.
    """
    # Pick the most informative arg key for the snippet. Most tools have a
    # primary string arg; fall back to a compact dict repr otherwise.
    arg_snippet = ""
    for primary in ("command", "file_path", "pattern", "task_description"):
        v = args.get(primary)
        if isinstance(v, str) and v:
            arg_snippet = v
            break
    if not arg_snippet and args:
        # Compact fallback — first string arg we find.
        for v in args.values():
            if isinstance(v, str) and v:
                arg_snippet = v
                break
    if len(arg_snippet) > TOOL_NOTE_ARG_CHARS:
        arg_snippet = arg_snippet[:TOOL_NOTE_ARG_CHARS] + "..."

    # Output preview — collapse newlines to a single line, cap length.
    preview = ""
    if isinstance(output, str) and output:
        first_line = output.replace("\r", "").split("\n", 1)[0].strip()
        if len(first_line) > TOOL_NOTE_PREVIEW_CHARS:
            first_line = first_line[:TOOL_NOTE_PREVIEW_CHARS] + "..."
        preview = first_line

    status = "error" if error else "ok"
    parts = [f"tool: {tool_name}"]
    if arg_snippet:
        parts.append(arg_snippet)
    if preview:
        parts.append(f"{status}: {preview}")
    elif error:
        parts.append("error")
    note = "[" + " · ".join(parts) + "]"

    if len(note) > TOOL_NOTE_MAX_CHARS:
        note = note[: TOOL_NOTE_MAX_CHARS - 1] + "]"
    return note


__all__ = [
    "GEMINI_MODEL",
    "MAX_TOOL_ROUNDS",
    "stream",
]
