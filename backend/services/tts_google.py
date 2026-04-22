"""Google Cloud Text-to-Speech service — Chirp 3 HD streaming.

Public interface mirrors services.tts.TTSService so the WebSocket handler can
swap implementations through services.voice_provider without changes:

    class GoogleTTSService:
        is_loaded: bool
        voice: str
        sample_rate: int
        async synthesize(text, speed=1.0) -> bytes            # WAV bytes
        async synthesize_stream(text, speed=1.0) -> AsyncIterator[bytes]  # WAV chunks
        def list_voices() -> list[dict]
        async set_voice(voice_id: str) -> None

Streaming uses `TextToSpeechAsyncClient.streaming_synthesize()` which yields
raw LINEAR16 PCM chunks as the server renders them. We wrap each chunk in a
minimal WAV container so the existing WS audio playback path (which expects
WAV) keeps working without a frontend change.

The Google client is lazy-loaded on first call so module import stays cheap
and credential-less.
"""

from __future__ import annotations

import asyncio
import logging
import re
import struct
import time
from collections.abc import AsyncIterator
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "en-US-Chirp3-HD-Aoede"  # American female, conversational
DEFAULT_LANGUAGE = "en-US"
DEFAULT_SAMPLE_RATE = 24000  # Matches Kokoro output to avoid frontend resample

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


class GoogleTTSService:
    """Streaming TTS using Google Cloud Text-to-Speech (Chirp 3 HD)."""

    # Tag used by usage_tracker to look up pricing + write tts_provider=...
    # Matches the VOICE_PRICING map key prefix "google_tts".
    provider_name: str = "google"

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        language: str = DEFAULT_LANGUAGE,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
    ) -> None:
        self._client = None
        self._client_lock = asyncio.Lock()
        self._voice = voice
        self._language = language
        self._sample_rate = sample_rate

    # ------------------------------------------------------------------ #
    # Public properties matching TTSService
    # ------------------------------------------------------------------ #

    @property
    def is_loaded(self) -> bool:
        return self._client is not None

    @property
    def voice(self) -> str:
        return self._voice

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    async def warm(self) -> None:
        """Instantiate the Google client up-front so the first live TTS call
        doesn't pay client-init latency. Mirrors TTSService.warm() so main.py's
        startup warming works across both providers."""
        await self._ensure_client()

    # ------------------------------------------------------------------ #
    # Lazy client bootstrap
    # ------------------------------------------------------------------ #

    async def _ensure_client(self):
        if self._client is not None:
            return self._client

        async with self._client_lock:
            if self._client is not None:
                return self._client

            logger.info("Initializing Google Cloud Text-to-Speech client...")
            try:
                # gRPC async client must be constructed on the event loop,
                # not a worker thread — otherwise cygrpc can't find the loop.
                self._client = self._build_client()
                logger.info("Google Cloud Text-to-Speech client ready")
            except Exception:
                logger.exception(
                    "Failed to init Google TTS client — check "
                    "GOOGLE_APPLICATION_CREDENTIALS and that the "
                    "'Cloud Text-to-Speech API' is enabled on the project"
                )
                raise
        return self._client

    @staticmethod
    def _build_client():
        from google.cloud import texttospeech  # type: ignore

        return texttospeech.TextToSpeechAsyncClient()

    # ------------------------------------------------------------------ #
    # Voice selection
    # ------------------------------------------------------------------ #

    def list_voices(self) -> list[dict]:
        """Curated subset of Chirp 3 HD voices available to the owner.

        Google exposes a `list_voices()` RPC too — we return a static list
        here so the endpoint can render without making a network call and
        so we stay consistent with how TTSService reports voices.
        """
        return [
            {
                "id": "en-US-Chirp3-HD-Aoede",
                "name": "Aoede",
                "gender": "female",
                "accent": "American English",
                "description": "Warm, conversational female (default)",
            },
            {
                "id": "en-US-Chirp3-HD-Charon",
                "name": "Charon",
                "gender": "male",
                "accent": "American English",
                "description": "Deep, confident male",
            },
            {
                "id": "en-US-Chirp3-HD-Kore",
                "name": "Kore",
                "gender": "female",
                "accent": "American English",
                "description": "Clear, professional female",
            },
            {
                "id": "en-US-Chirp3-HD-Fenrir",
                "name": "Fenrir",
                "gender": "male",
                "accent": "American English",
                "description": "Energetic, friendly male",
            },
            {
                "id": "en-US-Chirp3-HD-Leda",
                "name": "Leda",
                "gender": "female",
                "accent": "American English",
                "description": "Bright, youthful female",
            },
            {
                "id": "en-US-Chirp3-HD-Puck",
                "name": "Puck",
                "gender": "male",
                "accent": "American English",
                "description": "Upbeat, expressive male",
            },
        ]

    async def set_voice(self, voice_id: str) -> None:
        valid = {v["id"] for v in self.list_voices()}
        if voice_id not in valid:
            # Don't hard-fail — Google has many voices beyond our curated
            # list. Accept it but warn.
            logger.warning(
                "Voice '%s' not in curated list; accepting anyway (will "
                "fail at synthesis if Google rejects it).",
                voice_id,
            )
        old = self._voice
        self._voice = voice_id
        logger.info("Google TTS voice changed: %s -> %s", old, voice_id)

    # ------------------------------------------------------------------ #
    # Synthesis — one-shot
    # ------------------------------------------------------------------ #

    async def synthesize(self, text: str, speed: float = 1.0) -> bytes:
        """Render text to WAV bytes (24kHz, mono, 16-bit PCM)."""
        if not text or not text.strip():
            raise ValueError("Empty text input")

        client = await self._ensure_client()

        from google.cloud import texttospeech  # type: ignore

        input_ = texttospeech.SynthesisInput(text=text)
        voice_params = texttospeech.VoiceSelectionParams(
            language_code=self._language,
            name=self._voice,
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            sample_rate_hertz=self._sample_rate,
            speaking_rate=speed,
        )

        start = time.perf_counter()
        try:
            response = await client.synthesize_speech(
                input=input_, voice=voice_params, audio_config=audio_config
            )
        except Exception:
            logger.exception("Google TTS synthesize_speech() failed")
            raise

        audio = response.audio_content
        # Google returns a full WAV when LINEAR16 is requested via
        # synthesize_speech (it wraps the PCM). But some SDK versions return
        # raw PCM — normalize to WAV either way.
        wav_bytes = audio if audio[:4] == b"RIFF" else _wrap_pcm_as_wav(
            audio, self._sample_rate
        )

        elapsed = time.perf_counter() - start
        logger.info(
            "Google TTS synthesis completed in %.2fs: %d chars -> %d bytes",
            elapsed,
            len(text),
            len(wav_bytes),
        )
        return wav_bytes

    # ------------------------------------------------------------------ #
    # Synthesis — streaming
    # ------------------------------------------------------------------ #

    async def synthesize_stream(
        self,
        text: str,
        speed: float = 1.0,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> AsyncIterator[bytes]:
        """Stream WAV audio chunks as Google renders them.

        Uses the `streaming_synthesize` bidi RPC when available. For each
        PCM chunk the server returns we wrap it in its own WAV container so
        the existing WS playback path (which expects self-contained WAV
        frames per-chunk) keeps working unchanged.

        Falls back to per-sentence synthesize_speech if streaming is not
        available on the installed SDK.

        ``cancel_event`` (Track B #5): optional asyncio.Event checked
        between chunks / sentences. When set, the generator stops yielding
        and returns cleanly. This is how ``cancel_current_turn`` in the WS
        handler kills in-flight audio without waiting for Google's full
        synthesis to drain. A ``None`` cancel_event is equivalent to
        pre-patch behavior (stream until done) — callers that don't need
        cancellation can omit it.
        """
        if not text or not text.strip():
            return

        if cancel_event is not None and cancel_event.is_set():
            return

        client = await self._ensure_client()

        if hasattr(client, "streaming_synthesize"):
            async for chunk in self._streaming_synthesize(
                client, text, speed, cancel_event=cancel_event
            ):
                yield chunk
            return

        # Fallback: per-sentence one-shot synthesis.
        logger.info(
            "streaming_synthesize() not present on client; falling back to "
            "per-sentence synthesize_speech()"
        )
        for sentence in self._split_sentences(text):
            if cancel_event is not None and cancel_event.is_set():
                return
            if not sentence.strip():
                continue
            try:
                yield await self.synthesize(sentence, speed=speed)
            except Exception:
                logger.exception("Fallback TTS failed for sentence: %s", sentence[:80])
                continue

    async def _streaming_synthesize(
        self,
        client,
        text: str,
        speed: float,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> AsyncIterator[bytes]:
        from google.cloud import texttospeech  # type: ignore

        voice_params = texttospeech.VoiceSelectionParams(
            language_code=self._language,
            name=self._voice,
        )
        streaming_audio_config = texttospeech.StreamingAudioConfig(
            audio_encoding=texttospeech.AudioEncoding.PCM,
            sample_rate_hertz=self._sample_rate,
        )
        streaming_config = texttospeech.StreamingSynthesizeConfig(
            voice=voice_params,
            streaming_audio_config=streaming_audio_config,
        )

        # The server expects config as the first request, then one or more
        # input text requests.
        config_request = texttospeech.StreamingSynthesizeRequest(
            streaming_config=streaming_config,
        )
        text_request = texttospeech.StreamingSynthesizeRequest(
            input=texttospeech.StreamingSynthesisInput(text=text),
        )

        async def request_iter():
            yield config_request
            yield text_request

        start = time.perf_counter()
        try:
            response_stream = await client.streaming_synthesize(requests=request_iter())
        except Exception:
            logger.exception("Google streaming_synthesize() RPC failed")
            raise

        first_chunk = True
        async for response in response_stream:
            # Track B #5: check cancel between chunks so a barge-in stops
            # audio at the next chunk boundary instead of waiting for the
            # full synthesis to drain from Google.
            if cancel_event is not None and cancel_event.is_set():
                logger.info(
                    "Google TTS streaming_synthesize cancelled mid-stream "
                    "(text_len=%d, first_chunk=%s)",
                    len(text), first_chunk,
                )
                return
            pcm = getattr(response, "audio_content", b"")
            if not pcm:
                continue
            yield _wrap_pcm_as_wav(pcm, self._sample_rate)
            if first_chunk:
                logger.debug(
                    "First Google TTS audio chunk in %.2fs",
                    time.perf_counter() - start,
                )
                first_chunk = False

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        sentences = SENTENCE_SPLIT_RE.split(text)
        out: list[str] = []
        for s in sentences:
            s = s.strip()
            if not s:
                continue
            if out and len(s) < 10:
                out[-1] = out[-1] + " " + s
            else:
                out.append(s)
        return out if out else [text.strip()]


def _wrap_pcm_as_wav(pcm_bytes: bytes, sample_rate: int, channels: int = 1) -> bytes:
    """Wrap raw 16-bit PCM mono in a minimal WAV container.

    We avoid soundfile here to skip the numpy round-trip — this is on the
    per-chunk hot path.
    """
    bits_per_sample = 16
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    data_size = len(pcm_bytes)

    header = b"RIFF"
    header += struct.pack("<I", 36 + data_size)
    header += b"WAVE"
    header += b"fmt "
    header += struct.pack("<I", 16)  # fmt chunk size
    header += struct.pack("<H", 1)   # PCM format
    header += struct.pack("<H", channels)
    header += struct.pack("<I", sample_rate)
    header += struct.pack("<I", byte_rate)
    header += struct.pack("<H", block_align)
    header += struct.pack("<H", bits_per_sample)
    header += b"data"
    header += struct.pack("<I", data_size)
    return header + pcm_bytes


__all__ = ["GoogleTTSService"]
