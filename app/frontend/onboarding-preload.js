const { contextBridge, ipcRenderer } = require("electron");
const { SETTINGS_OPTIONS } = require("../shared/settings-options");

contextBridge.exposeInMainWorld("ocOnboarding", {
  options: SETTINGS_OPTIONS,
  load: () => ipcRenderer.invoke("onboarding:load"),
  getConfig: () => ipcRenderer.invoke("onboarding:getConfig"),
  save: (data) => ipcRenderer.invoke("onboarding:save", data),
  setConfig: (...args) => ipcRenderer.invoke("onboarding:setConfig", ...args),
  parseImport: (text) => ipcRenderer.invoke("onboarding:parseImport", text),
  writeImport: (payload) => ipcRenderer.invoke("onboarding:writeImport", payload),
  checkOllama: () => ipcRenderer.invoke("onboarding:checkOllama"),
  checkOllamaModels: (tags) => ipcRenderer.invoke("onboarding:check-models", tags),
  pullModel: (tag) => ipcRenderer.invoke("onboarding:pull-model", tag),
  runtimeStatus: (payload) => ipcRenderer.invoke("runtime:status", payload),
  runtimePrepare: (payload) => ipcRenderer.invoke("runtime:prepare", payload),
  runtimeCancel: () => ipcRenderer.invoke("runtime:cancel"),
  runtimeRetry: (payload) => ipcRenderer.invoke("runtime:retry", payload),
  resetMemory: () => ipcRenderer.invoke("onboarding:resetMemory"),
  apikey: {
    set: (provider, key) => ipcRenderer.invoke("apikey:set", provider, key),
    get: (provider) => ipcRenderer.invoke("apikey:get", provider),
    delete: (provider) => ipcRenderer.invoke("apikey:delete", provider),
    validate: (provider, options) => ipcRenderer.invoke("apikey:validate", provider, options),
  },
  onRuntimeEvent: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on("runtime:event", listener);
    return () => ipcRenderer.removeListener("runtime:event", listener);
  },
  onPullProgress: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on("onboarding:pull-progress", listener);
    return () => ipcRenderer.removeListener("onboarding:pull-progress", listener);
  },
  complete: () => ipcRenderer.invoke("onboarding:complete"),
  finishSetup: () => ipcRenderer.invoke("onboarding:finishSetup"),
  closeWindow: () => ipcRenderer.invoke("onboarding:close"),
  copyText: (text) => ipcRenderer.invoke("ui:copyText", text),
  checkVoicePreviews: () => ipcRenderer.invoke("settings:checkVoicePreviews"),
  generateVoicePreview: (voice) => ipcRenderer.invoke("settings:generateVoicePreview", voice),
  getVoicePreviewUrl: (voice) => ipcRenderer.invoke("settings:getVoicePreviewUrl", voice),
  onThemeApply: (callback) => {
    const listener = (_event, data) => callback(data);
    ipcRenderer.on("theme:apply", listener);
    return () => ipcRenderer.removeListener("theme:apply", listener);
  },
});
