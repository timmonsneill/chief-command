"""Smoke tests for services.tts_google.GoogleTTSService.

No real Google API calls — we monkeypatch the client builder to return a
Mock and verify argument shapes + WAV header wrapping.
"""

from __future__ import annotations

import asyncio
import struct
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Stub google.cloud.texttospeech
# ---------------------------------------------------------------------------


def _install_google_tts_stubs():
    google_pkg = sys.modules.get("google") or types.ModuleType("google")
    google_pkg.__path__ = getattr(google_pkg, "__path__", [])  # type: ignore[attr-defined]
    sys.modules["google"] = google_pkg

    cloud_pkg = sys.modules.get("google.cloud") or types.ModuleType("google.cloud")
    cloud_pkg.__path__ = getattr(cloud_pkg, "__path__", [])  # type: ignore[attr-defined]
    sys.modules["google.cloud"] = cloud_pkg
    google_pkg.cloud = cloud_pkg  # type: ignore[attr-defined]

    if "google.cloud.texttospeech" not in sys.modules:
        tts_mod = types.ModuleType("google.cloud.texttospeech")

        class _AudioEncoding:
            LINEAR16 = "LINEAR16"
            PCM = "PCM"

        class _SynthesisInput:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class _VoiceSelectionParams:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class _AudioConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class _StreamingAudioConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class _StreamingSynthesizeConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class _StreamingSynthesisInput:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class _StreamingSynthesizeRequest:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        tts_mod.AudioEncoding = _AudioEncoding
        tts_mod.SynthesisInput = _SynthesisInput
        tts_mod.VoiceSelectionParams = _VoiceSelectionParams
        tts_mod.AudioConfig = _AudioConfig
        tts_mod.StreamingAudioConfig = _StreamingAudioConfig
        tts_mod.StreamingSynthesizeConfig = _StreamingSynthesizeConfig
        tts_mod.StreamingSynthesisInput = _StreamingSynthesisInput
        tts_mod.StreamingSynthesizeRequest = _StreamingSynthesizeRequest
        tts_mod.TextToSpeechAsyncClient = MagicMock()

        sys.modules["google.cloud.texttospeech"] = tts_mod
        cloud_pkg.texttospeech = tts_mod  # type: ignore[attr-defined]


_install_google_tts_stubs()

from services.tts_google import (  # noqa: E402
    GoogleTTSService,
    _wrap_pcm_as_wav,
)


# ---------------------------------------------------------------------------
# Fake client helpers
# ---------------------------------------------------------------------------


class _OneShotOnlyClient:
    """Fake TTS client without a streaming_synthesize attr.

    Plain class so hasattr() correctly returns False — MagicMock auto-spawns
    attributes on access, which defeats the fallback-detection check in
    GoogleTTSService.synthesize_stream.
    """

    def __init__(self, pcm_payload: bytes):
        response = MagicMock()
        response.audio_content = pcm_payload
        self.synthesize_speech = AsyncMock(return_value=response)


def _make_fake_oneshot_client(pcm_payload: bytes):
    return _OneShotOnlyClient(pcm_payload)


def _make_fake_streaming_client(chunks: list[bytes]):
    async def response_gen():
        for c in chunks:
            r = MagicMock()
            r.audio_content = c
            yield r

    async def streaming_synthesize(requests=None):  # noqa: ARG001
        return response_gen()

    client = MagicMock()
    client.streaming_synthesize = streaming_synthesize
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_is_loaded_starts_false():
    svc = GoogleTTSService()
    assert svc.is_loaded is False


def test_default_voice_is_aoede():
    svc = GoogleTTSService()
    assert svc.voice == "en-US-Chirp3-HD-Aoede"


def test_default_sample_rate_matches_kokoro():
    svc = GoogleTTSService()
    assert svc.sample_rate == 24000  # so no resample on the frontend


@pytest.mark.asyncio
async def test_empty_text_raises():
    svc = GoogleTTSService()
    with pytest.raises(ValueError):
        await svc.synthesize("")


@pytest.mark.asyncio
async def test_synthesize_one_shot_wraps_pcm_as_wav(monkeypatch):
    raw_pcm = b"\x01\x02" * 100
    fake_client = _make_fake_oneshot_client(raw_pcm)
    monkeypatch.setattr(
        GoogleTTSService, "_build_client", staticmethod(lambda: fake_client)
    )

    svc = GoogleTTSService()
    wav = await svc.synthesize("Hello Chief.")

    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    fake_client.synthesize_speech.assert_awaited_once()

    # voice + language forwarded
    call_kwargs = fake_client.synthesize_speech.call_args.kwargs
    assert call_kwargs["voice"].kwargs["name"] == "en-US-Chirp3-HD-Aoede"
    assert call_kwargs["voice"].kwargs["language_code"] == "en-US"
    assert call_kwargs["audio_config"].kwargs["sample_rate_hertz"] == 24000


@pytest.mark.asyncio
async def test_synthesize_passes_through_wav_if_already_wrapped(monkeypatch):
    wav_header = _wrap_pcm_as_wav(b"\x00\x00" * 50, 24000)
    fake_client = _make_fake_oneshot_client(wav_header)
    monkeypatch.setattr(
        GoogleTTSService, "_build_client", staticmethod(lambda: fake_client)
    )

    svc = GoogleTTSService()
    wav = await svc.synthesize("Hi.")
    assert wav == wav_header  # already RIFF → passthrough


@pytest.mark.asyncio
async def test_streaming_synthesize_wraps_each_chunk(monkeypatch):
    chunks = [b"\x00\x01" * 40, b"\x02\x03" * 40, b"\x04\x05" * 40]
    fake_client = _make_fake_streaming_client(chunks)
    monkeypatch.setattr(
        GoogleTTSService, "_build_client", staticmethod(lambda: fake_client)
    )

    svc = GoogleTTSService()
    out = []
    async for frame in svc.synthesize_stream("One sentence. Two sentence."):
        out.append(frame)

    assert len(out) == 3
    for frame in out:
        assert frame[:4] == b"RIFF"
        assert frame[8:12] == b"WAVE"


@pytest.mark.asyncio
async def test_streaming_falls_back_to_oneshot_when_unavailable(monkeypatch):
    raw_pcm = b"\x0a\x0b" * 80
    fake_client = _make_fake_oneshot_client(raw_pcm)
    # Ensure no streaming_synthesize method
    assert not hasattr(fake_client, "streaming_synthesize")

    monkeypatch.setattr(
        GoogleTTSService, "_build_client", staticmethod(lambda: fake_client)
    )

    svc = GoogleTTSService()
    frames = []
    # Each sentence must exceed the 10-char merge threshold in
    # _split_sentences, otherwise they collapse into one.
    async for frame in svc.synthesize_stream(
        "This is the first sentence. Here is the second one."
    ):
        frames.append(frame)

    # Two sentences → two fallback one-shots
    assert len(frames) == 2
    assert all(f[:4] == b"RIFF" for f in frames)
    assert fake_client.synthesize_speech.await_count == 2


@pytest.mark.asyncio
async def test_set_voice_updates_voice():
    svc = GoogleTTSService()
    await svc.set_voice("en-US-Chirp3-HD-Kore")
    assert svc.voice == "en-US-Chirp3-HD-Kore"


def test_list_voices_returns_chirp3_variants():
    svc = GoogleTTSService()
    voices = svc.list_voices()
    ids = {v["id"] for v in voices}
    assert "en-US-Chirp3-HD-Aoede" in ids
    assert all(v["id"].startswith("en-US-Chirp3-HD-") for v in voices)


def test_wrap_pcm_as_wav_header_shape():
    pcm = b"\xab\xcd" * 10
    wav = _wrap_pcm_as_wav(pcm, 24000)
    assert wav[:4] == b"RIFF"
    riff_size = struct.unpack("<I", wav[4:8])[0]
    assert riff_size == 36 + len(pcm)
    assert wav[8:12] == b"WAVE"
    # data chunk at offset 36
    assert wav[36:40] == b"data"
    data_size = struct.unpack("<I", wav[40:44])[0]
    assert data_size == len(pcm)
    assert wav[44:] == pcm
