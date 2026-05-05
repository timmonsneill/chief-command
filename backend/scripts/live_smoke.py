"""End-to-end smoke test for services.gemini_live.LiveSession.

Stage 1 acceptance harness. Proves the Gemini Live API actually
round-trips on real Vertex AI / AI Studio credentials before we wire
the WebSocket integration in Stage 2.

What it does:
  1. Constructs a LiveSession with a "be brief" system prompt.
  2. Opens it (real connect to Live API).
  3. Pushes a real recorded prompt — backend/tests/fixtures/hello_chief.wav
     (16kHz mono int16) — in 20 ms chunks like a live mic would, then
     sends ``audio_stream_end`` so the server treats the buffer as a
     complete turn.
  4. Collects the response audio (24kHz mono int16) until
     ``generation_complete`` fires, plus any input/output transcripts.
  5. Writes the response to ``/tmp/live_smoke_out.wav`` for human
     playback verification.
  6. Reports time-to-first-audio-chunk (TTFT), output bytes, transcript
     text, usage tokens, total elapsed.

Run from the backend directory so .env is loaded:

    cd backend && .venv/bin/python scripts/live_smoke.py

Exits 0 on success, non-zero on any failure path.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
import wave
from pathlib import Path

# Make ``backend/`` the import root so ``services.*`` / ``config.*``
# resolve when run as a top-level script.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from services.gemini_live import (
    INPUT_SAMPLE_RATE_HZ,
    LIVE_MODEL,
    OUTPUT_SAMPLE_RATE_HZ,
    LiveSession,
)


SYSTEM_PROMPT = (
    "You are a helpful assistant. Reply briefly — one short sentence. "
    "Keep replies under 5 seconds of speech."
)
INPUT_FIXTURE = _BACKEND_ROOT / "tests" / "fixtures" / "hello_chief.wav"
OUTPUT_WAV = Path("/tmp/live_smoke_out.wav")
CHUNK_MS = 20
CHUNK_BYTES = (INPUT_SAMPLE_RATE_HZ * 2 * CHUNK_MS) // 1000  # 16000 * 2 * 0.02 = 640
TURN_TIMEOUT_S = 30.0  # max wall-clock to wait for generation_complete


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _load_pcm(path: Path) -> bytes:
    """Read a 16k mono int16 WAV and return its raw PCM bytes."""
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != INPUT_SAMPLE_RATE_HZ:
            raise SystemExit(
                f"smoke fixture must be {INPUT_SAMPLE_RATE_HZ} Hz, "
                f"got {w.getframerate()}"
            )
        if w.getnchannels() != 1:
            raise SystemExit(
                f"smoke fixture must be mono, got {w.getnchannels()} channels"
            )
        if w.getsampwidth() != 2:
            raise SystemExit(
                f"smoke fixture must be 16-bit, got "
                f"{w.getsampwidth() * 8}-bit"
            )
        return w.readframes(w.getnframes())


def _write_wav(path: Path, pcm: bytes, rate_hz: int) -> None:
    """Write raw int16 mono PCM bytes to a WAV at the given rate."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate_hz)
        w.writeframes(pcm)


async def _push_audio_in_chunks(sess: LiveSession, pcm: bytes) -> None:
    """Stream the PCM in CHUNK_BYTES slices to mimic a real mic source."""
    for i in range(0, len(pcm), CHUNK_BYTES):
        chunk = pcm[i : i + CHUNK_BYTES]
        await sess.send_audio(chunk)
        # Pace at ~real time so server-side VAD / endpointing behaves
        # the way it would in production. Skipping this on the smoke
        # path occasionally caused the Live server to keep waiting for
        # more audio because everything arrived before any silence
        # endpoint was detected.
        await asyncio.sleep(CHUNK_MS / 1000)


async def _push_silence_tail(sess: LiveSession, seconds: float = 1.5) -> None:
    """Send a tail of silence so server-side VAD endpoints the user turn.

    Live API's automatic activity detection waits for ~500-800ms of
    silence after speech before declaring end-of-turn. ``audio_stream_end``
    isn't reliably supported on the Vertex transport in the current SDK
    (1.75.0) — observed: setting it produces no generation. The portable
    move is to push a short silence buffer after the prompt, which works
    on both AI Studio and Vertex paths.
    """
    silence_chunk = b"\x00\x00" * (CHUNK_BYTES // 2)
    n_chunks = int(seconds * 1000 / CHUNK_MS)
    for _ in range(n_chunks):
        await sess.send_audio(silence_chunk)
        await asyncio.sleep(CHUNK_MS / 1000)


async def run_smoke() -> int:
    _setup_logging()
    log = logging.getLogger("live_smoke")

    if not INPUT_FIXTURE.exists():
        log.error("missing input fixture: %s", INPUT_FIXTURE)
        return 2

    pcm_in = _load_pcm(INPUT_FIXTURE)
    log.info(
        "input prompt: %s (%.2fs of audio, %d bytes)",
        INPUT_FIXTURE.name,
        len(pcm_in) / (INPUT_SAMPLE_RATE_HZ * 2),
        len(pcm_in),
    )

    audio_out_chunks: list[bytes] = []
    input_transcript_parts: list[str] = []
    output_transcript_parts: list[str] = []
    final_usage: dict = {}
    first_audio_at: dict[str, float] = {}
    turn_done = asyncio.Event()
    interrupted = asyncio.Event()

    async def on_audio(chunk: bytes) -> None:
        if "t" not in first_audio_at:
            first_audio_at["t"] = time.monotonic()
        audio_out_chunks.append(chunk)

    async def on_in_tx(text: str) -> None:
        input_transcript_parts.append(text)
        log.info("[input transcript] %s", text)

    async def on_out_tx(text: str) -> None:
        output_transcript_parts.append(text)
        log.info("[output transcript] %s", text)

    async def on_interrupted() -> None:
        log.warning("[interrupted] server said model was cut off")
        interrupted.set()

    async def on_turn_complete(usage: dict) -> None:
        log.info("[turn complete] usage=%s", usage)
        final_usage.update(usage)
        turn_done.set()

    async def on_go_away(seconds: float) -> None:
        log.warning("[go_away] %.1fs left on session", seconds)

    sess = LiveSession(
        model=LIVE_MODEL,
        system_prompt=SYSTEM_PROMPT,
        on_audio_chunk=on_audio,
        on_input_transcript=on_in_tx,
        on_output_transcript=on_out_tx,
        on_interrupted=on_interrupted,
        on_turn_complete=on_turn_complete,
        on_go_away=on_go_away,
    )

    log.info("connecting to %s ...", LIVE_MODEL)
    open_t0 = time.monotonic()
    try:
        await sess.open()
    except Exception:
        log.exception("Live API connect failed")
        return 3
    open_elapsed = time.monotonic() - open_t0
    log.info("connected in %.2fs — pushing audio", open_elapsed)

    send_t0 = time.monotonic()
    try:
        await _push_audio_in_chunks(sess, pcm_in)
        await _push_silence_tail(sess, seconds=1.5)
        send_elapsed = time.monotonic() - send_t0
        log.info("audio + silence tail sent in %.2fs", send_elapsed)

        try:
            await asyncio.wait_for(turn_done.wait(), timeout=TURN_TIMEOUT_S)
        except asyncio.TimeoutError:
            log.error(
                "no generation_complete after %.1fs — got %d audio chunks "
                "and %d output_transcript pieces; bailing",
                TURN_TIMEOUT_S,
                len(audio_out_chunks),
                len(output_transcript_parts),
            )
            await sess.close()
            return 4
    finally:
        await sess.close()

    audio_out = b"".join(audio_out_chunks)
    input_transcript = "".join(input_transcript_parts).strip()
    output_transcript = "".join(output_transcript_parts).strip()

    if not audio_out:
        log.error("no audio bytes received from Live — turn produced silence")
        return 5

    _write_wav(OUTPUT_WAV, audio_out, OUTPUT_SAMPLE_RATE_HZ)

    ttft = first_audio_at.get("t", 0.0) - send_t0 if first_audio_at else float("nan")
    duration_s = len(audio_out) / (OUTPUT_SAMPLE_RATE_HZ * 2)

    log.info("=" * 72)
    log.info("LIVE SMOKE — RESULT")
    log.info("=" * 72)
    log.info("model:                %s", LIVE_MODEL)
    log.info("connect time:         %.2fs", open_elapsed)
    log.info("audio push time:      %.2fs", send_elapsed)
    log.info("TTFT (first audio):   %.2fs", ttft)
    log.info("audio chunks:         %d", len(audio_out_chunks))
    log.info("audio bytes out:      %d (%.2fs of speech)", len(audio_out), duration_s)
    log.info("input transcript:     %r", input_transcript)
    log.info("output transcript:    %r", output_transcript)
    log.info("usage:                %s", final_usage)
    log.info("wrote:                %s", OUTPUT_WAV)
    log.info("interrupted flag:     %s", interrupted.is_set())
    log.info("=" * 72)
    log.info(
        "play with:  afplay %s   (or: ffplay -autoexit %s)",
        OUTPUT_WAV, OUTPUT_WAV,
    )
    return 0


if __name__ == "__main__":
    # Sanity check that auth env exists. Don't print the value — just
    # confirm presence so a misconfigured shell fails loud, not silent.
    if not (
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    ):
        print(
            "live_smoke: no auth env. Set GEMINI_API_KEY for AI Studio path or "
            "GOOGLE_APPLICATION_CREDENTIALS for the Vertex AI service-account "
            "path before running this harness.",
            file=sys.stderr,
        )
        sys.exit(1)
    rc = asyncio.run(run_smoke())
    sys.exit(rc)
