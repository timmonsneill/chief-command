"""Google Cloud Speech-to-Text v2 streaming service.

Public interface mirrors services.stt.STTService so the WebSocket handler can
swap implementations through services.voice_provider without changes:

    class GoogleSTTService:
        is_loaded: bool
        async transcribe(audio_bytes, sample_rate=16000, language=None) -> str
        async transcribe_stream(audio_chunks, sample_rate=16000, language=None) -> AsyncIterator[str]

The Google Cloud client is lazy-loaded on first call so module import stays
cheap and credential-less — safe for default-local mode and for pytest.

Auth: the google-cloud-speech client picks up GOOGLE_APPLICATION_CREDENTIALS
automatically. We do not read the path ourselves.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import time
from collections.abc import AsyncIterator
from typing import Optional

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)

DEFAULT_SAMPLE_RATE = 16000
DEFAULT_LANGUAGE = "en-US"


class GoogleSTTService:
    """Streaming STT using Google Cloud Speech-to-Text v2.

    The v2 `StreamingRecognize` endpoint emits both interim and final
    transcripts. We surface finals via `transcribe` and stream both via
    `transcribe_stream` (only non-empty deltas).
    """

    def __init__(
        self,
        language: str = DEFAULT_LANGUAGE,
        recognizer: str = "_",  # v2 default recognizer
        location: str = "global",
    ) -> None:
        self._client = None
        self._client_lock = asyncio.Lock()
        self._language = language
        self._recognizer = recognizer
        self._location = location

    # ------------------------------------------------------------------ #
    # Public properties matching STTService
    # ------------------------------------------------------------------ #

    @property
    def is_loaded(self) -> bool:
        """True once the Google client has been instantiated."""
        return self._client is not None

    @property
    def language(self) -> str:
        return self._language

    async def warm(self) -> None:
        """Instantiate the Google client up-front so the first live STT call
        doesn't pay client-init latency. Mirrors STTService.warm() so main.py's
        startup warming works across both providers."""
        await self._ensure_client()

    # ------------------------------------------------------------------ #
    # Lazy client bootstrap
    # ------------------------------------------------------------------ #

    async def _ensure_client(self):
        """Instantiate the SpeechAsyncClient on first use.

        Kept async so we don't block the event loop if the google-cloud-speech
        import does any first-time registration work.
        """
        if self._client is not None:
            return self._client

        async with self._client_lock:
            if self._client is not None:
                return self._client

            logger.info("Initializing Google Cloud Speech client...")
            try:
                self._client = await asyncio.to_thread(self._build_client)
                logger.info("Google Cloud Speech client ready")
            except Exception:
                logger.exception(
                    "Failed to init Google Speech client — check "
                    "GOOGLE_APPLICATION_CREDENTIALS and that the "
                    "'Cloud Speech-to-Text API' is enabled on the project"
                )
                raise
        return self._client

    @staticmethod
    def _build_client():
        """Import google-cloud-speech and return an async client.

        Import is inside this function to keep module import free of the
        heavy dependency — main runs without google-cloud-speech installed
        when VOICE_PROVIDER=local.
        """
        from google.cloud import speech_v2  # type: ignore

        return speech_v2.SpeechAsyncClient()

    def _project_id(self) -> str:
        """Pull the GCP project id from the env.

        The client library resolves this from ADC, but v2 recognizer paths
        require it explicitly. We read GOOGLE_CLOUD_PROJECT or fall back to a
        placeholder — the actual call will still fail cleanly with a helpful
        error if nothing is set.
        """
        return (
            os.environ.get("GOOGLE_CLOUD_PROJECT")
            or os.environ.get("GCP_PROJECT")
            or "chief-command"
        )

    def _recognizer_path(self) -> str:
        return (
            f"projects/{self._project_id()}"
            f"/locations/{self._location}"
            f"/recognizers/{self._recognizer}"
        )

    # ------------------------------------------------------------------ #
    # One-shot transcription
    # ------------------------------------------------------------------ #

    async def transcribe(
        self,
        audio_bytes: bytes,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        language: Optional[str] = None,
    ) -> str:
        """Transcribe a single audio payload (WAV or raw PCM)."""
        if not audio_bytes:
            return ""

        client = await self._ensure_client()
        pcm_bytes, detected_rate = self._to_raw_pcm(audio_bytes, sample_rate)
        rate = detected_rate or sample_rate

        from google.cloud.speech_v2.types import cloud_speech  # type: ignore

        config = cloud_speech.RecognitionConfig(
            explicit_decoding_config=cloud_speech.ExplicitDecodingConfig(
                encoding=cloud_speech.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=rate,
                audio_channel_count=1,
            ),
            language_codes=[language or self._language],
            model="long",
            features=cloud_speech.RecognitionFeatures(enable_automatic_punctuation=True),
        )

        request = cloud_speech.RecognizeRequest(
            recognizer=self._recognizer_path(),
            config=config,
            content=pcm_bytes,
        )

        start = time.perf_counter()
        try:
            response = await client.recognize(request=request)
        except Exception:
            logger.exception("Google STT recognize() failed")
            raise

        parts: list[str] = []
        for result in response.results:
            if result.alternatives:
                parts.append(result.alternatives[0].transcript.strip())

        text = " ".join(p for p in parts if p).strip()
        elapsed = time.perf_counter() - start
        logger.info(
            "Google STT recognize completed in %.2fs: %d chars", elapsed, len(text)
        )
        return text

    # ------------------------------------------------------------------ #
    # Streaming transcription (bi-di)
    # ------------------------------------------------------------------ #

    async def transcribe_stream(
        self,
        audio_chunks: AsyncIterator[bytes],
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        language: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """Stream raw PCM chunks to Google and yield transcript updates.

        Yields interim + final deltas. Callers that only care about finals
        can filter by detecting result metadata — here we yield strings to
        match the existing STTService contract, so we yield finals only.
        """
        client = await self._ensure_client()

        from google.cloud.speech_v2.types import cloud_speech  # type: ignore

        lang = language or self._language

        streaming_features = cloud_speech.StreamingRecognitionFeatures(
            interim_results=True,
        )
        recognition_config = cloud_speech.RecognitionConfig(
            explicit_decoding_config=cloud_speech.ExplicitDecodingConfig(
                encoding=cloud_speech.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=sample_rate,
                audio_channel_count=1,
            ),
            language_codes=[lang],
            model="long",
            features=cloud_speech.RecognitionFeatures(enable_automatic_punctuation=True),
        )
        streaming_config = cloud_speech.StreamingRecognitionConfig(
            config=recognition_config,
            streaming_features=streaming_features,
        )

        first_request = cloud_speech.StreamingRecognizeRequest(
            recognizer=self._recognizer_path(),
            streaming_config=streaming_config,
        )

        async def request_iter():
            yield first_request
            async for chunk in audio_chunks:
                if not chunk:
                    continue
                yield cloud_speech.StreamingRecognizeRequest(audio=chunk)

        try:
            responses = await client.streaming_recognize(requests=request_iter())
        except Exception:
            logger.exception("Google STT streaming_recognize() failed")
            raise

        async for response in responses:
            for result in response.results:
                if not result.alternatives:
                    continue
                # Only surface finals to callers — matches STTService
                # semantics (it only yields when transcription completes a
                # buffered segment).
                if getattr(result, "is_final", False):
                    text = result.alternatives[0].transcript.strip()
                    if text:
                        yield text

    # ------------------------------------------------------------------ #
    # Audio helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _to_raw_pcm(audio_bytes: bytes, sample_rate: int) -> tuple[bytes, int]:
        """Accept WAV or raw PCM and return (pcm_bytes, sample_rate).

        Google's LINEAR16 wants raw PCM without a WAV header.
        """
        if audio_bytes[:4] == b"RIFF":
            try:
                with io.BytesIO(audio_bytes) as buf:
                    data, file_sr = sf.read(buf, dtype="int16")
                if data.ndim > 1:
                    data = data.mean(axis=1).astype(np.int16)
                return data.tobytes(), int(file_sr)
            except Exception:
                logger.warning(
                    "Failed to parse WAV header, forwarding bytes as raw PCM"
                )

        return audio_bytes, sample_rate


# Singleton accessor matching STTService's usage pattern. The factory in
# services.voice_provider owns actual instantiation — this is only here so
# direct imports work symmetrically.
__all__ = ["GoogleSTTService"]
