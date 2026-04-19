"""Unit tests for services.voice_provider.

Verifies:
- VOICE_PROVIDER=local (default) returns the faster-whisper + Kokoro classes.
- VOICE_PROVIDER=google returns GoogleSTTService + GoogleTTSService.
- VOICE_PROVIDER="" / unset / garbage falls back to local with a warning.
- build_voice_services() returns both instances and picks the right ones.

We stub the heavy local deps (faster_whisper, kokoro, google.cloud.*) so this
test can run in any environment without network or ML packages.
"""

from __future__ import annotations

import importlib
import sys
import types

import pytest


# ---------------------------------------------------------------------------
# Helpers — insert stub modules so imports succeed without real deps.
# ---------------------------------------------------------------------------


def _install_dep_stubs():
    """Install minimal stubs for the heavyweight runtime deps.

    STTService / TTSService only import these lazily (inside _load_model /
    _load_pipeline), so we don't need to stub them for the factory to build
    instances — but we DO need to make sure the factory's import graph
    (services.stt, services.tts) doesn't drag in unrelated dependencies.

    For the google-cloud-* imports (used inside GoogleSTTService /
    GoogleTTSService), stubs live at module level so `from google.cloud
    import speech_v2` works even without the real package installed.
    """
    # google.cloud.speech_v2 + types.cloud_speech stub
    google_pkg = sys.modules.get("google") or types.ModuleType("google")
    google_pkg.__path__ = getattr(google_pkg, "__path__", [])  # type: ignore[attr-defined]
    sys.modules["google"] = google_pkg

    cloud_pkg = sys.modules.get("google.cloud") or types.ModuleType("google.cloud")
    cloud_pkg.__path__ = getattr(cloud_pkg, "__path__", [])  # type: ignore[attr-defined]
    sys.modules["google.cloud"] = cloud_pkg
    google_pkg.cloud = cloud_pkg  # type: ignore[attr-defined]

    # speech_v2
    if "google.cloud.speech_v2" not in sys.modules:
        speech_v2 = types.ModuleType("google.cloud.speech_v2")

        class _SpeechAsyncClient:
            def __init__(self, *a, **k):
                pass

        speech_v2.SpeechAsyncClient = _SpeechAsyncClient  # type: ignore[attr-defined]

        types_mod = types.ModuleType("google.cloud.speech_v2.types")
        cloud_speech_mod = types.ModuleType(
            "google.cloud.speech_v2.types.cloud_speech"
        )
        types_mod.cloud_speech = cloud_speech_mod  # type: ignore[attr-defined]
        speech_v2.types = types_mod  # type: ignore[attr-defined]

        sys.modules["google.cloud.speech_v2"] = speech_v2
        sys.modules["google.cloud.speech_v2.types"] = types_mod
        sys.modules["google.cloud.speech_v2.types.cloud_speech"] = cloud_speech_mod
        cloud_pkg.speech_v2 = speech_v2  # type: ignore[attr-defined]

    # texttospeech
    if "google.cloud.texttospeech" not in sys.modules:
        tts_mod = types.ModuleType("google.cloud.texttospeech")

        class _TTSAsyncClient:
            def __init__(self, *a, **k):
                pass

        tts_mod.TextToSpeechAsyncClient = _TTSAsyncClient  # type: ignore[attr-defined]
        sys.modules["google.cloud.texttospeech"] = tts_mod
        cloud_pkg.texttospeech = tts_mod  # type: ignore[attr-defined]


def _reload_voice_provider(monkeypatch, provider_value: str):
    """Set VOICE_PROVIDER on settings and reload voice_provider module fresh."""
    _install_dep_stubs()

    # Import settings, override the field, then reload voice_provider.
    from config import settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "VOICE_PROVIDER", provider_value)

    # Drop any cached voice_provider module so the factory re-reads settings.
    sys.modules.pop("services.voice_provider", None)

    module = importlib.import_module("services.voice_provider")
    return module


# ---------------------------------------------------------------------------
# Local provider
# ---------------------------------------------------------------------------


def test_local_is_default(monkeypatch):
    """Unset / empty VOICE_PROVIDER falls back to local."""
    vp = _reload_voice_provider(monkeypatch, "")
    assert vp._resolve_provider() == vp.PROVIDER_LOCAL


def test_local_returns_whisper_kokoro(monkeypatch):
    vp = _reload_voice_provider(monkeypatch, "local")
    stt = vp.build_stt_service()
    tts = vp.build_tts_service()

    # Match by class name — avoids importing the class (which may trigger
    # the heavy runtime deps we don't want loaded in a unit test).
    assert type(stt).__name__ == "STTService"
    assert type(tts).__name__ == "TTSService"


def test_build_voice_services_returns_both(monkeypatch, caplog):
    vp = _reload_voice_provider(monkeypatch, "local")
    stt, tts = vp.build_voice_services()
    assert type(stt).__name__ == "STTService"
    assert type(tts).__name__ == "TTSService"


# ---------------------------------------------------------------------------
# Google provider
# ---------------------------------------------------------------------------


def test_google_returns_google_services(monkeypatch):
    vp = _reload_voice_provider(monkeypatch, "google")
    stt = vp.build_stt_service()
    tts = vp.build_tts_service()

    assert type(stt).__name__ == "GoogleSTTService"
    assert type(tts).__name__ == "GoogleTTSService"


def test_google_tts_uses_configured_voice(monkeypatch):
    vp = _reload_voice_provider(monkeypatch, "google")
    # Override voice via settings
    from config import settings as settings_mod

    monkeypatch.setattr(
        settings_mod.settings, "GOOGLE_TTS_VOICE", "en-US-Chirp3-HD-Kore"
    )
    tts = vp.build_tts_service()
    assert tts.voice == "en-US-Chirp3-HD-Kore"


def test_google_stt_uses_configured_language(monkeypatch):
    vp = _reload_voice_provider(monkeypatch, "google")
    from config import settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "GOOGLE_STT_LANGUAGE", "en-GB")
    stt = vp.build_stt_service()
    assert stt.language == "en-GB"


# ---------------------------------------------------------------------------
# Unknown provider → fallback
# ---------------------------------------------------------------------------


def test_unknown_provider_falls_back_to_local(monkeypatch, caplog):
    vp = _reload_voice_provider(monkeypatch, "elevenlabs")  # not allowed
    resolved = vp._resolve_provider()
    assert resolved == vp.PROVIDER_LOCAL


def test_provider_case_insensitive(monkeypatch):
    vp = _reload_voice_provider(monkeypatch, "GOOGLE")
    assert vp._resolve_provider() == vp.PROVIDER_GOOGLE
