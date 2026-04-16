"""Audio format conversion utilities — ffmpeg-based transforms for browser ↔ whisper pipeline."""

import asyncio
import io
import logging
import struct
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)


async def convert_webm_to_wav(webm_bytes: bytes) -> bytes:
    """Convert WebM/Opus audio (from browser MediaRecorder) to WAV 16kHz mono for whisper.

    Uses ffmpeg subprocess. Runs in a thread to avoid blocking the event loop.
    """
    return await asyncio.to_thread(_convert_webm_to_wav_sync, webm_bytes)


def _convert_webm_to_wav_sync(webm_bytes: bytes) -> bytes:
    """Synchronous WebM → WAV conversion via ffmpeg."""
    if not webm_bytes:
        raise ValueError("Empty audio input")

    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-i", "pipe:0",       # read from stdin
                "-ar", "16000",        # 16kHz sample rate
                "-ac", "1",            # mono
                "-f", "wav",           # WAV output format
                "-acodec", "pcm_s16le",
                "pipe:1",             # write to stdout
            ],
            input=webm_bytes,
            capture_output=True,
            timeout=30,
        )
    except FileNotFoundError:
        raise RuntimeError("ffmpeg not found — install it with: brew install ffmpeg")
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffmpeg conversion timed out after 30 seconds")

    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")[:500]
        raise RuntimeError(f"ffmpeg WebM→WAV conversion failed: {stderr}")

    logger.debug("Converted WebM to WAV: %d → %d bytes", len(webm_bytes), len(result.stdout))
    return result.stdout


async def convert_wav_to_mp3(wav_bytes: bytes, bitrate: str = "128k") -> bytes:
    """Convert WAV audio to MP3 for smaller transfer over cellular.

    Uses ffmpeg subprocess. Runs in a thread to avoid blocking the event loop.
    """
    return await asyncio.to_thread(_convert_wav_to_mp3_sync, wav_bytes, bitrate)


def _convert_wav_to_mp3_sync(wav_bytes: bytes, bitrate: str = "128k") -> bytes:
    """Synchronous WAV → MP3 conversion via ffmpeg."""
    if not wav_bytes:
        raise ValueError("Empty audio input")

    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-i", "pipe:0",
                "-ab", bitrate,
                "-f", "mp3",
                "pipe:1",
            ],
            input=wav_bytes,
            capture_output=True,
            timeout=30,
        )
    except FileNotFoundError:
        raise RuntimeError("ffmpeg not found — install it with: brew install ffmpeg")
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffmpeg conversion timed out after 30 seconds")

    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")[:500]
        raise RuntimeError(f"ffmpeg WAV→MP3 conversion failed: {stderr}")

    logger.debug("Converted WAV to MP3: %d → %d bytes", len(wav_bytes), len(result.stdout))
    return result.stdout


async def get_audio_duration(audio_bytes: bytes) -> float:
    """Return duration in seconds for WAV audio bytes."""
    return await asyncio.to_thread(_get_audio_duration_sync, audio_bytes)


def _get_audio_duration_sync(audio_bytes: bytes) -> float:
    """Synchronous audio duration calculation."""
    if not audio_bytes:
        raise ValueError("Empty audio input")

    try:
        with io.BytesIO(audio_bytes) as buf:
            info = sf.info(buf)
            return info.duration
    except Exception:
        # Fallback: try ffprobe
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    "-i", "pipe:0",
                ],
                input=audio_bytes,
                capture_output=True,
                timeout=10,
            )
            if result.returncode == 0:
                return float(result.stdout.decode().strip())
        except Exception:
            pass
        raise RuntimeError("Could not determine audio duration")


async def normalize_audio(audio_bytes: bytes, target_db: float = -20.0) -> bytes:
    """Normalize volume levels for consistent playback.

    Adjusts audio to target_db LUFS using peak normalization.
    Returns WAV bytes.
    """
    return await asyncio.to_thread(_normalize_audio_sync, audio_bytes, target_db)


def _normalize_audio_sync(audio_bytes: bytes, target_db: float = -20.0) -> bytes:
    """Synchronous audio normalization."""
    if not audio_bytes:
        raise ValueError("Empty audio input")

    with io.BytesIO(audio_bytes) as buf:
        data, sample_rate = sf.read(buf, dtype="float32")

    # Calculate current peak amplitude
    peak = np.max(np.abs(data))
    if peak < 1e-6:
        logger.debug("Audio is silent, skipping normalization")
        return audio_bytes

    # Calculate gain needed to reach target dB
    current_db = 20.0 * np.log10(peak)
    gain_db = target_db - current_db
    gain = 10.0 ** (gain_db / 20.0)

    # Apply gain with clipping protection
    normalized = np.clip(data * gain, -1.0, 1.0)

    # Write back to WAV
    output = io.BytesIO()
    sf.write(output, normalized, sample_rate, format="WAV", subtype="PCM_16")
    result = output.getvalue()

    logger.debug(
        "Normalized audio: peak %.2f dB → %.2f dB (%d bytes)",
        current_db,
        target_db,
        len(result),
    )
    return result
