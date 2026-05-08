import { TalkingHead } from "@met4citizen/talkinghead";

const AVATAR_URL = new URL("./assets/talkinghead/avatar.glb", import.meta.url).href;
const AVATAR_ASSET_PATH = "app/frontend/assets/talkinghead/avatar.glb";
const DEFAULT_LANGUAGE = "en";
const SPEECH_END_PADDING_MS = 500;

function normalizeBase64Wav(value) {
  const text = String(value || "").trim();
  const commaIndex = text.indexOf(",");
  return commaIndex >= 0 && text.slice(0, commaIndex).includes("base64")
    ? text.slice(commaIndex + 1)
    : text;
}

function base64ToArrayBuffer(base64Wav) {
  const clean = normalizeBase64Wav(base64Wav);
  if (!clean) {
    throw new Error("Missing base64 WAV audio.");
  }

  const binary = window.atob(clean);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes.buffer;
}

function tokenizeWords(text) {
  return String(text || "")
    .replace(/[\u2018\u2019]/g, "'")
    .match(/[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?/g) || [];
}

function estimateWordTimings(text, durationSeconds) {
  const words = tokenizeWords(text);
  if (!words.length || !Number.isFinite(durationSeconds) || durationSeconds <= 0) {
    return {
      words,
      wtimes: [],
      wdurations: [],
    };
  }

  const totalMs = Math.max(1, durationSeconds * 1000);
  const gapMs = words.length > 1 ? Math.min(90, totalMs * 0.08 / (words.length - 1)) : 0;
  const usableMs = Math.max(words.length, totalMs - gapMs * Math.max(0, words.length - 1));
  const weights = words.map((word) => Math.max(1, Math.sqrt(word.length)));
  const totalWeight = weights.reduce((sum, value) => sum + value, 0) || 1;

  let cursor = 0;
  const wtimes = [];
  const wdurations = [];

  for (let i = 0; i < words.length; i += 1) {
    const isLast = i === words.length - 1;
    const remainingSlots = words.length - i;
    const weightedMs = usableMs * (weights[i] / totalWeight);
    const duration = isLast
      ? Math.max(1, totalMs - cursor)
      : Math.max(80, Math.min(weightedMs, totalMs - cursor - remainingSlots));

    wtimes.push(Math.max(0, Math.round(cursor - 120)));
    wdurations.push(Math.max(1, Math.round(duration)));
    cursor += duration + gapMs;
  }

  return {
    words,
    wtimes,
    wdurations,
  };
}

function parseSpeakAudioArgs(base64OrOptions, textOrOptions, maybeOptions) {
  if (base64OrOptions && typeof base64OrOptions === "object") {
    return {
      base64Wav:
        base64OrOptions.base64Wav ||
        base64OrOptions.audioB64 ||
        base64OrOptions.audio_b64 ||
        base64OrOptions.wavBase64 ||
        "",
      text: base64OrOptions.text || base64OrOptions.transcript || "",
      options: base64OrOptions.options || {},
    };
  }

  if (textOrOptions && typeof textOrOptions === "object") {
    return {
      base64Wav: base64OrOptions,
      text: textOrOptions.text || textOrOptions.transcript || "",
      options: textOrOptions,
    };
  }

  return {
    base64Wav: base64OrOptions,
    text: textOrOptions,
    options: maybeOptions || {},
  };
}

export function createTalkingHeadScene(container) {
  if (!container || typeof container.appendChild !== "function") {
    throw new TypeError("createTalkingHeadScene requires a DOM container.");
  }

  const presence = {
    listening: false,
    speaking: false,
  };
  let destroyed = false;
  let speechEndTimer = null;
  let loadError = null;
  let loaded = false;

  const head = new TalkingHead(container, {
    ttsEndpoint: null,
    lipsyncModules: [DEFAULT_LANGUAGE],
    lipsyncLang: DEFAULT_LANGUAGE,
    cameraView: "upper",
    cameraRotateEnable: false,
    cameraPanEnable: false,
    cameraZoomEnable: false,
    modelPixelRatio: Math.min(window.devicePixelRatio || 1, 2),
    mixerGainSpeech: 3,
  });

  const ready = head.showAvatar({
    url: AVATAR_URL,
    body: "F",
    avatarMood: "neutral",
    lipsyncLang: DEFAULT_LANGUAGE,
  }).then(() => {
    loaded = true;
    if (!destroyed) {
      head.start();
    }
    return head;
  }).catch((error) => {
    loadError = error;
    console.warn("[talkinghead-scene] avatar load failed:", error);
    return null;
  });

  function clearSpeechEndTimer() {
    if (speechEndTimer) {
      window.clearTimeout(speechEndTimer);
      speechEndTimer = null;
    }
  }

  function setPresence(nextPresence = {}) {
    presence.listening = Boolean(nextPresence.listening);
    presence.speaking = Boolean(nextPresence.speaking);

    if (!destroyed) {
      head.isListening = presence.listening;
      if (presence.listening) {
        head.lookAtCamera?.(700);
      }
    }
  }

  function setPresenceState(nextPresence = {}) {
    setPresence(nextPresence);
  }

  async function load() {
    return ready;
  }

  async function applyConfig() {
    return ready;
  }

  async function speakAudio(base64OrOptions, textOrOptions = "", maybeOptions = {}) {
    if (destroyed) {
      return false;
    }

    const { base64Wav, text, options } = parseSpeakAudioArgs(base64OrOptions, textOrOptions, maybeOptions);

    try {
      const readyHead = await ready;
      if (!readyHead) {
        throw loadError || new Error("TalkingHead avatar is not ready.");
      }
      clearSpeechEndTimer();

      const arrayBuffer = base64ToArrayBuffer(base64Wav);
      const audioBuffer = await head.audioCtx.decodeAudioData(arrayBuffer);
      const timings = estimateWordTimings(text, audioBuffer.duration);

      head.speakAudio({
        audio: audioBuffer,
        words: timings.words,
        wtimes: timings.wtimes,
        wdurations: timings.wdurations,
        markers: [],
        mtimes: [],
      }, {
        lipsyncLang: options.lipsyncLang || DEFAULT_LANGUAGE,
      });

      setPresence({ ...presence, speaking: true });
      speechEndTimer = window.setTimeout(() => {
        setPresence({ ...presence, speaking: false });
      }, Math.max(0, audioBuffer.duration * 1000 + SPEECH_END_PADDING_MS));

      return true;
    } catch (error) {
      console.warn("[talkinghead-scene] speakAudio failed:", error);
      setPresence({ ...presence, speaking: false });
      return false;
    }
  }

  function speakText() {
    return false;
  }

  function stopSpeaking() {
    clearSpeechEndTimer();
    try {
      head.stopSpeaking();
    } catch (error) {
      console.warn("[talkinghead-scene] stopSpeaking failed:", error);
    }
    setPresence({ ...presence, speaking: false });
  }

  function destroy() {
    if (destroyed) {
      return;
    }
    destroyed = true;
    clearSpeechEndTimer();
    try {
      head.dispose();
    } catch (error) {
      console.warn("[talkinghead-scene] destroy failed:", error);
    }
  }

  function stop() {
    stopSpeaking();
  }

  function getVisualState() {
    return {
      kind: "model",
      assetPath: AVATAR_ASSET_PATH,
      ready: loaded,
      lastLoadError: loadError ? (loadError.message || String(loadError)) : "",
    };
  }

  return {
    ready,
    head,
    load,
    applyConfig,
    speakAudio,
    speakText,
    stopSpeaking,
    stop,
    setPresence,
    setPresenceState,
    getVisualState,
    destroy,
  };
}
