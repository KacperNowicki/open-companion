"""
STT Engine — whisper.cpp via pywhispercpp
==========================================
Lazy-loading wrapper around pywhispercpp. Transcribes WAV audio bytes to text.

If pywhispercpp is not installed, is_available() returns False and the Electron
shell falls back to browser SpeechRecognition.

pywhispercpp auto-downloads GGML model files on first use, so no manual model
placement is required unless the user wants a specific model path.
"""

import base64
import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class WhisperSTT:
    """
    whisper.cpp STT engine with lazy model loading.

    Config fields (from config.json voice block):
    - whisper_model: model name or path (default "base.en")
      Names like "base.en", "tiny.en", "small.en" are auto-downloaded.
      Absolute paths to .bin files are used directly.
    """

    def __init__(self, config: dict):
        voice_config = config.get("voice", {})
        self.model_name = voice_config.get("whisper_model", "base.en")
        self._model = None
        self._available = None

    def is_available(self) -> bool:
        """Check if pywhispercpp is importable."""
        if self._available is not None:
            return self._available

        try:
            import pywhispercpp  # noqa: F401
            self._available = True
        except Exception:
            logger.info("pywhispercpp not importable — backend STT unavailable", exc_info=True)
            self._available = False

        return self._available

    def _ensure_loaded(self):
        """Load the whisper model on first use. May download the model."""
        if self._model is not None:
            return

        from pywhispercpp.model import Model

        logger.info("Loading whisper model: %s (may download on first use)", self.model_name)
        self._model = Model(self.model_name)
        logger.info("Whisper model loaded successfully")

    def transcribe(self, wav_bytes: bytes) -> str | None:
        """
        Transcribe WAV audio bytes to text.

        Writes bytes to a temporary file, runs whisper transcription, and
        cleans up the temp file afterward.

        Returns the transcript string, or None on failure.
        """
        if not wav_bytes or not self.is_available():
            return None

        tmp_path = None
        try:
            self._ensure_loaded()

            # Write WAV bytes to a temp file for pywhispercpp
            with tempfile.NamedTemporaryFile(
                suffix=".wav", delete=False, prefix="oc_stt_"
            ) as tmp:
                tmp.write(wav_bytes)
                tmp_path = tmp.name

            logger.info("Transcribing %d bytes of audio from %s", len(wav_bytes), tmp_path)
            segments = self._model.transcribe(tmp_path)
            transcript = " ".join(seg.text.strip() for seg in segments if seg.text.strip())

            if transcript:
                logger.info("Transcription result: %s", transcript[:200])
            else:
                logger.info("Transcription returned empty result")

            return transcript or None

        except Exception as exc:
            logger.error("Whisper STT transcription failed: %s", exc)
            return None

        finally:
            # Clean up temp file
            if tmp_path:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except OSError:
                    pass

    def transcribe_b64(self, audio_b64: str) -> str | None:
        """
        Transcribe base64-encoded WAV audio to text.

        Convenience wrapper that decodes base64 before calling transcribe().
        """
        if not audio_b64:
            return None

        try:
            wav_bytes = base64.b64decode(audio_b64)
        except Exception as exc:
            logger.error("Failed to decode base64 audio: %s", exc)
            return None

        return self.transcribe(wav_bytes)
