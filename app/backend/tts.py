"""
TTS Engine  -- Kokoro TTS via ONNX Runtime
==========================================
Lazy-loading wrapper around kokoro-onnx. Synthesizes text to base64-encoded WAV
audio for transport over the JSONL IPC bridge.

If kokoro-onnx is not installed or model files are missing, is_available() returns
False and the Electron shell falls back to browser speechSynthesis.
"""

import base64
import io
import logging
import re
import sys
from pathlib import Path
from runtime_paths import KOKORO_MODEL_PATH, KOKORO_VOICES_PATH

logger = logging.getLogger(__name__)

_DEFAULT_MODEL_PATH = str(KOKORO_MODEL_PATH)
_DEFAULT_VOICES_PATH = str(KOKORO_VOICES_PATH)


def _patch_windows_espeak_loader() -> None:
    """Avoid phonemizer's temp-DLL copy path on Windows.

    phonemizer duplicates espeak-ng.dll into a fresh temp directory for every
    EspeakAPI instance. In this environment the copied DLL loads with
    PermissionError, while the original packaged DLL loads correctly. We only
    need one in-process TTS backend, so loading the original DLL directly is
    sufficient and avoids the temp-directory failure.
    """
    if sys.platform != "win32":
        return

    try:
        import ctypes
        from phonemizer.backend.espeak import api as espeak_api_module
    except Exception:
        return

    if getattr(espeak_api_module.EspeakAPI, "_open_companion_no_temp_patch", False):
        return

    def _init_without_temp_copy(self, library, data_path):
        self._library = None
        self._tempdir = None

        if data_path is not None:
            data_path = str(data_path).encode("utf-8")

        try:
            espeak = ctypes.cdll.LoadLibrary(str(library))
            library_path = self._shared_library_path(espeak)
            del espeak
        except OSError as error:
            raise RuntimeError(f"failed to load espeak library: {str(error)}") from None

        self._library = ctypes.cdll.LoadLibrary(str(library_path))
        try:
            if self._library.espeak_Initialize(0x02, 0, data_path, 0) <= 0:
                raise RuntimeError("failed to initialize espeak shared library")
        except AttributeError:
            raise RuntimeError("failed to load espeak library") from None

        self._library_path = library_path

    espeak_api_module.EspeakAPI.__init__ = _init_without_temp_copy
    espeak_api_module.EspeakAPI._open_companion_no_temp_patch = True

def _strip_gemma_token_bleed(s: str) -> str:
    """Strip all Gemma 4 special token bleed variants. Keep in sync with wrapper.py."""
    s = re.sub(r"<\|tool_call>.*?<tool_call\|>", "", s, flags=re.DOTALL)
    s = re.sub(r"call:\w+\{[^}]*\}<tool_call\|>", "", s, flags=re.DOTALL)
    s = re.sub(r"<\|tool_response>.*?<tool_response\|>", "", s, flags=re.DOTALL)
    s = re.sub(r"<\|tool>.*?<tool\|>", "", s, flags=re.DOTALL)
    s = re.sub(r'<\|tool_call>|<tool_call\|>|<\|tool_response>|<tool_response\|>|<\|tool>|<tool\|>|<\|"\|>', "", s)
    return s


def _prepare_for_speech(text: str) -> str:
    """Strip markdown and code blocks so TTS only speaks natural prose."""
    # 0. Gemma 4 special token bleed -- must run before markdown stripping
    text = _strip_gemma_token_bleed(text)
    # Remove fenced code blocks entirely -- replace with brief spoken label
    text = re.sub(r'```[\w]*\n.*?```', '[code block]', text, flags=re.DOTALL)
    # Remove inline code  -- keep short content, drop long commands
    text = re.sub(r'`[^`\n]{1,40}`', lambda m: m.group(0)[1:-1], text)
    text = re.sub(r'`[^`\n]{41,}`', '', text)
    # Remove markdown headers
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Remove bold/italic markers
    text = re.sub(r'\*{1,3}([^*\n]+)\*{1,3}', r'\1', text)
    text = re.sub(r'_{1,2}([^_\n]+)_{1,2}', r'\1', text)
    # Remove markdown links, keep text
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # Remove horizontal rules
    text = re.sub(r'^[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
    # Collapse multiple blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


class KokoroTTS:
    """
    Kokoro TTS engine with lazy model loading.

    Config fields (from config.json voice block):
    - kokoro_voice: voice ID string (default "af_bella")
    - tts_speed: float playback speed (default 1.0)
    - kokoro_model_path: path to .onnx model file
    - kokoro_voices_path: path to voices .bin file
    """

    def __init__(self, config: dict):
        voice_config = config.get("voice", {})
        self.voice = voice_config.get("kokoro_voice", "af_bella")
        self.speed = float(voice_config.get("tts_speed", 1.0))
        self.model_path = str(voice_config.get("kokoro_model_path") or _DEFAULT_MODEL_PATH)
        self.voices_path = str(voice_config.get("kokoro_voices_path") or _DEFAULT_VOICES_PATH)

        self._kokoro = None
        self._available = None

    def is_available(self) -> bool:
        """Check if kokoro-onnx is importable and model files exist."""
        if self._available is not None:
            return self._available

        try:
            import kokoro_onnx  # noqa: F401
        except Exception:
            logger.info("kokoro-onnx not importable  -- backend TTS unavailable", exc_info=True)
            self._available = False
            return False

        if not Path(self.model_path).is_file():
            logger.info("Kokoro model not found at %s  -- backend TTS unavailable", self.model_path)
            self._available = False
            return False

        if not Path(self.voices_path).is_file():
            logger.info("Kokoro voices not found at %s  -- backend TTS unavailable", self.voices_path)
            self._available = False
            return False

        self._available = True
        return True

    def _ensure_loaded(self):
        """Load the Kokoro model on first use."""
        if self._kokoro is not None:
            return

        _patch_windows_espeak_loader()
        from kokoro_onnx import Kokoro

        logger.info("Loading Kokoro TTS model from %s", self.model_path)
        self._kokoro = Kokoro(self.model_path, self.voices_path)
        logger.info("Kokoro TTS model loaded successfully")

    def synthesize(self, text: str) -> tuple[str, int] | None:
        """
        Synthesize text to speech.

        Returns (base64_wav_string, sample_rate) on success, or None on failure.
        The WAV is 16-bit PCM suitable for playback via Web Audio API.
        """
        if not text or not self.is_available():
            return None

        text = _prepare_for_speech(text)
        if not text:
            return None

        try:
            import numpy as np
            import soundfile as sf

            self._ensure_loaded()

            # kokoro-onnx returns (samples_ndarray, sample_rate)
            samples, sample_rate = self._kokoro.create(
                text,
                voice=self.voice,
                speed=self.speed,
            )

            # Write to in-memory WAV buffer
            buffer = io.BytesIO()
            # Ensure float32 samples are in [-1, 1] range for 16-bit PCM output
            samples = np.clip(samples, -1.0, 1.0)
            sf.write(buffer, samples, sample_rate, format="WAV", subtype="PCM_16")
            wav_bytes = buffer.getvalue()

            b64 = base64.b64encode(wav_bytes).decode("ascii")
            logger.info(
                "Synthesized %d chars â†’ %d bytes WAV (%.1fs at %dHz)",
                len(text),
                len(wav_bytes),
                len(samples) / sample_rate,
                sample_rate,
            )
            return b64, sample_rate

        except Exception as exc:
            logger.error("Kokoro TTS synthesis failed: %s", exc)
            return None
