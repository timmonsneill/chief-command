"""Text-to-Speech service — Kokoro TTS with streaming support."""

import asyncio
import io
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import Optional

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)

# Default: American female, warm/conversational (Kokoro "af_heart").
# Fallback: American male if the primary voice .pt file fails to load.
DEFAULT_VOICE = "af_heart"
FALLBACK_VOICE = "am_adam"
DEFAULT_SAMPLE_RATE = 24000  # Kokoro outputs 24kHz audio

# Sentence splitting pattern — split on . ! ? followed by space or end
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


class TTSService:
    """Text-to-Speech using Kokoro with lazy model loading."""

    def __init__(self) -> None:
        self._pipeline = None
        self._lock = asyncio.Lock()
        self._loading = False
        self._voice = DEFAULT_VOICE
        self._sample_rate = DEFAULT_SAMPLE_RATE

    @property
    def is_loaded(self) -> bool:
        """Check if the Kokoro pipeline is loaded."""
        return self._pipeline is not None

    @property
    def voice(self) -> str:
        """Currently active voice ID."""
        return self._voice

    @property
    def sample_rate(self) -> int:
        """Output sample rate."""
        return self._sample_rate

    async def warm(self) -> None:
        """Public warm-up entry point — loads the Kokoro pipeline if not already loaded.

        Called by FastAPI startup to avoid cold-start latency on the first turn.
        Thin wrapper over ``_ensure_pipeline`` so callers don't reach into private API.
        """
        await self._ensure_pipeline()

    async def _ensure_pipeline(self) -> None:
        """Lazy-load the Kokoro TTS pipeline on first use. Thread-safe."""
        if self._pipeline is not None:
            return

        async with self._lock:
            if self._pipeline is not None:
                return

            self._loading = True
            logger.info("Loading Kokoro TTS pipeline...")
            logger.info(
                "Model downloads automatically on first run (~300 MB)."
            )

            try:
                self._pipeline = await asyncio.to_thread(self._load_pipeline)
                logger.info("Kokoro TTS pipeline loaded successfully")
            except Exception:
                logger.exception("Failed to load Kokoro TTS pipeline")
                raise
            finally:
                self._loading = False

    @staticmethod
    def _load_pipeline():
        """Load Kokoro pipeline synchronously (runs in thread)."""
        from kokoro import KPipeline

        print("[TTS] Loading Kokoro TTS pipeline...")
        print("[TTS] This may take a few minutes on first run (model download).")

        pipeline = KPipeline(lang_code="a")  # 'a' = American English

        print("[TTS] Kokoro pipeline loaded and ready.")
        return pipeline

    async def synthesize(self, text: str, speed: float = 1.0) -> bytes:
        """Synthesize text to WAV audio bytes.

        Args:
            text: Text string to synthesize.
            speed: Playback speed multiplier (1.0 = normal).

        Returns:
            WAV audio bytes (24kHz, mono, 16-bit PCM).
        """
        if not text or not text.strip():
            raise ValueError("Empty text input")

        await self._ensure_pipeline()

        start = time.perf_counter()

        try:
            wav_bytes = await asyncio.to_thread(
                self._synthesize_sync, text, speed
            )
        except Exception:
            logger.exception("TTS synthesis failed")
            raise

        elapsed = time.perf_counter() - start
        logger.info(
            "TTS synthesis completed in %.2fs: %d chars → %d bytes audio",
            elapsed,
            len(text),
            len(wav_bytes),
        )
        return wav_bytes

    def _synthesize_sync(self, text: str, speed: float) -> bytes:
        """Run Kokoro synthesis synchronously (called via to_thread).

        If the configured voice fails (e.g. voice pt file missing), fall back
        to FALLBACK_VOICE once and record the switch so subsequent calls use it.
        """
        audio_segments: list = []

        try:
            for _gs, _ps, audio in self._pipeline(
                text, voice=self._voice, speed=speed
            ):
                if audio is not None:
                    audio_segments.append(audio)
        except Exception as exc:
            # Common failure mode: voice .pt file missing on disk for new voices
            # that haven't been downloaded. Log loudly and fall back once.
            if self._voice != FALLBACK_VOICE:
                logger.warning(
                    "TTS voice %r failed (%s); falling back to %r for this and future calls",
                    self._voice, exc, FALLBACK_VOICE,
                )
                self._voice = FALLBACK_VOICE
                audio_segments = []
                for _gs, _ps, audio in self._pipeline(
                    text, voice=self._voice, speed=speed
                ):
                    if audio is not None:
                        audio_segments.append(audio)
            else:
                raise

        if not audio_segments:
            raise RuntimeError("Kokoro produced no audio output")

        # Concatenate all segments
        full_audio = np.concatenate(audio_segments)

        # Convert to WAV bytes
        output = io.BytesIO()
        sf.write(output, full_audio, self._sample_rate, format="WAV", subtype="PCM_16")
        return output.getvalue()

    async def synthesize_stream(
        self,
        text: str,
        speed: float = 1.0,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> AsyncIterator[bytes]:
        """Synthesize text and yield WAV audio chunks as they are produced.

        Splits text into sentences and synthesizes each independently,
        yielding audio as soon as each sentence is ready. This enables
        audio playback to start before full synthesis completes.

        Args:
            text: Text string to synthesize.
            speed: Playback speed multiplier.
            cancel_event: Optional asyncio.Event checked between sentences.
                When set, the generator returns without synthesizing more —
                mirrors the Google TTS cancellation contract so the WS
                handler can kill local-provider audio the same way.

        Yields:
            WAV audio byte chunks (one per sentence/segment).
        """
        if not text or not text.strip():
            return

        if cancel_event is not None and cancel_event.is_set():
            return

        await self._ensure_pipeline()

        sentences = self._split_sentences(text)
        logger.debug("Streaming TTS: %d sentence(s)", len(sentences))

        for i, sentence in enumerate(sentences):
            if cancel_event is not None and cancel_event.is_set():
                logger.info(
                    "Local TTS synthesize_stream cancelled at sentence %d/%d",
                    i, len(sentences),
                )
                return
            sentence = sentence.strip()
            if not sentence:
                continue

            start = time.perf_counter()
            try:
                wav_bytes = await asyncio.to_thread(
                    self._synthesize_sync, sentence, speed
                )
            except Exception:
                logger.exception("Stream synthesis failed for sentence %d", i)
                continue

            elapsed = time.perf_counter() - start
            logger.debug(
                "Stream chunk %d/%d synthesized in %.2fs",
                i + 1,
                len(sentences),
                elapsed,
            )
            yield wav_bytes

    def list_voices(self) -> list[dict]:
        """Return available Kokoro voice options.

        These are the built-in Kokoro v1.0 voices. The pipeline does not need
        to be loaded to list them.
        """
        return [
            {
                "id": "af_heart",
                "name": "Heart",
                "gender": "female",
                "accent": "American English",
                "description": "Warm, natural female voice (default)",
            },
            {
                "id": "af_bella",
                "name": "Bella",
                "gender": "female",
                "accent": "American English",
                "description": "Clear, professional female voice",
            },
            {
                "id": "af_nicole",
                "name": "Nicole",
                "gender": "female",
                "accent": "American English",
                "description": "Friendly, conversational female voice",
            },
            {
                "id": "af_sarah",
                "name": "Sarah",
                "gender": "female",
                "accent": "American English",
                "description": "Calm, measured female voice",
            },
            {
                "id": "af_sky",
                "name": "Sky",
                "gender": "female",
                "accent": "American English",
                "description": "Bright, energetic female voice",
            },
            {
                "id": "am_adam",
                "name": "Adam",
                "gender": "male",
                "accent": "American English",
                "description": "Deep, authoritative male voice",
            },
            {
                "id": "am_michael",
                "name": "Michael",
                "gender": "male",
                "accent": "American English",
                "description": "Warm, friendly male voice",
            },
            {
                "id": "bf_emma",
                "name": "Emma",
                "gender": "female",
                "accent": "British English",
                "description": "Polished British female voice",
            },
            {
                "id": "bm_george",
                "name": "George",
                "gender": "male",
                "accent": "British English",
                "description": "Classic British male voice",
            },
        ]

    async def set_voice(self, voice_id: str) -> None:
        """Change the active TTS voice.

        Args:
            voice_id: Kokoro voice identifier (e.g. "af_heart", "am_adam").

        Raises:
            ValueError: If voice_id is not recognized.
        """
        valid_ids = {v["id"] for v in self.list_voices()}
        if voice_id not in valid_ids:
            raise ValueError(
                f"Unknown voice '{voice_id}'. "
                f"Available: {', '.join(sorted(valid_ids))}"
            )

        old_voice = self._voice
        self._voice = voice_id
        logger.info("TTS voice changed: %s → %s", old_voice, voice_id)

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """Split text into sentences for streaming synthesis."""
        sentences = SENTENCE_SPLIT_RE.split(text)
        # Filter empty strings and merge very short fragments
        result = []
        for s in sentences:
            s = s.strip()
            if not s:
                continue
            # Merge short fragments (< 10 chars) with previous sentence
            if result and len(s) < 10:
                result[-1] = result[-1] + " " + s
            else:
                result.append(s)
        return result if result else [text.strip()]
