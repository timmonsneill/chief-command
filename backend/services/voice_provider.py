"""Voice provider factory — picks STT/TTS backends from settings.

Contract:
    VOICE_PROVIDER=local   (default) -> faster-whisper + Kokoro
    VOICE_PROVIDER=google            -> Google Cloud Speech v2 + Cloud TTS Chirp3

The factory returns *instances*, not classes. It NEVER eagerly imports the
Google client libraries when in local mode — Google imports only happen when
the user flips VOICE_PROVIDER=google AND the instance is used. That keeps
`main` branch usable with no google-cloud-* packages installed.

Used by services/__init__.py to build the singleton service instances.
"""

from __future__ import annotations

import logging
from typing import Tuple

from config.settings import settings

logger = logging.getLogger(__name__)

PROVIDER_LOCAL = "local"
PROVIDER_GOOGLE = "google"
VALID_PROVIDERS = {PROVIDER_LOCAL, PROVIDER_GOOGLE}


def _resolve_provider() -> str:
    name = (getattr(settings, "VOICE_PROVIDER", PROVIDER_LOCAL) or PROVIDER_LOCAL).strip().lower()
    if name not in VALID_PROVIDERS:
        logger.warning(
            "Unknown VOICE_PROVIDER='%s'; falling back to '%s'. Valid: %s",
            name,
            PROVIDER_LOCAL,
            sorted(VALID_PROVIDERS),
        )
        return PROVIDER_LOCAL
    return name


def build_stt_service():
    """Return an STT instance matching the configured provider."""
    provider = _resolve_provider()
    if provider == PROVIDER_GOOGLE:
        from services.stt_google import GoogleSTTService

        language = getattr(settings, "GOOGLE_STT_LANGUAGE", "en-US") or "en-US"
        return GoogleSTTService(language=language)

    from services.stt import STTService

    return STTService()


def build_tts_service():
    """Return a TTS instance matching the configured provider."""
    provider = _resolve_provider()
    if provider == PROVIDER_GOOGLE:
        from services.tts_google import GoogleTTSService, DEFAULT_VOICE

        voice = getattr(settings, "GOOGLE_TTS_VOICE", DEFAULT_VOICE) or DEFAULT_VOICE
        return GoogleTTSService(voice=voice)

    from services.tts import TTSService

    return TTSService()


def build_voice_services() -> Tuple[object, object]:
    """Build and return (stt_service, tts_service) in one call.

    Logs which provider is active at startup so it's obvious in the logs
    whether the owner is running local or google.
    """
    provider = _resolve_provider()
    stt = build_stt_service()
    tts = build_tts_service()

    if provider == PROVIDER_GOOGLE:
        creds_path = getattr(settings, "GOOGLE_APPLICATION_CREDENTIALS", None)
        logger.info(
            "Voice provider: google (STT v2 streaming + Chirp3 HD TTS). "
            "Credentials: %s",
            creds_path or "<unset — Google client will fail at first call>",
        )
    else:
        logger.info("Voice provider: local (faster-whisper + Kokoro)")

    return stt, tts


__all__ = [
    "PROVIDER_LOCAL",
    "PROVIDER_GOOGLE",
    "VALID_PROVIDERS",
    "build_stt_service",
    "build_tts_service",
    "build_voice_services",
]
