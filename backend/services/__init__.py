"""Backend services — singleton instances for STT, TTS, and auth."""

from services.auth import (
    create_token,
    hash_password,
    require_auth,
    verify_password,
    verify_token,
)
from services.audio_utils import (
    convert_webm_to_wav,
    convert_wav_to_mp3,
    get_audio_duration,
    normalize_audio,
)
from services.stt import STTService
from services.tts import TTSService
from services.voice_provider import build_voice_services

# ---------------------------------------------------------------------------
# Singleton service instances — imported by WebSocket handlers and routes.
# The factory picks local (faster-whisper + Kokoro) or google (Cloud Speech
# v2 + Chirp3 HD TTS) based on settings.VOICE_PROVIDER. Models/clients are
# lazy-loaded on first use, so creating these is instant either way.
# ---------------------------------------------------------------------------
stt_service, tts_service = build_voice_services()

__all__ = [
    # Service instances
    "stt_service",
    "tts_service",
    # STT / TTS classes (for testing or custom instances)
    "STTService",
    "TTSService",
    # Audio utilities
    "convert_webm_to_wav",
    "convert_wav_to_mp3",
    "get_audio_duration",
    "normalize_audio",
    # Auth (re-exported for convenience)
    "create_token",
    "hash_password",
    "require_auth",
    "verify_password",
    "verify_token",
]
