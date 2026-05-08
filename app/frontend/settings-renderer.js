// ── State ──
let loadedConfig = {};
let currentBaseMode = "dark";
let statusTimers = {};
let selectedVoice = "af_nova";
let currentAudio = null;
let currentPlayingVoice = null;
let memoryViewerState = { files: [] };
let memoryViewerLoadPromise = null;
let ollamaModelsCache = [];
let ollamaRunningModelsCache = [];
let ollamaModelInfoCache = {};
let providerModelsCache = {};
let modelPullCleanup = null;
let isHydratingSettings = false;
let autoSaveSuspended = false;
let autoSaveTimers = {};
let autoSaveInFlight = {};
let autoSaveQueued = {};
let layerRuntimeValidationState = {
  token: 0,
  layer: "",
  validation: null,
  all: {},
};
const parseOverlayScaleValue = (value, fallback = 50) => (
  typeof window.ocSettings?.parseOverlayScaleValue === "function"
    ? window.ocSettings.parseOverlayScaleValue(value, fallback)
    : (() => {
      const parsed = Number.parseInt(value ?? fallback, 10);
      const safeValue = Number.isFinite(parsed) ? parsed : fallback;
      return Math.max(0, Math.min(100, safeValue));
    })()
);
let discoverSectionInitialized = false;
const DEFAULT_SETTINGS_OPTIONS = window.ocSettings?.options || {};
let CURATED_MODELS = [
  { name: "gemma4:e4b",       description: "Google Gemma 4 — efficient 4B model",        tags: ["e4b", "12b", "27b"] },
  { name: "gemma4:12b",       description: "Google Gemma 4 — balanced 12B model",         tags: ["12b", "27b"] },
  { name: "qwen2.5:7b",       description: "Alibaba Qwen 2.5 — fast 7B model",            tags: ["3b", "7b", "14b", "32b", "72b"] },
  { name: "llama3.2:3b",      description: "Meta Llama 3.2 — lightweight 3B model",       tags: ["1b", "3b"] },
  { name: "llama3.3:70b",     description: "Meta Llama 3.3 — powerful 70B model",         tags: ["70b"] },
  { name: "mistral:7b",       description: "Mistral 7B — fast general-purpose model",     tags: ["7b"] },
  { name: "phi4:14b",         description: "Microsoft Phi-4 — reasoning 14B model",       tags: ["14b", "mini"] },
  { name: "deepseek-r1:8b",   description: "DeepSeek R1 — reasoning model",               tags: ["1.5b", "7b", "8b", "14b", "32b", "70b"] },
  { name: "nomic-embed-text", description: "Nomic embed-text — embedding model",          tags: ["latest"] },
];
let EMBEDDING_MODEL_HINTS = [
  "nomic-embed-text",
  "mxbai-embed-large",
  "all-minilm",
  "snowflake-arctic-embed",
  "granite-embedding",
  "bge-",
];
let MODEL_LAYERS = [
  { uiId: "companion",       configKey: "companion" },
  { uiId: "assistant",       configKey: "assistant", legacyConfigKey: "assistant_low" },
];

function normalizeOllamaTag(name) {
  // Strip a bare ":latest" suffix so "gemma4:e4b:latest" matches "gemma4:e4b"
  return String(name || "").replace(/:latest$/, "");
}

function normalizeLayerTemperatureValue(value) {
  if (value === "" || value == null) {
    return "";
  }
  const parsed = Number.parseFloat(value);
  if (!Number.isFinite(parsed)) {
    return "";
  }
  return Math.max(0, Math.min(2, parsed));
}

function normalizeLayerMaxTokensValue(value) {
  if (value === "" || value == null) {
    return "";
  }
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) {
    return "";
  }
  return Math.max(64, parsed);
}
let CLOUD_PROVIDER_SPECS = {
  openai: {
    label: "OpenAI",
    keyAccount: "openai",
    keyLabel: "OpenAI API key",
    keyPlaceholder: "sk-...",
    models: [],
  },
  anthropic: {
    label: "Anthropic",
    keyAccount: "anthropic",
    keyLabel: "Anthropic API key",
    keyPlaceholder: "sk-ant-...",
    models: [],
  },
  gemini: {
    label: "Gemini",
    keyAccount: "gemini",
    keyLabel: "Gemini API key",
    keyPlaceholder: "AIza...",
    models: ["gemini-3.1-pro-preview", "gemini-3-pro-preview", "gemini-3-flash-preview", "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"],
  },
  openrouter: {
    label: "OpenRouter",
    keyAccount: "openrouter",
    keyLabel: "OpenRouter API key",
    keyPlaceholder: "sk-or-...",
    models: ["deepseek/deepseek-v4-pro", "deepseek/deepseek-v4-flash", "moonshotai/kimi-k2.6", "meta-llama/llama-3.3-70b-instruct", "google/gemini-2.5-flash", "anthropic/claude-sonnet-4", "qwen/qwen3-235b-a22b"],
  },
  qwen_cloud: {
    label: "Qwen DashScope",
    keyAccount: "qwen",
    keyLabel: "DashScope API key",
    keyPlaceholder: "sk-...",
    models: ["qwen3-max", "qwen3-max-preview", "qwen3.5-plus", "qwen3-235b-a22b", "qwen3-coder-plus", "qwen3.5-flash"],
  },
  custom: {
    label: "Custom",
    keyAccount: "custom",
    keyLabel: "Custom API key",
    keyPlaceholder: "Optional",
    models: [],
    customOnly: true,
    urlAccount: "custom_url",
  },
};
let CLOUD_PROVIDER_ORDER = ["anthropic", "chatgpt_oauth", "custom", "gemini", "openai", "openrouter", "qwen_cloud"];
let API_KEY_FIELD_MAP = {
  openai: "apikey-openai",
  anthropic: "apikey-anthropic",
  gemini: "apikey-gemini",
  openrouter: "apikey-openrouter",
  qwen: "apikey-qwen",
  qwen_cloud: "apikey-qwen",
  custom: "apikey-custom",
  custom_url: "apikey-custom-url",
};

let OLLAMA_BACKED_PROVIDERS = new Set(["gemma", "qwen", "ollama"]);

function isOllamaBackedProvider(provider) {
  return OLLAMA_BACKED_PROVIDERS.has(String(provider || "").trim().toLowerCase());
}
const MODEL_VALUE_MEMORY = {
  brain: {},
  companion: {},
  assistant: {},
};

let HEARTBEAT_INTERVAL_OPTIONS = [
  { seconds: 600, label: "10 min" },
  { seconds: 1800, label: "30 min" },
  { seconds: 3600, label: "1 hour" },
  { seconds: 10800, label: "3 hours" },
  { seconds: 21600, label: "6 hours" },
];
const COMPANION_READ_ONLY_TOOLS = new Set();

let VOICE_GROUP_ORDER = [
  "US English / Female",
  "US English / Male",
  "British English / Female",
  "British English / Male",
];
let VOICES = [
  { id: "af_alloy",    label: "Alloy",    tag: "US English", group: "US English / Female" },
  { id: "af_aoede",    label: "Aoede",    tag: "US English", group: "US English / Female" },
  { id: "af_bella",    label: "Bella",    tag: "US English", group: "US English / Female" },
  { id: "af_heart",    label: "Heart",    tag: "US English", group: "US English / Female" },
  { id: "af_jessica",  label: "Jessica",  tag: "US English", group: "US English / Female" },
  { id: "af_kore",     label: "Kore",     tag: "US English", group: "US English / Female" },
  { id: "af_nicole",   label: "Nicole",   tag: "US English", group: "US English / Female" },
  { id: "af_nova",     label: "Nova",     tag: "US English", group: "US English / Female" },
  { id: "af_river",    label: "River",    tag: "US English", group: "US English / Female" },
  { id: "af_sarah",    label: "Sarah",    tag: "US English", group: "US English / Female" },
  { id: "af_sky",      label: "Sky",      tag: "US English", group: "US English / Female" },
  { id: "am_adam",     label: "Adam",     tag: "US English", group: "US English / Male" },
  { id: "am_echo",     label: "Echo",     tag: "US English", group: "US English / Male" },
  { id: "am_eric",     label: "Eric",     tag: "US English", group: "US English / Male" },
  { id: "am_fenrir",   label: "Fenrir",   tag: "US English", group: "US English / Male" },
  { id: "am_liam",     label: "Liam",     tag: "US English", group: "US English / Male" },
  { id: "am_michael",  label: "Michael",  tag: "US English", group: "US English / Male" },
  { id: "am_onyx",     label: "Onyx",     tag: "US English", group: "US English / Male" },
  { id: "am_puck",     label: "Puck",     tag: "US English", group: "US English / Male" },
  { id: "am_santa",    label: "Santa",    tag: "US English", group: "US English / Male" },
  { id: "bf_alice",    label: "Alice",    tag: "British English", group: "British English / Female" },
  { id: "bf_emma",     label: "Emma",     tag: "British English", group: "British English / Female" },
  { id: "bf_isabella", label: "Isabella", tag: "British English", group: "British English / Female" },
  { id: "bf_lily",     label: "Lily",     tag: "British English", group: "British English / Female" },
  { id: "bm_daniel",   label: "Daniel",   tag: "British English", group: "British English / Male" },
  { id: "bm_fable",    label: "Fable",    tag: "British English", group: "British English / Male" },
  { id: "bm_george",   label: "George",   tag: "British English", group: "British English / Male" },
  { id: "bm_lewis",    label: "Lewis",    tag: "British English", group: "British English / Male" },
];

const CHATGPT_OAUTH_MODELS = Object.freeze(
  Array.isArray(window.ocSettings?.oauth?.chatgpt?.models) && window.ocSettings.oauth.chatgpt.models.length
    ? window.ocSettings.oauth.chatgpt.models.map((model) => String(model || "").trim()).filter(Boolean)
    : ["gpt-5.4"]
);
let PERSONA_SOUL_IDENTITY_BLOCK_MARKER = "## OpenCompanion Identity";

// ── DOM helpers ──
const $ = (id) => document.getElementById(id);

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function mergeConfig(base, override) {
  if (!isPlainObject(base) || !isPlainObject(override)) {
    return override;
  }

  const merged = { ...base };
  for (const [key, value] of Object.entries(override)) {
    if (isPlainObject(value) && isPlainObject(base[key])) {
      merged[key] = mergeConfig(base[key], value);
    } else {
      merged[key] = value;
    }
  }

  return merged;
}

function cloneConfigValue(value) {
  if (Array.isArray(value)) {
    return value.map((item) => cloneConfigValue(item));
  }
  if (isPlainObject(value)) {
    const cloned = {};
    for (const [key, item] of Object.entries(value)) {
      cloned[key] = cloneConfigValue(item);
    }
    return cloned;
  }
  return value;
}

function configuredArray(options, key, fallback) {
  return Array.isArray(options?.[key]) ? cloneConfigValue(options[key]) : cloneConfigValue(fallback);
}

function configuredObject(options, key, fallback) {
  return isPlainObject(options?.[key]) ? cloneConfigValue(options[key]) : cloneConfigValue(fallback);
}

function applySettingsOptions(options) {
  if (!isPlainObject(options)) {
    return;
  }
  CURATED_MODELS = configuredArray(options, "curated_ollama_models", CURATED_MODELS);
  EMBEDDING_MODEL_HINTS = configuredArray(options, "embedding_model_hints", EMBEDDING_MODEL_HINTS);
  MODEL_LAYERS = configuredArray(options, "model_layers", MODEL_LAYERS);
  CLOUD_PROVIDER_SPECS = configuredObject(options, "cloud_provider_specs", CLOUD_PROVIDER_SPECS);
  CLOUD_PROVIDER_ORDER = configuredArray(options, "cloud_provider_order", CLOUD_PROVIDER_ORDER);
  API_KEY_FIELD_MAP = configuredObject(options, "api_key_field_map", API_KEY_FIELD_MAP);
  OLLAMA_BACKED_PROVIDERS = new Set(configuredArray(options, "ollama_backed_providers", Array.from(OLLAMA_BACKED_PROVIDERS)));
  HEARTBEAT_INTERVAL_OPTIONS = configuredArray(options, "heartbeat_interval_options", HEARTBEAT_INTERVAL_OPTIONS);
  VOICE_GROUP_ORDER = configuredArray(options, "voice_group_order", VOICE_GROUP_ORDER);
  VOICES = configuredArray(options, "voices", VOICES);
  PERSONA_SOUL_IDENTITY_BLOCK_MARKER = String(options.persona_soul_identity_block_marker || PERSONA_SOUL_IDENTITY_BLOCK_MARKER);
}

applySettingsOptions(DEFAULT_SETTINGS_OPTIONS);

function normalizePronouns(value) {
  const raw = String(value || "").trim().toLowerCase();
  return raw || "she/her";
}

function getSoulPronounsValue() {
  if ($("persona-pronouns")?.value === "custom") {
    const subject = $("persona-pronoun-subject")?.value.trim().toLowerCase();
    const object = $("persona-pronoun-object")?.value.trim().toLowerCase();
    const possessive = $("persona-pronoun-possessive")?.value.trim().toLowerCase();
    if (subject && object && possessive) {
      return `${subject}/${object}/${possessive}`;
    }
    return "they/them/their";
  }
  return normalizePronouns($("persona-pronouns")?.value);
}

function toggleCustomPronounsRow() {
  const row = $("persona-pronouns-custom-row");
  if (!row) {
    return;
  }
  row.style.display = $("persona-pronouns")?.value === "custom" ? "flex" : "none";
}

function buildPersonaSoulIdentityBlock({ name, pronouns, userName }) {
  const lines = [
    PERSONA_SOUL_IDENTITY_BLOCK_MARKER,
    `- Companion name: ${String(name || "").trim() || "Nova"}`,
    `- Pronouns: ${normalizePronouns(pronouns)}`,
  ];
  if (String(userName || "").trim()) {
    lines.push(`- What to call the user: ${String(userName).trim()}`);
  }
  lines.push("", "---");
  return lines.join("\n");
}

function syncPersonaIdentityIntoSoul(rawSoul, identity) {
  const block = buildPersonaSoulIdentityBlock(identity);
  const cleanSoul = String(rawSoul || "").trim();
  if (!cleanSoul) {
    return block;
  }
  const blockPattern = /## OpenCompanion Identity[\s\S]*?\n---\s*/;
  if (blockPattern.test(cleanSoul)) {
    return cleanSoul.replace(blockPattern, `${block}\n\n`);
  }
  return `${block}\n\n${cleanSoul}`;
}

function setAutoSaveSuspended(value) {
  autoSaveSuspended = Boolean(value);
}

function showStatus(section, message, isError = false) {
  const el = $(`status-${section}`) || $(`${section}-status`);
  if (!el) return;
  el.textContent = message || "Saved.";
  el.classList.remove("hidden");
  el.style.color = isError ? "#ff8a8a" : "";
  if (statusTimers[section]) clearTimeout(statusTimers[section]);
  statusTimers[section] = setTimeout(() => {
    el.classList.add("hidden");
    el.style.color = "";
  }, 3000);
}

// ── Tab navigation ──
const tabLoaders = new Map();

function registerTabLoader(tab, loader) {
  if (!tab || typeof loader !== "function") return;
  tabLoaders.set(tab, loader);
}

async function activateTab(tab) {
  const nextTab = String(tab || "").trim();
  if (!nextTab) return;

  document.querySelectorAll(".oc-nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === nextTab));
  document.querySelectorAll(".oc-page").forEach((p) => p.classList.toggle("active", p.id === `page-${nextTab}`));

  const loader = tabLoaders.get(nextTab);
  if (loader) {
    try {
      await loader();
    } catch (error) {
      console.error(`Failed to load ${nextTab} tab:`, error);
    }
  }
}

document.querySelectorAll(".oc-nav-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    activateTab(btn.dataset.tab);
  });
});

function normalizeMemoryFiles(files) {
  if (!Array.isArray(files)) {
    return [];
  }

  return files
    .map((fileEntry) => {
      const file = String((fileEntry && fileEntry.file) || "").trim();
      const entries = Array.isArray(fileEntry && fileEntry.entries)
        ? fileEntry.entries
            .map((entry) => ({
              line: Number(entry && entry.line),
              text: String((entry && entry.text) || ""),
            }))
            .filter((entry) => Number.isInteger(entry.line) && entry.line >= 0)
        : [];

      return { file, entries };
    })
    .filter((fileEntry) => fileEntry.file && fileEntry.entries.length > 0)
    .sort((a, b) => a.file.localeCompare(b.file));
}

function formatMemorySectionLabel(file) {
  const base = String(file || "")
    .replace(/^memory_/i, "")
    .replace(/\.md$/i, "")
    .replace(/[_-]+/g, " ")
    .trim();

  if (!base) {
    return "Memory";
  }

  return base
    .split(/\s+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function renderMemoryMessage(message) {
  const container = $("memory-viewer-content");
  if (!container) {
    return;
  }

  container.replaceChildren();
  const el = document.createElement("div");
  el.className = "memory-viewer-empty";
  el.textContent = message;
  container.appendChild(el);
}

function syncMemorySectionLines(sectionEl, fileEntry) {
  if (!sectionEl || !fileEntry || !Array.isArray(fileEntry.entries)) {
    return;
  }

  const rows = sectionEl.querySelectorAll(".memory-entry");
  rows.forEach((row, index) => {
    const entry = fileEntry.entries[index];
    if (entry) {
      row.dataset.line = String(entry.line);
    }
  });
}

function applyMemoryDeleteToState(file, line) {
  const nextFiles = [];

  for (const fileEntry of memoryViewerState.files) {
    if (fileEntry.file !== file) {
      nextFiles.push(fileEntry);
      continue;
    }

    const nextEntries = [];
    for (const entry of fileEntry.entries) {
      if (entry.line === line) {
        continue;
      }
      nextEntries.push({
        line: entry.line > line ? entry.line - 1 : entry.line,
        text: entry.text,
      });
    }

    if (nextEntries.length > 0) {
      nextFiles.push({
        file: fileEntry.file,
        entries: nextEntries,
      });
    }
  }

  memoryViewerState = { files: nextFiles };
  return nextFiles.find((fileEntry) => fileEntry.file === file) || null;
}

function renderMemoryViewer() {
  const container = $("memory-viewer-content");
  if (!container) {
    return;
  }

  container.replaceChildren();
  const files = Array.isArray(memoryViewerState.files) ? memoryViewerState.files : [];

  if (!files.length) {
    renderMemoryMessage("No memories stored yet.");
    return;
  }

  for (const fileEntry of files) {
    const section = document.createElement("section");
    section.className = "memory-section";
    section.dataset.file = fileEntry.file;

    const label = document.createElement("div");
    label.className = "memory-section-label";
    label.textContent = formatMemorySectionLabel(fileEntry.file);

    const card = document.createElement("div");
    card.className = "oc-card memory-section-card";

    for (const entry of fileEntry.entries) {
      const row = document.createElement("div");
      row.className = "memory-entry";
      row.dataset.file = fileEntry.file;
      row.dataset.line = String(entry.line);

      const text = document.createElement("div");
      text.className = "memory-entry-text";
      text.textContent = entry.text;

      const deleteBtn = document.createElement("button");
      deleteBtn.type = "button";
      deleteBtn.className = "memory-entry-delete";
      deleteBtn.dataset.file = fileEntry.file;
      deleteBtn.dataset.line = String(entry.line);
      deleteBtn.textContent = "×";
      deleteBtn.addEventListener("click", async () => {
        const targetFile = row.dataset.file;
        const targetLine = Number.parseInt(row.dataset.line, 10);
        if (!targetFile || !Number.isInteger(targetLine)) {
          return;
        }

        try {
          const result = await window.ocSettings.deleteMemoryEntry(targetFile, targetLine);
          if (!result || result.ok === false) {
            throw new Error((result && result.error) || "Unable to delete memory entry.");
          }

          const nextFileState = applyMemoryDeleteToState(targetFile, targetLine);
          row.remove();

          if (!nextFileState) {
            section.remove();
          } else {
            syncMemorySectionLines(section, nextFileState);
          }

          if (!memoryViewerState.files.length) {
            renderMemoryMessage("No memories stored yet.");
          }
        } catch (error) {
          console.error("Failed to delete memory entry:", error);
        }
      });

      row.appendChild(text);
      row.appendChild(deleteBtn);
      card.appendChild(row);
    }

    section.appendChild(label);
    section.appendChild(card);
    container.appendChild(section);
  }
}

async function loadMemoryViewer() {
  if (memoryViewerLoadPromise) {
    return memoryViewerLoadPromise;
  }

  renderMemoryMessage("Loading...");
  memoryViewerLoadPromise = (async () => {
    try {
      const result = await window.ocSettings.getMemoryFiles();
      if (!result || result.ok === false) {
        memoryViewerState = { files: [] };
        renderMemoryMessage(`Unable to load memories${result && result.error ? `: ${result.error}` : "."}`);
        return;
      }

      memoryViewerState = { files: normalizeMemoryFiles(result.files) };
      renderMemoryViewer();
    } catch (error) {
      memoryViewerState = { files: [] };
      renderMemoryMessage(`Unable to load memories: ${error.message}`);
    }
  })();

  try {
    await memoryViewerLoadPromise;
  } finally {
    memoryViewerLoadPromise = null;
  }
}

registerTabLoader("memory", loadMemoryViewer);
registerTabLoader("heartbeat", async () => {
  renderHeartbeatUi();
});

const memoryRefreshButton = $("memory-refresh-btn");
if (memoryRefreshButton) {
  memoryRefreshButton.addEventListener("click", () => {
    void loadMemoryViewer();
  });
}

function setMemoryActionButtonsDisabled(disabled) {
  $("memory-dream-now-btn") && ($("memory-dream-now-btn").disabled = Boolean(disabled));
  $("memory-reset-now-btn") && ($("memory-reset-now-btn").disabled = Boolean(disabled));
}

async function runDreamNow() {
  try {
    setMemoryActionButtonsDisabled(true);
    showStatus("memory", "Dream started.");
    const result = await window.ocSettings.runDreamNow();
    if (!result || result.ok === false) {
      throw new Error(result?.error || "Dream run failed.");
    }
  } catch (error) {
    showStatus("memory", error.message || "Dream run failed.", true);
  } finally {
    setMemoryActionButtonsDisabled(false);
  }
}

function openMemoryResetModal() {
  const modal = $("memory-reset-modal");
  if (modal) {
    modal.style.display = "flex";
  }
}

function closeMemoryResetModal() {
  const modal = $("memory-reset-modal");
  if (modal) {
    modal.style.display = "none";
  }
}

async function confirmMemoryResetNow() {
  const confirmButton = $("memory-reset-confirm-btn");
  const closeButton = $("memory-reset-close");
  const cancelButton = $("memory-reset-cancel");

  try {
    setMemoryActionButtonsDisabled(true);
    if (confirmButton) {
      confirmButton.disabled = true;
      confirmButton.textContent = "Resetting...";
    }
    if (closeButton) {
      closeButton.disabled = true;
    }
    if (cancelButton) {
      cancelButton.disabled = true;
    }
    const result = await window.ocSettings.resetMemoryNow();
    if (!result || result.ok === false) {
      throw new Error(result?.error || "Could not reset long-term memory.");
    }
    closeMemoryResetModal();
    await loadMemoryViewer();
    showStatus("memory", "Memory reset.");
  } catch (error) {
    showStatus("memory", error.message || "Could not reset long-term memory.", true);
  } finally {
    setMemoryActionButtonsDisabled(false);
    if (confirmButton) {
      confirmButton.disabled = false;
      confirmButton.textContent = "Reset memory";
    }
    if (closeButton) {
      closeButton.disabled = false;
    }
    if (cancelButton) {
      cancelButton.disabled = false;
    }
  }
}

$("memory-dream-now-btn")?.addEventListener("click", () => {
  void runDreamNow();
});

$("memory-reset-now-btn")?.addEventListener("click", openMemoryResetModal);
$("memory-reset-close")?.addEventListener("click", closeMemoryResetModal);
$("memory-reset-cancel")?.addEventListener("click", closeMemoryResetModal);
$("memory-reset-modal")?.addEventListener("click", (event) => {
  if (event.target === $("memory-reset-modal")) {
    closeMemoryResetModal();
  }
});
$("memory-reset-confirm-btn")?.addEventListener("click", () => {
  void confirmMemoryResetNow();
});

window.ocSettings.onRuntimeEvent((payload) => {
  if (payload?.type === "dream_completed") {
    void loadMemoryViewer();
    showStatus("memory", "Dream complete.");
    return;
  }
  if (payload?.type === "dream_failed") {
    showStatus("memory", payload?.message || "Dream run failed.", true);
  }
});

document.querySelectorAll(".heartbeat-segment-btn").forEach((button) => {
  button.addEventListener("click", () => {
    selectHeartbeatInterval(button.dataset.heartbeatInterval);
  });
});

$("heartbeat-enabled")?.addEventListener("change", () => {
  renderHeartbeatUi();
  scheduleAutoSave("heartbeat", { immediate: true });
});
$("heartbeat-only-idle")?.addEventListener("change", () => {
  renderHeartbeatUi();
  scheduleAutoSave("heartbeat", { immediate: true });
});
$("heartbeat-custom-minutes")?.addEventListener("input", () => {
  selectHeartbeatInterval("custom");
});
$("heartbeat-custom-minutes")?.addEventListener("blur", () => {
  const input = $("heartbeat-custom-minutes");
  if (input) {
    input.value = String(clampHeartbeatCustomMinutes(input.value));
  }
  renderHeartbeatUi();
});

function formatLastChecked(timestamp) {
  const value = Number(timestamp || 0);
  if (!Number.isFinite(value) || value <= 0) {
    return "Last checked: never";
  }
  return `Last checked: ${new Date(value).toLocaleString()}`;
}

function renderRuntimeAssetFooter(status) {
  const el = $("settings-assets-footer");
  if (!el) {
    return;
  }

  if (!status || typeof status !== "object") {
    el.textContent = "";
    return;
  }

  if (status.updating || status.checking) {
    el.textContent = "Runtime assets: checking";
    return;
  }

  if (status.last_error) {
    el.textContent = `Runtime assets: ${status.last_error}`;
    return;
  }

  el.textContent = status.ready ? "Runtime assets: ready" : "Runtime assets: pending";
}

function renderUpdaterFooter(status) {
  const footerEl = $("settings-updater-footer");
  const buttonEl = $("check-updates-btn");
  if (footerEl) {
    footerEl.textContent = formatLastChecked(status?.lastCheckedAt);
  }
  if (buttonEl) {
    buttonEl.disabled = Boolean(status?.checking);
    buttonEl.textContent = status?.checking ? "Checking..." : "Check for updates";
  }
}

function updaterStatusMessage(status) {
  if (!status || typeof status !== "object") {
    return "";
  }
  if (status.error) {
    return `Update check failed: ${status.error}`;
  }
  if (status.ready) {
    return "Update downloaded. Restart to apply.";
  }
  if (status.downloading) {
    return `Downloading update${Number.isFinite(status.downloadProgressPercent) && status.downloadProgressPercent > 0 ? `... ${status.downloadProgressPercent}%` : "..."}`;
  }
  if (status.available) {
    return status.latestVersion ? `Update available: v${status.latestVersion}` : "Update available.";
  }
  if (status.upToDate) {
    return "App is up to date.";
  }
  return "";
}

async function refreshUpdaterControls() {
  try {
    const [updaterStatus, assetStatus] = await Promise.all([
      window.ocSettings.updaterStatus(),
      window.ocSettings.assetsStatus(),
    ]);
    renderUpdaterFooter(updaterStatus);
    renderRuntimeAssetFooter(assetStatus);
    return updaterStatus;
  } catch (error) {
    renderUpdaterFooter({});
    renderRuntimeAssetFooter({});
    console.error("Failed to refresh updater controls:", error);
    return {};
  }
}

$("check-updates-btn")?.addEventListener("click", async () => {
  renderUpdaterFooter({ checking: true, lastCheckedAt: Date.now() });
  try {
    const status = await window.ocSettings.updaterCheckNow();
    renderUpdaterFooter(status);
    showStatus("updater", updaterStatusMessage(status) || "Update check finished.", Boolean(status?.error));
  } catch (error) {
    renderUpdaterFooter({ checking: false, lastCheckedAt: Date.now() });
    showStatus("updater", error.message || "Update check failed.", true);
  }
});


// ── Close button ──
$("s-close").addEventListener("click", () => {
  window.ocSettings.closeWindow();
});

// ── Slider live-update wiring ──
function wireSlider(sliderId, valId, formatter) {
  const slider = $(sliderId);
  const display = $(valId);
  if (!slider || !display) return;
  slider.addEventListener("input", () => {
    display.textContent = formatter(slider.value);
  });
}

// s-temp is now a 0–2 range slider — wired via initGlobalTempControl() below
wireSlider("s-topk", "s-topk-val", (v) => v);
wireSlider("s-vol", "s-vol-val", (v) => `${v}%`);
wireSlider("s-speed", "s-speed-val", (v) => `${(v / 10).toFixed(1)}x`);
wireSlider("s-overlay-scale", "s-overlay-scale-val", (v) => String(v));
// s-temp input is handled by initGlobalTempControl()
$("s-max-tokens")?.addEventListener("input", () => {
  for (const { uiId } of MODEL_LAYERS) {
    updateLayerMaxTokensHint(uiId);
  }
});
$("s-max-tokens")?.addEventListener("change", () => {
  const input = $("s-max-tokens");
  if (!input) {
    return;
  }
  input.value = String(getGlobalMaxTokensValue());
  for (const { uiId } of MODEL_LAYERS) {
    updateLayerMaxTokensHint(uiId);
  }
});
function activateCustomContextWindowEntry() {
  const input = $("s-ctx");
  const btn = $("s-ctx-auto-btn");
  if (!input || !btn || !btn.classList.contains("active")) {
    return;
  }
  applyContextWindowSetting(32768);
  scheduleAutoSave("model", { immediate: true });
  input.focus();
  input.select?.();
  void refreshModelUiState();
}
$("s-ctx")?.addEventListener("input", () => { scheduleAutoSave("model", { delay: 700 }); void refreshModelUiState(); });
$("s-ctx")?.addEventListener("change", () => { scheduleAutoSave("model", { immediate: true }); void refreshModelUiState(); });
// Wrapper click activates the field when Auto is on — disabled inputs swallow pointer events
document.querySelector(".oc-ctx-wrap")?.addEventListener("click", (e) => {
  const input = $("s-ctx");
  if (input?.disabled && e.target !== $("s-ctx-auto-btn")) {
    applyContextWindowSetting("");
    input.focus();
    void refreshModelUiState();
  }
});
$("s-ctx")?.addEventListener("focus", () => {
  activateCustomContextWindowEntry();
});
document.querySelector(".oc-ctx-wrap")?.addEventListener("click", (e) => {
  if (e.target !== $("s-ctx-auto-btn")) {
    activateCustomContextWindowEntry();
  }
});
$("s-ctx-auto-btn")?.addEventListener("click", () => {
  applyContextWindowSetting("auto");
  scheduleAutoSave("model", { immediate: true });
  void refreshModelUiState();
});
// Layer override wiring is done in initLayerOverrideControls() below

// ── Provider change listeners ──
const brainProviderSelect = $("brain-provider");
if (brainProviderSelect) {
  brainProviderSelect.addEventListener("change", () => {
    handleBrainProviderChange().catch((error) => {
      console.error("Brain provider update failed:", error);
    });
  });
}

const brainModelContainer = $("brain-model-container");
if (brainModelContainer) {
  const syncInheritedBrainModel = () => {
    MODEL_LAYERS.forEach(({ uiId }) => {
      if ($(`${uiId}-provider`)?.value === "default") {
        setLayerModelValue(uiId, getBrainModelValue());
      }
    });
    void refreshModelUiState();
  };
  brainModelContainer.addEventListener("input", syncInheritedBrainModel);
  brainModelContainer.addEventListener("change", syncInheritedBrainModel);
}

MODEL_LAYERS.forEach(({ uiId }) => {
  const sel = $(`${uiId}-provider`);
  if (sel) {
    sel.addEventListener("change", () => {
      updateModelField(uiId).catch(() => {});
      void refreshModelUiState();
    });
  }
});

// ── Soul pronouns ──
$("persona-pronouns")?.addEventListener("change", () => {
  toggleCustomPronounsRow();
});

// ── RGB / Accent ──
function toHex(r, g, b) {
  return (
    "#" +
    [r, g, b]
      .map((v) =>
        Math.max(0, Math.min(255, Number(v) || 0))
          .toString(16)
          .padStart(2, "0")
      )
      .join("")
  );
}

function updateAccent() {
  const r = $("s-r").value;
  const g = $("s-g").value;
  const b = $("s-b").value;
  const hex = toHex(r, g, b);
  $("s-accent-preview").style.background = `rgb(${r},${g},${b})`;
  $("s-accent-hex").textContent = hex;
  document.documentElement.style.setProperty("--oc-accent", hex);
  document.documentElement.style.setProperty("--oc-accent-rgb", `${r}, ${g}, ${b}`);
  broadcastTheme();
}

// Broadcast current theme to all windows (main excludes the sender, so no loop)
function broadcastTheme() {
  const r = Number($("s-r").value) || 0;
  const g = Number($("s-g").value) || 0;
  const b = Number($("s-b").value) || 0;
  window.ocSettings.applyTheme({ mode: currentBaseMode, accent_rgb: [r, g, b] }).catch(() => {});
}

// Debounce RGB inputs so rapid typing doesn't flood the IPC channel
let accentDebounceTimer = null;
function onRgbInput() {
  updateAccentLocal(); // update preview swatch immediately, no IPC
  clearTimeout(accentDebounceTimer);
  accentDebounceTimer = setTimeout(broadcastTheme, 50);
  scheduleAutoSave("themes", { delay: 700 });
}

// Update local preview without broadcasting (used for debounced input path)
function updateAccentLocal() {
  const r = $("s-r").value;
  const g = $("s-g").value;
  const b = $("s-b").value;
  const hex = toHex(r, g, b);
  $("s-accent-preview").style.background = `rgb(${r},${g},${b})`;
  $("s-accent-hex").textContent = hex;
  document.documentElement.style.setProperty("--oc-accent", hex);
  document.documentElement.style.setProperty("--oc-accent-rgb", `${r}, ${g}, ${b}`);
}

$("s-r").addEventListener("input", onRgbInput);
$("s-g").addEventListener("input", onRgbInput);
$("s-b").addEventListener("input", onRgbInput);

// ── Theme preset chips ──
document.querySelectorAll(".theme-chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    document.querySelectorAll(".theme-chip").forEach((c) => c.classList.remove("selected"));
    chip.classList.add("selected");
    $("s-r").value = chip.dataset.r;
    $("s-g").value = chip.dataset.g;
    $("s-b").value = chip.dataset.b;
    updateAccent(); // updateAccent calls broadcastTheme()
    scheduleAutoSave("themes", { immediate: true });
  });
});

// ── Base mode ──
function setBaseMode(mode) {
  currentBaseMode = mode;
  const root = document.documentElement;
  $("btn-dark").classList.toggle("selected", mode === "dark");
  $("btn-day").classList.toggle("selected", mode === "day");
  if (mode === "day") {
    // Warm parchment — accent tints surfaces, text stays near-black
    root.style.setProperty("--oc-bg", "#f4f1ec");
    root.style.setProperty("--oc-bg-grad-a", "#f5f2ed");
    root.style.setProperty("--oc-bg-grad-b", "#ede9e2");
    root.style.setProperty("--oc-bg-grad-c", "#e8e4dc");
    root.style.setProperty("--oc-surface", "rgba(var(--oc-accent-rgb), 0.07)");
    root.style.setProperty("--oc-surface2", "rgba(var(--oc-accent-rgb), 0.11)");
    root.style.setProperty("--oc-surface3", "rgba(var(--oc-accent-rgb), 0.16)");
    root.style.setProperty("--oc-border", "rgba(var(--oc-accent-rgb), 0.18)");
    root.style.setProperty("--oc-text", "#0a0710");
    root.style.setProperty("--oc-muted", "#2a2438");
    root.style.setProperty("--oc-dim", "#3d3652");
    root.style.setProperty("--oc-control-track", "rgba(0,0,0,0.12)");
    root.style.setProperty("--oc-select-option-bg", "#e8e4dc");
  } else {
    root.style.setProperty("--oc-bg", "#0a0714");
    root.style.setProperty("--oc-bg-grad-a", "#100c1e");
    root.style.setProperty("--oc-bg-grad-b", "#090714");
    root.style.setProperty("--oc-bg-grad-c", "#050410");
    root.style.setProperty("--oc-surface", "rgba(var(--oc-accent-rgb), 0.08)");
    root.style.setProperty("--oc-surface2", "rgba(var(--oc-accent-rgb), 0.12)");
    root.style.setProperty("--oc-surface3", "rgba(var(--oc-accent-rgb), 0.18)");
    root.style.setProperty("--oc-border", "rgba(var(--oc-accent-rgb), 0.18)");
    root.style.setProperty("--oc-text", "#f2f2f5");
    root.style.setProperty("--oc-muted", "rgba(242,242,245,0.55)");
    root.style.setProperty("--oc-dim", "#b8b4c8");
    root.style.setProperty("--oc-control-track", "rgba(255,255,255,0.14)");
    root.style.setProperty("--oc-select-option-bg", "#120e22");
  }
}

$("btn-dark").addEventListener("click", () => { setBaseMode("dark"); broadcastTheme(); scheduleAutoSave("themes", { immediate: true }); });
$("btn-day").addEventListener("click", () => { setBaseMode("day"); broadcastTheme(); scheduleAutoSave("themes", { immediate: true }); });

// ── Windows accent color ──
$("btn-win-accent").addEventListener("click", async () => {
  const rgb = await window.ocSettings.getWindowsAccent();
  if (!rgb) return;
  $("s-r").value = rgb[0];
  $("s-g").value = rgb[1];
  $("s-b").value = rgb[2];
  document.querySelectorAll(".theme-chip").forEach((c) => c.classList.remove("selected"));
  updateAccent(); // updateAccent calls broadcastTheme()
  scheduleAutoSave("themes", { immediate: true });
});

// ── Tag / keyword chip system ──
// ── Voice card grid ──
function renderVoiceGrid(missingPreviews = []) {
  const grid = $("s-voice-grid");
  if (!grid) return;
  grid.innerHTML = "";

  const selectedGroup = VOICES.find((v) => v.id === selectedVoice)?.group || VOICE_GROUP_ORDER[0];

  for (let gi = 0; gi < VOICE_GROUP_ORDER.length; gi++) {
    const groupName = VOICE_GROUP_ORDER[gi];
    const voices = VOICES.filter((voice) => voice.group === groupName);
    if (!voices.length) continue;

    const isExpanded = gi === 0 || groupName === selectedGroup;

    const section = document.createElement("section");
    section.className = "voice-group" + (isExpanded ? " expanded" : "");

    const title = document.createElement("div");
    title.className = "voice-group-title";

    const chevron = document.createElement("span");
    chevron.className = "voice-group-chevron";
    chevron.textContent = isExpanded ? "▾" : "▸";

    const titleText = document.createElement("span");
    titleText.textContent = groupName;

    title.appendChild(chevron);
    title.appendChild(titleText);
    title.style.cursor = "pointer";
    title.addEventListener("click", () => {
      const expanded = section.classList.toggle("expanded");
      chevron.textContent = expanded ? "▾" : "▸";
    });
    section.appendChild(title);

    const groupGrid = document.createElement("div");
    groupGrid.className = "voice-group-grid";

    for (const voice of voices) {
      const card = document.createElement("div");
      card.className = "voice-card" + (voice.id === selectedVoice ? " selected" : "");
      card.dataset.voiceId = voice.id;

      const name = document.createElement("div");
      name.className = "voice-card-name";
      name.textContent = voice.label;

      const tag = document.createElement("div");
      tag.className = "voice-card-tag";
      tag.textContent = `${voice.tag} · ${voice.id}`;

      card.appendChild(name);
      card.appendChild(tag);

      if (missingPreviews.includes(voice.id)) {
        const spinner = document.createElement("div");
        spinner.className = "voice-card-spinner";
        spinner.innerHTML = `<div class="spinner-icon"></div><span>Generatingâ€¦</span>`;
        card.appendChild(spinner);
      } else {
        const listenBtn = document.createElement("button");
        listenBtn.className = "voice-card-listen";
        listenBtn.dataset.voiceId = voice.id;
        listenBtn.textContent = "Listen";
        listenBtn.type = "button";
        listenBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          playVoicePreview(voice.id, listenBtn);
        });
        card.appendChild(listenBtn);
      }

      card.addEventListener("click", () => selectVoice(voice.id));
      groupGrid.appendChild(card);
    }

    section.appendChild(groupGrid);
    grid.appendChild(section);
  }
  return;

  for (const voice of VOICES) {
    const card = document.createElement("div");
    card.className = "voice-card" + (voice.id === selectedVoice ? " selected" : "");
    card.dataset.voiceId = voice.id;

    const name = document.createElement("div");
    name.className = "voice-card-name";
    name.textContent = voice.label;

    const tag = document.createElement("div");
    tag.className = "voice-card-tag";
    tag.textContent = voice.tag;

    card.appendChild(name);
    card.appendChild(tag);

    if (missingPreviews.includes(voice.id)) {
      // Show spinner — generation in progress
      const spinner = document.createElement("div");
      spinner.className = "voice-card-spinner";
      spinner.innerHTML = `<div class="spinner-icon"></div><span>Generating…</span>`;
      card.appendChild(spinner);
    } else {
      const listenBtn = document.createElement("button");
      listenBtn.className = "voice-card-listen";
      listenBtn.dataset.voiceId = voice.id;
      listenBtn.textContent = "Listen";
      listenBtn.type = "button";
      listenBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        playVoicePreview(voice.id, listenBtn);
      });
      card.appendChild(listenBtn);
    }

    card.addEventListener("click", () => selectVoice(voice.id));
    grid.appendChild(card);
  }
}

function selectVoice(voiceId) {
  selectedVoice = voiceId;
  document.querySelectorAll(".voice-card").forEach((c) => {
    c.classList.toggle("selected", c.dataset.voiceId === voiceId);
  });
  scheduleAutoSave("audio", { immediate: true });
}

function stopCurrentAudio() {
  if (!currentAudio) return;
  currentAudio.pause();
  currentAudio.currentTime = 0;
  currentAudio = null;
  // Reset the previously playing button back to Listen
  if (currentPlayingVoice) {
    const prevBtn = document.querySelector(`.voice-card[data-voice-id="${currentPlayingVoice}"] .voice-card-listen`);
    if (prevBtn) {
      prevBtn.textContent = "Listen";
      prevBtn.disabled = false;
    }
    currentPlayingVoice = null;
  }
}

async function playVoicePreview(voiceId, btn) {
  // Toggle: clicking the currently playing voice stops it
  if (currentPlayingVoice === voiceId) {
    stopCurrentAudio();
    return;
  }

  // Stop whatever was playing before (resets its button too)
  stopCurrentAudio();

  // Ask main process for the absolute file:// URL — relative paths don't
  // resolve reliably in Electron's renderer context.
  const url = await window.ocSettings.getVoicePreviewUrl(voiceId);
  if (!url) return; // file doesn't exist on disk

  const audio = new Audio(url);

  function onEnd() {
    audio.removeEventListener("ended", onEnd);
    audio.removeEventListener("error", onEnd);
    if (currentAudio === audio) {
      currentAudio = null;
      currentPlayingVoice = null;
    }
    btn.textContent = "Listen";
    btn.disabled = false;
  }

  audio.addEventListener("ended", onEnd);
  audio.addEventListener("error", onEnd);

  currentAudio = audio;
  currentPlayingVoice = voiceId;
  btn.textContent = "Playing…";
  btn.disabled = true;

  audio.play().catch(() => onEnd());
}

function clampHeartbeatCustomMinutes(value) {
  const minutes = Number(value);
  if (!Number.isFinite(minutes)) {
    return 30;
  }
  return Math.max(1, Math.min(1440, Math.round(minutes)));
}

function getHeartbeatIntervalLabel(seconds) {
  const match = HEARTBEAT_INTERVAL_OPTIONS.find((option) => option.seconds === Number(seconds));
  if (match) {
    return match.label;
  }
  const minutes = clampHeartbeatCustomMinutes((Number(seconds) || 1800) / 60);
  return `${minutes} min`;
}

function normalizeHeartbeatConfig(heartbeat = {}, legacyVision = {}) {
  const source = isPlainObject(heartbeat) ? heartbeat : {};
  const legacy = isPlainObject(legacyVision) ? legacyVision : {};
  const rawInterval = Number(source.interval ?? legacy.heartbeat_interval);
  const heartbeatInterval = Number.isFinite(rawInterval)
    ? Math.max(60, Math.min(86400, Math.round(rawInterval)))
    : 1800;

  return {
    enabled: Boolean(source.enabled ?? legacy.heartbeat_enabled),
    interval: heartbeatInterval,
    only_when_idle: Boolean(source.only_when_idle ?? legacy.only_when_idle),
    idle_threshold_minutes: Math.max(1, Number(source.idle_threshold_minutes ?? legacy.idle_threshold_minutes) || 5),
  };
}

function getSelectedHeartbeatInterval() {
  const active = document.querySelector(".heartbeat-segment-btn.active");
  const activeValue = String(active?.dataset.heartbeatInterval || "");
  if (activeValue === "custom") {
    const input = $("heartbeat-custom-minutes");
    const minutes = clampHeartbeatCustomMinutes(input?.value);
    if (input) {
      input.value = String(minutes);
    }
    return minutes * 60;
  }
  const presetSeconds = Number(activeValue);
  return Number.isFinite(presetSeconds) && presetSeconds > 0 ? presetSeconds : 1800;
}

function selectHeartbeatInterval(value) {
  const rawValue = String(value ?? "").trim();
  const normalizedSeconds = Number(rawValue);
  const presetMatch = HEARTBEAT_INTERVAL_OPTIONS.find((option) => option.seconds === normalizedSeconds);
  const nextValue = presetMatch ? String(presetMatch.seconds) : "custom";
  if (nextValue === "custom") {
    const input = $("heartbeat-custom-minutes");
    if (input) {
      const sourceMinutes = rawValue === "custom" ? input.value : (normalizedSeconds || 1800) / 60;
      input.value = String(clampHeartbeatCustomMinutes(sourceMinutes));
    }
  }
  document.querySelectorAll(".heartbeat-segment-btn").forEach((button) => {
    button.classList.toggle("active", String(button.dataset.heartbeatInterval) === nextValue);
  });
  renderHeartbeatUi();
  scheduleAutoSave("heartbeat", { immediate: true });
}

function renderHeartbeatUi() {
  const idleRow = $("heartbeat-idle-threshold-row");
  const customIntervalRow = $("heartbeat-custom-interval-row");
  const onlyIdle = Boolean($("heartbeat-only-idle")?.checked);
  const customSelected = Boolean(document.querySelector('.heartbeat-segment-btn.active[data-heartbeat-interval="custom"]'));

  idleRow?.classList.toggle("hidden", !onlyIdle);
  customIntervalRow?.classList.toggle("hidden", !customSelected);
  const customInput = $("heartbeat-custom-minutes");
  if (customInput && customSelected) {
    customInput.value = String(clampHeartbeatCustomMinutes(customInput.value));
  }
}

async function initVoicePreviews() {
  renderVoiceGrid([]);
  try {
    const missing = await window.ocSettings.checkVoicePreviews();
    renderVoiceGrid(missing);
  } catch (error) {
    console.error("Failed to check voice previews:", error);
  }
}
registerTabLoader("audio", initVoicePreviews);

// ── Save handlers ──
function uniqueStrings(values) {
  const seen = new Set();
  const result = [];
  for (const value of Array.isArray(values) ? values : []) {
    const clean = String(value || "").trim();
    if (!clean || seen.has(clean)) {
      continue;
    }
    seen.add(clean);
    result.push(clean);
  }
  return result;
}

async function saveSection(section) {
  autoSaveQueued[section] = true;
  if (autoSaveInFlight[section]) {
    return autoSaveInFlight[section];
  }
  autoSaveInFlight[section] = (async () => {
    try {
      do {
        autoSaveQueued[section] = false;
        let data = {};
        if (section === "persona") {
    const name = $("persona-name").value.trim() || "Nova";
    const pronouns = getSoulPronounsValue();
    const userName = $("persona-user-name").value.trim();
    const rawSoul = $("persona-soul-raw") ? $("persona-soul-raw").value : "";
    const syncedSoul = syncPersonaIdentityIntoSoul(rawSoul, { name, pronouns, userName });
    // Save name/pronouns/user_name to config
    data = {
      companion: {
        name,
        pronouns,
        user_name: userName,
        soul: {
          name,
          pronouns,
          user_name: userName,
        },
      },
    };
    await window.ocSettings.save("persona", data);
    loadedConfig = mergeConfig(loadedConfig, data);
    // Avoid clobbering newer textarea edits queued behind this autosave pass.
    if ($("persona-soul-raw") && !autoSaveQueued[section]) {
      $("persona-soul-raw").value = syncedSoul;
    }
    if (syncedSoul && window.ocSettings.writeSoulFile) {
      await window.ocSettings.writeSoulFile(syncedSoul);
    }
    await window.ocSettings.configReload();
    showStatus("persona", "Saved.");
    continue;
  } else if (section === "heartbeat") {
    const previousHeartbeat = normalizeHeartbeatConfig(loadedConfig.heartbeat || {}, loadedConfig.vision || {});
    const nextHeartbeat = {
      enabled: $("heartbeat-enabled").checked,
      interval: getSelectedHeartbeatInterval(),
      only_when_idle: $("heartbeat-only-idle").checked,
      idle_threshold_minutes: Number($("heartbeat-idle-threshold").value) || 0,
    };

    if (nextHeartbeat.enabled && nextHeartbeat.only_when_idle && nextHeartbeat.idle_threshold_minutes <= 0) {
      showStatus("heartbeat", "Please enter an idle threshold greater than zero.", true);
      return;
    }

    data = {
      heartbeat: nextHeartbeat,
    };

    await window.ocSettings.save("heartbeat", data);
    loadedConfig = mergeConfig(loadedConfig, data);

    if (previousHeartbeat.enabled && nextHeartbeat.enabled) {
      await window.ocSettings.heartbeatRestart();
    } else if (!previousHeartbeat.enabled && nextHeartbeat.enabled) {
      await window.ocSettings.heartbeatStart();
    } else if (previousHeartbeat.enabled && !nextHeartbeat.enabled) {
      await window.ocSettings.heartbeatStop();
    }

      showStatus("heartbeat", "Heartbeat settings saved.");
      renderHeartbeatUi();
      continue;
  } else if (section === "model") {
    const brainProvider = getBrainProviderValue();
    const customBaseUrl = String($("apikey-custom-url")?.value || "").trim();
    let selectedBrainModel = getSelectedModelName("brain");
    if (isOllamaBackedProvider(brainProvider) && !selectedBrainModel) {
      selectedBrainModel = getFallbackOllamaModelName();
      if (!selectedBrainModel) {
        showStatus("model", "Pick a local Ollama model before saving.", true);
        return;
      }
      applyOllamaModelSelection("brain", selectedBrainModel);
    } else if (!isOllamaBackedProvider(brainProvider) && !selectedBrainModel) {
      if (brainProvider === "custom") {
        showStatus("model", "Enter a custom model name.", true);
        return;
      }
      selectedBrainModel = "";
      setLayerModelValue("brain", "");
    }
    if (brainProvider === "custom" && !customBaseUrl) {
      showStatus("model", "Enter a custom endpoint URL before saving.", true);
      return;
    }

    const layers = {};
    for (const { uiId, configKey } of MODEL_LAYERS) {
      const providerSelection = getTargetProviderSelection(uiId);
      const effectiveProvider = providerSelection === "default"
        ? getBrainProviderValue()
        : providerSelection;
      const layerTemperature = readLayerTemperatureValue(uiId);
      const layerMaxTokens = readLayerMaxTokensValue(uiId);
      let modelValue = providerSelection === "default" ? "" : getSelectedModelName(uiId);
      if (providerSelection === "default") {
        layers[configKey] = {
          provider: "",
          model: "",
          temperature: layerTemperature === "" ? "" : layerTemperature,
          max_tokens: layerMaxTokens === "" ? "" : layerMaxTokens,
        };
        continue;
      }
      if (isOllamaBackedProvider(effectiveProvider) && !modelValue) {
        modelValue = getFallbackOllamaModelName();
        if (!modelValue) {
          showStatus("model", "Pick a local Ollama model before saving.", true);
          return;
        }
        applyOllamaModelSelection(uiId, modelValue);
      } else if (!isOllamaBackedProvider(effectiveProvider) && providerSelection !== "default") {
        if (!modelValue && effectiveProvider === "custom") {
          showStatus("model", "Enter a custom model name.", true);
          return;
        }
        setLayerModelValue(uiId, modelValue);
      }
      layers[configKey] = {
        provider: providerSelection === "default" ? "" : providerSelection,
        model: modelValue,
        temperature: layerTemperature === "" ? "" : layerTemperature,
        max_tokens: layerMaxTokens === "" ? "" : layerMaxTokens,
      };
    }
    data = {
      brain: {
        provider: brainProvider,
        model: selectedBrainModel,
        ...(customBaseUrl ? { base_url: customBaseUrl } : {}),
        temperature: getGlobalTemperatureValue(),
        max_tokens: getGlobalMaxTokensValue(),
        context_window: readContextWindowValue(),
        stream: $("s-stream").checked,
        fallback_cpu: $("s-cpu").checked,
        layers,
        // API keys are in OS keychain — not written to config.json
      },
    };
  } else if (section === "memory") {
    const extractionSource = getMemoryExtractionSourceValue();
    const extractionProvider = String($("s-extraction-provider").value || "").trim().toLowerCase();
    const extractionModel = extractionSource === "local"
      ? String($("s-extraction-model").value || "").trim()
      : String($("s-extraction-model-remote").value || "").trim();
    const extractionBaseUrl = String($("s-extraction-base-url").value || "").trim();
    if (extractionSource === "api" && extractionProvider === "custom" && !extractionBaseUrl) {
      showStatus("memory", "Enter a custom API base URL for memory extraction.", true);
      return;
    }
    data = {
      memory: {
        enabled: $("s-mem-writeback").checked,
        embedding_enabled: $("s-mem-embed").checked,
        max_context_memories: Number($("s-topk").value),
        extraction_source: extractionSource,
        extraction_provider: extractionSource === "api" ? extractionProvider : "",
        extraction_base_url: extractionSource === "api" && extractionProvider === "custom" ? extractionBaseUrl : "",
        extraction_model: extractionModel,
        extraction_mode: extractionSource === "local" ? "local" : "provider",
        dream_enabled: $("s-dream-enabled").checked,
        dream_schedule: $("s-dream-schedule").value,
        max_entries: Number($("s-max-entries").value) || 500,
      },
    };
  } else if (section === "audio") {
    data = {
      voice: {
        tts_enabled: $("s-tts-enabled").checked,
        kokoro_voice: selectedVoice,
        volume: Number($("s-vol").value) / 100,
        tts_speed: Number($("s-speed").value) / 10,
        stt_enabled: $("s-stt-enabled").checked,
        whisper_model: $("s-whisper").value,
        push_to_talk_key: $("s-ptt-key").value.trim(),
        auto_send_on_silence: $("s-auto-send").checked,
      },
    };
  } else if (section === "themes") {
    const r = Number($("s-r").value) || 0;
    const g = Number($("s-g").value) || 0;
    const b = Number($("s-b").value) || 0;
    const themeData = { mode: currentBaseMode, accent_rgb: [r, g, b] };
    // Broadcast live to all windows
    await window.ocSettings.applyTheme(themeData);
    const idleSec = Math.max(0, Math.min(60, parseInt($("s-idle-timeout").value, 10) || 0));
    data = {
      ui: {
        theme: themeData,
        idle_timeout_seconds: idleSec,
        overlay_scale: parseOverlayScaleValue($("s-overlay-scale").value, 50),
        layer_visibility: $("s-layer-vis").checked,
      },
    };
  }

        await window.ocSettings.save(section, data);
        loadedConfig = mergeConfig(loadedConfig, data);
        showStatus(section);
      } while (autoSaveQueued[section]);
    } finally {
      delete autoSaveInFlight[section];
      delete autoSaveQueued[section];
    }
  })();
  return autoSaveInFlight[section];
}

function scheduleAutoSave(section, options = {}) {
  if (autoSaveSuspended || isHydratingSettings) {
    return;
  }
  const delay = options.immediate ? 0 : options.delay ?? 500;
  clearTimeout(autoSaveTimers[section]);
  autoSaveTimers[section] = setTimeout(() => {
    saveSection(section).catch((error) => {
      console.error(`Failed autosaving ${section}:`, error);
      showStatus(section, error.message || "Could not save changes.", true);
    });
  }, delay);
}

function wireSectionAutoSave(section, selector = "input, select, textarea") {
  const page = $(`page-${section}`);
  if (!page) {
    return;
  }
  page.addEventListener("input", (event) => {
    if (event.target?.matches(selector)) {
      scheduleAutoSave(section, { delay: 700 });
    }
  });
  page.addEventListener("change", (event) => {
    if (event.target?.matches(selector)) {
      scheduleAutoSave(section, { immediate: true });
    }
  });
  page.addEventListener("focusout", (event) => {
    if (event.target?.matches(selector)) {
      scheduleAutoSave(section, { immediate: true });
    }
  });
}

// Wire save and reset buttons
document.querySelectorAll("[data-save]").forEach((btn) => {
  btn.addEventListener("click", () => saveSection(btn.dataset.save));
});

document.querySelectorAll("[data-reset]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    setAutoSaveSuspended(true);
    populateFromConfig(loadedConfig);

    if (btn.dataset.reset === "persona" && window.ocSettings.readSoulFile) {
      window.ocSettings.readSoulFile().then((result) => {
        if (result?.ok && $("persona-soul-raw")) {
          $("persona-soul-raw").value = result.content || "";
        }
      }).catch(() => {});
    }

    if (btn.dataset.reset === "model") {
      await loadModelTab();
      setAutoSaveSuspended(false);
      return;
    }

    if (btn.dataset.reset === "audio") {
      await initVoicePreviews();
    }
    setAutoSaveSuspended(false);
  });
});

// ── Populate fields from config ──
function populateFromConfig(config) {
  isHydratingSettings = true;
  const c = config || {};
  const companion = c.companion || {};
  const brain = c.brain || {};
  const memory = c.memory || {};
  const heartbeat = normalizeHeartbeatConfig(c.heartbeat || {}, c.vision || {});
  const voice = c.voice || {};
  const ui = c.ui || {};
  const theme = ui.theme || {};

  // Soul fields
  const soul = companion.soul || {};
  $("persona-name").value = soul.name || "";
  const pronouns = normalizePronouns(soul.pronouns || companion.pronouns || "she/her");
  if (["she/her", "he/him", "they/them", "it/its"].includes(pronouns)) {
    $("persona-pronouns").value = pronouns;
    $("persona-pronoun-subject").value = "";
    $("persona-pronoun-object").value = "";
    $("persona-pronoun-possessive").value = "";
  } else {
    const [subject = "", object = "", possessive = ""] = pronouns.split("/");
    $("persona-pronouns").value = "custom";
    $("persona-pronoun-subject").value = subject;
    $("persona-pronoun-object").value = object;
    $("persona-pronoun-possessive").value = possessive;
  }
  $("persona-user-name").value = soul.user_name || companion.user_name || "";
  toggleCustomPronounsRow();
  // Load raw soul file asynchronously
  if (window.ocSettings.readSoulFile) {
    window.ocSettings.readSoulFile().then((result) => {
      if (result?.ok && $("persona-soul-raw")) {
        $("persona-soul-raw").value = result.content || "";
      }
    }).catch(() => {});
  }


  // Model sliders — API keys and layer fields are loaded separately via loadModelTab()
  setGlobalTemperatureValue(brain.temperature ?? 0.8);
  $("s-max-tokens").value = String(normalizeLayerMaxTokensValue(brain.max_tokens ?? 1024) || 1024);
  const brainLayers = brain.layers || {};
  for (const { uiId, configKey, legacyConfigKey } of MODEL_LAYERS) {
    const layerConfig = brainLayers[configKey] || (legacyConfigKey ? brainLayers[legacyConfigKey] : {}) || {};
    setLayerTemperatureValue(uiId, layerConfig.temperature ?? "");
    setLayerMaxTokensValue(uiId, layerConfig.max_tokens ?? "");
  }

  applyContextWindowSetting(brain.context_window);

  $("s-stream").checked = brain.stream !== false;
  $("s-cpu").checked = brain.fallback_cpu === true;

  // Memory
  $("s-mem-writeback").checked = memory.enabled !== false;
  $("s-mem-embed").checked = memory.embedding_enabled !== false;
  const topk = memory.max_context_memories || 5;
  $("s-topk").value = topk;
  $("s-topk-val").textContent = topk;
  const extractionSource = String(memory.extraction_source || (memory.extraction_mode === "provider" ? "brain" : "local")).trim().toLowerCase() || "local";
  $("s-extraction-source").value = ["brain", "local", "api"].includes(extractionSource) ? extractionSource : "local";
  $("s-extraction-provider").value = String(memory.extraction_provider || "openai").trim().toLowerCase() || "openai";
  $("s-extraction-base-url").value = String(memory.extraction_base_url || "").trim();
  populateExtractionModelSelect(String(memory.extraction_model ?? "").trim());
  $("s-extraction-model-remote").value = extractionSource === "local"
    ? ""
    : String(memory.extraction_model ?? "").trim();
  updateMemoryExtractionUi();
  $("s-dream-enabled").checked = memory.dream_enabled !== false;
  $("s-dream-schedule").value = memory.dream_schedule || "On idle (10 min)";
  $("s-max-entries").value = memory.max_entries || 500;

  // Heartbeat
  $("heartbeat-enabled").checked = heartbeat.enabled;
  $("heartbeat-only-idle").checked = heartbeat.only_when_idle;
  $("heartbeat-idle-threshold").value = heartbeat.idle_threshold_minutes;
  selectHeartbeatInterval(heartbeat.interval);
  renderHeartbeatUi();

  // Audio
  $("s-tts-enabled").checked = voice.tts_enabled !== false;
  selectedVoice = voice.kokoro_voice || "af_nova";
  const vol = Math.round((voice.volume ?? 0.8) * 100);
  $("s-vol").value = vol;
  $("s-vol-val").textContent = `${vol}%`;
  const speed = Math.round((voice.tts_speed || 1.0) * 10);
  $("s-speed").value = speed;
  $("s-speed-val").textContent = `${(speed / 10).toFixed(1)}x`;
  $("s-stt-enabled").checked = voice.stt_enabled !== false;
  $("s-whisper").value = voice.whisper_model || "base.en";
  $("s-ptt-key").value = voice.push_to_talk_key || "";
  $("s-auto-send").checked = voice.auto_send_on_silence !== false;

  // Theme
  const accent = theme.accent_rgb || [124, 106, 247];
  $("s-r").value = accent[0];
  $("s-g").value = accent[1];
  $("s-b").value = accent[2];
  updateAccent();

  const idleEl = $("s-idle-timeout");
  if (idleEl) {
    idleEl.value = config.ui?.idle_timeout_seconds ?? 5;
  }
  const overlayScaleEl = $("s-overlay-scale");
  const overlayScaleValueEl = $("s-overlay-scale-val");
  const overlayScale = parseOverlayScaleValue(config.ui?.overlay_scale, 50);
  if (overlayScaleEl) {
    overlayScaleEl.value = String(overlayScale);
  }
  if (overlayScaleValueEl) {
    overlayScaleValueEl.textContent = String(overlayScale);
  }
  $("s-layer-vis").checked = ui.layer_visibility !== false;

  const mode = theme.mode || "dark";
  setBaseMode(mode);

  // Select matching preset chip
  document.querySelectorAll(".theme-chip").forEach((chip) => {
    const matches =
      Number(chip.dataset.r) === accent[0] &&
      Number(chip.dataset.g) === accent[1] &&
      Number(chip.dataset.b) === accent[2];
    chip.classList.toggle("selected", matches);
  });

  isHydratingSettings = false;
}

// ── Model layer sub-tabs ──
document.querySelectorAll(".layer-tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    const layer = btn.dataset.layer;
    document.querySelectorAll(".layer-tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".layer-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    const panel = $(`model-panel-${layer}`);
    if (panel) panel.classList.add("active");
    const scroller = document.querySelector(".oc-content");
    const savedScroll = scroller ? scroller.scrollTop : 0;
    void refreshModelUiState().then(() => {
      if (scroller) scroller.scrollTop = savedScroll;
    });
  });
});

function getGlobalTemperatureValue() {
  const v = Number.parseFloat($("s-temp-number")?.value ?? $("s-temp")?.value ?? "");
  return Number.isFinite(v) ? Math.max(0, Math.min(2, v)) : 0.8;
}

function setGlobalTemperatureValue(value) {
  const v = Math.max(0, Math.min(2, Number.isFinite(Number(value)) ? Number(value) : 0.8));
  const slider = $("s-temp");
  const numberInput = $("s-temp-number");
  if (slider) slider.value = String(v);
  if (numberInput) numberInput.value = v.toFixed(2);
  syncTempPresetChips(v);
  for (const { uiId } of MODEL_LAYERS) {
    updateLayerTemperatureHint(uiId);
  }
}

function syncTempPresetChips(v) {
  document.querySelectorAll("#global-temp-control .preset-chip").forEach((chip) => {
    const chipVal = parseFloat(chip.dataset.value);
    chip.classList.toggle("active", Math.abs(chipVal - v) < 0.01);
  });
}

function initGlobalTempControl() {
  const slider = $("s-temp");
  const numberInput = $("s-temp-number");
  if (!slider || !numberInput) return;

  slider.addEventListener("input", () => {
    const v = parseFloat(slider.value);
    numberInput.value = v.toFixed(2);
    syncTempPresetChips(v);
    for (const { uiId } of MODEL_LAYERS) {
      updateLayerTemperatureHint(uiId);
    }
    scheduleAutoSave("model", { delay: 700 });
  });

  numberInput.addEventListener("input", () => {
    const raw = parseFloat(numberInput.value);
    if (Number.isFinite(raw)) {
      const v = Math.max(0, Math.min(2, raw));
      slider.value = String(v);
      syncTempPresetChips(v);
      for (const { uiId } of MODEL_LAYERS) {
        updateLayerTemperatureHint(uiId);
      }
    }
  });

  numberInput.addEventListener("change", () => {
    const raw = parseFloat(numberInput.value);
    const v = Number.isFinite(raw) ? Math.max(0, Math.min(2, raw)) : 0.8;
    numberInput.value = v.toFixed(2);
    slider.value = String(v);
    syncTempPresetChips(v);
    for (const { uiId } of MODEL_LAYERS) {
      updateLayerTemperatureHint(uiId);
    }
    scheduleAutoSave("model", { immediate: true });
  });

  document.querySelectorAll("#global-temp-control .preset-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const v = parseFloat(chip.dataset.value);
      setGlobalTemperatureValue(v);
      scheduleAutoSave("model", { immediate: true });
    });
  });
}

function initLayerOverrideControls() {
  for (const { uiId } of MODEL_LAYERS) {
    const layer = uiId;

    // Temperature override toggle
    const tempCheckbox = $(`${layer}-temperature-override-enabled`);
    const tempArea = $(`${layer}-temperature-override-area`);
    const tempHint = $(`${layer}-temperature-hint`);
    const tempSlider = $(`${layer}-temperature`);
    const tempNumber = $(`${layer}-temperature-number`);

    if (tempCheckbox && tempArea) {
      tempCheckbox.addEventListener("change", () => {
        const overriding = tempCheckbox.checked;
        tempArea.hidden = !overriding;
        if (tempHint) tempHint.classList.toggle("faded", overriding);
        if (!overriding) {
          scheduleAutoSave("model", { immediate: true });
        }
      });
    }

    if (tempSlider && tempNumber) {
      tempSlider.addEventListener("input", () => {
        const v = parseFloat(tempSlider.value);
        tempNumber.value = v.toFixed(2);
        scheduleAutoSave("model", { delay: 700 });
      });
      tempNumber.addEventListener("input", () => {
        const raw = parseFloat(tempNumber.value);
        if (Number.isFinite(raw)) {
          tempSlider.value = String(Math.max(0, Math.min(2, raw)));
        }
      });
      tempNumber.addEventListener("change", () => {
        const raw = parseFloat(tempNumber.value);
        const v = Number.isFinite(raw) ? Math.max(0, Math.min(2, raw)) : 0.8;
        tempNumber.value = v.toFixed(2);
        tempSlider.value = String(v);
        scheduleAutoSave("model", { immediate: true });
      });
    }

    // Max tokens override toggle
    const tokensCheckbox = $(`${layer}-max-tokens-override-enabled`);
    const tokensArea = $(`${layer}-max-tokens-override-area`);
    const tokensHint = $(`${layer}-max-tokens-hint`);

    if (tokensCheckbox && tokensArea) {
      tokensCheckbox.addEventListener("change", () => {
        const overriding = tokensCheckbox.checked;
        tokensArea.hidden = !overriding;
        if (tokensHint) tokensHint.classList.toggle("faded", overriding);
        if (!overriding) {
          scheduleAutoSave("model", { immediate: true });
        }
      });
    }
  }
}

function getGlobalMaxTokensValue() {
  const parsed = normalizeLayerMaxTokensValue($("s-max-tokens")?.value);
  return parsed === "" ? 1024 : parsed;
}

function updateLayerTemperatureHint(layer) {
  const cleanLayer = String(layer || "").trim();
  const hintEl = $(`${cleanLayer}-temperature-hint`);
  if (!hintEl) return;
  hintEl.textContent = `${getGlobalTemperatureValue().toFixed(2)} (global)`;
}

function setLayerTemperatureValue(layer, value) {
  const cleanLayer = String(layer || "").trim();
  const checkbox = $(`${cleanLayer}-temperature-override-enabled`);
  const area = $(`${cleanLayer}-temperature-override-area`);
  const hintEl = $(`${cleanLayer}-temperature-hint`);
  const slider = $(`${cleanLayer}-temperature`);
  const numberInput = $(`${cleanLayer}-temperature-number`);
  const normalized = normalizeLayerTemperatureValue(value);
  const overriding = normalized !== "";
  if (checkbox) checkbox.checked = overriding;
  if (area) area.hidden = !overriding;
  if (hintEl) hintEl.classList.toggle("faded", overriding);
  if (overriding && slider) {
    const v = Number(normalized);
    slider.value = String(v);
    if (numberInput) numberInput.value = v.toFixed(2);
  }
  updateLayerTemperatureHint(cleanLayer);
}

function readLayerTemperatureValue(layer) {
  const cleanLayer = String(layer || "").trim();
  const checkbox = $(`${cleanLayer}-temperature-override-enabled`);
  if (!checkbox?.checked) return "";
  const numberInput = $(`${cleanLayer}-temperature-number`);
  const slider = $(`${cleanLayer}-temperature`);
  const raw = numberInput?.value ?? slider?.value ?? "";
  return normalizeLayerTemperatureValue(raw);
}

function updateLayerMaxTokensHint(layer) {
  const cleanLayer = String(layer || "").trim();
  const hintEl = $(`${cleanLayer}-max-tokens-hint`);
  if (!hintEl) return;
  hintEl.textContent = `${getGlobalMaxTokensValue().toLocaleString()} (global)`;
}

function setLayerMaxTokensValue(layer, value) {
  const cleanLayer = String(layer || "").trim();
  const checkbox = $(`${cleanLayer}-max-tokens-override-enabled`);
  const area = $(`${cleanLayer}-max-tokens-override-area`);
  const hintEl = $(`${cleanLayer}-max-tokens-hint`);
  const input = $(`${cleanLayer}-max-tokens`);
  const normalized = normalizeLayerMaxTokensValue(value);
  const overriding = normalized !== "";
  if (checkbox) checkbox.checked = overriding;
  if (area) area.hidden = !overriding;
  if (hintEl) hintEl.classList.toggle("faded", overriding);
  if (overriding && input) input.value = String(normalized);
  updateLayerMaxTokensHint(cleanLayer);
}

function readLayerMaxTokensValue(layer) {
  const cleanLayer = String(layer || "").trim();
  const checkbox = $(`${cleanLayer}-max-tokens-override-enabled`);
  if (!checkbox?.checked) return "";
  const input = $(`${cleanLayer}-max-tokens`);
  return normalizeLayerMaxTokensValue(input?.value);
}

// ── Password eye toggle ──
document.querySelectorAll(".oc-pw-eye").forEach((btn) => {
  btn.addEventListener("click", () => {
    const input = $(btn.dataset.target);
    if (!input) return;
    input.type = input.type === "password" ? "text" : "password";
  });
});

// ── Per-layer model field ──
const PROVIDER_PLACEHOLDERS = {
  gemma:     "gemma4:e4b",
  qwen:      "qwen3:8b",
  qwen_cloud:"qwen3-max",
  openai:    "gpt-5.4-mini",
  anthropic: "claude-opus-4-6",
  custom:    "model-name",
};
const PROVIDER_HINTS = {
  qwen_cloud:"Requires DashScope API key",
  openai:    "Requires OpenAI API key",
  anthropic: "Requires Anthropic API key",
  custom:    "Uses Custom base URL",
};

const MASKED = "••••••••";
let _confirmTimers = {};

function getApiKeyFieldId(account) {
  return API_KEY_FIELD_MAP[account] || `apikey-${account}`;
}

function resolveCredentialAccount(account) {
  const cleanAccount = String(account || "").trim().toLowerCase();
  switch (cleanAccount) {
    case "openai":
    case "openai_api_key":
      return "openai_api_key";
    case "anthropic":
    case "anthropic_api_key":
      return "anthropic_api_key";
    case "gemini":
    case "gemini_api_key":
      return "gemini_api_key";
    case "openrouter":
    case "openrouter_api_key":
      return "openrouter_api_key";
    case "qwen":
    case "qwen_cloud":
    case "qwen_api_key":
    case "dashscope_api_key":
      return "qwen_api_key";
    case "custom":
    case "custom_api_key":
      return "custom_api_key";
    case "custom_url":
      return "custom_url";
    default:
      return cleanAccount;
  }
}

function getProviderSpec(provider) {
  return CLOUD_PROVIDER_SPECS[String(provider || "").trim()] || null;
}

function getProviderModels(provider) {
  const key = String(provider || "").trim();
  if (providerModelsCache[key]) {
    return [...providerModelsCache[key]];
  }
  return getProviderSpec(provider)?.models || [];
}

function getDefaultCloudModel(provider) {
  return getProviderModels(provider)[0] || "";
}

async function refreshProviderModelCache(provider) {
  const cleanProvider = String(provider || "").trim();
  if (!cleanProvider || isOllamaBackedProvider(cleanProvider) || cleanProvider === "chatgpt_oauth") {
    return [];
  }

  const options = cleanProvider === "custom"
    ? { baseUrl: getConfiguredCustomBaseUrl() }
    : {};

  try {
    const models = await window.ocSettings.getProviderModels(cleanProvider, options);
    providerModelsCache[cleanProvider] = Array.isArray(models)
      ? models.map((model) => String(model || "").trim()).filter(Boolean)
      : [];
  } catch {
    providerModelsCache[cleanProvider] = [];
  }
  return [...providerModelsCache[cleanProvider]];
}

function isCloudProvider(provider) {
  return Boolean(getProviderSpec(provider));
}

function rememberModelValue(targetId, provider, value) {
  const targetKey = String(targetId || "").trim();
  const providerKey = String(provider || "").trim();
  const cleanValue = String(value || "").trim();
  if (!targetKey || !providerKey || !cleanValue || !MODEL_VALUE_MEMORY[targetKey]) {
    return;
  }
  MODEL_VALUE_MEMORY[targetKey][providerKey] = cleanValue;
}

function getRememberedModelValue(targetId, provider, fallback = "") {
  const targetKey = String(targetId || "").trim();
  const providerKey = String(provider || "").trim();
  const remembered = MODEL_VALUE_MEMORY[targetKey]?.[providerKey];
  return String(remembered || fallback || "").trim();
}

async function getStoredCredential(account) {
  const cleanAccount = String(account || "").trim();
  if (!cleanAccount) {
    return "";
  }

  const resolvedAccount = resolveCredentialAccount(cleanAccount);

  try {
    if (resolvedAccount === "custom_url") {
      if (typeof window.ocSettings?.getApiKey === "function") {
        const result = await window.ocSettings.getApiKey("custom_url");
        const value = String(result?.value || "").trim();
        return value || "";
      }
      if (typeof window.ocSettings?.getApiKeys === "function") {
        const keys = await window.ocSettings.getApiKeys();
        return String(keys?.custom_url || "").trim();
      }
      return "";
    }

    if (typeof window.ocSettings?.getApiKeys === "function") {
      const keys = await window.ocSettings.getApiKeys();
      if (keys && Object.prototype.hasOwnProperty.call(keys, cleanAccount)) {
        return keys[cleanAccount] ? MASKED : "";
      }
    }

    if (window.ocSettings?.apikey && typeof window.ocSettings.apikey.get === "function") {
      const status = await window.ocSettings.apikey.get(resolvedAccount);
      return String(status || "").trim() === "set" ? MASKED : "";
    }
  } catch {
    // Ignore and fall through.
  }

  try {
    if (typeof window.ocSettings?.getApiKey === "function") {
      const result = await window.ocSettings.getApiKey(cleanAccount);
      const value = String(result?.value || "").trim();
      if (cleanAccount === "custom_url") {
        return value;
      }
      return value === "set" ? MASKED : "";
    }
  } catch {
    // Ignore and fall through.
  }

  return "";
}

async function setStoredCredential(account, value) {
  const cleanAccount = String(account || "").trim();
  const cleanValue = String(value || "").trim();
  if (!cleanAccount) {
    return false;
  }

  if (!cleanValue) {
    return false;
  }

  const resolvedAccount = resolveCredentialAccount(cleanAccount);

  if (resolvedAccount === "custom_url") {
    try {
      if (typeof window.ocSettings?.setApiKey === "function") {
        await window.ocSettings.setApiKey("custom_url", cleanValue);
        return true;
      }
    } catch {
      // Ignore and fall through.
    }
  }

  try {
    if (window.ocSettings?.apikey && typeof window.ocSettings.apikey.set === "function") {
      await window.ocSettings.apikey.set(resolvedAccount, cleanValue);
      return true;
    }
  } catch {
    // Try alternative preload shapes below.
  }

  try {
    if (typeof window.ocSettings?.setApiKey === "function") {
      await window.ocSettings.setApiKey(cleanAccount, cleanValue);
      return true;
    }
  } catch {
    // Ignore and fall through.
  }

  return false;
}

async function removeStoredCredential(account) {
  const cleanAccount = String(account || "").trim();
  if (!cleanAccount) {
    return false;
  }

  const resolvedAccount = resolveCredentialAccount(cleanAccount);

  if (resolvedAccount === "custom_url") {
    try {
      if (typeof window.ocSettings?.setApiKey === "function") {
        await window.ocSettings.setApiKey("custom_url", "");
        return true;
      }
    } catch {
      // Ignore and fall through.
    }
  }

  try {
    if (window.ocSettings?.apikey && typeof window.ocSettings.apikey.delete === "function") {
      await window.ocSettings.apikey.delete(resolvedAccount);
      return true;
    }
  } catch {
    // Try alternative preload shapes below.
  }

  try {
    if (typeof window.ocSettings?.setApiKey === "function") {
      await window.ocSettings.setApiKey(cleanAccount, "");
      return true;
    }
  } catch {
    // Ignore and fall through.
  }

  return false;
}

function showInlineConfirm(confirmId, text = "✓") {
  const el = $(`confirm-${confirmId}`);
  if (!el) return;
  el.textContent = text;
  el.classList.remove("hidden");
  clearTimeout(_confirmTimers[confirmId]);
  _confirmTimers[confirmId] = setTimeout(() => {
    el.textContent = "✓";
    el.classList.add("hidden");
  }, 1500);
}

async function onApiKeyBlur(account, fieldId) {
  const field = $(fieldId);
  if (!field) return;
  const val = String(field.value || "");
  if (val === MASKED) return;

  if (!val.trim()) {
    if (field.dataset.hasStoredKey === "true") {
      field.value = MASKED;
    }
    return;
  }

  const saved = await setStoredCredential(account, val);
  if (saved) {
    field.dataset.hasStoredKey = "true";
    field.value = MASKED;
    showInlineConfirm(account);
  }
}

async function onUrlBlur(account, fieldId) {
  const field = $(fieldId);
  if (!field) return;
  const val = String(field.value || "").trim();
  if (!val) {
    await removeStoredCredential(account);
    return;
  }
  const saved = await setStoredCredential(account, val);
  if (saved) {
    showInlineConfirm(account);
  }
}

async function onRemoveCredential(account, fieldId) {
  const field = $(fieldId);
  if (!field) return;
  await removeStoredCredential(account);
  field.dataset.hasStoredKey = "false";
  field.value = "";
  const confirm = $(`confirm-${account}`);
  if (confirm) {
    confirm.classList.add("hidden");
    confirm.textContent = "✓";
  }
  showStatus("model", `${getProviderLabel(account)} key removed.`);
}

function wireSecretField(account, fieldId) {
  const field = $(fieldId);
  if (!field) return;
  field.addEventListener("focus", () => {
    if (field.value === MASKED) {
      field.value = "";
    }
  });
  field.addEventListener("blur", () => onApiKeyBlur(account, fieldId));
  const removeButton = document.querySelector(`[data-remove-key="${account}"]`);
  removeButton?.addEventListener("click", () => {
    onRemoveCredential(account, fieldId).catch((error) => {
      console.error(`Failed to remove ${account} key:`, error);
      showStatus("model", `Could not remove the ${getProviderLabel(account)} key.`, true);
    });
  });
}

function wireUrlField(fieldId, account) {
  const field = $(fieldId);
  if (!field) return;
  field.addEventListener("blur", () => onUrlBlur(account, fieldId));
}

async function loadCredentialField(fieldId, account, masked = true) {
  const field = $(fieldId);
  if (!field) return;
  const value = await getStoredCredential(account).catch(() => "");
  const hasStoredKey = account === "custom_url"
    ? Boolean(String(value || "").trim())
    : value === MASKED;
  field.dataset.hasStoredKey = hasStoredKey ? "true" : "false";
  field.value = hasStoredKey ? (masked ? MASKED : value) : "";
}

function renderKeyStatus(fieldId, hasValue) {
  const field = $(fieldId);
  if (!field) return;
  field.dataset.hasStoredKey = hasValue ? "true" : "false";
  field.value = hasValue ? MASKED : "";
}

function getCloudConnectionStatusId(targetId) {
  return `${targetId}-connection-status`;
}

function setCloudConnectionStatus(targetId, state, message) {
  const status = $(getCloudConnectionStatusId(targetId));
  if (!status) return;
  status.classList.remove("success", "error", "running");
  if (!message) {
    status.textContent = "";
    return;
  }
  if (state) {
    status.classList.add(state);
  }
  status.textContent = message;
}

function setCloudConnectionRunning(targetId, running) {
  const btn = $(`${targetId}-connection-test-btn`);
  const spinner = $(`${targetId}-connection-spinner`);
  const status = $(getCloudConnectionStatusId(targetId));
  if (btn) {
    btn.disabled = running;
  }
  if (spinner) {
    spinner.classList.toggle("hidden", !running);
  }
  if (status) {
    status.classList.toggle("running", running);
  }
}

async function runCloudConnectionTest(targetId) {
  const provider = getModelTargetState(targetId).effectiveProvider;
  if (!isCloudProvider(provider)) {
    return;
  }
  if (provider === "custom" && !String($("apikey-custom-url")?.value || "").trim()) {
    setCloudConnectionStatus(targetId, "error", "✗ Enter a custom endpoint URL first.");
    return;
  }

  setCloudConnectionRunning(targetId, true);
  setCloudConnectionStatus(targetId, "running", "Testing connection...");

  try {
    if (!window.ocSettings?.apikey || typeof window.ocSettings.apikey.validate !== "function") {
      throw new Error("Connection testing is unavailable in this build.");
    }

    const result = await window.ocSettings.apikey.validate(provider, {
      model: getSelectedModelName(targetId),
      baseUrl: provider === "custom" ? String($("apikey-custom-url")?.value || "").trim() : "",
    });

    if (result?.valid || result?.ok || result?.success) {
      setCloudConnectionStatus(targetId, "success", "✓ Connected");
      return;
    }

    const errorText = String(result?.error || result?.message || "Connection test failed.").trim();
    const normalized = errorText ? errorText.charAt(0).toUpperCase() + errorText.slice(1) : "Connection test failed.";
    setCloudConnectionStatus(targetId, "error", `✗ ${normalized}`);
  } catch (error) {
    const errorText = String(error?.message || "Connection test failed.").trim();
    const normalized = errorText ? errorText.charAt(0).toUpperCase() + errorText.slice(1) : "Connection test failed.";
    setCloudConnectionStatus(targetId, "error", `✗ ${normalized}`);
  } finally {
    setCloudConnectionRunning(targetId, false);
  }
}

function clearCloudConnectionStatus(targetId) {
  setCloudConnectionStatus(targetId, null, "");
  setCloudConnectionRunning(targetId, false);
}

function renderProviderNotice(container, provider, targetId, isDefaultLayer) {
  const spec = getProviderSpec(provider);
  if (!spec || isOllamaBackedProvider(provider)) {
    return;
  }

  const privacy = document.createElement("div");
  privacy.className = "oc-provider-privacy";
  privacy.textContent = isDefaultLayer
    ? `This provider sends your conversations to ${spec.label}. Your API key is stored locally in your OS keychain.`
    : `${spec.label} credentials are stored in the OS keychain. This layer override uses its own provider and model for remote requests.`;
  container.appendChild(privacy);

  if (provider === "custom") {
    const note = document.createElement("div");
    note.className = "oc-provider-note";
    note.textContent = "Custom base URL is configured in the API Keys card. The model field accepts free text.";
    container.appendChild(note);
  }
}

function renderCloudConnectionControls(container, targetId, provider) {
  if (!isCloudProvider(provider)) {
    return;
  }

  const row = document.createElement("div");
  row.className = "oc-provider-connection";

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "oc-btn";
  btn.id = `${targetId}-connection-test-btn`;
  btn.textContent = "Test connection";
  btn.addEventListener("click", () => {
    runCloudConnectionTest(targetId).catch((error) => {
      console.error("Cloud connection test failed:", error);
      setCloudConnectionStatus(targetId, "error", error.message || "Connection test failed.");
    });
  });

  const spinner = document.createElement("span");
  spinner.className = "spinner-icon hidden";
  spinner.id = `${targetId}-connection-spinner`;

  const status = document.createElement("span");
  status.className = "oc-provider-connection-status";
  status.id = getCloudConnectionStatusId(targetId);

  row.appendChild(btn);
  row.appendChild(spinner);
  row.appendChild(status);
  container.appendChild(row);
}

// Wire API key fields
wireSecretField("openai", "apikey-openai");
wireSecretField("anthropic", "apikey-anthropic");
wireSecretField("gemini", "apikey-gemini");
wireSecretField("openrouter", "apikey-openrouter");
wireSecretField("qwen", "apikey-qwen");
wireSecretField("custom", "apikey-custom");
wireUrlField("apikey-custom-url", "custom_url");
document.querySelectorAll("[data-custom-base-url]").forEach((button) => {
  button.addEventListener("click", () => {
    const value = String(button.getAttribute("data-custom-base-url") || "").trim();
    const field = $("apikey-custom-url");
    if (!value || !field) return;
    field.value = value;
    void onUrlBlur("custom_url", "apikey-custom-url");
    if ($("brain-provider")?.value === "custom") {
      void handleBrainProviderChange();
    }
  });
});

function getProviderLabel(provider) {
  switch (String(provider || "").trim()) {
    case "gemma":
      return "Gemma (Local)";
    case "qwen":
      return "Qwen (Local)";
    case "qwen_cloud":
      return "Qwen (DashScope)";
    case "ollama":
      return "Ollama";
    case "openai":
      return "OpenAI";
    case "anthropic":
      return "Anthropic";
    case "gemini":
      return "Gemini";
    case "openrouter":
      return "OpenRouter";
    case "chatgpt_oauth":
      return "ChatGPT Plus";
    case "custom":
      return "Custom endpoint";
    default:
      return "Default";
  }
}

function getBrainProviderValue() {
  return String($("brain-provider")?.value || loadedConfig?.brain?.provider || "gemma").trim() || "gemma";
}

function getBrainModelValue() {
  return String($("brain-model")?.value || loadedConfig?.brain?.model || "").trim();
}

function getConfiguredCustomBaseUrl() {
  return String(
    $("apikey-custom-url")?.value
    || loadedConfig?.providers?.custom?.base_url
    || loadedConfig?.brain?.base_url
    || loadedConfig?.brain?.api_url
    || ""
  ).trim();
}

function getImmediateBrainModelForProvider(provider, currentValue = "") {
  const nextProvider = String(provider || "").trim() || "gemma";
  const currentModel = String(currentValue || "").trim();

  if (isOllamaBackedProvider(nextProvider)) {
    const rememberedOllama = normalizeChatModelSelection(getRememberedModelValue("brain", nextProvider, ""));
    const chatModels = getChatCapableOllamaModels().map((model) => String(model?.name || "").trim()).filter(Boolean);
    if (rememberedOllama && chatModels.includes(rememberedOllama)) {
      return rememberedOllama;
    }
    return getFallbackOllamaModelName() || normalizeChatModelSelection(currentModel) || currentModel;
  }

  if (nextProvider === "custom") {
    const remembered = getRememberedModelValue("brain", nextProvider, "");
    if (remembered) {
      return remembered;
    }
    return currentModel || String(loadedConfig?.brain?.model || "").trim();
  }

  if (nextProvider === "chatgpt_oauth") {
    const remembered = getRememberedModelValue("brain", nextProvider, "");
    return remembered || CHATGPT_OAUTH_MODELS[0];
  }

  const providerModels = getProviderModels(nextProvider);
  const remembered = getRememberedModelValue("brain", nextProvider, "");
  if (remembered && (!providerModels.length || providerModels.includes(remembered))) {
    return remembered;
  }

  return getDefaultCloudModel(nextProvider) || currentModel || "";
}

async function handleBrainProviderChange() {
  const select = $("brain-provider");
  if (!select) {
    return;
  }

  const nextProvider = String(select.value || "gemma").trim() || "gemma";
  const previousProvider = String(select.dataset.prevValue || loadedConfig?.brain?.provider || "gemma").trim() || "gemma";

  if (nextProvider === previousProvider) {
    return;
  }

  const currentModelValue = getBrainModelValue();
  rememberModelValue("brain", previousProvider, currentModelValue);
  if (isOllamaBackedProvider(nextProvider)) {
    await refreshOllamaModelCaches();
  } else if (isCloudProvider(nextProvider) && nextProvider !== "custom" && nextProvider !== "chatgpt_oauth") {
    await refreshProviderModelCache(nextProvider);
  }
  const nextModel = getImmediateBrainModelForProvider(nextProvider, currentModelValue);
  rememberModelValue("brain", nextProvider, nextModel);

  try {
    await window.ocSettings.save("model", {
      brain: {
        provider: nextProvider,
        ...(nextModel ? { model: nextModel } : {}),
        ...(nextProvider === "custom"
          ? { base_url: getConfiguredCustomBaseUrl() }
          : {}),
      },
    });
    loadedConfig = mergeConfig(loadedConfig, {
      brain: {
        provider: nextProvider,
        ...(nextModel ? { model: nextModel } : {}),
        ...(nextProvider === "custom"
          ? { base_url: getConfiguredCustomBaseUrl() }
          : {}),
      },
    });
    select.dataset.prevValue = nextProvider;
    await window.ocSettings.configReload();
  } catch (error) {
    select.value = previousProvider;
    select.dataset.prevValue = previousProvider;
    await updateModelField("brain");
    MODEL_LAYERS.forEach(({ uiId }) => {
      if ($(`${uiId}-provider`)?.value === "default") {
        updateModelField(uiId).catch(() => {});
      }
    });
    refreshModelUiState();
    showStatus("model", error.message || "Could not update the brain provider.", true);
    return;
  }

  await updateModelField("brain");
  if (nextModel) {
    setLayerModelValue("brain", nextModel);
  }
  MODEL_LAYERS.forEach(({ uiId }) => {
    if ($(`${uiId}-provider`)?.value === "default") {
      updateModelField(uiId).catch(() => {});
    }
  });
  refreshModelUiState();
  if (isOllamaBackedProvider(nextProvider)) {
    void loadPulledModelsGrid();
  } else if (nextProvider === "chatgpt_oauth") {
    showStatus("model", "ChatGPT Plus OAuth selected. Connect your account using the panel below.");
  } else {
    showStatus("model", `This provider sends your conversations to ${getProviderLabel(nextProvider)}. Your API key is stored locally in your OS keychain.`);
  }
}

function setLayerProviderSummary(layer, providerSelection, effectiveProvider) {
  const summary = $(`${layer}-provider-summary`);
  if (!summary) return;
  if (providerSelection === "default") {
    summary.textContent = `Uses the default provider above: ${getProviderLabel(effectiveProvider)}.`;
  } else {
    summary.textContent = `Overrides the default provider with ${getProviderLabel(effectiveProvider)}.`;
  }
}

function getModelTargetState(targetId) {
  const isGlobal = targetId === "brain";
  const providerSelection = isGlobal
    ? getBrainProviderValue()
    : String($(`${targetId}-provider`)?.value || "default").trim() || "default";
  const effectiveProvider = !isGlobal && providerSelection === "default"
    ? getBrainProviderValue()
    : providerSelection;
  const modelValue = String($(`${targetId}-model`)?.value || "").trim();
  return {
    isGlobal,
    providerSelection,
    effectiveProvider,
    modelValue,
  };
}

function getAllModelTargets() {
  return ["brain", ...MODEL_LAYERS.map(({ uiId }) => uiId)];
}

function isEmbeddingOnlyOllamaModelName(name) {
  const cleanName = String(name || "").trim().toLowerCase();
  if (!cleanName) {
    return false;
  }
  if (cleanName.includes("embed")) {
    return true;
  }
  return EMBEDDING_MODEL_HINTS.some((prefix) => cleanName === prefix || cleanName.startsWith(`${prefix}:`));
}

function getChatCapableOllamaModels() {
  return ollamaModelsCache.filter((model) => !isEmbeddingOnlyOllamaModelName(model?.name));
}

function populateExtractionModelSelect(selectedValue) {
  const sel = $("s-extraction-model");
  if (!sel) return;
  const current = selectedValue ?? String(sel.value || "").trim();
  sel.innerHTML = "";
  // Default option — use companion model
  const defaultOpt = document.createElement("option");
  defaultOpt.value = "";
  defaultOpt.textContent = "(use companion model)";
  sel.appendChild(defaultOpt);
  // One option per chat-capable Ollama model
  getChatCapableOllamaModels().forEach((model) => {
    const name = String(model?.name || "").trim();
    if (!name) return;
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    sel.appendChild(opt);
  });
  // Restore the selected value; if it's no longer in the list, keep it as a
  // dynamic option so the user can see what was previously saved.
  if (current) {
    const matchingOpt = Array.from(sel.options).find(
      (o) => o.value === current || normalizeOllamaTag(o.value) === normalizeOllamaTag(current)
    );
    if (!matchingOpt) {
      const orphanOpt = document.createElement("option");
      orphanOpt.value = current;
      orphanOpt.textContent = current;
      sel.appendChild(orphanOpt);
    } else if (matchingOpt.value !== current) {
      // Normalize to the pulled model's actual name
      sel.value = matchingOpt.value;
      return;
    }
  }
  sel.value = current;
}

function getMemoryExtractionSourceValue() {
  const source = String($("s-extraction-source")?.value || "local").trim().toLowerCase();
  return ["brain", "local", "api"].includes(source) ? source : "local";
}

function updateMemoryExtractionUi() {
  const source = getMemoryExtractionSourceValue();
  const provider = String($("s-extraction-provider")?.value || "openai").trim().toLowerCase();
  const providerRow = $("s-extraction-provider-row");
  const baseUrlRow = $("s-extraction-base-url-row");
  const localModelRow = $("s-extraction-model-local-row");
  const remoteModelRow = $("s-extraction-model-remote-row");
  const remoteModelHint = $("s-extraction-remote-model-hint");

  if (providerRow) providerRow.style.display = source === "api" ? "flex" : "none";
  if (baseUrlRow) baseUrlRow.style.display = source === "api" && provider === "custom" ? "flex" : "none";
  if (localModelRow) localModelRow.style.display = source === "local" ? "flex" : "none";
  if (remoteModelRow) remoteModelRow.style.display = source === "local" ? "none" : "flex";

  if (remoteModelHint) {
    remoteModelHint.textContent = source === "brain"
      ? "Leave blank to inherit the companion brain model."
      : provider === "custom"
        ? "Required for custom endpoints. Uses the credentials configured in the model settings."
        : "Pick the exact remote model used only for memory extraction.";
  }
}

function normalizeChatModelSelection(modelName) {
  const cleanName = String(modelName || "").trim();
  if (!isEmbeddingOnlyOllamaModelName(cleanName)) {
    return cleanName;
  }
  return getFallbackOllamaModelName(cleanName);
}

function getFallbackOllamaModelName(excludedName = "") {
  const excluded = String(excludedName || "").trim();
  const candidate = getChatCapableOllamaModels().find((model) => String(model?.name || "").trim() !== excluded);
  return String(candidate?.name || "").trim();
}

function applyOllamaModelSelection(targetId, modelName) {
  const nextModel = String(modelName || "").trim();
  const hiddenInput = $(`${targetId}-model`);
  if (!hiddenInput) {
    return;
  }
  hiddenInput.value = nextModel;
  rememberModelValue(targetId, getTargetEffectiveProvider(targetId), nextModel);
  if (targetId === "brain") {
    MODEL_LAYERS.forEach(({ uiId }) => {
      if ($(`${uiId}-provider`)?.value === "default") {
        setLayerModelValue(uiId, nextModel);
      }
    });
  }
  syncOllamaListboxSelection(targetId);
  refreshModelUiState();
}

function getSelectedModelName(targetId) {
  const modelValue = String($(`${targetId}-model`)?.value || "").trim();
  if (modelValue) {
    return modelValue;
  }
  if (targetId === "brain") {
    return String(loadedConfig?.brain?.model || "").trim();
  }
  const { configKey, legacyConfigKey } = MODEL_LAYERS.find(({ uiId }) => uiId === targetId) || {};
  const layers = loadedConfig?.brain?.layers || {};
  const layerConfig = (configKey && layers[configKey]) || (legacyConfigKey && layers[legacyConfigKey]) || {};
  return String(layerConfig?.model || loadedConfig?.brain?.model || "").trim();
}

function formatContextTokens(value) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return null;
  }
  return parsed.toLocaleString();
}

function normalizeOllamaModelEntry(entry) {
  if (!entry) {
    return null;
  }
  const name = String(entry.name || "").trim();
  if (!name) {
    return null;
  }
  return {
    name,
    size_gb: entry.size_gb == null || entry.size_gb === "" ? null : String(entry.size_gb),
    modified_at: entry.modified_at || null,
  };
}

function getModelInfo(name) {
  return ollamaModelInfoCache[String(name || "").trim()] || null;
}

function isModelLoadedInVram(name) {
  const cleanName = String(name || "").trim();
  return ollamaRunningModelsCache.some((model) => String(model?.name || "").trim() === cleanName);
}

function getRunningModelEntry(name) {
  const cleanName = String(name || "").trim();
  return ollamaRunningModelsCache.find((model) => String(model?.name || "").trim() === cleanName) || null;
}

function buildOllamaMetaParts(model, info) {
  const parts = [];
  if (info?.parameter_size) {
    parts.push(String(info.parameter_size));
  }
  if (info?.quantization_level) {
    parts.push(String(info.quantization_level));
  }
  if (model?.size_gb) {
    parts.push(`${model.size_gb} GB`);
  }
  return parts;
}

function buildOllamaOptionLabel(model, info, loaded) {
  const marker = loaded ? "🟢" : "⚪";
  const parts = buildOllamaMetaParts(model, info);
  return `${marker} ${model.name}${parts.length ? ` — ${parts.join(" · ")}` : ""}`;
}

function buildOllamaOptionTitle(model, info) {
  const lines = [];
  if (info?.family) {
    lines.push(`Family: ${info.family}`);
  }
  if (info?.parameter_size) {
    lines.push(`Parameters: ${info.parameter_size}`);
  }
  if (info?.quantization_level) {
    lines.push(`Quantization: ${info.quantization_level}`);
  }
  const context = formatContextTokens(info?.context_length);
  if (context) {
    lines.push(`Context: ${context} tokens`);
  }
  if (model?.size_gb) {
    lines.push(`Size: ${model.size_gb} GB`);
  }
  return lines.join("\n");
}

async function refreshOllamaModelCaches(force = false) {
  if (force || !Array.isArray(ollamaModelsCache) || !ollamaModelsCache.length) {
    const models = await window.ocSettings.getOllamaModels().catch(() => []);
    ollamaModelsCache = Array.isArray(models)
      ? models.map(normalizeOllamaModelEntry).filter(Boolean)
      : [];
  }
  if (force || !Array.isArray(ollamaRunningModelsCache) || !ollamaRunningModelsCache.length) {
    const running = await window.ocSettings.getOllamaRunningModels().catch(() => []);
    ollamaRunningModelsCache = Array.isArray(running) ? running : [];
  }
  // Keep extraction model select in sync with Ollama model list
  populateExtractionModelSelect();
  return {
    models: ollamaModelsCache,
    running: ollamaRunningModelsCache,
  };
}

async function hydrateOllamaModelInfoAsync() {
  const pendingNames = ollamaModelsCache
    .map((model) => model?.name)
    .filter((name) => name && !Object.prototype.hasOwnProperty.call(ollamaModelInfoCache, name));

  if (!pendingNames.length) {
    return;
  }

  await Promise.all(pendingNames.map(async (name) => {
    try {
      const info = await window.ocSettings.getOllamaModelInfo(name);
      ollamaModelInfoCache[name] = info || null;
    } catch {
      ollamaModelInfoCache[name] = null;
    }
  }));

  for (const targetId of getAllModelTargets()) {
    const { effectiveProvider } = getModelTargetState(targetId);
    if (isOllamaBackedProvider(effectiveProvider)) {
      await updateModelField(targetId);
    }
  }
  updateModelStatusCard();
  updateContextWindowWarning();
}

function syncOllamaListboxSelection(targetId) {
  const listbox = $(`${targetId}-model-listbox`);
  const selectedModel = String($(`${targetId}-model`)?.value || "").trim();
  listbox?.querySelectorAll(".oc-model-option").forEach((row) => {
    row.setAttribute("aria-selected", row.dataset.model === selectedModel ? "true" : "false");
  });
}

function getActiveModelTargetId() {
  return document.querySelector(".layer-panel.active")?.id?.replace("model-panel-", "") || "companion";
}

function getActiveLayerRuntimeValidation() {
  const activeTargetId = getActiveModelTargetId();
  return layerRuntimeValidationState.layer === activeTargetId
    ? layerRuntimeValidationState.validation
    : null;
}

function setModelStatusRow(rowId, textId, value) {
  const row = $(rowId);
  const text = $(textId);
  if (!row || !text) {
    return;
  }
  if (value) {
    text.textContent = value;
    row.style.display = "flex";
  } else {
    text.textContent = "-";
    row.style.display = "none";
  }
}

function formatTransportLabel(transport) {
  switch (String(transport || "").trim()) {
    case "responses":
      return "Responses API";
    case "chat_completions":
      return "Chat Completions";
    case "ollama_native":
      return "Ollama native chat";
    case "ollama":
      return "Ollama";
    case "anthropic":
      return "Anthropic";
    case "messages":
      return "Anthropic Messages API";
    case "gemini":
      return "Gemini";
    case "openrouter":
      return "OpenRouter";
    case "custom":
      return "Custom endpoint";
    default:
      return transport ? String(transport) : "";
  }
}

function formatReasoningProfile(info) {
  const efforts = Array.isArray(info?.reasoningEfforts) ? info.reasoningEfforts.filter(Boolean) : [];
  if (!efforts.length) {
    return "";
  }
  const parts = [];
  if (info?.defaultReasoningEffort) {
    parts.push(`Default: ${info.defaultReasoningEffort}`);
  }
  if (info?.defaultReasoningSummary) {
    parts.push(`Summary: ${info.defaultReasoningSummary}`);
  }
  parts.push(`Modes: ${efforts.join(", ")}`);
  return parts.join(" | ");
}

function formatCapabilitySummary(info) {
  const caps = [];
  if (info?.supportsStructuredOutputs) {
    caps.push("structured outputs");
  }
  if (info?.supportsImageInput) {
    caps.push("image input");
  }
  if (info?.knowledgeCutoff) {
    caps.push(`knowledge cutoff ${info.knowledgeCutoff}`);
  }
  return caps.join(", ");
}

async function refreshActiveLayerRuntimeValidation() {
  const activeTargetId = getActiveModelTargetId();
  const token = layerRuntimeValidationState.token + 1;
  layerRuntimeValidationState.token = token;

  const results = await Promise.all(
    MODEL_LAYERS.map(async ({ uiId }) => {
      try {
        const validation = await window.ocSettings.validateLayerRuntime(uiId);
        return { uiId, validation: validation || null };
      } catch (error) {
        return {
          uiId,
          validation: {
            ok: false,
            warnings: [],
            errors: [String(error?.message || "Could not inspect the selected model runtime.")],
            resolved: { layer: uiId },
          },
        };
      }
    })
  );

  if (layerRuntimeValidationState.token !== token) return;

  for (const { uiId, validation } of results) {
    layerRuntimeValidationState.all[uiId] = validation;
    if (uiId === activeTargetId) {
      layerRuntimeValidationState.layer = activeTargetId;
      layerRuntimeValidationState.validation = validation;
    }
  }

  updateModelStatusCard();
  updateModelInfoCard();
  updateContextWindowWarning();
}

function updateModelStatusCard() {
  updateActiveLayersCard();
}

function updateActiveLayersCard() {
  const embedModelPresent = ollamaModelsCache.some((m) => {
    const n = String(m?.name || "").trim();
    return n === "nomic-embed-text" || n.startsWith("nomic-embed-text:");
  });

  for (const { uiId } of MODEL_LAYERS) {
    const { effectiveProvider } = getModelTargetState(uiId);
    const isOllama = isOllamaBackedProvider(effectiveProvider);
    const modelName = getSelectedModelName(uiId);

    const modelEl   = $(`al-${uiId}-model`);
    const badgeEl   = $(`al-${uiId}-badge`);
    const provEl    = $(`al-${uiId}-provider`);
    const paramsEl  = $(`al-${uiId}-params`);
    const quantEl   = $(`al-${uiId}-quant`);
    const ctxEl     = $(`al-${uiId}-ctx`);
    const vramEl    = $(`al-${uiId}-vram`);
    const sizeEl    = $(`al-${uiId}-size`);
    const outputEl  = $(`al-${uiId}-output`);
    const toolsEl   = $(`al-${uiId}-tools`);
    const embedEl   = $(`al-${uiId}-embed`);

    if (!modelEl) continue;

    modelEl.textContent = modelName || "No model selected";

    if (provEl) {
      const layerCfg = getModelTargetState(uiId);
      const isDefault = layerCfg.providerSelection === "default";
      provEl.textContent = isDefault
        ? `${getProviderLabel(effectiveProvider)} (default)`
        : getProviderLabel(effectiveProvider);
    }

    if (isOllama && modelName) {
      const info = getModelInfo(modelName);
      const cacheEntry = ollamaModelsCache.find((m) => m?.name === modelName);
      const runningEntry = getRunningModelEntry(modelName);

      if (paramsEl) paramsEl.textContent = info?.parameter_size || "";
      if (quantEl) quantEl.textContent = info?.quantization_level || "";
      if (ctxEl) ctxEl.textContent = info?.context_length
        ? `${Number(info.context_length).toLocaleString()} ctx`
        : "";
      if (sizeEl) sizeEl.textContent = cacheEntry?.size
        ? `${(Number(cacheEntry.size) / 1e9).toFixed(1)} GB`
        : "";

      if (runningEntry) {
        if (badgeEl) badgeEl.textContent = "In VRAM";
        if (vramEl) vramEl.textContent = runningEntry.size_vram != null
          ? `${(Number(runningEntry.size_vram) / 1e9).toFixed(1)} GB VRAM`
          : "VRAM";
      } else {
        if (badgeEl) badgeEl.textContent = "";
        if (vramEl) vramEl.textContent = "";
      }

      if (outputEl) outputEl.textContent = "";
      if (toolsEl) toolsEl.textContent = "";
      if (embedEl) embedEl.textContent = embedModelPresent ? "Embedding: ready" : "Embedding: not pulled";
    } else {
      const validation = layerRuntimeValidationState.all[uiId] ?? null;
      const resolved = validation?.resolved || {};

      if (paramsEl) paramsEl.textContent = resolved.family || "";
      if (quantEl) quantEl.textContent = "";
      if (ctxEl) ctxEl.textContent = Number.isFinite(Number(resolved.contextLength)) && Number(resolved.contextLength) > 0
        ? `${Number(resolved.contextLength).toLocaleString()} ctx`
        : "";
      if (sizeEl) sizeEl.textContent = "";
      if (vramEl) vramEl.textContent = "";
      if (outputEl) outputEl.textContent = Number.isFinite(Number(resolved.maxOutputTokens)) && Number(resolved.maxOutputTokens) > 0
        ? `${Number(resolved.maxOutputTokens).toLocaleString()} max out`
        : "";
      if (toolsEl) toolsEl.textContent = resolved.supportsTools === true
        ? "Tools: yes"
        : resolved.supportsTools === false ? "Tools: no" : "";
      if (embedEl) embedEl.textContent = "";

      if (badgeEl) {
        if (!modelName) {
          badgeEl.textContent = "";
        } else if (!validation) {
          badgeEl.textContent = "checking...";
        } else if (!validation.ok) {
          badgeEl.textContent = "error";
          badgeEl.style.background = "rgba(255,100,100,0.15)";
          badgeEl.style.color = "#ff9c9c";
        } else {
          badgeEl.textContent = "ready";
          badgeEl.style.background = "";
          badgeEl.style.color = "";
        }
      }
    }
  }

  // Update context max hint for the active layer
  const activeTargetId = getActiveModelTargetId();
  const { effectiveProvider: activeProvider } = getModelTargetState(activeTargetId);
  const activeIsOllama = isOllamaBackedProvider(activeProvider);
  const activeModelName = activeIsOllama ? getSelectedModelName(activeTargetId) : null;
  const activeInfo = activeModelName ? getModelInfo(activeModelName) : null;
  const activeValidation = layerRuntimeValidationState.all[activeTargetId] ?? null;
  const activeResolved = activeValidation?.resolved || {};
  const ctx = activeIsOllama
    ? (activeInfo?.context_length ? Number(activeInfo.context_length) : null)
    : (Number.isFinite(Number(activeResolved.contextLength)) ? Number(activeResolved.contextLength) : null);
  const ctxModelMax = $("s-ctx-model-max");
  if (ctxModelMax) {
    ctxModelMax.textContent = ctx
      ? `Model maximum: ${ctx.toLocaleString()} tokens`
      : "Auto uses the model's maximum reported context";
  }
}

function updateContextWindowWarning() {
  const warning = $("context-window-warning");
  if (!warning) {
    return;
  }
  const activeTargetId = getActiveModelTargetId();
  const modelName = getSelectedModelName(activeTargetId);
  const validation = getActiveLayerRuntimeValidation();
  const runtimeContextLength = Number.parseInt(validation?.resolved?.contextLength, 10);
  const contextLength = Number.isFinite(runtimeContextLength) && runtimeContextLength > 0
    ? runtimeContextLength
    : Number.parseInt(getModelInfo(modelName)?.context_length, 10);
  const ctxVal = readContextWindowValue();
  const configuredContextWindow = typeof ctxVal === "number" ? ctxVal : null;
  if (Number.isFinite(contextLength) && contextLength > 0 && configuredContextWindow !== null && configuredContextWindow > contextLength) {
    warning.textContent = `Configured context window (${configuredContextWindow.toLocaleString()}) exceeds model max (${contextLength.toLocaleString()}). Use Auto or reduce.`;
    warning.style.display = "block";
    return;
  }

  warning.style.display = "none";
  warning.textContent = "";
}

function applyContextWindowSetting(value) {
  const input = $("s-ctx");
  const btn = $("s-ctx-auto-btn");
  if (!input || !btn) return;
  if (value === "auto" || value == null) {
    btn.classList.add("active");
    input.disabled = false;
    input.readOnly = true;
    input.classList.add("is-auto");
    input.value = "";
  } else {
    btn.classList.remove("active");
    input.disabled = false;
    input.readOnly = false;
    input.classList.remove("is-auto");
    input.value = String(value);
  }
}

function readContextWindowValue() {
  const btn = $("s-ctx-auto-btn");
  if (btn && btn.classList.contains("active")) return "auto";
  const v = parseInt($("s-ctx")?.value, 10);
  return Number.isFinite(v) && v > 0 ? v : "auto";
}

function updateModelInfoCard() {
  updateActiveLayersCard();
}

async function refreshModelUiState() {
  updateModelStatusCard();
  updateModelInfoCard();
  updateContextWindowWarning();
  updateModelSectionVisibility();
  await refreshActiveLayerRuntimeValidation();
}

function updateModelSectionVisibility() {
  const brainProvider = getBrainProviderValue();
  const isOllama = isOllamaBackedProvider(brainProvider);
  const isOAuth = brainProvider === "chatgpt_oauth";

  // Show API keys card only for non-Ollama, non-OAuth providers
  const apiKeysCard = $("model-apikeys-card");
  if (apiKeysCard) apiKeysCard.style.display = (isOllama || isOAuth) ? "none" : "";

  // Show ChatGPT OAuth card only when OAuth provider selected
  const oauthCard = $("chatgpt-oauth-card");
  if (oauthCard) oauthCard.style.display = isOAuth ? "" : "none";

  // Show pull model section only for Ollama
  const pullSection = $("model-pull-section");
  if (pullSection) pullSection.style.display = isOllama ? "" : "none";

  // Show pulled models card grid only for Ollama
  const pulledSection = $("model-pulled-section");
  if (pulledSection) pulledSection.style.display = isOllama ? "" : "none";

  // Show system memory bar only for Ollama (loadSystemMemoryBar reveals it on success)
  const memoryBar = $("model-memory-bar");
  if (memoryBar && !isOllama) memoryBar.style.display = "none";

  // Active Layers card is always visible once a provider is configured
  const ollamaSection = $("model-ollama-section");
  if (ollamaSection) ollamaSection.style.display = "";

  if (isOAuth) {
    refreshChatGPTOAuthStatus().catch(() => {});
  }
}

// ─── ChatGPT Plus OAuth UI helpers ────────────────────────────────────────

async function refreshChatGPTOAuthStatus() {
  const disconnected = $("chatgpt-oauth-disconnected");
  const connected = $("chatgpt-oauth-connected");
  if (!disconnected || !connected) return;

  try {
    const status = await window.ocSettings.oauth.chatgpt.status();
    if (status.connected) {
      disconnected.style.display = "none";
      connected.style.display = "";
      const accountEl = $("chatgpt-oauth-account-text");
      if (accountEl) accountEl.textContent = status.accountId || "Connected";
      const statusEl = $("chatgpt-oauth-status-text");
      if (statusEl) {
        if (status.fresh) {
          const expDate = status.expiresMs ? new Date(status.expiresMs).toLocaleString() : "";
          statusEl.textContent = expDate ? `Token valid until ${expDate}` : "Token valid";
        } else if (status.hasRefresh) {
          statusEl.textContent = "Token will auto-refresh on next use";
        } else {
          statusEl.textContent = "Token expired - reconnect your account";
        }
      }
    } else {
      disconnected.style.display = "";
      connected.style.display = "none";
    }
  } catch {
    disconnected.style.display = "";
    connected.style.display = "none";
  }
}

// Wire connect button
document.addEventListener("click", (e) => {
  const target = e.target instanceof Element ? e.target : e.target?.parentElement;
  const btn = target?.closest("#chatgpt-oauth-connect-btn");
  if (!btn) return;
  e.preventDefault();
  const errEl = $("chatgpt-oauth-error");
  console.info("[ChatGPT OAuth] Connect button clicked");
  btn.disabled = true;
  btn.textContent = "Connecting...";
  if (errEl) errEl.style.display = "none";
  showStatus("model", "Opening ChatGPT OAuth in your browser...");

  window.ocSettings.oauth.chatgpt.login().then((result) => {
    console.info("[ChatGPT OAuth] Login result:", result);
    if (result.ok) {
      return refreshChatGPTOAuthStatus().then(() => {
        showStatus("model", "ChatGPT Plus account connected.");
      });
    } else {
      if (errEl) { errEl.textContent = result.error || "Connection failed."; errEl.style.display = ""; }
    }
  }).catch((err) => {
    if (errEl) { errEl.textContent = err.message || "Connection failed."; errEl.style.display = ""; }
  }).finally(() => {
    btn.disabled = false;
    btn.textContent = "Connect with ChatGPT";
  });
});

// Wire disconnect button
document.addEventListener("click", (e) => {
  const target = e.target instanceof Element ? e.target : e.target?.parentElement;
  const btn = target?.closest("#chatgpt-oauth-disconnect-btn");
  if (!btn) return;
  e.preventDefault();
  console.info("[ChatGPT OAuth] Disconnect button clicked");
  showStatus("model", "Disconnecting ChatGPT Plus account...");
  window.ocSettings.oauth.chatgpt.logout().then(() => {
    return refreshChatGPTOAuthStatus();
  }).then(() => {
    showStatus("model", "ChatGPT Plus account disconnected.");
  }).catch((err) => {
    showStatus("model", `Disconnect failed: ${err.message}`, true);
  });
});

// ─── End ChatGPT Plus OAuth UI helpers ────────────────────────────────────

function getTargetProviderSelection(targetId) {
  return targetId === "brain"
    ? getBrainProviderValue()
    : String($(`${targetId}-provider`)?.value || "default").trim() || "default";
}

function getTargetEffectiveProvider(targetId) {
  const providerSelection = getTargetProviderSelection(targetId);
  return targetId !== "brain" && providerSelection === "default"
    ? getBrainProviderValue()
    : providerSelection;
}

function rememberCurrentModelValue(targetId, providerSelection, container) {
  const previousValue = String($(`${targetId}-model`)?.value || "").trim();
  const previousProvider = String(container?.dataset?.renderedProvider || "").trim();
  if (previousValue && previousProvider) {
    rememberModelValue(targetId, previousProvider, previousValue);
  }
}

function createCloudModelControls(targetId, provider, initialValue, readOnly = false) {
  const wrapper = document.createElement("div");
  wrapper.className = "provider-model-stack";

  // ChatGPT OAuth uses a fixed model list — not from CLOUD_PROVIDER_SPECS
  if (provider === "chatgpt_oauth") {
    const models = CHATGPT_OAUTH_MODELS;
    let currentValue = String(initialValue || "").trim();
    if (!models.includes(currentValue)) currentValue = models[0];

    const select = document.createElement("select");
    select.className = "oc-select oc-provider-model-select";
    select.id = `${targetId}-model-select`;
    select.style.width = "180px";
    for (const model of models) {
      const option = document.createElement("option");
      option.value = model;
      option.textContent = model;
      if (model === currentValue) option.selected = true;
      select.appendChild(option);
    }
    // Mirror to hidden input so save logic can read it
    const hidden = document.createElement("input");
    hidden.type = "hidden";
    hidden.id = `${targetId}-model`;
    hidden.value = currentValue;
    select.addEventListener("change", () => { hidden.value = select.value; });
    if (!readOnly) {
      select.addEventListener("change", refreshModelUiState);
    }
    wrapper.appendChild(select);
    wrapper.appendChild(hidden);
    return { wrapper, input: hidden, select };
  }

  const spec = getProviderSpec(provider);
  const models = spec?.customOnly ? [] : getProviderModels(provider);
  let currentValue = String(initialValue || "").trim();

  if (provider !== "custom" && !models.includes(currentValue)) {
    currentValue = currentValue || models[0] || "";
  }

  if (provider === "custom") {
    const input = document.createElement("input");
    input.type = "text";
    input.className = "oc-input oc-provider-model-custom";
    input.id = `${targetId}-model`;
    input.placeholder = "Custom model name";
    input.value = currentValue;
    input.style.width = "180px";
    if (!readOnly) {
      input.addEventListener("input", refreshModelUiState);
      input.addEventListener("change", refreshModelUiState);
    }
    wrapper.appendChild(input);
    return { wrapper, input, select: null };
  }

  const select = document.createElement("select");
  select.className = "oc-select oc-provider-model-select";
  select.id = `${targetId}-model-select`;
  select.style.width = "180px";

  let visibleModels = [...models];
  const renderOptions = () => {
    select.textContent = "";
    for (const model of visibleModels) {
      const option = document.createElement("option");
      option.value = model;
      option.textContent = model;
      select.appendChild(option);
    }
    const customOption = document.createElement("option");
    customOption.value = "__custom__";
    customOption.textContent = "Custom...";
    select.appendChild(customOption);
  };
  renderOptions();

  if (provider === "openrouter") {
    const filter = document.createElement("input");
    filter.type = "search";
    filter.className = "oc-input oc-provider-model-custom";
    filter.placeholder = "Filter models";
    filter.style.width = "180px";
    filter.addEventListener("input", () => {
      const query = filter.value.trim().toLowerCase();
      visibleModels = query ? models.filter((model) => model.toLowerCase().includes(query)) : [...models];
      renderOptions();
      select.value = visibleModels.includes(input.value) ? input.value : "__custom__";
    });
    wrapper.appendChild(filter);
  }

  const input = document.createElement("input");
  input.type = "text";
  input.className = "oc-input oc-provider-model-custom";
  input.id = `${targetId}-model`;
  input.placeholder = `Custom ${getProviderLabel(provider)} model`;
  input.value = currentValue;
  input.style.width = "180px";

  const presetValue = models.includes(currentValue) ? currentValue : "__custom__";
  select.value = presetValue;
  input.classList.toggle("hidden", presetValue !== "__custom__");
  if (presetValue !== "__custom__") {
    input.value = presetValue;
  }

  if (!readOnly) {
    select.addEventListener("change", () => {
      if (select.value === "__custom__") {
        input.classList.remove("hidden");
        window.setTimeout(() => input.focus(), 0);
      } else {
        input.value = select.value;
        input.classList.add("hidden");
      }
      refreshModelUiState();
    });

    input.addEventListener("input", () => {
      const nextValue = input.value.trim();
      if (models.includes(nextValue)) {
        select.value = nextValue;
        input.classList.add("hidden");
      } else {
        select.value = "__custom__";
        input.classList.remove("hidden");
      }
      refreshModelUiState();
    });

    input.addEventListener("change", refreshModelUiState);
  }

  wrapper.appendChild(select);
  wrapper.appendChild(input);
  return { wrapper, input, select };
}

async function updateModelField(targetId) {
  const container = $(`${targetId}-model-container`);
  if (!container) return;

  const isGlobal = targetId === "brain";
  const providerSelection = getTargetProviderSelection(targetId);
  const effectiveProvider = getTargetEffectiveProvider(targetId);
  rememberCurrentModelValue(targetId, providerSelection, container);
  container.innerHTML = "";
  container.dataset.renderedProvider = effectiveProvider;

  if (!isGlobal) {
    setLayerProviderSummary(targetId, providerSelection, effectiveProvider);
  }

  if (!isGlobal && providerSelection === "default") {
    const inheritedInput = document.createElement("input");
    inheritedInput.type = "text";
    inheritedInput.className = "oc-input";
    inheritedInput.id = `${targetId}-model`;
    inheritedInput.placeholder = "auto-selected";
    inheritedInput.value = getBrainModelValue();
    inheritedInput.disabled = true;
    inheritedInput.style.width = "160px";
    container.appendChild(inheritedInput);

    const hint = document.createElement("div");
    hint.className = "field-hint";
    hint.textContent = `Uses the default ${getProviderLabel(effectiveProvider)} model from above.`;
    container.appendChild(hint);
    container.dataset.renderedProvider = "default";
    return;
  }

  if (isOllamaBackedProvider(effectiveProvider)) {
    await refreshOllamaModelCaches();
    const chatModels = getChatCapableOllamaModels();
    const rememberedValue = getRememberedModelValue(targetId, effectiveProvider, "");
    const currentValue = String($(`${targetId}-model`)?.value || "").trim();
    const nextValue = isEmbeddingOnlyOllamaModelName(rememberedValue || currentValue)
      ? getFallbackOllamaModelName(rememberedValue || currentValue)
      : (rememberedValue || currentValue || getFallbackOllamaModelName());

    const hiddenInput = document.createElement("input");
    hiddenInput.type = "hidden";
    hiddenInput.id = `${targetId}-model`;
    hiddenInput.value = nextValue;
    container.appendChild(hiddenInput);

    const listbox = document.createElement("div");
    listbox.className = "oc-model-listbox";
    listbox.id = `${targetId}-model-listbox`;
    listbox.setAttribute("role", "listbox");
    listbox.setAttribute("aria-label", `${targetId} Ollama models`);

    if (!chatModels.length) {
      const empty = document.createElement("div");
      empty.className = "oc-model-empty";
      empty.textContent = ollamaModelsCache.length
        ? "No local Ollama chat models found. Embedding models like nomic-embed-text cannot be used as the brain."
        : "No local Ollama models found.";
      listbox.appendChild(empty);
    } else {
      for (const model of chatModels) {
        const info = getModelInfo(model.name);
        const loaded = isModelLoadedInVram(model.name);
        const row = document.createElement("div");
        row.className = "oc-model-option";
        row.dataset.model = model.name;
        row.setAttribute("role", "option");
        row.setAttribute("aria-selected", nextValue === model.name ? "true" : "false");
        const title = buildOllamaOptionTitle(model, info);
        if (title) {
          row.title = title;
        }

        const main = document.createElement("button");
        main.type = "button";
        main.className = "oc-model-option-main";
        main.dataset.model = model.name;
        main.title = title;

        const copy = document.createElement("div");
        copy.className = "oc-model-option-copy";
        copy.textContent = buildOllamaOptionLabel(model, info, loaded);

        main.appendChild(copy);
        main.addEventListener("click", () => {
          applyOllamaModelSelection(targetId, model.name);
        });

        const deleteButton = document.createElement("button");
        deleteButton.type = "button";
        deleteButton.className = "oc-btn oc-btn-danger";
        deleteButton.textContent = "🗑";
        deleteButton.title = `Delete ${model.name}`;
        deleteButton.addEventListener("click", async (event) => {
          event.stopPropagation();
          const sizeLabel = model.size_gb ? ` (${model.size_gb} GB will be freed)` : "";
          const confirmed = window.confirm(`Remove ${model.name}?${sizeLabel}`);
          if (!confirmed) {
            return;
          }

          const result = await window.ocSettings.deleteModel(model.name);
          if (!result || result.success !== true) {
            showStatus("model", `Failed to remove ${model.name}.`, true);
            return;
          }

          ollamaModelsCache = ollamaModelsCache.filter((entry) => entry.name !== model.name);
          delete ollamaModelInfoCache[model.name];
          ollamaRunningModelsCache = ollamaRunningModelsCache.filter((entry) => String(entry?.name || "").trim() !== model.name);

          if (hiddenInput.value === model.name) {
            const fallbackModel = getFallbackOllamaModelName(model.name);
            applyOllamaModelSelection(targetId, fallbackModel);
            if (fallbackModel) {
              showStatus("model", `Selected model was deleted. Switched to ${fallbackModel}.`);
            } else {
              showStatus("model", "Selected model was deleted. No local Ollama models remain.", true);
            }
          } else {
            showStatus("model", `${model.name} removed.`);
          }

          for (const modelTargetId of getAllModelTargets()) {
            const { effectiveProvider: candidateProvider } = getModelTargetState(modelTargetId);
            if (isOllamaBackedProvider(candidateProvider)) {
              await updateModelField(modelTargetId);
            }
          }
          refreshModelUiState();
        });

        row.appendChild(main);
        row.appendChild(deleteButton);
        listbox.appendChild(row);
      }
    }

    container.appendChild(listbox);
    syncOllamaListboxSelection(targetId);
    void hydrateOllamaModelInfoAsync();
    container.dataset.renderedProvider = effectiveProvider;
    refreshModelUiState();
    return;
  }

  await refreshProviderModelCache(effectiveProvider);

  const rememberedValue = getRememberedModelValue(targetId, effectiveProvider, "");
  const currentValue = String($(`${targetId}-model`)?.value || "").trim();
  let initialValue = rememberedValue;
  if (!initialValue) {
    initialValue = effectiveProvider === "custom"
      ? currentValue
      : getDefaultCloudModel(effectiveProvider);
  }

  const { wrapper } = createCloudModelControls(targetId, effectiveProvider, initialValue);
  container.appendChild(wrapper);
  renderProviderNotice(container, effectiveProvider, targetId, isGlobal);
  renderCloudConnectionControls(container, targetId, effectiveProvider);
  clearCloudConnectionStatus(targetId);
}

function setLayerModelValue(layer, model) {
  const el = $(`${layer}-model`);
  if (!el) return;
  const provider = getTargetEffectiveProvider(layer);
  const rawValue = String(model || "").trim();
  const nextModel = isOllamaBackedProvider(provider) ? normalizeChatModelSelection(rawValue) : rawValue;
  rememberModelValue(layer, provider, nextModel);
  if (el.type === "hidden") {
    el.value = nextModel || "";
    syncOllamaListboxSelection(layer);
    return;
  }

  const select = $(`${layer}-model-select`);
  if (select) {
    const optionValues = Array.from(select.options).map((option) => option.value);
    if (provider === "custom") {
      el.value = nextModel || "";
    } else if (optionValues.includes(nextModel)) {
      select.value = nextModel;
      el.value = nextModel;
      el.classList.add("hidden");
    } else {
      select.value = "__custom__";
      el.value = nextModel || "";
      el.classList.remove("hidden");
    }
  } else {
    el.value = nextModel || "";
  }
}


// ── System memory bar ──────────────────────────────────────────────────────

/**
 * Returns a runability color based on model size vs available memory.
 * @param {number|null} sizeGB   - Model size in GB (from Ollama metadata)
 * @param {object|null} systemMemory - Result from window.ocSettings.systemMemory()
 * @returns {"green"|"amber"|"red"|null}
 */
function computeRunability(sizeGB, systemMemory) {
  if (!systemMemory || systemMemory.vramFreeMB === null || !sizeGB) return null;
  const { vramFreeMB, ramFreeMB } = systemMemory;
  const requiredMB = sizeGB * 1024;
  if (requiredMB <= vramFreeMB) return "green";
  if (requiredMB <= vramFreeMB + ramFreeMB) return "amber";
  return "red";
}

async function loadSystemMemoryBar() {
  const bar = $("model-memory-bar");
  if (!bar) return;
  let mem;
  try {
    mem = await window.ocSettings.systemMemory();
  } catch (_) {
    return;
  }
  if (!mem) return;

  const { vramFreeMB, vramTotalMB, ramFreeMB, ramTotalMB } = mem;

  const vramRow = $("vram-row");
  const vramFill = $("vram-fill");
  const vramValue = $("vram-value");
  const vramUnavailable = $("vram-unavailable");

  if (vramFreeMB === null) {
    if (vramRow) vramRow.style.display = "none";
    if (vramUnavailable) vramUnavailable.style.display = "";
  } else {
    if (vramRow) vramRow.style.display = "";
    if (vramUnavailable) vramUnavailable.style.display = "none";
    if (vramFill) {
      const usedPct = ((vramTotalMB - vramFreeMB) / vramTotalMB) * 100;
      vramFill.style.width = `${Math.min(100, usedPct).toFixed(1)}%`;
    }
    if (vramValue) {
      vramValue.textContent = `${(vramFreeMB / 1024).toFixed(1)} GB free / ${(vramTotalMB / 1024).toFixed(1)} GB total`;
    }
  }

  const ramFill = $("ram-fill");
  const ramValue = $("ram-value");
  if (ramFill) {
    const ramUsedPct = ((ramTotalMB - ramFreeMB) / ramTotalMB) * 100;
    ramFill.style.width = `${Math.min(100, ramUsedPct).toFixed(1)}%`;
  }
  if (ramValue) {
    ramValue.textContent = `${(ramFreeMB / 1024).toFixed(1)} GB free / ${(ramTotalMB / 1024).toFixed(1)} GB total`;
  }

  bar.style.display = "";
}

// ── End system memory bar ──────────────────────────────────────────────────

// ── Pulled models card grid ────────────────────────────────────────────────

/**
 * Format context length to a short string like "131K ctx" or "8.2K ctx".
 */
function formatCtxShort(contextLength) {
  const n = Number(contextLength);
  if (!Number.isFinite(n) || n <= 0) return null;
  if (n >= 1000) return `${(n / 1000).toFixed(0)}K ctx`;
  return `${n} ctx`;
}

/**
 * Render a capability badge element.
 */
function makeCapBadge(type, label) {
  const el = document.createElement("span");
  el.className = `oc-cap-badge ${type}`;
  el.textContent = label;
  return el;
}

/**
 * Render the full pulled-models card grid.
 * @param {Array} models        - Array from window.ocSettings.listPulledModels()
 * @param {object|null} systemMemory - From window.ocSettings.systemMemory()
 * @param {string} activeModel  - Currently selected brain model name
 */
function renderPulledModels(models, systemMemory, activeModel) {
  const grid = $("model-cards-grid");
  const emptyEl = $("model-cards-empty");
  if (!grid) return;

  grid.replaceChildren();

  if (!Array.isArray(models) || !models.length) {
    if (emptyEl) emptyEl.style.display = "";
    return;
  }
  if (emptyEl) emptyEl.style.display = "none";

  for (const model of models) {
    const name = typeof model === "string" ? model : String(model?.name || "").trim();
    if (!name) continue;

    // Get model info from cache
    const info = getModelInfo(name);
    const cacheEntry = ollamaModelsCache.find((m) => m?.name === name);
    const sizeGB = cacheEntry?.size_gb ? parseFloat(cacheEntry.size_gb) : null;
    const isActive = normalizeOllamaTag(name) === normalizeOllamaTag(activeModel);

    // Build card element
    const card = document.createElement("div");
    card.className = "oc-model-card" + (isActive ? " active" : "");

    // Delete button (top-right, revealed on hover)
    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "oc-model-delete-btn";
    deleteBtn.title = `Delete ${name}`;
    deleteBtn.textContent = "\uD83D\uDDD1"; // 🗑 trash icon
    deleteBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const sizeLabel = sizeGB ? ` (${sizeGB.toFixed(1)} GB will be freed)` : "";
      if (!window.confirm(`Remove ${name}?${sizeLabel}`)) return;
      try {
        const result = await window.ocSettings.deleteModel(name);
        if (!result || result.success !== true) {
          showStatus("model", `Failed to remove ${name}.`, true);
          return;
        }
        // Update caches
        ollamaModelsCache = ollamaModelsCache.filter((entry) => entry.name !== name);
        delete ollamaModelInfoCache[name];
        ollamaRunningModelsCache = ollamaRunningModelsCache.filter(
          (entry) => String(entry?.name || "").trim() !== name
        );
        // If deleted model was active, switch to fallback
        const currentActive = String($("brain-model")?.value || "").trim();
        if (currentActive === name) {
          const fallback = getFallbackOllamaModelName(name);
          applyOllamaModelSelection("brain", fallback || "");
        }
        // Re-render the grid
        await loadPulledModelsGrid();
        showStatus("model", `${name} removed.`);
      } catch (err) {
        showStatus("model", err.message || `Failed to remove ${name}.`, true);
      }
    });
    card.appendChild(deleteBtn);

    // Model name
    const nameEl = document.createElement("div");
    nameEl.className = "oc-model-card-name";
    nameEl.textContent = name;
    card.appendChild(nameEl);

    // Arch + params line
    const archParts = [];
    if (info?.family) archParts.push(info.family);
    if (info?.parameter_size) archParts.push(info.parameter_size);
    const archEl = document.createElement("div");
    archEl.className = "oc-model-card-arch";
    archEl.textContent = archParts.join(" · ") || "";
    card.appendChild(archEl);

    // Capability badges
    const caps = Array.isArray(info?.capabilities) ? info.capabilities : [];
    const hasTools   = caps.some((c) => String(c).toLowerCase().includes("tool"));
    const hasVision  = caps.some((c) => String(c).toLowerCase().includes("vision") || String(c).toLowerCase().includes("image"));
    const hasThink   = caps.some((c) => String(c).toLowerCase().includes("think"));
    const hasAudio   = caps.some((c) => String(c).toLowerCase().includes("audio"));
    const otherCaps  = !hasTools && !hasVision && !hasThink && !hasAudio;

    const badgesEl = document.createElement("div");
    badgesEl.className = "oc-model-card-badges";
    if (hasTools)  badgesEl.appendChild(makeCapBadge("tools", "tools"));
    if (hasVision) badgesEl.appendChild(makeCapBadge("vision", "vision"));
    if (hasThink)  badgesEl.appendChild(makeCapBadge("thinking", "thinking"));
    if (hasAudio)  badgesEl.appendChild(makeCapBadge("audio", "audio"));
    if (otherCaps) badgesEl.appendChild(makeCapBadge("completion", "completion"));
    if (badgesEl.children.length) card.appendChild(badgesEl);

    // Meta line: quant · ctx · size
    const metaParts = [];
    if (info?.quantization_level) metaParts.push(info.quantization_level);
    const ctxShort = formatCtxShort(info?.context_length);
    if (ctxShort) metaParts.push(ctxShort);
    if (sizeGB) metaParts.push(`${sizeGB.toFixed(1)} GB`);
    if (metaParts.length) {
      const metaEl = document.createElement("div");
      metaEl.className = "oc-model-card-meta";
      metaEl.textContent = metaParts.join(" · ");
      card.appendChild(metaEl);
    }

    // Footer: active badge + runability dot
    const footer = document.createElement("div");
    footer.className = "oc-model-card-footer";

    if (isActive) {
      const activeBadge = document.createElement("span");
      activeBadge.className = "oc-model-active-badge";
      activeBadge.textContent = "Active";
      footer.appendChild(activeBadge);
    } else {
      footer.appendChild(document.createElement("span")); // spacer
    }

    const runability = computeRunability(sizeGB, systemMemory);
    if (runability) {
      const dot = document.createElement("span");
      dot.className = `oc-model-runability ${runability}`;
      dot.title = runability === "green"
        ? "Fits in VRAM"
        : runability === "amber"
          ? "Needs RAM offload"
          : "May not fit in memory";
      footer.appendChild(dot);
    }

    card.appendChild(footer);

    // Click to select model
    card.addEventListener("click", (e) => {
      if (e.target === deleteBtn || deleteBtn.contains(e.target)) return;
      const hiddenInput = $("brain-model");
      if (hiddenInput) hiddenInput.value = name;
      applyOllamaModelSelection("brain", name);
      // Persist and reload config
      window.ocSettings.save("model", { brain: { model: name } })
        .then(() => window.ocSettings.configReload())
        .catch((err) => console.error("Failed to set active model:", err));
      // Re-render cards with new active
      renderPulledModels(models, systemMemory, name);
      // Update layer model displays for layers using "default" provider
      MODEL_LAYERS.forEach(({ uiId }) => {
        if ($(`${uiId}-provider`)?.value === "default") {
          setLayerModelValue(uiId, name);
        }
      });
    });

    grid.appendChild(card);
  }
}

/**
 * Load the pulled models grid (shows loading state, then renders cards).
 */
async function loadPulledModelsGrid() {
  const loadingEl = $("model-cards-loading");
  const emptyEl = $("model-cards-empty");
  const grid = $("model-cards-grid");
  if (!grid) return;

  if (loadingEl) loadingEl.style.display = "";
  if (emptyEl) emptyEl.style.display = "none";
  grid.replaceChildren();

  let models = [];
  let systemMemory = null;

  try {
    [models, systemMemory] = await Promise.all([
      window.ocSettings.listPulledModels().catch(() => []),
      window.ocSettings.systemMemory().catch(() => null),
    ]);
  } catch (_) {
    // Silently fall through — will show empty state
  }

  if (loadingEl) loadingEl.style.display = "none";

  const activeModel = String($("brain-model")?.value || "").trim()
    || String(loadedConfig?.brain?.model || "").trim();

  renderPulledModels(models, systemMemory, activeModel);
}

// Wire refresh button
$("refresh-pulled-btn")?.addEventListener("click", () => {
  void loadPulledModelsGrid();
});

// ── End pulled models card grid ────────────────────────────────────────────

async function loadModelTab() {
  // Load keys from keychain and render the saved state quickly before metadata hydration.
  await Promise.all([
    loadCredentialField("apikey-openai", "openai"),
    loadCredentialField("apikey-anthropic", "anthropic"),
    loadCredentialField("apikey-gemini", "gemini"),
    loadCredentialField("apikey-openrouter", "openrouter"),
    loadCredentialField("apikey-qwen", "qwen"),
    loadCredentialField("apikey-custom", "custom"),
    loadCredentialField("apikey-custom-url", "custom_url", false),
  ]);

  await refreshOllamaModelCaches(true);
  await Promise.all(CLOUD_PROVIDER_ORDER.map((provider) => refreshProviderModelCache(provider)));
  const brain = loadedConfig.brain || {};
  if (!$("apikey-custom-url")?.value) {
    $("apikey-custom-url").value = getConfiguredCustomBaseUrl();
  }
  const brainProvider = brain.provider || "gemma";
  $("brain-provider").value = brainProvider;
  $("brain-provider").dataset.prevValue = brainProvider;
  rememberModelValue("brain", brainProvider, brain.model || "");
  await updateModelField("brain");
  setLayerModelValue("brain", brain.model || "");

  const layers = brain.layers || {};
  for (const { uiId, configKey, legacyConfigKey } of MODEL_LAYERS) {
    const lc = layers[configKey] || (legacyConfigKey ? layers[legacyConfigKey] : {}) || {};
    const provider = lc.provider ? lc.provider : "default";
    $(`${uiId}-provider`).value = provider;
    setLayerTemperatureValue(uiId, lc.temperature ?? "");
    setLayerMaxTokensValue(uiId, lc.max_tokens ?? "");
    if (provider !== "default") {
      rememberModelValue(uiId, provider, lc.model || brain.model || "");
    }
    await updateModelField(uiId);
    if (provider === "default") {
      setLayerModelValue(uiId, brain.model || "");
    } else {
      setLayerModelValue(uiId, lc.model || brain.model || "");
    }
  }

  if (isOllamaBackedProvider(brainProvider)) {
    await loadPulledModelsGrid();
  }

  if (!modelPullCleanup) {
    initPullModel();
  }
  void initDiscoverSection();
  refreshModelUiState();
  void hydrateOllamaModelInfoAsync();
  void loadSystemMemoryBar();
}

function initPullModel() {
  const btn = $("pull-model-btn");
  const cancelBtn = $("cancel-pull-btn");
  const input = $("pull-model-input");
  const progressWrap = $("pull-progress-wrap");
  const progressBar = $("pull-progress-bar");
  const statusText = $("pull-status-text");
  const sizeText = $("pull-size-text");
  const errorBanner = $("pull-error-banner");
  const successBanner = $("pull-success-banner");
  if (!btn || !cancelBtn || !input || !progressWrap || !progressBar || !statusText || !sizeText || !errorBanner || !successBanner) {
    return;
  }

  const hideBanner = (el) => {
    el.classList.add("hidden");
    el.textContent = "";
  };

  const showBanner = (el, text) => {
    el.textContent = text;
    el.classList.remove("hidden");
  };

  const resetProgress = () => {
    progressWrap.style.display = "none";
    progressBar.style.width = "0%";
    statusText.textContent = "Downloading...";
    sizeText.textContent = "";
  };

  modelPullCleanup = window.ocSettings.onPullProgress((data) => {
    progressWrap.style.display = "block";
    statusText.textContent = data?.status || "Downloading...";
    if (data?.percent != null) {
      progressBar.style.width = `${data.percent}%`;
    }
    if (data?.completed_gb && data?.total_gb) {
      sizeText.textContent = `${data.completed_gb} GB / ${data.total_gb} GB`;
    } else {
      sizeText.textContent = "";
    }
  });

  btn.addEventListener("click", async () => {
    const modelName = input.value.trim();
    if (!modelName) {
      return;
    }

    hideBanner(errorBanner);
    hideBanner(successBanner);
    progressWrap.style.display = "block";
    progressBar.style.width = "0%";
    statusText.textContent = "Starting download...";
    sizeText.textContent = "";
    btn.disabled = true;
    cancelBtn.style.display = "inline-flex";

    const result = await window.ocSettings.pullModel(modelName);

    btn.disabled = false;
    cancelBtn.style.display = "none";

    if (result?.success) {
      showBanner(successBanner, `✓ ${modelName} pulled successfully. Refreshing model list...`);
      input.value = "";
      await refreshOllamaModelCaches(true);
      for (const targetId of getAllModelTargets()) {
        const { effectiveProvider } = getModelTargetState(targetId);
        if (isOllamaBackedProvider(effectiveProvider)) {
          await updateModelField(targetId);
        }
      }
      refreshModelUiState();
      void hydrateOllamaModelInfoAsync();
      void loadPulledModelsGrid();
      setTimeout(() => hideBanner(successBanner), 4000);
      resetProgress();
    } else if (result?.cancelled) {
      statusText.textContent = "Download cancelled.";
      setTimeout(resetProgress, 1200);
    } else {
      showBanner(errorBanner, `✗ Failed: ${result?.error || "Unknown error"}`);
      resetProgress();
    }
  });

  cancelBtn.addEventListener("click", async () => {
    await window.ocSettings.cancelPull();
  });
}

// ── Discover & Pull section ──────────────────────────────────────────────────

function renderDiscoverResults(results, pulledModelNames) {
  const grid = $("discover-results-grid");
  if (!grid) return;
  grid.innerHTML = "";

  for (const item of results) {
    const baseName = item.name;
    // Derive default tag from the name (the part after ":")
    const colonIdx = baseName.indexOf(":");
    let selectedTag = colonIdx !== -1 ? baseName.slice(colonIdx + 1) : (item.tags && item.tags.length ? item.tags[0] : "");
    const baseWithoutTag = colonIdx !== -1 ? baseName.slice(0, colonIdx) : baseName;

    // Build the card element
    const card = document.createElement("div");
    card.className = "oc-discover-card";

    const infoDiv = document.createElement("div");
    infoDiv.className = "oc-discover-card-info";

    const nameEl = document.createElement("div");
    nameEl.className = "oc-discover-card-name";
    nameEl.textContent = baseName;
    infoDiv.appendChild(nameEl);

    if (item.description) {
      const descEl = document.createElement("div");
      descEl.className = "oc-discover-card-desc";
      descEl.textContent = item.description;
      infoDiv.appendChild(descEl);
    }

    if (item.pull_count != null) {
      const metaEl = document.createElement("div");
      metaEl.className = "oc-discover-card-meta";
      const count = Number(item.pull_count);
      let label;
      if (count >= 1_000_000) {
        label = (count / 1_000_000).toFixed(1).replace(/\.0$/, "") + "M pulls";
      } else if (count >= 1_000) {
        label = (count / 1_000).toFixed(1).replace(/\.0$/, "") + "K pulls";
      } else {
        label = count + " pulls";
      }
      metaEl.textContent = label;
      infoDiv.appendChild(metaEl);
    }

    let pullBtn;

    if (item.tags && item.tags.length) {
      const tagsDiv = document.createElement("div");
      tagsDiv.className = "oc-discover-tags";
      for (const tag of item.tags) {
        const chip = document.createElement("span");
        chip.className = "oc-discover-tag" + (tag === selectedTag ? " selected" : "");
        chip.textContent = tag;
        chip.dataset.tag = tag;
        chip.addEventListener("click", () => {
          selectedTag = tag;
          // Update visual selection
          tagsDiv.querySelectorAll(".oc-discover-tag").forEach((c) => {
            c.classList.toggle("selected", c.dataset.tag === tag);
          });
          // Update pull button state
          const resolvedName = `${baseWithoutTag}:${tag}`;
          nameEl.textContent = resolvedName;
          const isPulled = pulledModelNames.has(resolvedName) || pulledModelNames.has(baseWithoutTag);
          if (pullBtn) {
            pullBtn.disabled = isPulled;
            pullBtn.classList.toggle("pulled", isPulled);
            pullBtn.textContent = isPulled ? "✓ Pulled" : "Pull";
            pullBtn.dataset.modelName = resolvedName;
          }
        });
        tagsDiv.appendChild(chip);
      }
      infoDiv.appendChild(tagsDiv);
    }

    const actionDiv = document.createElement("div");
    actionDiv.className = "oc-discover-card-action";

    const resolvedName = selectedTag ? `${baseWithoutTag}:${selectedTag}` : baseName;
    const isPulled = pulledModelNames.has(resolvedName) || pulledModelNames.has(baseName) || pulledModelNames.has(baseWithoutTag);

    pullBtn = document.createElement("button");
    pullBtn.className = "oc-discover-pull-btn" + (isPulled ? " pulled" : "");
    pullBtn.textContent = isPulled ? "✓ Pulled" : "Pull";
    pullBtn.disabled = isPulled;
    pullBtn.dataset.modelName = resolvedName;

    const progressEl = document.createElement("div");
    progressEl.className = "oc-discover-progress";
    progressEl.style.display = "none";

    const errorEl = document.createElement("div");
    errorEl.className = "oc-discover-pull-error";
    errorEl.style.display = "none";

    pullBtn.addEventListener("click", async () => {
      const modelName = pullBtn.dataset.modelName;
      if (!modelName || pullBtn.disabled) return;

      pullBtn.disabled = true;
      pullBtn.textContent = "Pulling\u2026";
      progressEl.style.display = "block";
      progressEl.textContent = "Starting\u2026";
      errorEl.style.display = "none";

      // Subscribe to pull progress for inline display
      const cleanup = window.ocSettings.onPullProgress((data) => {
        if (data?.percent != null) {
          progressEl.textContent = `${Math.round(data.percent)}%`;
        } else if (data?.status) {
          progressEl.textContent = data.status;
        }
      });

      try {
        const result = await window.ocSettings.pullModel(modelName);
        cleanup();
        progressEl.style.display = "none";

        if (result?.success) {
          pullBtn.classList.add("pulled");
          pullBtn.textContent = "\u2713 Pulled";
          // Refresh model dropdowns and pulled models grid
          await refreshOllamaModelCaches(true);
          for (const targetId of getAllModelTargets()) {
            const { effectiveProvider } = getModelTargetState(targetId);
            if (isOllamaBackedProvider(effectiveProvider)) {
              await updateModelField(targetId);
            }
          }
          refreshModelUiState();
          void hydrateOllamaModelInfoAsync();
          void loadPulledModelsGrid();
        } else if (result?.cancelled) {
          pullBtn.disabled = false;
          pullBtn.textContent = "Pull";
          progressEl.style.display = "none";
        } else {
          pullBtn.disabled = false;
          pullBtn.textContent = "Pull";
          errorEl.textContent = result?.error || "Pull failed";
          errorEl.style.display = "block";
          setTimeout(() => { errorEl.style.display = "none"; }, 4000);
        }
      } catch (err) {
        cleanup();
        pullBtn.disabled = false;
        pullBtn.textContent = "Pull";
        progressEl.style.display = "none";
        errorEl.textContent = String(err?.message || err || "Error");
        errorEl.style.display = "block";
        setTimeout(() => { errorEl.style.display = "none"; }, 4000);
      }
    });

    actionDiv.appendChild(pullBtn);
    actionDiv.appendChild(progressEl);
    actionDiv.appendChild(errorEl);

    card.appendChild(infoDiv);
    card.appendChild(actionDiv);
    grid.appendChild(card);
  }
}

async function runDiscoverSearch(query) {
  const loadingEl = $("discover-loading");
  const offlineEl = $("discover-offline");

  // Get current pulled model names
  let pulledNames = new Set();
  try {
    const pulled = await window.ocSettings.listPulledModels();
    if (Array.isArray(pulled)) {
      for (const m of pulled) {
        const n = typeof m === "string" ? m : (m?.name || "");
        if (n) {
          pulledNames.add(n);
          // Also add base name without tag for "already pulled" detection
          const ci = n.indexOf(":");
          if (ci !== -1) pulledNames.add(n.slice(0, ci));
        }
      }
    }
  } catch (_) {
    // ignore — no Ollama running, pulledNames stays empty
  }

  if (!query) {
    if (loadingEl) loadingEl.style.display = "none";
    if (offlineEl) offlineEl.style.display = "none";
    renderDiscoverResults(CURATED_MODELS, pulledNames);
    return;
  }

  if (loadingEl) loadingEl.style.display = "block";
  if (offlineEl) offlineEl.style.display = "none";

  try {
    const result = await window.ocSettings.searchRegistry(query);
    if (loadingEl) loadingEl.style.display = "none";
    if (result?.error) {
      if (offlineEl) offlineEl.style.display = "block";
      const q = query.toLowerCase();
      const filtered = CURATED_MODELS.filter(
        (m) => m.name.toLowerCase().includes(q) || (m.description || "").toLowerCase().includes(q)
      );
      renderDiscoverResults(filtered.length ? filtered : CURATED_MODELS, pulledNames);
    } else {
      if (offlineEl) offlineEl.style.display = "none";
      const items = Array.isArray(result) ? result : (result?.models || []);
      renderDiscoverResults(items, pulledNames);
    }
  } catch (_) {
    if (loadingEl) loadingEl.style.display = "none";
    if (offlineEl) offlineEl.style.display = "block";
    const q = query.toLowerCase();
    const filtered = CURATED_MODELS.filter(
      (m) => m.name.toLowerCase().includes(q) || (m.description || "").toLowerCase().includes(q)
    );
    renderDiscoverResults(filtered.length ? filtered : CURATED_MODELS, pulledNames);
  }
}

let discoverSearchTimer = null;

async function initDiscoverSection() {
  if (discoverSectionInitialized) {
    // Re-render with fresh pulled models set on subsequent tab loads
    await runDiscoverSearch("");
    return;
  }
  discoverSectionInitialized = true;

  // Initial render with curated models
  await runDiscoverSearch("");

  // Wire up search input (only once)
  const searchInput = $("model-discover-search");
  if (searchInput) {
    searchInput.addEventListener("input", (e) => {
      clearTimeout(discoverSearchTimer);
      discoverSearchTimer = setTimeout(() => runDiscoverSearch(e.target.value.trim()), 300);
    });
  }
}

// ── Theme apply from main process ──
window.ocSettings.onThemeApply((data) => {
  if (!data) return;
  if (data.accent_rgb) {
    $("s-r").value = data.accent_rgb[0];
    $("s-g").value = data.accent_rgb[1];
    $("s-b").value = data.accent_rgb[2];
    updateAccent();
  }
  if (data.mode) {
    setBaseMode(data.mode);
  }
});

// ── Sandbox tab ──────────────────────────────────────────────────────────────
function $sand(id) { return document.getElementById(id); }

function setSandboxBadge(id, value) {
  const el = $sand(id);
  if (!el) return;
  if (value === true) {
    el.textContent = "Yes";
    el.className = "sandbox-badge ok";
  } else if (value === false) {
    el.textContent = "No";
    el.className = "sandbox-badge fail";
  } else {
    el.textContent = "—";
    el.className = "sandbox-badge";
  }
}

function renderSandboxStatus(status) {
  setSandboxBadge("sandbox-badge-wsl", status.available);
  setSandboxBadge("sandbox-badge-installed", status.installed);
  setSandboxBadge("sandbox-badge-vault", status.vaultMounted);

  const provisionBtn = $sand("sandbox-provision-btn");
  if (provisionBtn) {
    provisionBtn.disabled = Boolean(status.installed);
  }
}

function showSandboxOutput(text) {
  const el = $sand("sandbox-output");
  if (!el) return;
  el.textContent = String(text || "").trim();
  el.classList.toggle("hidden", !el.textContent);
}

async function loadSandboxTab() {
  showSandboxOutput("");
  try {
    const status = await window.ocSettings.sandboxStatus();
    renderSandboxStatus(status);
  } catch (err) {
    showSandboxOutput(`Failed to load sandbox status: ${err.message}`);
  }
}

$sand("sandbox-provision-btn")?.addEventListener("click", async () => {
  const btn = $sand("sandbox-provision-btn");
  if (btn) btn.disabled = true;
  showSandboxOutput("Provisioning sandbox… this may take a few minutes.");
  try {
    const result = await window.ocSettings.sandboxProvision();
    showSandboxOutput(result?.message || "Done.");
  } catch (err) {
    showSandboxOutput(`Error: ${err.message}`);
  }
  await loadSandboxTab();
});

$sand("sandbox-verify-btn")?.addEventListener("click", async () => {
  showSandboxOutput("Verifying…");
  try {
    const result = await window.ocSettings.sandboxVerify();
    showSandboxOutput(result?.ok ? "Verification passed." : `Verification failed: ${result?.reason || "unknown"}`);
  } catch (err) {
    showSandboxOutput(`Error: ${err.message}`);
  }
  await loadSandboxTab();
});

$sand("sandbox-reset-btn")?.addEventListener("click", async () => {
  const confirmed = window.confirm(
    "This will unregister and re-provision the sandbox. All data inside it will be lost.\n\nContinue?"
  );
  if (!confirmed) return;
  showSandboxOutput("Resetting sandbox… this may take a few minutes.");
  try {
    const result = await window.ocSettings.sandboxReset();
    showSandboxOutput(result?.message || "Done.");
  } catch (err) {
    showSandboxOutput(`Error: ${err.message}`);
  }
  await loadSandboxTab();
});

registerTabLoader("sandbox", loadSandboxTab);
// ── End sandbox tab ───────────────────────────────────────────────────────────

// ── Init ──
$("s-extraction-source")?.addEventListener("change", () => {
  updateMemoryExtractionUi();
});
$("s-extraction-provider")?.addEventListener("change", () => {
  updateMemoryExtractionUi();
});

wireSectionAutoSave("persona");

// ── Soul regenerate modal ──
(function () {
  const openModal = async () => {
    const modal = $("soul-regen-modal");
    if (!modal) return;
    modal.style.display = "flex";
    const promptEl = $("soul-regen-prompt");
    if (promptEl && window.ocSettings.readSoulGenerateTemplate) {
      promptEl.value = "Loading\u2026";
      try {
        const result = await window.ocSettings.readSoulGenerateTemplate();
        promptEl.value = result?.content || "";
      } catch (_) {
        promptEl.value = "";
      }
    }
  };

  const closeModal = () => {
    const modal = $("soul-regen-modal");
    if (modal) modal.style.display = "none";
  };

  $("soul-regenerate-btn")?.addEventListener("click", openModal);
  $("soul-regen-close")?.addEventListener("click", closeModal);
  $("soul-regen-cancel")?.addEventListener("click", closeModal);
  $("soul-regen-modal")?.addEventListener("click", (e) => {
    if (e.target === $("soul-regen-modal")) closeModal();
  });

  $("soul-regen-generate")?.addEventListener("click", async () => {
    const prompt = $("soul-regen-prompt")?.value?.trim();
    if (!prompt) return;
    const btn = $("soul-regen-generate");
    if (btn) { btn.disabled = true; btn.textContent = "Generating\u2026"; }
    showStatus("soul-regen", "Sending to AI\u2026");
    try {
      // Send prompt to companion layer and use response as new soul content
      const result = await window.ipc?.sendToCompanion?.(prompt)
        || await window.electronAPI?.invoke?.("invoke_layer", { layer: "companion", content: prompt });
      let newContent = typeof result === "string" ? result : result?.content || "";
      newContent = newContent.trim();
      if (newContent) {
        if ($("persona-soul-raw")) $("persona-soul-raw").value = newContent;
        await window.ocSettings.writeSoulFile?.(newContent);
        closeModal();
        showStatus("persona", "Soul regenerated.");
      } else {
        showStatus("soul-regen", "No response received.", true);
      }
    } catch (err) {
      showStatus("soul-regen", err.message || "Generation failed.", true);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = "Generate"; }
    }
  });
})();

wireSectionAutoSave("model");
initGlobalTempControl();
initLayerOverrideControls();
wireSectionAutoSave("memory");
wireSectionAutoSave("heartbeat");
wireSectionAutoSave("audio");
wireSectionAutoSave("themes");
wireSectionAutoSave("tools");

async function init() {
  try {
    loadedConfig = await window.ocSettings.load();
    applySettingsOptions(loadedConfig.settings_options);
    populateFromConfig(loadedConfig);
    await loadModelTab();
    await refreshUpdaterControls();
    await initVoicePreviews();
    await activateTab(document.querySelector(".oc-nav-btn.active")?.dataset.tab || "persona");
  } catch (err) {
    console.error("Failed to load settings:", err);
  }
}

init();
