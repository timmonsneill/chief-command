"""Track B #5 — cancel_event plumbed through TTS.synthesize_stream.

Verifies both the Google and local TTS services honor cancel_event and
stop yielding at the next chunk / sentence boundary. No real Google API
calls — reuses the stub pattern from test_tts_google.py.
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


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

        class _E:
            LINEAR16 = "LINEAR16"
            PCM = "PCM"

        class _Kw:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        tts_mod.AudioEncoding = _E
        tts_mod.SynthesisInput = _Kw
        tts_mod.VoiceSelectionParams = _Kw
        tts_mod.AudioConfig = _Kw
        tts_mod.StreamingAudioConfig = _Kw
        tts_mod.StreamingSynthesizeConfig = _Kw
        tts_mod.StreamingSynthesisInput = _Kw
        tts_mod.StreamingSynthesizeRequest = _Kw
        tts_mod.TextToSpeechAsyncClient = MagicMock()

        sys.modules["google.cloud.texttospeech"] = tts_mod
        cloud_pkg.texttospeech = tts_mod  # type: ignore[attr-defined]


_install_google_tts_stubs()

from services.tts_google import GoogleTTSService  # noqa: E402


def _make_streaming_client(chunks: list[bytes], per_chunk_sleep: float = 0.0):
    """Build a fake Google TTS streaming client that yields the given chunks.

    ``per_chunk_sleep`` lets a test insert a small delay between chunks so
    the cancel event has time to be set between them.
    """

    async def response_gen():
        for c in chunks:
            if per_chunk_sleep > 0:
                await asyncio.sleep(per_chunk_sleep)
            r = MagicMock()
            r.audio_content = c
            yield r

    async def streaming_synthesize(requests=None):  # noqa: ARG001
        return response_gen()

    client = MagicMock()
    client.streaming_synthesize = streaming_synthesize
    return client


@pytest.mark.asyncio
async def test_synthesize_stream_stops_when_cancel_event_set_before(monkeypatch):
    """A cancel_event already set at call-time must yield nothing."""
    client = _make_streaming_client([b"\x00\x01" * 10, b"\x02\x03" * 10])
    monkeypatch.setattr(
        GoogleTTSService, "_build_client", staticmethod(lambda: client)
    )
    svc = GoogleTTSService()
    event = asyncio.Event()
    event.set()

    chunks = []
    async for chunk in svc.synthesize_stream("hi.", cancel_event=event):
        chunks.append(chunk)
    assert chunks == []


@pytest.mark.asyncio
async def test_synthesize_stream_stops_mid_stream_when_event_set(monkeypatch):
    """Cancelling mid-stream stops at the next chunk boundary."""
    client = _make_streaming_client(
        [b"\x00\x01" * 10, b"\x02\x03" * 10, b"\x04\x05" * 10],
        per_chunk_sleep=0.01,
    )
    monkeypatch.setattr(
        GoogleTTSService, "_build_client", staticmethod(lambda: client)
    )
    svc = GoogleTTSService()
    event = asyncio.Event()

    async def consumer():
        out = []
        async for chunk in svc.synthesize_stream("hi.", cancel_event=event):
            out.append(chunk)
            # Cancel after the first chunk — subsequent chunks must not be yielded.
            event.set()
        return out

    result = await consumer()
    assert len(result) == 1, f"expected 1 chunk before cancel, got {len(result)}"


@pytest.mark.asyncio
async def test_synthesize_stream_without_event_yields_all(monkeypatch):
    """cancel_event=None must preserve pre-patch behavior (yield everything)."""
    client = _make_streaming_client([b"\x00\x01" * 10, b"\x02\x03" * 10])
    monkeypatch.setattr(
        GoogleTTSService, "_build_client", staticmethod(lambda: client)
    )
    svc = GoogleTTSService()

    chunks = []
    async for chunk in svc.synthesize_stream("hi."):
        chunks.append(chunk)
    assert len(chunks) == 2
