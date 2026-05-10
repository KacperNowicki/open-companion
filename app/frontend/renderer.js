import { createVoiceController } from "./voice-controller.js";
import { createTalkingHeadScene } from "./talkinghead-scene.js";

const companionNameEl = document.getElementById("companionName");
const micButtonEl = document.getElementById("micButton");
const minimizeButtonEl = document.getElementById("minimizeButton");
const settingsButtonEl = document.getElementById("settingsButton");
const pcDoctorButtonEl = document.getElementById("pcDoctorButton");
const stateChipEl = document.getElementById("stateChip");
const responseTextEl = document.getElementById("responseText");
const copyResponseButtonEl = document.getElementById("copyResponseButton");
const footnoteEl = document.getElementById("footnote");
const textInputEl = document.getElementById("textInput");
const sendButtonEl = document.getElementById("sendButton");
const targetLayerSelectEl = document.getElementById("targetLayerSelect");
const reasoningWrapEl = document.getElementById("reasoningWrap");
const reasoningEffortSelectEl = document.getElementById("reasoningEffortSelect");
const messageFormEl = document.getElementById("messageForm");
const modalBackdropEl = document.getElementById("modalBackdrop");
const modalDescriptionEl = document.getElementById("modalDescription");
const modalArgumentsEl = document.getElementById("modalArguments");
const approveButtonEl = document.getElementById("approveButton");
const denyButtonEl = document.getElementById("denyButton");
const pcDoctorModalBackdropEl = document.getElementById("pcDoctorModalBackdrop");
const pcDoctorInputEl = document.getElementById("pcDoctorInput");
const pcDoctorFormEl = document.getElementById("pcDoctorForm");
const pcDoctorCancelButtonEl = document.getElementById("pcDoctorCancelButton");
const pcDoctorSubmitButtonEl = document.getElementById("pcDoctorSubmitButton");
const sceneEl = document.getElementById("scene");
const avatarHostEl = document.getElementById("avatar-host");
const sceneLoadingEl = document.getElementById("scene-loading");
const sceneLoadingLabelEl = document.getElementById("scene-loading-label");
const sceneLoadingStatusEl = document.getElementById("scene-loading-status");
const layerLabelEl = document.getElementById("layer-label");
const historyToggleButtonEl = document.getElementById("historyToggleButton");
const replyBoxEl = document.getElementById("replyBox");
const replyLabelEl = document.getElementById("reply-label");
const historyCloseBtnEl = document.getElementById("history-close-btn");
const historyMessagesEl = document.getElementById("history-messages");
const historyOverlayEl = document.getElementById("history-overlay");
const historyOverlayMessagesEl = document.getElementById("history-overlay-messages");
const historyOverlayCloseEl = document.getElementById("history-overlay-close");
const updateBannerEl = document.getElementById("update-banner");
const updateBannerTextEl = document.getElementById("update-banner-text");
const updateDownloadButtonEl = document.getElementById("update-download-btn");
const updateDismissButtonEl = document.getElementById("update-dismiss-btn");
const updateProgressBannerEl = document.getElementById("update-progress-banner");
const updateProgressTextEl = document.getElementById("update-progress-text");
const updateReadyBannerEl = document.getElementById("update-ready-banner");
const updateInstallButtonEl = document.getElementById("update-install-btn");
const updateReadyDismissButtonEl = document.getElementById("update-ready-dismiss-btn");
const closeSessionButtonEl = ensureCloseSessionButton();

let pendingConfirmation = null;
let currentState = "starting";
let visionCapable = false;
let pendingImageBase64 = null;
let visionCapabilityRequestToken = 0;
let copyFeedbackTimer = null;
let transientNote = "";
let transientNoteTimer = null;
let stateNote = "";
let voiceNote = "";
let streamingAssistantText = "";
let latestAssistantMessageId = "";
const talkingHead = createTalkingHeadScene(avatarHostEl || sceneEl);
let sceneLoadState = "loading";
let voiceCapabilities = {};
let runtimeConfig = {
  ui: {},
  voice: {},
  layers: {},
};

function setTalkingHeadPresence(presence) {
  if (typeof talkingHead.setPresence === "function") {
    talkingHead.setPresence(presence);
  } else if (typeof talkingHead.setPresenceState === "function") {
    talkingHead.setPresenceState(presence);
  }
}

function stopTalkingHeadAudio() {
  if (typeof talkingHead.stopSpeaking === "function") {
    talkingHead.stopSpeaking();
  } else if (typeof talkingHead.stop === "function") {
    talkingHead.stop();
  }
}

function getTalkingHeadVisualState() {
  if (typeof talkingHead.getVisualState === "function") {
    return talkingHead.getVisualState();
  }
  return {
    kind: "talking-head",
    ready: Boolean(talkingHead.ready),
  };
}

function getSceneLoadingName() {
  return (companionNameEl.textContent || "Companion").trim() || "Companion";
}

function syncSceneLoadingCopy(statusText = "") {
  if (sceneLoadingLabelEl) {
    sceneLoadingLabelEl.textContent = `Waking ${getSceneLoadingName()}`;
  }
  if (statusText && sceneLoadingStatusEl) {
    sceneLoadingStatusEl.textContent = statusText;
  }
}

function setSceneLoadingState(nextState, statusText = "") {
  sceneLoadState = nextState;
  if (sceneEl) {
    sceneEl.classList.toggle("scene-ready", nextState === "ready");
    sceneEl.classList.toggle("scene-error", nextState === "error");
    sceneEl.setAttribute("aria-busy", nextState === "loading" ? "true" : "false");
  }

  const fallbackStatus = nextState === "error"
    ? "Avatar unavailable"
    : "Preparing avatar";
  syncSceneLoadingCopy(statusText || fallbackStatus);
}

function getSceneLoadingState() {
  return {
    state: sceneLoadState,
    visible: Boolean(sceneLoadingEl) && sceneLoadState !== "ready",
    label: sceneLoadingLabelEl?.textContent || "",
    status: sceneLoadingStatusEl?.textContent || "",
  };
}

function waitForNextScenePaint() {
  return new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  });
}

async function watchTalkingHeadReady() {
  setSceneLoadingState("loading", "Preparing avatar");

  try {
    const readyHead = await Promise.resolve(talkingHead.ready || talkingHead.load?.());
    if (!readyHead) {
      setSceneLoadingState("error", "Avatar unavailable");
      return;
    }
    await waitForNextScenePaint();
    setSceneLoadingState("ready", "Avatar ready");
  } catch (error) {
    console.warn("[renderer] scene failed to load:", error);
    setSceneLoadingState("error", "Avatar unavailable");
  }
}

function ensureCloseSessionButton() {
  const existingButton = document.getElementById("nav-close-session");
  if (existingButton) {
    return existingButton;
  }

  const actions = document.querySelector(".header-actions");
  if (!actions) {
    return null;
  }

  const button = document.createElement("button");
  button.type = "button";
  button.id = "nav-close-session";
  button.className = "header-btn";
  button.title = "Close session";
  button.setAttribute("aria-label", "Close session");
  button.innerHTML = `<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4l8 8M12 4 4 12"/></svg>`;
  actions.appendChild(button);
  return button;
}

function applyOverlayScale(scaleValue) {
  const factor = typeof window.openCompanion.getOverlayScaleFactor === "function"
    ? window.openCompanion.getOverlayScaleFactor(scaleValue)
    : 1;
  document.documentElement.style.setProperty("--overlay-ui-scale", String(factor));
}
let selectedTargetLayer = "companion";
let reasoningControlRequestToken = 0;
if (pcDoctorButtonEl) pcDoctorButtonEl.style.display = "none";

// ── Idle-mode state ────────────────────────────────────────────────────────
let idleTimeoutMs = 5000;
let idleTimer = null;
let settingsWindowOpen = false;

function maybeEnterIdle() {
  if (settingsWindowOpen) return;
  if (voiceController.isListening() || voiceController.isSpeaking()) return;
  if (textInputEl.value.trim().length > 0) return;
  if (document.activeElement === textInputEl) return;
  enterIdle();
}

function enterIdle() {
  document.body.classList.add("idle");
  window.openCompanion.setIgnoreMouseEvents(true, { forward: true });
}

function exitIdle() {
  document.body.classList.remove("idle");
  window.openCompanion.setIgnoreMouseEvents(false);
}

function resetIdleTimer() {
  exitIdle();
  clearTimeout(idleTimer);
  if (idleTimeoutMs > 0) {
    idleTimer = setTimeout(maybeEnterIdle, idleTimeoutMs);
  }
}

function handleIdleMouseMove() {
  if (document.body.classList.contains("idle")) {
    exitIdle();
    clearTimeout(idleTimer);
    if (idleTimeoutMs > 0) {
      idleTimer = setTimeout(maybeEnterIdle, idleTimeoutMs);
    }
    return;
  }
  clearTimeout(idleTimer);
  if (idleTimeoutMs > 0) {
    idleTimer = setTimeout(maybeEnterIdle, idleTimeoutMs);
  }
}

function normalizeTargetLayer(layer) {
  const value = String(layer || "").trim();
  if (["assistant", "assistant_low", "assistant_high", "pc_doctor", "pcdoctor"].includes(value)) {
    return "assistant";
  }
  if (["companion", "assistant"].includes(value)) {
    return value;
  }
  return "companion";
}

function getConfiguredTargetLayer() {
  return normalizeTargetLayer(runtimeConfig?.ui?.selected_target_layer || "companion");
}

function applySelectedTargetLayer(layer, { syncControl = true } = {}) {
  selectedTargetLayer = normalizeTargetLayer(layer);
  if (syncControl && targetLayerSelectEl) {
    targetLayerSelectEl.value = selectedTargetLayer;
  }
}

function getLayerBrainConfig(layer) {
  const normalized = normalizeTargetLayer(layer);
  const layers = runtimeConfig?.brain?.layers;
  if (!layers || typeof layers !== "object") {
    return {};
  }
  return layers[normalized] && typeof layers[normalized] === "object" ? layers[normalized] : {};
}

function getConfiguredReasoningEffort(layer) {
  const layerBrain = getLayerBrainConfig(layer);
  return String(layerBrain.reasoning_effort || "").trim();
}

function setConfiguredReasoningEffort(layer, effort) {
  const normalized = normalizeTargetLayer(layer);
  const nextEffort = String(effort || "").trim();
  runtimeConfig = {
    ...runtimeConfig,
    brain: {
      ...(runtimeConfig.brain || {}),
      layers: {
        ...((runtimeConfig.brain && runtimeConfig.brain.layers) || {}),
        [normalized]: {
          ...getLayerBrainConfig(normalized),
          reasoning_effort: nextEffort,
        },
      },
    },
  };
}

function formatReasoningEffortLabel(effort) {
  const clean = String(effort || "").trim();
  if (!clean) {
    return "Auto";
  }
  if (clean === "xhigh") {
    return "xhigh";
  }
  return clean;
}

async function refreshReasoningEffortControl() {
  if (!reasoningWrapEl || !reasoningEffortSelectEl) {
    return;
  }

  const layer = normalizeTargetLayer(selectedTargetLayer);
  const requestToken = ++reasoningControlRequestToken;

  reasoningWrapEl.classList.remove("visible");
  reasoningEffortSelectEl.disabled = true;

  try {
    const validation = await window.openCompanion.validateLayerRuntime(layer);
    if (requestToken !== reasoningControlRequestToken) {
      return;
    }

    const resolved = validation?.resolved || {};
    const efforts = Array.isArray(resolved.reasoningEfforts)
      ? resolved.reasoningEfforts.map((value) => String(value || "").trim()).filter(Boolean)
      : [];
    if (!efforts.length) {
      reasoningEffortSelectEl.innerHTML = '<option value="">Auto</option>';
      return;
    }

    const configuredEffort = String(
      resolved.configuredReasoningEffort
      || getConfiguredReasoningEffort(layer)
      || ""
    ).trim();
    const defaultEffort = String(
      resolved.effectiveReasoningEffort
      || resolved.defaultReasoningEffort
      || ""
    ).trim();

    const fragment = document.createDocumentFragment();
    const autoOption = document.createElement("option");
    autoOption.value = "";
    autoOption.textContent = defaultEffort ? `Auto (${formatReasoningEffortLabel(defaultEffort)})` : "Auto";
    fragment.appendChild(autoOption);

    for (const effort of efforts) {
      const option = document.createElement("option");
      option.value = effort;
      option.textContent = formatReasoningEffortLabel(effort);
      fragment.appendChild(option);
    }

    reasoningEffortSelectEl.innerHTML = "";
    reasoningEffortSelectEl.appendChild(fragment);
    reasoningEffortSelectEl.value = efforts.includes(configuredEffort) ? configuredEffort : "";
    reasoningWrapEl.classList.add("visible");

    const busy = currentState === "awaiting_approval" || currentState === "starting" || currentState === "thinking" || currentState === "resuming";
    reasoningEffortSelectEl.disabled = busy;
    reasoningEffortSelectEl.title = resolved.model
      ? `Thinking modes for ${resolved.model}`
      : "Thinking modes for the selected layer";
  } catch (_error) {
    if (requestToken !== reasoningControlRequestToken) {
      return;
    }
    reasoningEffortSelectEl.innerHTML = '<option value="">Auto</option>';
  }
}

const imagePreviewStripEl = document.getElementById("image-preview-strip");
const imagePreviewThumbEl = document.getElementById("image-preview-thumb");
const imagePreviewRemoveEl = document.getElementById("image-preview-remove");

function hasVisionAttachmentSupport(validationResult = {}) {
  const resolved = validationResult?.resolved || validationResult || {};
  if (resolved.supportsImageInput === true) {
    return true;
  }
  const capabilities = Array.isArray(resolved.capabilities) ? resolved.capabilities : [];
  return capabilities.some((capability) => {
    const value = String(capability || "").trim().toLowerCase();
    return value === "vision" || value === "image_input" || value === "supports_image_input";
  });
}

async function refreshVisionCapability(layer = selectedTargetLayer) {
  const nextLayer = normalizeTargetLayer(layer);
  const requestToken = ++visionCapabilityRequestToken;
  try {
    const result = await window.openCompanion.getActiveModelCapabilities(nextLayer);
    if (requestToken !== visionCapabilityRequestToken) {
      return visionCapable;
    }
    const capabilities = Array.isArray(result?.capabilities) ? result.capabilities : [];
    visionCapable = Boolean(result?.supportsImageInput) || capabilities.some((capability) => {
      const value = String(capability || "").trim().toLowerCase();
      return value.includes("vision") || value.includes("image");
    });
  } catch (_err) {
    if (requestToken !== visionCapabilityRequestToken) {
      return visionCapable;
    }
    visionCapable = false;
  }
  if (!visionCapable) {
    clearPendingImage();
  }
  document.body.classList.toggle("vision-active", visionCapable);
  return visionCapable;
}

function attachPastedImage(base64, previewDataUrl) {
  pendingImageBase64 = base64;
  if (imagePreviewThumbEl) imagePreviewThumbEl.src = previewDataUrl;
  if (imagePreviewStripEl) imagePreviewStripEl.style.display = "";
}

function clearPendingImage() {
  pendingImageBase64 = null;
  if (imagePreviewStripEl) imagePreviewStripEl.style.display = "none";
  if (imagePreviewThumbEl) imagePreviewThumbEl.src = "";
}

if (imagePreviewRemoveEl) {
  imagePreviewRemoveEl.addEventListener("click", clearPendingImage);
}

textInputEl.addEventListener("paste", async (e) => {
  if (!visionCapable) return;
  const items = Array.from(e.clipboardData?.items || []);
  const imageItem = items.find((item) => item.type.startsWith("image/"));
  if (!imageItem) return;
  e.preventDefault();
  const blob = imageItem.getAsFile();
  if (!blob) return;
  const reader = new FileReader();
  reader.onload = (ev) => {
    const dataUrl = ev.target.result;
    const base64 = dataUrl.split(",")[1];
    attachPastedImage(base64, dataUrl);
  };
  reader.readAsDataURL(blob);
});

if (true) {
  window.__ocOverlayTest = {
    getSceneState: () => getTalkingHeadVisualState(),
    getSceneLoadingState: () => getSceneLoadingState(),
    getRuntimeConfig: () => runtimeConfig,
    getCompanionName: () => companionNameEl.textContent,
    getLatestReply: () => responseTextEl.textContent,
    getReasoningControlState: () => ({
      visible: Boolean(reasoningWrapEl?.classList.contains("visible")),
      value: String(reasoningEffortSelectEl?.value || ""),
      options: Array.from(reasoningEffortSelectEl?.options || []).map((option) => ({
        value: option.value,
        label: option.textContent,
      })),
    }),
    getVisionCapability: () => visionCapable,
    getPendingImagePresent: () => Boolean(pendingImageBase64),
    navControlsPresent: () => false,
  };
}

void watchTalkingHeadReady();

const voiceController = createVoiceController({
  onBackendAudio: (audioB64, text) => talkingHead.speakAudio(audioB64, text),
  onCancelBackendAudio: () => stopTalkingHeadAudio(),
  onStatus: (note) => {
    voiceNote = note;
    renderFootnote();
  },
  onInterimTranscript: (transcript) => {
    if (voiceController.isListening()) {
      textInputEl.value = transcript;
    }
  },
  onFinalTranscript: (transcript) => {
    textInputEl.value = transcript;
    void submitOutboundMessage(transcript, { clearInput: true });
  },
  onAudioCaptured: (base64Wav) => {
    // Backend STT path: send recorded audio for transcription
    textInputEl.value = "";
    window.openCompanion.sendAudioInput(base64Wav).catch((error) => {
      voiceNote = error.message || "Failed to send audio to backend.";
      renderFootnote();
    });
  },
  onListeningChange: (isListening) => {
    setTalkingHeadPresence({ listening: isListening });
    syncMicButton();
    renderFootnote();
  },
  onSpeakingChange: (isSpeaking) => {
    setTalkingHeadPresence({ speaking: isSpeaking });
    syncMicButton();
    renderFootnote();
  },
  onError: (error) => {
    voiceNote = error.message;
    renderFootnote();
    syncMicButton();
  },
});

function setCompanionName(name) {
  companionNameEl.textContent = name;
  syncSceneLoadingCopy();
  textInputEl.placeholder = `Say something to ${name}…`;
}

function setTransientNote(note, durationMs = 0) {
  transientNote = note;
  renderFootnote();

  if (transientNoteTimer) {
    clearTimeout(transientNoteTimer);
    transientNoteTimer = null;
  }

  if (durationMs > 0) {
    transientNoteTimer = window.setTimeout(() => {
      transientNote = "";
      transientNoteTimer = null;
      renderFootnote();
    }, durationMs);
  }
}

function getDefaultFootnote() {
  if (currentState === "awaiting_approval") {
    return "Input is paused until you approve or deny the current action.";
  }
  if (currentState === "thinking" || currentState === "resuming") {
    return `${getActiveLayerDisplayName()} is working through the request.`;
  }
  if (currentState === "error") {
    return "The shell hit a problem, but you can still recover from here.";
  }
  if (voiceController.canUseStt()) {
    return "Tap the mic to talk, or type if you want silence.";
  }
  return "Voice input is not available here, so text stays available.";
}

function renderFootnote() {
  footnoteEl.textContent = transientNote || voiceNote || stateNote || getDefaultFootnote();
}

function syncMicButton() {
  const micAvailable = voiceController.canUseStt();
  const backendBusy =
    currentState === "starting" ||
    currentState === "awaiting_approval" ||
    currentState === "thinking" ||
    currentState === "resuming";
  const disabled = backendBusy || !micAvailable;
  const listening = voiceController.isListening();
  const speaking = voiceController.isSpeaking();

  micButtonEl.disabled = disabled;
  micButtonEl.classList.toggle("is-ready", !disabled);
  micButtonEl.classList.toggle("is-listening", listening);
  micButtonEl.classList.toggle("is-speaking", speaking);
  micButtonEl.innerHTML = listening
    ? `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>`
    : `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0"/><line x1="12" y1="19" x2="12" y2="22"/><line x1="8" y1="22" x2="16" y2="22"/></svg>`;
  micButtonEl.title = disabled
    ? (micAvailable ? "Mic is paused right now" : "Enable voice input to use tap-to-talk")
    : (listening ? "Stop listening" : "Tap to talk");
}

function applyRuntimeConfig(config = {}, capabilities = {}) {
  const nextConfig = config || {};
  voiceCapabilities = capabilities || voiceCapabilities;
  runtimeConfig = {
    ...runtimeConfig,
    ...nextConfig,
    ui: {
      ...(runtimeConfig.ui || {}),
      ...(nextConfig.ui || {}),
    },
    voice: {
      ...(runtimeConfig.voice || {}),
      ...(nextConfig.voice || {}),
    },
    layers: nextConfig.layers || runtimeConfig.layers || {},
  };
  applySelectedTargetLayer(getConfiguredTargetLayer());
  applyOverlayScale(runtimeConfig.ui?.overlay_scale);

  if (typeof talkingHead.load === "function") {
    void talkingHead.load();
  }
  syncActiveLayerUi();
  voiceController.applyConfig(runtimeConfig.voice || {}, companionNameEl.textContent, voiceCapabilities);
  syncMicButton();
  renderFootnote();
  void refreshReasoningEffortControl();

  const cfgIdleSec = runtimeConfig.ui?.idle_timeout_seconds;
  if (cfgIdleSec != null) {
    idleTimeoutMs = Number(cfgIdleSec) * 1000;
  }
}

function setState(state, note = "") {
  currentState = state;
  stateNote = note;

  const busy = state === "awaiting_approval" || state === "starting" || state === "thinking" || state === "resuming";
  renderStateChip();
  textInputEl.disabled = state === "awaiting_approval" || state === "starting";
  sendButtonEl.disabled = textInputEl.disabled;
  if (targetLayerSelectEl) {
    targetLayerSelectEl.disabled = busy;
  }
  if (reasoningEffortSelectEl) {
    reasoningEffortSelectEl.disabled = busy || !reasoningWrapEl?.classList.contains("visible");
  }
  pcDoctorButtonEl.disabled = busy;
  syncMicButton();
  renderFootnote();
}

function setResponse(text, isError = false) {
  if (copyFeedbackTimer) {
    clearTimeout(copyFeedbackTimer);
    copyFeedbackTimer = null;
  }

  responseTextEl.textContent = text;
  responseTextEl.classList.toggle("error-text", isError);
  copyResponseButtonEl.textContent = "Copy";
  copyResponseButtonEl.disabled = !text.trim();
}

async function handleCopyResponse() {
  const text = responseTextEl.textContent.trim();
  if (!text) {
    return;
  }

  try {
    await window.openCompanion.copyText(text);
    copyResponseButtonEl.textContent = "Copied";
    setTransientNote("Latest reply copied to clipboard.", 1400);
    copyFeedbackTimer = window.setTimeout(() => {
      copyResponseButtonEl.textContent = "Copy";
      copyFeedbackTimer = null;
    }, 1400);
  } catch (error) {
    setState("error", "The latest reply could not be copied.");
    setResponse(error.message, true);
  }
}

function handleSettingsClick() {
  settingsWindowOpen = true;
  window.openCompanion.openSettings();
}

async function closeSession() {
  try {
    voiceController.stopListening();
    voiceController.cancelSpeech();

    const result = await window.openCompanion.closeSession();
    if (result && result.ok === false) {
      throw new Error(result.error || "The session could not be closed.");
    }

    streamingAssistantText = "";
    latestAssistantMessageId = "";
    voiceNote = "";
    hideApprovalModal();
    clearConversationHistory();
    setResponse("");
    setTransientNote("Session cleared.", 1800);
  } catch (error) {
    setState("error", "The session could not be closed.");
    setResponse(error.message || "Unknown error", true);
  }
}

function showPcDoctorModal() {
  pcDoctorInputEl.value = "";
  pcDoctorModalBackdropEl.classList.add("visible");
  pcDoctorInputEl.focus();
}

function hidePcDoctorModal() {
  pcDoctorModalBackdropEl.classList.remove("visible");
}

function handlePcDoctorButtonClick() {
  if (currentState === "awaiting_approval" || currentState === "starting" || currentState === "thinking" || currentState === "resuming") {
    setTransientNote("PC Doctor can only be summoned when idle.", 1800);
    return;
  }
  showPcDoctorModal();
}

async function handlePcDoctorSubmit(event) {
  event.preventDefault();
  hidePcDoctorModal();
}

async function handleMinimizeClick() {
  try {
    voiceController.stopListening();
    voiceController.cancelSpeech();
    await window.openCompanion.minimizeWindow();
  } catch (error) {
    setState("error", "The shell could not minimize to the taskbar.");
    setResponse(error.message, true);
  }
}

function showApprovalModal(payload) {
  pendingConfirmation = payload;
  const requester = payload.layer_display_name || companionNameEl.textContent;
  modalDescriptionEl.textContent = `${requester} wants to run "${payload.tool_name}".`;
  modalArgumentsEl.textContent = JSON.stringify(payload.arguments || {}, null, 2);
  modalBackdropEl.classList.add("visible");
  window.setTimeout(() => approveButtonEl?.focus(), 0);
}

function hideApprovalModal() {
  pendingConfirmation = null;
  modalBackdropEl.classList.remove("visible");
}

async function submitOutboundMessage(rawContent, options = {}) {
  const content = String(rawContent || "").trim();
  if (!content || currentState === "awaiting_approval" || currentState === "starting") {
    return false;
  }

  voiceController.stopListening();
  voiceController.cancelSpeech();
  streamingAssistantText = "";
  latestAssistantMessageId = "";
  setResponse("...", false);
  if (options.clearInput !== false) {
    textInputEl.value = "";
  }

  try {
    const validation = await window.openCompanion.validateLayerRuntime(selectedTargetLayer);
    if (!validation?.ok) {
      const errorText = Array.isArray(validation?.errors) && validation.errors.length
        ? validation.errors.join(" ")
        : "The selected layer is not ready to run.";
      setState("error", "The selected layer failed runtime validation.");
      setResponse(errorText, true);
      return false;
    }
    const canAttachImage = hasVisionAttachmentSupport(validation);
    const imageToSend = canAttachImage ? pendingImageBase64 : null;
    clearPendingImage();
    if (Array.isArray(validation?.warnings) && validation.warnings.length) {
      setTransientNote(validation.warnings[0], 3200);
    }
    setActiveLayer(selectedTargetLayer);
    await window.openCompanion.invokeLayer(selectedTargetLayer, content, imageToSend || undefined);
    appendToHistory("You", content);
    return true;
  } catch (error) {
    setState("error", "The backend did not accept the message.");
    setResponse(error.message, true);
    return false;
  }
}

async function handleSubmit(event) {
  event.preventDefault();
  await submitOutboundMessage(textInputEl.value, { clearInput: true });
}

async function handleMicClick() {
  if (voiceController.isListening()) {
    voiceController.stopListening();
    return;
  }

  if (
    currentState === "awaiting_approval" ||
    currentState === "starting" ||
    currentState === "thinking" ||
    currentState === "resuming"
  ) {
    setTransientNote("Voice input is paused until Nova is ready again.", 1800);
    return;
  }

  await voiceController.startListening();
  syncMicButton();
}

async function resolveDecision(approved) {
  if (!pendingConfirmation) {
    return;
  }

  const payload = pendingConfirmation;
  hideApprovalModal();

  try {
    if (approved && payload.tool_name === "move_companion_window") {
      await window.openCompanion.moveCompanionWindow(payload.arguments || {});
    }

    await window.openCompanion.resolveToolDecision(approved);
  } catch (error) {
    setState("error", "The tool decision could not be sent.");
    setResponse(error.message, true);
  }
}

// --- Layer status ---
let activeLayer = "companion";

function isLayerLabelVisible() {
  return runtimeConfig?.ui?.layer_visibility !== false;
}

function getActiveLayerDisplayName() {
  if (activeLayer === "assistant") {
    return "Assistant";
  }
  return "Companion";
}

function getLayerDisplayName(layer) {
  const normalized = normalizeTargetLayer(layer);
  if (normalized === "assistant") {
    return "Assistant";
  }
  return companionNameEl.textContent || "Companion";
}

function getStateDisplayName(state) {
  const labelMap = {
    starting: "starting",
    idle: "idle",
    thinking: "thinking",
    awaiting_approval: "awaiting approval",
    resuming: "resuming",
    error: "error",
  };
  return labelMap[state] || String(state || "idle").replace(/_/g, " ").toLowerCase();
}

function renderStateChip() {
  const stateLabel = getStateDisplayName(currentState);
  if (!isLayerLabelVisible()) {
    stateChipEl.textContent = stateLabel.charAt(0).toUpperCase() + stateLabel.slice(1);
  } else {
    stateChipEl.textContent = `${getActiveLayerDisplayName()} is ${stateLabel}`;
  }
  stateChipEl.dataset.state = currentState;
}

function syncActiveLayerUi() {
  sceneEl.classList.remove("layer-assistant", "layer-pcdoctor");
  if (activeLayer === "assistant") {
    sceneEl.classList.add("layer-assistant");
    layerLabelEl.style.display = "none";
    renderStateChip();
    return;
  }

  layerLabelEl.style.display = "none";
  renderStateChip();
}

function setActiveLayer(layer) {
  activeLayer = layer;
  syncActiveLayerUi();
}

// --- Conversation history ---
const conversationHistory = [];
let historyOpen = false;

function escapeHtml(str) {
  return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/**
 * Strip internal model artifacts from text before display:
 *  1. <think>…</think> / <thinking>…</thinking> blocks (reasoning traces, e.g. gemma4)
 *  2. [calls tool_name{…}] bracket-wrapped invocations
 *  3. Markdown emphasis markers are flattened to plain text
 *  4. *emote text* single-asterisk action/emote segments
 * Returns the cleaned string, or null if nothing visible remains.
 */
function sanitizeForDisplay(text) {
  if (!text) return null;
  let s = String(text);
  // 1. Think blocks (multiline, greedy — they can be long)
  s = s.replace(/<think(?:ing)?>[^]*?<\/think(?:ing)?>/gi, "");
  // 2. Tool call brackets: [calls name{...}] — non-greedy
  s = s.replace(/\[calls\s+[^\]]*?\]/g, "");
  // 3. Preserve emphasized content instead of dropping the inner text.
  s = s.replace(/(?<!\*)\*\*([^*\n]+?)\*\*(?!\*)/g, "$1");
  s = s.replace(/(?<!_)__([^_\n]+?)__(?!_)/g, "$1");
  // 4. Remove only single-asterisk emotes/actions, not markdown bold markers.
  s = s.replace(/(?<!\*)\*[^*\n]+?\*(?!\*)/g, "");
  s = s.trim();
  return s.length > 0 ? s : null;
}

function appendToHistory(speaker, text) {
  conversationHistory.push({ speaker, text, ts: Date.now() });
  if (historyOpen) {
    renderHistoryEntry(conversationHistory[conversationHistory.length - 1]);
  }
}

function renderConversationHistory() {
  if (historyOverlayMessagesEl) {
    historyOverlayMessagesEl.innerHTML = "";
  }
  if (historyMessagesEl) {
    historyMessagesEl.innerHTML = "";
  }
  conversationHistory.forEach(renderHistoryEntry);
}

function clearConversationHistory() {
  conversationHistory.length = 0;
  renderConversationHistory();
}

function renderHistoryEntry(entry) {
  const isYou = entry.speaker === "You";
  const el = document.createElement("div");
  el.className = `history-entry ${isYou ? "entry-you" : "entry-nova"}`;
  const time = new Date(entry.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  el.innerHTML = `<div class="history-entry-meta"><span class="${isYou ? "" : "speaker-nova"}">${escapeHtml(entry.speaker)}</span><span> · ${time}</span></div><div class="history-entry-text">${escapeHtml(entry.text)}</div>`;
  if (historyOverlayMessagesEl) {
    historyOverlayMessagesEl.appendChild(el);
    historyOverlayMessagesEl.scrollTop = historyOverlayMessagesEl.scrollHeight;
  } else {
    historyMessagesEl.appendChild(el);
    historyMessagesEl.scrollTop = historyMessagesEl.scrollHeight;
  }
}

function setHistoryButtonActive(active) {
  historyToggleButtonEl.style.color = active ? "var(--accent, #fff)" : "";
  historyToggleButtonEl.style.background = active ? "rgba(var(--accent-rgb), 0.18)" : "";
}

function openHistory() {
  historyOpen = true;
  if (historyOverlayEl) {
    renderConversationHistory();
    historyOverlayEl.classList.add("open");
  } else {
    renderConversationHistory();
    replyBoxEl.classList.add("history-open");
    replyLabelEl.textContent = "THIS SESSION";
    responseTextEl.style.display = "none";
    historyMessagesEl.style.display = "flex";
    copyResponseButtonEl.style.display = "none";
    historyCloseBtnEl.style.display = "";
    historyMessagesEl.scrollTop = historyMessagesEl.scrollHeight;
  }
  setHistoryButtonActive(true);
}

function closeHistory() {
  historyOpen = false;
  if (historyOverlayEl) {
    historyOverlayEl.classList.remove("open");
  } else {
    replyBoxEl.classList.remove("history-open");
    replyLabelEl.textContent = "LATEST REPLY";
    responseTextEl.style.display = "";
    historyMessagesEl.style.display = "none";
    copyResponseButtonEl.style.display = "";
    historyCloseBtnEl.style.display = "none";
  }
  setHistoryButtonActive(false);
}

const startupBannersEl = document.getElementById("startup-banners");
const transientBannerIds = new Set();
let pendingUpdateVersion = "";

function dismissBanner(id, el) {
  el.classList.add("dismissing");
  el.addEventListener("animationend", () => el.remove(), { once: true });
  transientBannerIds.add(id);
  window.openCompanion.dismissBanner(id);
}

function renderStartupBanners(banners) {
  if (!banners || banners.length === 0) return;

  for (const banner of banners) {
    if (!banner || !banner.id) {
      continue;
    }
    if (transientBannerIds.has(banner.id) || startupBannersEl.querySelector(`[data-banner-id="${banner.id}"]`)) {
      continue;
    }

    const el = document.createElement("div");
    el.className = "startup-banner";
    el.dataset.bannerId = banner.id;

    const icon = document.createElement("span");
    icon.className = "startup-banner-icon";
    icon.textContent = "⚠";

    const body = document.createElement("div");
    body.className = "startup-banner-body";

    const label = document.createElement("div");
    label.className = "startup-banner-label";
    label.textContent = banner.label;

    const hint = document.createElement("div");
    hint.className = "startup-banner-hint";
    hint.textContent = banner.hint;

    body.appendChild(label);
    body.appendChild(hint);

    const dismissBtn = document.createElement("button");
    dismissBtn.className = "startup-banner-dismiss";
    dismissBtn.type = "button";
    dismissBtn.textContent = "×";
    dismissBtn.setAttribute("aria-label", "Dismiss");
    dismissBtn.addEventListener("click", () => dismissBanner(banner.id, el));

    el.appendChild(icon);
    el.appendChild(body);
    el.appendChild(dismissBtn);

    startupBannersEl.appendChild(el);
  }
}

function showErrorBanner(label, hint, options = {}) {
  const id = options.id || `banner-${Date.now()}`;
  if (options.once && transientBannerIds.has(id)) {
    return;
  }
  renderStartupBanners([{ id, label, hint }]);
}

function hideUpdateBanners() {
  updateBannerEl?.classList.add("hidden");
  updateProgressBannerEl?.classList.add("hidden");
  updateReadyBannerEl?.classList.add("hidden");
}

function handleAssistantLikeMessage(payload) {
  latestAssistantMessageId = payload.message_id || "";
  streamingAssistantText = "";
  const displayText = sanitizeForDisplay(payload.content) || "[No response]";
  appendToHistory(getLayerDisplayName(payload.layer), displayText);
  setResponse(displayText);
  hideApprovalModal();
  voiceNote = "";

  if (payload.audio_b64) {
    voiceController.playAudioB64(payload.audio_b64, payload.content || displayText);
  } else {
    voiceController.speak(payload.content || "");
  }
}

window.openCompanion.onStartupHealth((banners) => {
  renderStartupBanners(banners);
});

window.openCompanion.onCompanionNameUpdated((name) => {
  if (name) {
    setCompanionName(name);
  }
});

window.openCompanion.onUpdateAvailable((info) => {
  pendingUpdateVersion = String(info?.version || "");
  hideUpdateBanners();
});

window.openCompanion.onUpdateProgress((progress) => {
  updateProgressTextEl.textContent = `Downloading update... ${progress.percent}%`;
});

window.openCompanion.onUpdateReady(() => {
  updateBannerEl.classList.add("hidden");
  updateProgressBannerEl.classList.add("hidden");
  updateReadyBannerEl.classList.remove("hidden");
});

window.openCompanion.onBackendEvent((payload) => {
  // Log every backend event for debugging
  console.log("[backend]", JSON.stringify(payload));
  resetIdleTimer();

  if (payload.type === "ready") {
    if (payload.companion_name) setCompanionName(payload.companion_name);
    applyRuntimeConfig(payload.config || {}, payload.voice_capabilities || {});
    setState(payload.state || "idle");
    setResponse(`Hi. ${companionNameEl.textContent} is ready whenever you are.`);
    textInputEl.focus();
    void refreshVisionCapability();
    return;
  }

  if (payload.type === "config_reloaded") {
    if (payload.companion_name) setCompanionName(payload.companion_name);
    applyRuntimeConfig(payload.config || {}, payload.voice_capabilities || {});
    setState(payload.state || currentState);
    void refreshVisionCapability();
    return;
  }

  if (payload.type === "busy") {
    setState(payload.state || "thinking");
    return;
  }

  if (payload.type === "assistant_message") {
    handleAssistantLikeMessage(payload);
    return;
  }

  if (payload.type === "reminder_fired") {
    const prefix = payload.missed ? `[Missed - ${payload.original_time}] ` : "";
    handleAssistantLikeMessage({
      content: payload.content || `${prefix}Reminder: ${payload.text}`,
      audio_b64: payload.audio_b64,
      sample_rate: payload.sample_rate,
      layer: "companion",
      reminder_id: payload.id,
    });
    return;
  }

  if (payload.type === "reminders_missed_batch") {
    const lines = Array.isArray(payload.items)
      ? payload.items.map((item) => `- ${item.text} (was ${item.original_time})`).join("\n")
      : "";
    handleAssistantLikeMessage({
      content: payload.content || `You had some reminders while the app was closed:\n${lines}`,
      audio_b64: payload.audio_b64,
      sample_rate: payload.sample_rate,
      layer: "companion",
      is_missed_batch: true,
    });
    return;
  }

  if (payload.type === "assistant_chunk") {
    const chunk = payload.content || "";
    if (!chunk) {
      return;
    }
    if (!latestAssistantMessageId) {
      latestAssistantMessageId = payload.message_id || "";
    }
    if (payload.message_id && latestAssistantMessageId && payload.message_id !== latestAssistantMessageId) {
      return;
    }
    streamingAssistantText += chunk;
    const stripped = streamingAssistantText.replace(
      /^\s*\{"type"\s*:\s*"animation"[^\n]*\}\s*\n?/,
      ""
    );
    const displayChunk = sanitizeForDisplay(stripped);
    if (displayChunk) {
      setResponse(displayChunk, false);
    }
    return;
  }

  if (payload.type === "assistant_audio") {
    if (!payload.audio_b64) {
      return;
    }
    if (payload.message_id && latestAssistantMessageId && payload.message_id !== latestAssistantMessageId) {
      return;
    }
    if (currentState === "thinking" || currentState === "resuming") {
      return;
    }
    voiceController.playAudioB64(payload.audio_b64, responseTextEl.textContent || "");
    return;
  }

  if (payload.type === "tool_confirmation_requested") {
    setState(payload.state || "awaiting_approval");
    setResponse(`${companionNameEl.textContent} is waiting for your approval.`, false);
    showApprovalModal(payload);
    return;
  }

  if (payload.type === "idle") {
    setState(payload.state || "idle");
    setActiveLayer(selectedTargetLayer);
    return;
  }

  if (payload.type === "stt_result") {
    textInputEl.value = payload.text || "";
    voiceNote = "";
    renderFootnote();
    // The backend auto-submits the transcript as a user message,
    // so we just display it — no need to submit from here.
    return;
  }

  if (payload.type === "stt_error") {
    setState(payload.state || "idle");
    voiceNote = payload.message || "Transcription failed.";
    renderFootnote();
    syncMicButton();
    return;
  }

  if (payload.type === "assistant_started") {
    setState("thinking");
    setActiveLayer(payload.layer || "assistant");
    return;
  }

  if (payload.type === "layer_completed") {
    const label = payload.layer_display_name || "Worker";
    setActiveLayer(selectedTargetLayer);
    setTransientNote(`${label} finished.`, 2400);
    return;
  }

  if (payload.type === "error") {
    setState("error", "The shell is still running, but the backend reported a problem.");
    setResponse(payload.message || "Unknown error", true);
  }
});

window.openCompanion.onBackendStderr((line) => {
  console.warn("[backend]", line);
});

sceneEl.addEventListener("pointerdown", (e) => {
  e.stopPropagation();
  if (document.body.classList.contains("idle")) {
    resetIdleTimer();
  }
});

messageFormEl.addEventListener("submit", handleSubmit);
targetLayerSelectEl?.addEventListener("change", async (event) => {
  const nextLayer = normalizeTargetLayer(event.target.value);
  applySelectedTargetLayer(nextLayer, { syncControl: false });
  setActiveLayer(nextLayer);
  void refreshReasoningEffortControl();
  void refreshVisionCapability(nextLayer);
  try {
    await window.openCompanion.setSelectedTargetLayer(nextLayer);
  } catch (error) {
    setTransientNote(error.message || "Could not save target layer.", 2200);
  }
});
reasoningEffortSelectEl?.addEventListener("change", async (event) => {
  const nextEffort = String(event.target.value || "").trim();
  const layer = normalizeTargetLayer(selectedTargetLayer);
  const previousEffort = getConfiguredReasoningEffort(layer);
  setConfiguredReasoningEffort(layer, nextEffort);
  try {
    await window.openCompanion.setLayerReasoningEffort(layer, nextEffort);
    setTransientNote(
      nextEffort ? `Thinking set to ${nextEffort} for ${getLayerDisplayName(layer)}.` : `Thinking reset to auto for ${getLayerDisplayName(layer)}.`,
      2200
    );
    void refreshReasoningEffortControl();
  } catch (error) {
    setConfiguredReasoningEffort(layer, previousEffort);
    event.target.value = previousEffort;
    setTransientNote(error.message || "Could not save thinking mode.", 2200);
  }
});
approveButtonEl.addEventListener("click", () => resolveDecision(true));
denyButtonEl.addEventListener("click", () => resolveDecision(false));
copyResponseButtonEl.addEventListener("click", handleCopyResponse);
micButtonEl.addEventListener("click", handleMicClick);
minimizeButtonEl.addEventListener("click", handleMinimizeClick);
settingsButtonEl.addEventListener("click", handleSettingsClick);
pcDoctorButtonEl.addEventListener("click", handlePcDoctorButtonClick);
pcDoctorFormEl.addEventListener("submit", handlePcDoctorSubmit);
pcDoctorCancelButtonEl.addEventListener("click", hidePcDoctorModal);
historyToggleButtonEl.addEventListener("click", () => { historyOpen ? closeHistory() : openHistory(); });
historyCloseBtnEl.addEventListener("click", closeHistory);
historyOverlayCloseEl?.addEventListener("click", closeHistory);
closeSessionButtonEl?.addEventListener("click", () => { void closeSession(); });
updateDownloadButtonEl?.addEventListener("click", async () => {
  updateBannerEl.classList.add("hidden");
  updateProgressBannerEl.classList.remove("hidden");
  await window.openCompanion.downloadUpdate();
});
updateDismissButtonEl?.addEventListener("click", async () => {
  updateBannerEl.classList.add("hidden");
  await window.openCompanion.dismissUpdate(pendingUpdateVersion);
});
updateInstallButtonEl?.addEventListener("click", async () => {
  await window.openCompanion.installUpdate();
});
updateReadyDismissButtonEl?.addEventListener("click", async () => {
  updateReadyBannerEl.classList.add("hidden");
  await window.openCompanion.dismissUpdate(pendingUpdateVersion);
});

window.openCompanion.onThemeApply((themeData) => {
  if (!themeData) return;
  const root = document.documentElement;
  if (themeData.accent_rgb) {
    const [r, g, b] = themeData.accent_rgb;
    root.style.setProperty("--accent-rgb", `${r}, ${g}, ${b}`);
    root.style.setProperty("--accent", `rgb(${r}, ${g}, ${b})`);
    root.style.setProperty("--accent-soft", `rgba(${r}, ${g}, ${b}, 0.16)`);
    root.style.setProperty("--accent-glow", `rgba(${r}, ${g}, ${b}, 0.20)`);
    root.style.setProperty("--accent-mic-shadow", `rgba(${r}, ${g}, ${b}, 0.34)`);
    root.style.setProperty("--accent-mic-shadow-hover", `rgba(${r}, ${g}, ${b}, 0.40)`);
    root.style.setProperty("--accent-mic-shadow-listen", `rgba(${r}, ${g}, ${b}, 0.12)`);
    root.style.setProperty("--accent-mic-shadow-listen2", `rgba(${r}, ${g}, ${b}, 0.42)`);
  }
  if (themeData.mode === "day") {
    root.style.setProperty("--bg-strong", "rgba(244, 241, 236, 0.97)");
    root.style.setProperty("--text-main", "#0a0710");
    root.style.setProperty("--text-muted", "#2a2438");
    root.style.setProperty("--panel-line", `rgba(${(themeData.accent_rgb || [168,85,247]).join(",")}, 0.20)`);
    root.style.setProperty("--bg-soft", `rgba(${(themeData.accent_rgb || [168,85,247]).join(",")}, 0.08)`);
    root.style.setProperty("--shell-grad-a", "rgba(245, 242, 237, 0.97)");
    root.style.setProperty("--shell-grad-b", "rgba(237, 233, 226, 0.98)");
    root.style.setProperty("--shell-grad-c", "rgba(232, 228, 220, 0.99)");
    root.style.setProperty("--input-bg", "rgba(230, 226, 218, 0.80)");
    root.style.setProperty("--modal-bg", "rgba(242, 239, 234, 0.98)");
    root.style.setProperty("--dnd-bg", "rgba(235, 231, 224, 0.98)");
    root.style.setProperty("--control-track", "rgba(0, 0, 0, 0.10)");
    root.style.setProperty("--icon-default", "rgba(15, 11, 20, 0.65)");
    root.style.setProperty("--icon-hover-bg", "rgba(0, 0, 0, 0.08)");
    root.style.setProperty("--divider-color", "rgba(0, 0, 0, 0.12)");
    root.style.setProperty("--copy-btn-bg", "rgba(0, 0, 0, 0.07)");
    root.style.setProperty("--modal-pre-bg", "rgba(0, 0, 0, 0.04)");
    root.style.setProperty("--modal-pre-border", "rgba(0, 0, 0, 0.10)");
    root.style.setProperty("--deny-btn-bg", "rgba(0, 0, 0, 0.07)");
    root.style.setProperty("--history-meta-color", "rgba(15, 11, 20, 0.45)");
    root.style.setProperty("--history-close-color", "rgba(15, 11, 20, 0.40)");
    root.style.setProperty("--history-close-hover", "#0a0710");
    root.style.setProperty("--scrollbar-thumb", "rgba(0, 0, 0, 0.15)");
    root.style.setProperty("--placeholder-color", "rgba(15, 11, 20, 0.35)");
  } else {
    root.style.setProperty("--bg-strong", "rgba(10, 7, 20, 0.96)");
    root.style.setProperty("--text-main", "#f0eeff");
    root.style.setProperty("--text-muted", "rgba(205, 198, 225, 0.82)");
    root.style.setProperty("--panel-line", `rgba(${(themeData.accent_rgb || [168,85,247]).join(",")}, 0.18)`);
    root.style.setProperty("--bg-soft", `rgba(${(themeData.accent_rgb || [168,85,247]).join(",")}, 0.10)`);
    root.style.setProperty("--shell-grad-a", "rgba(16, 10, 28, 0.97)");
    root.style.setProperty("--shell-grad-b", "rgba(9, 7, 18, 0.98)");
    root.style.setProperty("--shell-grad-c", "rgba(5, 4, 12, 0.99)");
    root.style.setProperty("--input-bg", "rgba(10, 8, 18, 0.75)");
    root.style.setProperty("--modal-bg", "rgba(10, 9, 18, 0.97)");
    root.style.setProperty("--dnd-bg", "rgba(14, 21, 34, 0.97)");
    root.style.setProperty("--control-track", "rgba(255, 255, 255, 0.10)");
    root.style.setProperty("--icon-default", "rgba(235, 230, 218, 0.60)");
    root.style.setProperty("--icon-hover-bg", "rgba(255, 255, 255, 0.12)");
    root.style.setProperty("--divider-color", "rgba(255, 255, 255, 0.10)");
    root.style.setProperty("--copy-btn-bg", "rgba(255, 255, 255, 0.08)");
    root.style.setProperty("--modal-pre-bg", "rgba(255, 255, 255, 0.04)");
    root.style.setProperty("--modal-pre-border", "rgba(255, 255, 255, 0.08)");
    root.style.setProperty("--deny-btn-bg", "rgba(255, 255, 255, 0.08)");
    root.style.setProperty("--history-meta-color", "rgba(255, 255, 255, 0.42)");
    root.style.setProperty("--history-close-color", "rgba(255, 255, 255, 0.40)");
    root.style.setProperty("--history-close-hover", "#fff");
    root.style.setProperty("--scrollbar-thumb", "rgba(255, 255, 255, 0.12)");
    root.style.setProperty("--placeholder-color", "rgba(245, 247, 251, 0.48)");
  }
});

window.openCompanion.onLayerVisibilityUpdated((visible) => {
  runtimeConfig = {
    ...runtimeConfig,
    ui: {
      ...(runtimeConfig.ui || {}),
      layer_visibility: Boolean(visible),
    },
  };
  syncActiveLayerUi();
});

// ── DND controller ──────────────────────────────────────────────────────────
const dndButtonEl = document.getElementById("dndButton");
const dndWrapperEl = document.getElementById("dndWrapper");
const dndDropdownEl = document.getElementById("dndDropdown");
const dndTimerEl = document.getElementById("dndTimer");
const dndCustomInputEl = document.getElementById("dndCustomInput");
const dndCustomGoEl = document.getElementById("dndCustomGo");

let dndUntil = 0;
let dndTimerInterval = null;

function formatDndRemaining(ms) {
  const totalSec = Math.max(0, Math.ceil(ms / 1000));
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return m > 0 ? `${m}m` : `${s}s`;
}

function startDndTimer() {
  if (dndTimerInterval) clearInterval(dndTimerInterval);
  dndTimerInterval = setInterval(() => {
    const remaining = dndUntil * 1000 - Date.now();
    if (remaining <= 0) {
      dndUntil = 0;
      clearInterval(dndTimerInterval);
      dndTimerInterval = null;
      syncDndUi();
      return;
    }
    dndTimerEl.textContent = formatDndRemaining(remaining);
  }, 1000);
}

function syncDndUi() {
  const active = dndUntil > 0 && Date.now() < dndUntil * 1000;
  dndButtonEl.classList.toggle("dnd-active", active);
  if (active) {
    const remaining = dndUntil * 1000 - Date.now();
    dndTimerEl.textContent = formatDndRemaining(remaining);
    dndTimerEl.style.display = "block";
  } else {
    dndTimerEl.style.display = "none";
    dndTimerEl.textContent = "";
  }
}

function closeDndDropdown() {
  dndDropdownEl.classList.remove("open");
}

function activateDnd(minutes) {
  const until = Math.floor(Date.now() / 1000) + minutes * 60;
  dndUntil = until;
  window.openCompanion.dndStart(until).catch(() => {});
  syncDndUi();
  startDndTimer();
  closeDndDropdown();
}

function cancelDnd() {
  dndUntil = 0;
  if (dndTimerInterval) { clearInterval(dndTimerInterval); dndTimerInterval = null; }
  window.openCompanion.dndCancel().catch(() => {});
  syncDndUi();
  closeDndDropdown();
}

dndButtonEl.addEventListener("click", (e) => {
  e.stopPropagation();
  const active = dndUntil > 0 && Date.now() < dndUntil * 1000;
  if (active) {
    // Cancel DND immediately — no dropdown needed
    cancelDnd();
    return;
  }
  dndDropdownEl.classList.toggle("open");
});

dndDropdownEl.querySelectorAll(".dnd-option[data-minutes]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const minutes = parseInt(btn.dataset.minutes, 10);
    if (Number.isFinite(minutes) && minutes > 0) activateDnd(minutes);
  });
});

dndCustomGoEl.addEventListener("click", () => {
  const minutes = parseInt(dndCustomInputEl.value, 10);
  if (Number.isFinite(minutes) && minutes > 0) {
    activateDnd(minutes);
    dndCustomInputEl.value = "";
  }
});

dndCustomInputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    const minutes = parseInt(dndCustomInputEl.value, 10);
    if (Number.isFinite(minutes) && minutes > 0) {
      activateDnd(minutes);
      dndCustomInputEl.value = "";
    }
  }
  e.stopPropagation();
});

document.addEventListener("click", (e) => {
  if (!dndWrapperEl.contains(e.target)) closeDndDropdown();
});
// ── End DND controller ───────────────────────────────────────────────────────

// ── Idle-mode listeners ────────────────────────────────────────────────────
document.addEventListener("mousemove", handleIdleMouseMove);
document.addEventListener("click", resetIdleTimer);
document.addEventListener("keydown", resetIdleTimer);
// Wake from idle on tap/click directly on the avatar scene.
sceneEl.addEventListener("pointerdown", resetIdleTimer);
// When this window regains focus the settings window has been closed.
window.addEventListener("focus", () => { settingsWindowOpen = false; });

window.openCompanion.onIdleTimeoutChanged((ms) => {
  idleTimeoutMs = ms;
  if (ms <= 0) {
    clearTimeout(idleTimer);
    exitIdle();
  } else {
    resetIdleTimer();
  }
});

resetIdleTimer();

window.openCompanion.getInitialState()
  .then((state) => {
    setCompanionName(state.companionName || "Companion");
    applyRuntimeConfig(state.config || {}, state.voiceCapabilities || {});
    applySelectedTargetLayer(normalizeTargetLayer(state?.config?.ui?.selected_target_layer || "companion"));
    setActiveLayer(selectedTargetLayer);
    setState(state.state || "starting");
    if (state.lastError) {
      setResponse(state.lastError, true);
      return;
    }
    if (String(state.state || "").trim().toLowerCase() === "idle") {
      setResponse(`Hi. ${companionNameEl.textContent} is ready whenever you are.`);
      textInputEl.focus();
    }
    void refreshVisionCapability();
  })
  .catch((error) => {
    setState("error", "The shell could not read its initial backend state.");
    setResponse(error.message, true);
  });
