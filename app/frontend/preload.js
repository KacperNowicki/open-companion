const { contextBridge, ipcRenderer } = require("electron");
const { overlayScaleFactor } = require("./overlay-scale");

contextBridge.exposeInMainWorld("openCompanion", {
  getInitialState: () => ipcRenderer.invoke("backend:getInitialState"),
  getOverlayScaleFactor: (value) => overlayScaleFactor(value),
  sendUserMessage: (content, imageBase64) => ipcRenderer.invoke("backend:sendUserMessage", content, imageBase64),
  invokeLayer: (layer, content, imageBase64) => ipcRenderer.invoke("backend:invokeLayer", layer, content, imageBase64),
  resetSession: (layer, preserveSummary) => ipcRenderer.invoke("backend:resetSession", layer, preserveSummary),
  closeSession: () => ipcRenderer.invoke("backend:closeSession"),
  resolveToolDecision: (approved) => ipcRenderer.invoke("backend:resolveToolDecision", approved),
  copyText: (text) => ipcRenderer.invoke("ui:copyText", text),
  moveCompanionWindow: (movement) => ipcRenderer.invoke("ui:moveCompanionWindow", movement),
  minimizeWindow: () => ipcRenderer.invoke("ui:minimizeWindow"),
  sendAudioInput: (audioB64) => ipcRenderer.invoke("backend:sendAudioInput", audioB64),
  openSettings: () => ipcRenderer.invoke("ui:openSettings"),
  getAppVersion: () => ipcRenderer.invoke("app:version"),
  downloadUpdate: () => ipcRenderer.invoke("update:download"),
  installUpdate: () => ipcRenderer.invoke("update:install"),
  dismissUpdate: (version) => ipcRenderer.invoke("update:dismiss", version),
  apikey: {
    set: (provider, key) => ipcRenderer.invoke("apikey:set", provider, key),
    get: (provider) => ipcRenderer.invoke("apikey:get", provider),
    delete: (provider) => ipcRenderer.invoke("apikey:delete", provider),
    validate: (provider, options) => ipcRenderer.invoke("apikey:validate", provider, options),
  },
  onThemeApply: (callback) => {
    const listener = (_event, data) => callback(data);
    ipcRenderer.on("theme:apply", listener);
    return () => ipcRenderer.removeListener("theme:apply", listener);
  },
  onLayerVisibilityUpdated: (callback) => {
    const listener = (_event, visible) => callback(visible);
    ipcRenderer.on("ui:layerVisibilityUpdated", listener);
    return () => ipcRenderer.removeListener("ui:layerVisibilityUpdated", listener);
  },
  onIdleTimeoutChanged: (callback) => {
    const listener = (_event, ms) => callback(ms);
    ipcRenderer.on("ui:idleTimeoutChanged", listener);
    return () => ipcRenderer.removeListener("ui:idleTimeoutChanged", listener);
  },
  onCompanionNameUpdated: (callback) => {
    const listener = (_event, name) => callback(name);
    ipcRenderer.on("ui:companionNameUpdated", listener);
    return () => ipcRenderer.removeListener("ui:companionNameUpdated", listener);
  },
  onBackendEvent: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on("backend:event", listener);
    return () => ipcRenderer.removeListener("backend:event", listener);
  },
  onBackendStderr: (cb) =>
    ipcRenderer.on("backend:stderr", (_event, line) => cb(line)),
  onStartupHealth: (cb) => ipcRenderer.on("startup:health", (_, data) => cb(data)),
  onUpdateAvailable: (callback) => {
    const listener = (_event, data) => callback(data);
    ipcRenderer.on("update:available", listener);
    return () => ipcRenderer.removeListener("update:available", listener);
  },
  onUpdateProgress: (callback) => {
    const listener = (_event, data) => callback(data);
    ipcRenderer.on("update:progress", listener);
    return () => ipcRenderer.removeListener("update:progress", listener);
  },
  onUpdateReady: (callback) => {
    const listener = () => callback();
    ipcRenderer.on("update:ready", listener);
    return () => ipcRenderer.removeListener("update:ready", listener);
  },
  setIgnoreMouseEvents: (ignore, options) => ipcRenderer.send("ui:setIgnoreMouseEvents", ignore, options),
  dismissBanner: (id) => ipcRenderer.send("startup:dismiss", id),
  dndStart: (until) => ipcRenderer.invoke("backend:dndStart", until),
  dndCancel: () => ipcRenderer.invoke("backend:dndCancel"),
  sandbox: {
    status: () => ipcRenderer.invoke("sandbox:status"),
    provision: () => ipcRenderer.invoke("sandbox:provision"),
    reset: () => ipcRenderer.invoke("sandbox:reset"),
    verify: () => ipcRenderer.invoke("sandbox:verify"),
  },
  getActiveModelCapabilities: (layer) => ipcRenderer.invoke("brain:activeModelCapabilities", layer),
  validateLayerRuntime: (layer) => ipcRenderer.invoke("backend:validateLayerRuntime", layer),
  setSelectedTargetLayer: (layer) => ipcRenderer.invoke("ui:setSelectedTargetLayer", layer),
  setLayerReasoningEffort: (layer, effort) => ipcRenderer.invoke("ui:setLayerReasoningEffort", layer, effort),
  ...(process.env.NODE_ENV !== "production" ? {
    dev: {
      simulateUpdateAvailable: () => ipcRenderer.invoke("dev:simulateUpdateAvailable"),
      simulateUpdateReady: () => ipcRenderer.invoke("dev:simulateUpdateReady"),
      simulateUpdateProgress: () => ipcRenderer.invoke("dev:simulateUpdateProgress"),
    },
  } : {}),
});

contextBridge.exposeInMainWorld("openCompanionTest", {
  getBackendDiagnostics: () => ipcRenderer.invoke("test:getBackendDiagnostics"),
  getProfileInfo: () => ipcRenderer.invoke("test:getProfileInfo"),
});
