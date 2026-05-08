const { contextBridge, ipcRenderer } = require("electron");
const CHATGPT_OAUTH_MODEL_CARDS = require("../shared/chatgpt_oauth_models.json");
const { SETTINGS_OPTIONS } = require("../shared/settings-options");
const { parseOverlayScaleValue } = require("./overlay-scale");
const CHATGPT_OAUTH_MODELS = Object.freeze(
  CHATGPT_OAUTH_MODEL_CARDS.map((card) => String(card?.id || "").trim()).filter(Boolean)
);

contextBridge.exposeInMainWorld("ocSettings", {
  testMode: process.env.OPEN_COMPANION_TEST_MODE === "1",
  options: SETTINGS_OPTIONS,
  load: () => ipcRenderer.invoke("settings:load"),
  save: (section, data) => ipcRenderer.invoke("settings:save", section, data),
  parseOverlayScaleValue: (value, fallback) => parseOverlayScaleValue(value, fallback),
  configReload: () => ipcRenderer.invoke("settings:configReload"),
  getOllamaModels: () => ipcRenderer.invoke("settings:getOllamaModels"),
  getProviderModels: (provider, options) => ipcRenderer.invoke("settings:getProviderModels", provider, options),
  validateLayerRuntime: (layer) => ipcRenderer.invoke("backend:validateLayerRuntime", layer),
  getOllamaModelInfo: (modelName) => ipcRenderer.invoke("settings:getOllamaModelInfo", modelName),
  getBrainModelInfo: (modelName) => ipcRenderer.invoke("brain:modelInfo", modelName),
  listPulledModels: () => ipcRenderer.invoke("brain:listPulledModels"),
  systemMemory: () => ipcRenderer.invoke("brain:systemMemory"),
  searchRegistry: (query) => ipcRenderer.invoke("brain:searchRegistry", query),
  activeModelCapabilities: () => ipcRenderer.invoke("brain:activeModelCapabilities"),
  getBrainContextInfo: () => ipcRenderer.invoke("brain:contextInfo"),
  getOllamaRunningModels: () => ipcRenderer.invoke("settings:getOllamaRunningModels"),
  getWindowsAccent: () => ipcRenderer.invoke("settings:getWindowsAccent"),
  applyTheme: (themeData) => ipcRenderer.invoke("settings:applyTheme", themeData),
  onThemeApply: (callback) => {
    const listener = (_event, data) => callback(data);
    ipcRenderer.on("theme:apply", listener);
    return () => ipcRenderer.removeListener("theme:apply", listener);
  },
  apikey: {
    set: (provider, key) => ipcRenderer.invoke("apikey:set", provider, key),
    get: (provider) => ipcRenderer.invoke("apikey:get", provider),
    delete: (provider) => ipcRenderer.invoke("apikey:delete", provider),
    validate: (provider, options) => ipcRenderer.invoke("apikey:validate", provider, options),
  },
  getApiKeys:  ()               => ipcRenderer.invoke("settings:getApiKeys"),
  setApiKey:   (account, value) => ipcRenderer.invoke("settings:setApiKey", { account, value }),
  getApiKey:   (account)        => ipcRenderer.invoke("settings:getApiKey", { account }),
  heartbeatRestart: () => ipcRenderer.invoke("heartbeat:restart"),
  heartbeatStart: () => ipcRenderer.invoke("heartbeat:start"),
  heartbeatStop: () => ipcRenderer.invoke("heartbeat:stop"),
  closeWindow: () => ipcRenderer.invoke("settings:close"),
  checkVoicePreviews: () => ipcRenderer.invoke("settings:checkVoicePreviews"),
  generateVoicePreview: (voice) => ipcRenderer.invoke("settings:generateVoicePreview", voice),
  getVoicePreviewUrl: (voice) => ipcRenderer.invoke("settings:getVoicePreviewUrl", voice),
  pullModel: (modelName) => ipcRenderer.invoke("settings:pullModel", modelName),
  cancelPull: () => ipcRenderer.invoke("settings:cancelPull"),
  deleteModel: (modelName) => ipcRenderer.invoke("settings:deleteModel", modelName),
  oauth: {
    chatgpt: {
      login: () => ipcRenderer.invoke("oauth:chatgpt:login"),
      logout: () => ipcRenderer.invoke("oauth:chatgpt:logout"),
      status: () => ipcRenderer.invoke("oauth:chatgpt:status"),
      models: CHATGPT_OAUTH_MODELS,
    },
  },
  generateSoulFile: (soul) => ipcRenderer.invoke("settings:generateSoulFile", soul),
  readSoulFile: () => ipcRenderer.invoke("settings:readSoulFile"),
  writeSoulFile: (content) => ipcRenderer.invoke("settings:writeSoulFile", { content }),
  readSoulGenerateTemplate: () => ipcRenderer.invoke("settings:readSoulGenerateTemplate"),
  getMemoryFiles: () => ipcRenderer.invoke("settings:getMemoryFiles"),
  deleteMemoryEntry: (file, line) => ipcRenderer.invoke("settings:deleteMemoryEntry", { file, line }),
  runDreamNow: () => ipcRenderer.invoke("settings:runDreamNow"),
  resetMemoryNow: () => ipcRenderer.invoke("settings:resetMemoryNow"),
  sandboxStatus: () => ipcRenderer.invoke("sandbox:status"),
  sandboxProvision: () => ipcRenderer.invoke("sandbox:provision"),
  sandboxReset: () => ipcRenderer.invoke("sandbox:reset"),
  sandboxVerify: () => ipcRenderer.invoke("sandbox:verify"),
  runtimeStatus: () => ipcRenderer.invoke("runtime:status"),
  runtimePrepare: () => ipcRenderer.invoke("runtime:prepare"),
  runtimeCancel: () => ipcRenderer.invoke("runtime:cancel"),
  runtimeRetry: () => ipcRenderer.invoke("runtime:retry"),
  updaterStatus: () => ipcRenderer.invoke("updater:status"),
  updaterCheckNow: () => ipcRenderer.invoke("updater:checkNow"),
  assetsStatus: () => ipcRenderer.invoke("assets:status"),
  assetsCheckNow: () => ipcRenderer.invoke("assets:checkNow"),
  onPullProgress: (callback) => {
    const listener = (_event, data) => callback(data);
    ipcRenderer.on("model:pullProgress", listener);
    return () => ipcRenderer.removeListener("model:pullProgress", listener);
  },
  onRuntimeEvent: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on("runtime:event", listener);
    return () => ipcRenderer.removeListener("runtime:event", listener);
  },
});

contextBridge.exposeInMainWorld("ocSettingsTest", {
  getBackendDiagnostics: () => ipcRenderer.invoke("test:getBackendDiagnostics"),
  getProfileInfo: () => ipcRenderer.invoke("test:getProfileInfo"),
});
