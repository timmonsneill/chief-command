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
from services.llm import stream_turn
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
      {"type": "error", "message": "..."}
    """
    await ws.accept()
    if not await _authenticate_ws(ws):
        await ws.send_json({"type": "error", "message": "Unauthorized"})
        await ws.close(code=4001)
        return

    session_id = str(uuid.uuid4())
    await create_session(session_id)
    logger.info("Voice WS connected session=%s", session_id)

    history: list[dict] = []

    try:
        while True:
            message = await ws.receive()

            if message.get("type") == "websocket.disconnect":
                break

            if "text" in message:
                raw = message["text"]
                logger.info("Voice WS text frame: %.100s", raw)
                try:
                    data = json.loads(raw)
                    text_content = data.get("content", raw)
                except json.JSONDecodeError:
                    text_content = raw

                await _handle_text_turn(ws, session_id, history, text_content)

            elif "bytes" in message:
                audio_data: bytes = message["bytes"]
                logger.info("Voice WS audio frame: %d bytes", len(audio_data))
                await _handle_audio_turn(ws, session_id, history, audio_data)

    except WebSocketDisconnect:
        logger.info("Voice WS disconnected session=%s", session_id)
    except Exception as exc:
        logger.exception("Voice WS error session=%s: %s", session_id, exc)
        try:
            await ws.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        await close_session(session_id)


async def _run_llm_turn(
    ws: WebSocket,
    session_id: str,
    history: list[dict],
    user_text: str,
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

    try:
        usage = await stream_turn(
            history=history,
            model=model,
            send_token=send_token,
            send_tts_sentence=send_tts_sentence,
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

    except Exception:
        await tts_queue.put(None)
        try:
            await tts_task
        except Exception:
            pass
        raise


async def _handle_text_turn(
    ws: WebSocket,
    session_id: str,
    history: list[dict],
    text: str,
) -> None:
    try:
        await _run_llm_turn(ws, session_id, history, text)
    except Exception as exc:
        logger.exception("Error processing text turn session=%s: %s", session_id, exc)
        await ws.send_json({"type": "error", "message": str(exc)})


async def _handle_audio_turn(
    ws: WebSocket,
    session_id: str,
    history: list[dict],
    audio_data: bytes,
) -> None:
    try:
        wav_data = await convert_webm_to_wav(audio_data)
        transcript = await stt_service.transcribe(wav_data)
        if not transcript:
            await ws.send_json({"type": "error", "message": "Could not transcribe audio"})
            return

        await ws.send_json({"type": "transcript", "content": transcript})
        await _run_llm_turn(ws, session_id, history, transcript)

    except Exception as exc:
        logger.exception("Audio turn error session=%s: %s", session_id, exc)
        await ws.send_json({"type": "error", "message": str(exc)})


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
