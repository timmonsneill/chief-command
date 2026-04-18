"""WebSocket endpoints for voice and terminal streaming."""

import asyncio
import json
import logging
import signal
import uuid
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services.auth import verify_token
from services import stt_service, tts_service
from services.audio_utils import convert_webm_to_wav
from services.chief_context import build_chief_system
from services.llm import stream_turn
from services.project_context import AVAILABLE_PROJECTS, DEFAULT_PROJECT, detect_project_switch
from services.router import classify_and_route, random_thinking_phrase
from services.usage_tracker import create_session, close_session, record_turn, get_session_totals

logger = logging.getLogger(__name__)

router = APIRouter()


async def _authenticate_ws(ws: WebSocket) -> bool:
    token = ws.query_params.get("token")
    if token and verify_token(token):
        return True
    try:
        first = await asyncio.wait_for(ws.receive_text(), timeout=5.0)
        try:
            data = json.loads(first)
            token = data.get("token")
        except json.JSONDecodeError:
            token = first.strip()
        if token and verify_token(token):
            return True
    except (asyncio.TimeoutError, WebSocketDisconnect):
        pass
    return False


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
    if not await _authenticate_ws(ws):
        await ws.send_json({"type": "error", "message": "Unauthorized"})
        await ws.close(code=4001)
        return

    session_id: Optional[str] = None
    history: list[dict] = []
    # Default scope: Chief Command. Per owner: scope is ALWAYS a concrete single project.
    current_project: str = DEFAULT_PROJECT
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
        so sends are serialized on the WS — no concurrent writes with the turn task."""
        nonlocal current_turn_task
        if current_turn_task and not current_turn_task.done():
            logger.info("Voice WS cancelling turn session=%s reason=%s", session_id, reason)
            current_turn_task.cancel()
            try:
                await current_turn_task
            except (asyncio.CancelledError, Exception):
                pass
            try:
                await ws.send_json({"type": "turn_cancelled", "reason": reason})
            except Exception:
                pass
        current_turn_task = None

    async def _maybe_switch_project(user_text: str) -> None:
        """Run switch-intent detection on user text; update scope + notify client.

        Scope is ALWAYS a concrete project. If detection returns a value, we
        switch to it; if the new scope matches the current one, we no-op.
        """
        nonlocal current_project
        detected = detect_project_switch(user_text)
        if detected is None:
            return
        if detected == current_project:
            return
        current_project = detected
        logger.info(
            "Voice WS project-switch intent detected text=%r -> project=%s",
            user_text[:80], current_project,
        )
        try:
            await ws.send_json({"type": "context_switched", "project": detected})
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
                    raw_proj = data.get("project") or None
                    if raw_proj in AVAILABLE_PROJECTS:
                        current_project = raw_proj
                    else:
                        current_project = DEFAULT_PROJECT
                    logger.info("Voice WS context updated project=%s", current_project)
                    continue

                if msg_type == "interrupt":
                    await cancel_current_turn("barge-in")
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
                logger.info("Voice WS handling text turn session=%s len=%d scope=%s",
                            sid, len(text_content), current_project)
                current_turn_task = asyncio.create_task(
                    _handle_text_turn(ws, sid, history, text_content, current_project)
                )

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
                    await ws.send_json({"type": "error", "message": "Could not transcribe audio"})
                    continue

                if not transcript:
                    await ws.send_json({"type": "error", "message": "Could not transcribe audio"})
                    continue

                await ws.send_json({"type": "transcript", "content": transcript})
                await _maybe_switch_project(transcript)
                current_turn_task = asyncio.create_task(
                    _handle_text_turn(ws, sid, history, transcript, current_project)
                )

            else:
                logger.warning("Voice WS unknown message shape keys=%s", list(message.keys()))

    except WebSocketDisconnect:
        logger.info("Voice WS disconnected session=%s", session_id)
    except Exception as exc:
        logger.exception("Voice WS error session=%s: %s", session_id, exc)
        try:
            await ws.send_json({"type": "error", "message": "Internal error"})
        except Exception:
            pass
    finally:
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
) -> None:
    """Core LLM streaming loop: route → stream tokens → TTS → record."""
    model, is_deep = classify_and_route(user_text)
    await ws.send_json({"type": "active_model", "model": model, "is_deep": is_deep})

    tts_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()

    if is_deep:
        bridge = random_thinking_phrase()
        await ws.send_json({"type": "bridge_phrase", "text": bridge})
        await tts_queue.put(bridge)

    async def send_token(text: str) -> None:
        await ws.send_json({"type": "token", "text": text})

    async def send_tts_sentence(sentence: str) -> None:
        await tts_queue.put(sentence)

    async def tts_worker() -> None:
        try:
            await ws.send_json({"type": "tts_start"})
            while True:
                sentence = await tts_queue.get()
                if sentence is None:
                    break
                try:
                    async for chunk in tts_service.synthesize_stream(sentence):
                        await ws.send_bytes(chunk)
                except Exception as tts_err:
                    logger.warning("TTS failed for sentence: %s", tts_err)
        finally:
            await ws.send_json({"type": "tts_end"})

    history.append({"role": "user", "content": user_text})

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

        await ws.send_json({"type": "message_done"})

        turn = await record_turn(
            session_id=session_id,
            model=model,
            usage_dict=usage,
            user_text=user_text,
            assistant_text=assistant_text,
        )
        totals = await get_session_totals(session_id)

        await ws.send_json({
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
        tts_task.cancel()
        try:
            await tts_task
        except (asyncio.CancelledError, Exception):
            pass
        raise
    except Exception:
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
) -> None:
    try:
        await _run_llm_turn(ws, session_id, history, text, project_scope)
    except asyncio.CancelledError:
        # Turn was cancelled (barge-in / superseded) — don't emit a user-facing
        # error. The caller already sent turn_cancelled.
        raise
    except Exception as exc:
        logger.exception("Error processing text turn session=%s: %s", session_id, exc)
        try:
            await ws.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass


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
