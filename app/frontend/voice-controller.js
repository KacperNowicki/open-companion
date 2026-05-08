/**
 * Voice Controller — Dual-mode voice I/O
 * ========================================
 * Wraps both browser speech APIs (SpeechRecognition + speechSynthesis) and the
 * backend voice engines (Kokoro TTS + whisper.cpp STT). The active mode is
 * determined by config + backend capabilities reported in the ready event.
 *
 * When backend engines are available and configured, mic capture produces a
 * base64 WAV sent to the Python backend for transcription, and TTS audio
 * arrives as base64 WAV in assistant_message events. When backend engines are
 * unavailable, the controller falls back to browser APIs transparently.
 */

import { createAudioRecorder } from "./audio-recorder.js";

function normalizeText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

export function createVoiceController(callbacks = {}) {
  const SpeechRecognitionClass = window.SpeechRecognition || window.webkitSpeechRecognition || null;
  const speechSynthesisApi = window.speechSynthesis || null;

  let recognition = null;
  let voiceConfig = {};
  let companionName = "Companion";
  let isListening = false;
  let isSpeaking = false;
  let finalTranscript = "";

  // Backend capability flags — set from the ready event's voice_capabilities
  let backendTtsAvailable = false;
  let backendSttAvailable = false;

  // Derived mode flags — set in applyConfig
  let useBackendStt = false;
  let useBackendTts = false;

  // Audio recorder for backend STT
  const audioRecorder = createAudioRecorder({
    onComplete: (base64Wav) => {
      setListening(false);
      callbacks.onInterimTranscript?.("");

      if (base64Wav) {
        emitStatus("Voice captured. Sending for transcription.");
        callbacks.onAudioCaptured?.(base64Wav);
      } else {
        emitStatus("No audio was captured. Text input is still available.");
      }
    },
    onError: (error) => {
      setListening(false);
      callbacks.onInterimTranscript?.("");
      emitError(error.message || "Audio recording failed.");
    },
  });

  function emitStatus(note = "") {
    callbacks.onStatus?.(note);
  }

  function emitError(message) {
    callbacks.onError?.(new Error(message));
  }

  function setListening(nextValue) {
    if (isListening === nextValue) {
      return;
    }

    isListening = nextValue;
    callbacks.onListeningChange?.(isListening);
  }

  function setSpeaking(nextValue) {
    if (isSpeaking === nextValue) {
      return;
    }

    isSpeaking = nextValue;
    callbacks.onSpeakingChange?.(isSpeaking);
  }

  function canUseStt() {
    if (!voiceConfig.stt_enabled) {
      return false;
    }
    if (useBackendStt) {
      return true;
    }
    return Boolean(SpeechRecognitionClass);
  }

  function canUseTts() {
    if (!voiceConfig.tts_enabled) {
      return false;
    }
    if (useBackendTts) {
      return true;
    }
    return Boolean(speechSynthesisApi);
  }

  // =========================================================================
  // Browser STT (fallback)
  // =========================================================================

  function buildRecognition() {
    if (!SpeechRecognitionClass) {
      return null;
    }

    const nextRecognition = new SpeechRecognitionClass();
    nextRecognition.continuous = false;
    nextRecognition.interimResults = true;
    nextRecognition.maxAlternatives = 1;
    nextRecognition.lang = voiceConfig.language || "en-US";

    nextRecognition.onstart = () => {
      setListening(true);
      emitStatus("Listening. Tap again to stop.");
    };

    nextRecognition.onresult = (event) => {
      const interimParts = [];

      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const transcript = normalizeText(event.results[index][0]?.transcript);
        if (!transcript) {
          continue;
        }

        if (event.results[index].isFinal) {
          finalTranscript = normalizeText(`${finalTranscript} ${transcript}`);
        } else {
          interimParts.push(transcript);
        }
      }

      const interimTranscript = normalizeText(`${finalTranscript} ${interimParts.join(" ")}`);
      callbacks.onInterimTranscript?.(interimTranscript);
    };

    nextRecognition.onerror = (event) => {
      const reason = normalizeText(event.error || "speech recognition failed");
      setListening(false);

      if (reason === "aborted") {
        emitStatus("Voice capture stopped.");
        callbacks.onInterimTranscript?.("");
        return;
      }

      emitError(`Speech recognition failed: ${reason}`);
      callbacks.onInterimTranscript?.("");
    };

    nextRecognition.onend = () => {
      const transcript = finalTranscript;
      finalTranscript = "";
      setListening(false);
      callbacks.onInterimTranscript?.("");

      if (transcript) {
        emitStatus("Voice captured. Sending to Nova.");
        callbacks.onFinalTranscript?.(transcript);
      } else {
        emitStatus("No speech captured. Text input is still available.");
      }
    };

    return nextRecognition;
  }

  // =========================================================================
  // Config
  // =========================================================================

  function applyConfig(nextVoiceConfig = {}, nextCompanionName = "Companion", capabilities = {}) {
    voiceConfig = nextVoiceConfig || {};
    companionName = nextCompanionName || companionName;

    // Update backend capability flags
    backendTtsAvailable = Boolean(capabilities.backend_tts);
    backendSttAvailable = Boolean(capabilities.backend_stt);

    // Derive active modes
    useBackendStt = backendSttAvailable && voiceConfig.stt_provider === "whisper";
    useBackendTts = backendTtsAvailable && voiceConfig.tts_provider === "kokoro";

    // Rebuild browser recognition if needed for fallback
    if (recognition) {
      recognition.onstart = null;
      recognition.onresult = null;
      recognition.onerror = null;
      recognition.onend = null;
      recognition = null;
    }

    if (!useBackendStt && SpeechRecognitionClass) {
      recognition = buildRecognition();
    }
  }

  // =========================================================================
  // STT — start/stop listening
  // =========================================================================

  async function startListening() {
    if (!canUseStt()) {
      if (!voiceConfig.stt_enabled) {
        emitStatus("Voice input is disabled in config.");
      } else {
        emitError("Speech recognition is not available in this Electron runtime.");
      }
      return false;
    }

    cancelSpeech();
    finalTranscript = "";
    callbacks.onInterimTranscript?.("");

    if (useBackendStt) {
      try {
        const started = await audioRecorder.startRecording();
        if (!started) {
          return false;
        }
        setListening(true);
        emitStatus("Recording. Tap again to stop.");
        callbacks.onInterimTranscript?.("Recording...");
        return true;
      } catch (error) {
        emitError(error.message || "Microphone capture could not start.");
        return false;
      }
    }

    // Browser fallback
    try {
      recognition ??= buildRecognition();
      recognition.lang = voiceConfig.language || "en-US";
      recognition.start();
      return true;
    } catch (error) {
      emitError(error.message || "Speech recognition could not start.");
      return false;
    }
  }

  function stopListening() {
    if (!isListening) {
      return;
    }

    if (useBackendStt) {
      audioRecorder.stopRecording();
      // setListening(false) happens in onComplete/onError callbacks
      return;
    }

    if (recognition) {
      recognition.stop();
    }
  }

  // =========================================================================
  // TTS — speak text or play backend audio
  // =========================================================================

  function cancelSpeech() {
    callbacks.onCancelBackendAudio?.();

    // Cancel browser TTS
    if (speechSynthesisApi && (speechSynthesisApi.speaking || speechSynthesisApi.pending)) {
      speechSynthesisApi.cancel();
    }

    setSpeaking(false);
  }

  function speak(text) {
    // When backend TTS is active, speech is played via playAudioB64 instead.
    // This method only handles the browser TTS fallback path.
    if (useBackendTts) {
      return false;
    }

    const content = normalizeText(text);
    if (!content || !canUseTts()) {
      return false;
    }

    cancelSpeech();

    const utterance = new SpeechSynthesisUtterance(content);
    utterance.lang = voiceConfig.language || "en-US";
    utterance.rate = Number.isFinite(voiceConfig.tts_rate) ? voiceConfig.tts_rate : 1;
    utterance.pitch = Number.isFinite(voiceConfig.tts_pitch) ? voiceConfig.tts_pitch : 1;
    utterance.volume = Number.isFinite(voiceConfig.tts_volume) ? voiceConfig.tts_volume : 1;

    utterance.onstart = () => {
      setSpeaking(true);
      emitStatus(`${companionName} is speaking.`);
    };

    utterance.onend = () => {
      setSpeaking(false);
      emitStatus("");
    };

    utterance.onerror = () => {
      setSpeaking(false);
      emitError("Speech playback failed.");
    };

    speechSynthesisApi.speak(utterance);
    return true;
  }

  /**
   * Forward base64-encoded WAV audio from the backend TTS engine to the
   * renderer-owned avatar/talking-head playback path.
   */
  async function playAudioB64(base64Wav, text = "") {
    if (!base64Wav) {
      return false;
    }

    cancelSpeech();

    try {
      setSpeaking(true);
      emitStatus(`${companionName} is speaking.`);
      const handled = await callbacks.onBackendAudio?.(base64Wav, text);
      setSpeaking(false);
      emitStatus("");
      if (handled === false) {
        emitError("Audio playback failed.");
        return false;
      }
      return true;
    } catch (error) {
      setSpeaking(false);
      emitError(`Audio playback failed: ${error.message}`);
      return false;
    }
  }

  return {
    applyConfig,
    startListening,
    stopListening,
    cancelSpeech,
    speak,
    playAudioB64,
    canUseStt,
    canUseTts,
    isListening: () => isListening,
    isSpeaking: () => isSpeaking,
  };
}
