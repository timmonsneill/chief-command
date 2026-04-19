"""Smoke tests for services.stt_google.GoogleSTTService.

No real Google API calls — we monkeypatch the client builder to return a
Mock with the methods we expect to invoke, then verify our wrapper hands
off the correct request shape.
"""

from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Reuse the Google stubs from the voice_provider test
# ---------------------------------------------------------------------------


def _install_google_stubs():
    google_pkg = sys.modules.get("google") or types.ModuleType("google")
    google_pkg.__path__ = getattr(google_pkg, "__path__", [])  # type: ignore[attr-defined]
    sys.modules["google"] = google_pkg

    cloud_pkg = sys.modules.get("google.cloud") or types.ModuleType("google.cloud")
    cloud_pkg.__path__ = getattr(cloud_pkg, "__path__", [])  # type: ignore[attr-defined]
    sys.modules["google.cloud"] = cloud_pkg
    google_pkg.cloud = cloud_pkg  # type: ignore[attr-defined]

    if "google.cloud.speech_v2" not in sys.modules:
        speech_v2 = types.ModuleType("google.cloud.speech_v2")
        speech_v2.SpeechAsyncClient = MagicMock()  # type: ignore[attr-defined]

        types_mod = types.ModuleType("google.cloud.speech_v2.types")
        cloud_speech_mod = types.ModuleType("google.cloud.speech_v2.types.cloud_speech")

        # Classes / enums used in our wrapper
        class _ExplicitDecodingConfig:
            class AudioEncoding:
                LINEAR16 = "LINEAR16"

            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class _RecognitionFeatures:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class _RecognitionConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class _StreamingRecognitionFeatures:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class _StreamingRecognitionConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class _RecognizeRequest:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.content = kwargs.get("content")
                self.recognizer = kwargs.get("recognizer")
                self.config = kwargs.get("config")

        class _StreamingRecognizeRequest:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        cloud_speech_mod.ExplicitDecodingConfig = _ExplicitDecodingConfig
        cloud_speech_mod.RecognitionFeatures = _RecognitionFeatures
        cloud_speech_mod.RecognitionConfig = _RecognitionConfig
        cloud_speech_mod.StreamingRecognitionFeatures = _StreamingRecognitionFeatures
        cloud_speech_mod.StreamingRecognitionConfig = _StreamingRecognitionConfig
        cloud_speech_mod.RecognizeRequest = _RecognizeRequest
        cloud_speech_mod.StreamingRecognizeRequest = _StreamingRecognizeRequest

        types_mod.cloud_speech = cloud_speech_mod  # type: ignore[attr-defined]
        speech_v2.types = types_mod  # type: ignore[attr-defined]

        sys.modules["google.cloud.speech_v2"] = speech_v2
        sys.modules["google.cloud.speech_v2.types"] = types_mod
        sys.modules["google.cloud.speech_v2.types.cloud_speech"] = cloud_speech_mod
        cloud_pkg.speech_v2 = speech_v2  # type: ignore[attr-defined]


_install_google_stubs()

from services.stt_google import GoogleSTTService  # noqa: E402


# ---------------------------------------------------------------------------
# Fake Google Speech client returning canned responses
# ---------------------------------------------------------------------------


def _make_fake_client_with_transcript(transcript: str):
    """Return a Mock SpeechAsyncClient whose recognize() returns the text."""
    alt = MagicMock()
    alt.transcript = transcript
    result = MagicMock()
    result.alternatives = [alt]
    response = MagicMock()
    response.results = [result]

    client = MagicMock()
    client.recognize = AsyncMock(return_value=response)
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_loaded_starts_false():
    svc = GoogleSTTService()
    assert svc.is_loaded is False


@pytest.mark.asyncio
async def test_empty_audio_returns_empty(monkeypatch):
    svc = GoogleSTTService()
    # Should short-circuit without building a client.
    monkeypatch.setattr(
        GoogleSTTService, "_build_client",
        staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("no creds"))),
    )
    result = await svc.transcribe(b"")
    assert result == ""
    assert svc.is_loaded is False


@pytest.mark.asyncio
async def test_transcribe_calls_recognize_with_raw_pcm(monkeypatch):
    fake_client = _make_fake_client_with_transcript("hello chief")
    monkeypatch.setattr(
        GoogleSTTService, "_build_client", staticmethod(lambda: fake_client)
    )

    svc = GoogleSTTService()
    pcm = b"\x00\x01" * 100
    text = await svc.transcribe(pcm, sample_rate=16000)

    assert text == "hello chief"
    fake_client.recognize.assert_awaited_once()

    call_args = fake_client.recognize.call_args
    request = call_args.kwargs["request"]
    assert request.content == pcm
    assert "projects/" in request.recognizer
    assert "/locations/global/recognizers/_" in request.recognizer


@pytest.mark.asyncio
async def test_transcribe_uses_custom_language(monkeypatch):
    fake_client = _make_fake_client_with_transcript("bonjour")
    monkeypatch.setattr(
        GoogleSTTService, "_build_client", staticmethod(lambda: fake_client)
    )

    svc = GoogleSTTService(language="fr-FR")
    await svc.transcribe(b"\x00\x01" * 50, sample_rate=16000)

    request = fake_client.recognize.call_args.kwargs["request"]
    # language_codes is inside the RecognitionConfig kwargs
    assert request.config.kwargs["language_codes"] == ["fr-FR"]


@pytest.mark.asyncio
async def test_client_is_lazy(monkeypatch):
    """Constructing GoogleSTTService MUST NOT call _build_client."""
    call_count = {"n": 0}

    def counting_builder():
        call_count["n"] += 1
        return _make_fake_client_with_transcript("")

    monkeypatch.setattr(
        GoogleSTTService, "_build_client", staticmethod(counting_builder)
    )

    svc = GoogleSTTService()
    assert call_count["n"] == 0

    # First transcribe call triggers one build.
    await svc.transcribe(b"\x00\x01" * 50)
    assert call_count["n"] == 1

    # Second call reuses the cached client.
    await svc.transcribe(b"\x00\x01" * 50)
    assert call_count["n"] == 1
