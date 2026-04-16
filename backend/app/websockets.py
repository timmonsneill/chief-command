"""WebSocket endpoints for voice and terminal streaming."""

import asyncio
import json
import logging
import signal
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services.auth import verify_token
from services.claude_pipe import claude_pipe
from services import stt_service, tts_service
from services.audio_utils import convert_webm_to_wav

logger = logging.getLogger(__name__)

router = APIRouter()


async def _authenticate_ws(ws: WebSocket) -> bool:
    """Authenticate a WebSocket connection via token query param or first message."""
    token = ws.query_params.get("token")
    if token and verify_token(token):
        return True
    # Try reading token from first text message
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


# ---------------------------------------------------------------------------
# /ws/voice — audio + text bridge to Claude Code
# ---------------------------------------------------------------------------

@router.websocket("/ws/voice")
async def voice_ws(ws: WebSocket) -> None:
    """Voice WebSocket endpoint.

    Protocol:
      - Client sends text frames with JSON: {"type": "text", "content": "..."}
        or binary frames with audio chunks.
      - Server responds with text frames containing JSON:
        {"type": "transcript", "content": "..."} — STT result
        {"type": "response", "content": "..."} — Claude response text
        {"type": "agent_status", "agents": [...]} — agent status updates
        {"type": "audio", "format": "pcm"} followed by binary frame — TTS audio
        {"type": "error", "message": "..."} — errors
    """
    await ws.accept()
    if not await _authenticate_ws(ws):
        await ws.send_json({"type": "error", "message": "Unauthorized"})
        await ws.close(code=4001)
        return

    logger.info("Voice WebSocket connected")

    try:
        while True:
            message = await ws.receive()

            if message.get("type") == "websocket.disconnect":
                break

            # --- Text message ---
            if "text" in message:
                raw = message["text"]
                try:
                    data = json.loads(raw)
                    text_content = data.get("content", raw)
                except json.JSONDecodeError:
                    text_content = raw

                # Send to Claude Code and stream back
                await _handle_text_message(ws, text_content)

            # --- Binary message (audio) ---
            elif "bytes" in message:
                audio_data: bytes = message["bytes"]
                await _handle_audio_message(ws, audio_data)

    except WebSocketDisconnect:
        logger.info("Voice WebSocket disconnected")
    except Exception as exc:
        logger.exception("Voice WebSocket error: %s", exc)
        try:
            await ws.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass


async def _handle_text_message(ws: WebSocket, text: str) -> None:
    """Process a text command through Claude Code and stream results."""
    try:
        async for chunk in claude_pipe.send_message_stream(text):
            await ws.send_json({"type": "response", "content": chunk})
            # Also send agent status if it changed
            agents = claude_pipe.get_agents()
            if agents:
                await ws.send_json({"type": "agent_status", "agents": agents})
    except Exception as exc:
        logger.exception("Error processing text message: %s", exc)
        await ws.send_json({"type": "error", "message": str(exc)})


async def _handle_audio_message(ws: WebSocket, audio_data: bytes) -> None:
    """Process audio input: STT -> Claude Code -> TTS -> back to client."""
    try:
        # Convert browser WebM/Opus to WAV for whisper
        wav_data = await convert_webm_to_wav(audio_data)

        # Transcribe using the proper STT service (medium model, Apple Silicon optimized)
        transcript = await stt_service.transcribe(wav_data)
        if not transcript:
            await ws.send_json({"type": "error", "message": "Could not transcribe audio"})
            return

        await ws.send_json({"type": "transcript", "content": transcript})

        # Process through Claude and collect full response for TTS
        full_response = []
        async for chunk in claude_pipe.send_message_stream(transcript):
            await ws.send_json({"type": "response", "content": chunk})
            full_response.append(chunk)
            agents = claude_pipe.get_agents()
            if agents:
                await ws.send_json({"type": "agent_status", "agents": agents})

        # Convert response to speech and send audio back
        response_text = "".join(full_response).strip()
        if response_text:
            await ws.send_json({"type": "tts_start"})
            async for audio_chunk in tts_service.synthesize_stream(response_text):
                await ws.send_bytes(audio_chunk)
            await ws.send_json({"type": "tts_end"})

    except Exception as exc:
        logger.exception("Audio processing error: %s", exc)
        await ws.send_json({"type": "error", "message": str(exc)})


# ---------------------------------------------------------------------------
# /ws/terminal — remote shell access
# ---------------------------------------------------------------------------

@router.websocket("/ws/terminal")
async def terminal_ws(ws: WebSocket) -> None:
    """Terminal WebSocket endpoint.

    Protocol:
      - Client sends text frames with JSON:
        {"type": "command", "content": "ls -la"}
        {"type": "signal", "signal": "SIGINT"}
        {"type": "resize", "cols": 80, "rows": 24}
      - Server responds with text frames:
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

                # Stream stdout and stderr concurrently
                async def _stream_pipe(
                    pipe: asyncio.StreamReader, stream_type: str
                ) -> None:
                    while True:
                        line = await pipe.readline()
                        if not line:
                            break
                        await ws.send_json(
                            {
                                "type": stream_type,
                                "content": line.decode(errors="replace"),
                            }
                        )

                tasks = []
                if current_process.stdout:
                    tasks.append(
                        asyncio.create_task(
                            _stream_pipe(current_process.stdout, "stdout")
                        )
                    )
                if current_process.stderr:
                    tasks.append(
                        asyncio.create_task(
                            _stream_pipe(current_process.stderr, "stderr")
                        )
                    )

                if tasks:
                    await asyncio.gather(*tasks)

                exit_code = await current_process.wait()
                await ws.send_json({"type": "exit", "code": exit_code})
                current_process = None

            elif msg_type == "signal":
                sig_name = data.get("signal", "SIGINT")
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
