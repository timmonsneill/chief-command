"""Track B #7 — GoogleSTTService.transcribe_stream silence timeout.

Verifies:
  - A stream that never sends is_final gets its best interim surfaced
    after the silence window expires.
  - A stream that DOES send is_final yields that final and doesn't leak
    interim fallback on top.
  - A stream that ends cleanly (StopAsyncIteration) without is_final
    still surfaces the best interim before returning.
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

        class _Kw:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class _ExplicitDecodingConfig(_Kw):
            class AudioEncoding:
                LINEAR16 = "LINEAR16"

        cloud_speech_mod.ExplicitDecodingConfig = _ExplicitDecodingConfig
        cloud_speech_mod.RecognitionFeatures = _Kw
        cloud_speech_mod.RecognitionConfig = _Kw
        cloud_speech_mod.StreamingRecognitionFeatures = _Kw
        cloud_speech_mod.StreamingRecognitionConfig = _Kw
        cloud_speech_mod.RecognizeRequest = _Kw
        cloud_speech_mod.StreamingRecognizeRequest = _Kw

        types_mod.cloud_speech = cloud_speech_mod  # type: ignore[attr-defined]
        speech_v2.types = types_mod  # type: ignore[attr-defined]

        sys.modules["google.cloud.speech_v2"] = speech_v2
        sys.modules["google.cloud.speech_v2.types"] = types_mod
        sys.modules["google.cloud.speech_v2.types.cloud_speech"] = cloud_speech_mod
        cloud_pkg.speech_v2 = speech_v2  # type: ignore[attr-defined]


_install_google_stubs()

from services.stt_google import GoogleSTTService  # noqa: E402


def _result(transcript: str, is_final: bool):
    alt = MagicMock()
    alt.transcript = transcript
    r = MagicMock()
    r.alternatives = [alt]
    r.is_final = is_final
    return r


def _response(results):
    r = MagicMock()
    r.results = results
    return r


def _make_client(response_script):
    """response_script is a list of (response, delay_before_next_s)."""

    class _Responses:
        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            for item, delay in response_script:
                if delay > 0:
                    await asyncio.sleep(delay)
                yield item

    async def streaming_recognize(requests=None):  # noqa: ARG001
        return _Responses()

    client = MagicMock()
    client.streaming_recognize = streaming_recognize
    return client


async def _empty_audio_iter():
    # We don't actually care — the stub `streaming_recognize` ignores it.
    if False:
        yield b""


@pytest.mark.asyncio
async def test_silence_timeout_surfaces_best_interim(monkeypatch):
    """Interim arrives, then Google goes silent — we surface the interim."""
    script = [
        (_response([_result("hello chief", is_final=False)]), 0.0),
        # Then a LONG gap — no more responses; timeout should fire.
        (_response([]), 5.0),  # never reached in 100ms timeout test
    ]
    client = _make_client(script)
    monkeypatch.setattr(
        GoogleSTTService, "_build_client", staticmethod(lambda: client)
    )
    # Also provide a project so recognizer_path() works.
    import os
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test-proj")

    svc = GoogleSTTService()
    results = []
    async for text in svc.transcribe_stream(
        _empty_audio_iter(), silence_timeout_ms=100
    ):
        results.append(text)

    assert results == ["hello chief"]


@pytest.mark.asyncio
async def test_final_is_emitted_and_interim_not_double_emitted(monkeypatch):
    """When a final arrives, only the final surfaces (not the interim on top)."""
    script = [
        (_response([_result("hel", is_final=False)]), 0.0),
        (_response([_result("hello chief", is_final=True)]), 0.01),
    ]
    client = _make_client(script)
    monkeypatch.setattr(
        GoogleSTTService, "_build_client", staticmethod(lambda: client)
    )
    import os
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test-proj")

    svc = GoogleSTTService()
    results = []
    async for text in svc.transcribe_stream(
        _empty_audio_iter(), silence_timeout_ms=1000
    ):
        results.append(text)

    assert results == ["hello chief"]


@pytest.mark.asyncio
async def test_clean_stream_end_surfaces_buffered_interim(monkeypatch):
    """Google closes without is_final — we still surface the interim we saw."""
    script = [
        (_response([_result("hel", is_final=False)]), 0.0),
        (_response([_result("hello", is_final=False)]), 0.01),
        # No more items; async iterator will StopAsyncIteration.
    ]
    client = _make_client(script)
    monkeypatch.setattr(
        GoogleSTTService, "_build_client", staticmethod(lambda: client)
    )
    import os
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test-proj")

    svc = GoogleSTTService()
    results = []
    async for text in svc.transcribe_stream(
        _empty_audio_iter(), silence_timeout_ms=1000
    ):
        results.append(text)

    assert results == ["hello"]


@pytest.mark.asyncio
async def test_no_interim_no_final_timeout_yields_nothing(monkeypatch):
    """Silence with no interim at all yields no output (and doesn't hang)."""
    script = [
        # Never emit anything — but have ONE response so the stream is active
        # then a gap. Simulate by first response carrying no alternatives.
        (_response([]), 5.0),
    ]
    client = _make_client(script)
    monkeypatch.setattr(
        GoogleSTTService, "_build_client", staticmethod(lambda: client)
    )
    import os
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test-proj")

    svc = GoogleSTTService()
    results = []
    async for text in svc.transcribe_stream(
        _empty_audio_iter(), silence_timeout_ms=50
    ):
        results.append(text)

    assert results == []
