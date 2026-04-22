"""Speech-to-Text service — faster-whisper on Apple Silicon with streaming support."""

import asyncio
import io
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)

# Model cache directory — downloads automatically on first use
MODELS_DIR = Path("/Users/user/Desktop/chief-command/models")
MODEL_SIZE = "medium"
COMPUTE_TYPE = "int8"  # Optimized for Apple Silicon M-series Neural Engine

# Streaming buffer config
STREAM_BUFFER_SECONDS = 1.5  # Accumulate ~1.5s of audio before transcribing
STREAM_SAMPLE_RATE = 16000


class STTService:
    """Speech-to-Text using faster-whisper with lazy model loading."""

    # Tag used by usage_tracker to look up pricing + write stt_provider=...
    # Keep lowercase for consistency with the VOICE_PRICING map keys.
    provider_name: str = "local"

    def __init__(self) -> None:
        self._model = None
        self._lock = asyncio.Lock()
        self._loading = False

    @property
    def is_loaded(self) -> bool:
        """Check if the whisper model is loaded."""
        return self._model is not None

    async def warm(self) -> None:
        """Public warm-up entry point — loads the whisper model if not already loaded.

        Called by FastAPI startup to avoid cold-start latency on the first turn.
        Thin wrapper over ``_ensure_model`` so callers don't reach into private API.
        """
        await self._ensure_model()

    async def _ensure_model(self) -> None:
        """Lazy-load the faster-whisper model on first use. Thread-safe."""
        if self._model is not None:
            return

        async with self._lock:
            # Double-check after acquiring lock
            if self._model is not None:
                return

            self._loading = True
            logger.info(
                "Loading faster-whisper model '%s' (compute_type=%s)...",
                MODEL_SIZE,
                COMPUTE_TYPE,
            )
            logger.info(
                "Model cache directory: %s — first run will download ~1.5 GB",
                MODELS_DIR,
            )

            try:
                self._model = await asyncio.to_thread(self._load_model)
                logger.info("faster-whisper model loaded successfully")
            except Exception:
                logger.exception("Failed to load faster-whisper model")
                raise
            finally:
                self._loading = False

    @staticmethod
    def _load_model():
        """Load the whisper model synchronously (runs in thread)."""
        from faster_whisper import WhisperModel

        MODELS_DIR.mkdir(parents=True, exist_ok=True)

        print(f"[STT] Downloading/loading faster-whisper '{MODEL_SIZE}' model...")
        print(f"[STT] Cache directory: {MODELS_DIR}")
        print("[STT] This may take a few minutes on first run.")

        model = WhisperModel(
            MODEL_SIZE,
            device="cpu",  # faster-whisper uses CPU on macOS (no CUDA)
            compute_type=COMPUTE_TYPE,
            download_root=str(MODELS_DIR),
        )

        print("[STT] Model loaded and ready.")
        return model

    async def transcribe(
        self,
        audio_bytes: bytes,
        sample_rate: int = 16000,
        language: Optional[str] = "en",
    ) -> str:
        """Transcribe audio bytes to text.

        Args:
            audio_bytes: Raw audio in WAV or PCM format.
            sample_rate: Sample rate of the audio (default 16kHz).
            language: Language code or None for auto-detection.

        Returns:
            Transcribed text string.
        """
        await self._ensure_model()

        start = time.perf_counter()

        try:
            text = await asyncio.to_thread(
                self._transcribe_sync, audio_bytes, sample_rate, language
            )
        except Exception:
            logger.exception("Transcription failed")
            raise

        elapsed = time.perf_counter() - start
        logger.info("Transcription completed in %.2fs: %d chars", elapsed, len(text))
        return text

    def _transcribe_sync(
        self,
        audio_bytes: bytes,
        sample_rate: int,
        language: Optional[str],
    ) -> str:
        """Run whisper transcription synchronously (called via to_thread)."""
        # Convert bytes to numpy float32 array
        audio_array = self._bytes_to_numpy(audio_bytes, sample_rate)

        if audio_array.size == 0:
            return ""

        # Run transcription
        segments, info = self._model.transcribe(
            audio_array,
            language=language,
            beam_size=5,
            vad_filter=True,  # Filter out silence for faster processing
            vad_parameters=dict(
                min_silence_duration_ms=500,
            ),
        )

        # Collect all segment text
        parts = []
        for segment in segments:
            parts.append(segment.text.strip())

        text = " ".join(parts).strip()
        logger.debug(
            "Whisper detected language=%s probability=%.2f",
            info.language,
            info.language_probability,
        )
        return text

    async def transcribe_stream(
        self,
        audio_chunks: AsyncIterator[bytes],
        sample_rate: int = STREAM_SAMPLE_RATE,
        language: Optional[str] = "en",
    ) -> AsyncIterator[str]:
        """Transcribe streaming audio chunks in near-real-time.

        Buffers ~1.5 seconds of audio, transcribes, then yields text segments.
        Enables real-time transcription while the user is still speaking.

        Args:
            audio_chunks: Async iterator yielding raw PCM/WAV audio bytes.
            sample_rate: Sample rate of incoming audio.
            language: Language code or None for auto-detection.

        Yields:
            Transcribed text segments as they become available.
        """
        await self._ensure_model()

        buffer = bytearray()
        bytes_per_second = sample_rate * 2  # 16-bit PCM = 2 bytes per sample
        min_buffer_size = int(bytes_per_second * STREAM_BUFFER_SECONDS)

        async for chunk in audio_chunks:
            buffer.extend(chunk)

            if len(buffer) >= min_buffer_size:
                start = time.perf_counter()

                audio_segment = bytes(buffer)
                buffer.clear()

                try:
                    text = await asyncio.to_thread(
                        self._transcribe_sync, audio_segment, sample_rate, language
                    )
                except Exception:
                    logger.exception("Stream transcription chunk failed")
                    continue

                elapsed = time.perf_counter() - start

                if text:
                    logger.debug(
                        "Stream chunk transcribed in %.2fs: '%s'",
                        elapsed,
                        text[:80],
                    )
                    yield text

        # Flush remaining buffer
        if buffer:
            try:
                text = await asyncio.to_thread(
                    self._transcribe_sync, bytes(buffer), sample_rate, language
                )
                if text:
                    logger.debug("Stream flush: '%s'", text[:80])
                    yield text
            except Exception:
                logger.exception("Stream flush transcription failed")

    @staticmethod
    def _bytes_to_numpy(audio_bytes: bytes, sample_rate: int) -> np.ndarray:
        """Convert audio bytes to numpy float32 array suitable for whisper.

        Handles both WAV (with header) and raw PCM 16-bit formats.
        """
        if not audio_bytes:
            return np.array([], dtype=np.float32)

        # Try reading as WAV first (has RIFF header)
        if audio_bytes[:4] == b"RIFF":
            try:
                with io.BytesIO(audio_bytes) as buf:
                    data, file_sr = sf.read(buf, dtype="float32")
                # Convert to mono if stereo
                if data.ndim > 1:
                    data = data.mean(axis=1)
                return data.astype(np.float32)
            except Exception:
                logger.warning("Failed to read as WAV, falling back to raw PCM")

        # Treat as raw PCM 16-bit signed little-endian
        audio_array = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
        audio_array /= 32768.0  # Normalize to [-1.0, 1.0]
        return audio_array
