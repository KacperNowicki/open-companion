const { app, BrowserWindow, clipboard, dialog, globalShortcut, ipcMain, screen, shell, systemPreferences } = require("electron");
const { spawn, spawnSync, execFile } = require("child_process");
const os = require("os");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const http = require("http");
const keytar = require("keytar");
const {
  DEFAULT_COMPACT_OVERLAY_HEIGHT,
  DEFAULT_COMPACT_OVERLAY_WIDTH,
  DEFAULT_OVERLAY_SCALE,
  dimensionsFromOverlayScale,
  overlayScaleFromDimensions,
  parseOverlayScaleValue,
} = require("./overlay-scale");

function loadRepoDotEnv() {
  const dotenvPath = path.resolve(__dirname, "..", "..", ".env");
  if (!fs.existsSync(dotenvPath)) {
    return;
  }
  try {
    const raw = fs.readFileSync(dotenvPath, "utf8");
    for (const rawLine of raw.split(/\r?\n/)) {
      const line = String(rawLine || "").trim();
      if (!line || line.startsWith("#")) {
        continue;
      }
      const separatorIndex = line.indexOf("=");
      if (separatorIndex <= 0) {
        continue;
      }
      const key = line.slice(0, separatorIndex).trim();
      if (!key || process.env[key]) {
        continue;
      }
      let value = line.slice(separatorIndex + 1).trim();
      if (
        value.length >= 2 &&
        ((value.startsWith("\"") && value.endsWith("\"")) ||
          (value.startsWith("'") && value.endsWith("'")))
      ) {
        value = value.slice(1, -1);
      }
      process.env[key] = value;
    }
  } catch (error) {
    console.warn(`[env] Failed to read .env: ${error?.message || error}`);
  }
}

loadRepoDotEnv();

const runtimePaths = require("./runtime-paths");
const { RuntimeManager } = require("./runtime-manager");
const { RuntimeAssetManager } = require("./runtime-asset-manager");
const { SandboxManager } = require("./sandbox-manager");
const { runMigration } = require("./profile-migration");
const {
  ANTHROPIC_FALLBACK_CARD,
  CHATGPT_OAUTH_MODEL_CARDS,
  CHATGPT_OAUTH_MODELS,
  OPENAI_LEGACY_FALLBACK_CARD,
  QWEN_CLOUD_MODEL_IDS,
  detectModelFamily,
  getAnthropicModelCard,
  getChatGptOauthModelCard,
  getCuratedProviderModelIds,
  getOpenAiModelCard,
  isOllamaBackedProvider,
  mergeProviderModelIds,
} = require("./main/provider-models");
const { registerVoicePreviewIpc } = require("./main/ipc");
const { createVoicePreviewService } = require("./main/voice-previews");
const { createOllamaRuntimeCache } = require("./main/ollama-cache");

process.env.OPEN_COMPANION_PROFILE_DIR = runtimePaths.PROFILE_ROOT;
process.env.OPEN_COMPANION_PROJECT_ROOT = runtimePaths.READONLY_PROJECT_ROOT;
process.env.OPEN_COMPANION_RUNTIME_ASSETS_DIR = runtimePaths.RUNTIME_ASSETS_DIR;
process.env.OPEN_COMPANION_KOKORO_MODEL_PATH = runtimePaths.KOKORO_MODEL_PATH;
process.env.OPEN_COMPANION_KOKORO_VOICES_PATH = runtimePaths.KOKORO_VOICES_PATH;

const {
  CONFIG_PATH,
  DEFAULT_CONFIG,
  PROFILE_ROOT,
  TEST_MODE,
  deepMerge: mergeConfig,
  isPlainObject,
  loadConfig,
  readBaseConfig,
  readJsonFile,
  saveConfig,
  updateLocalConfig,
  writeJsonFile,
} = require("../../config/index");
const {
  setupUpdater,
  checkForAppUpdates,
  downloadAppUpdate,
  installAppUpdate,
  dismissReadyAppUpdate,
  getUpdaterStatus,
} = require("../scripts/updater");

const KEYCHAIN_SERVICE = String(process.env.OPEN_COMPANION_KEYCHAIN_SERVICE || "open-companion").trim() || "open-companion";
const LEGACY_KEYCHAIN_SERVICE = "OpenCompanion";
const APP_USER_MODEL_ID = "com.opencompanion.app";
const APP_ICON_PATH = path.join(__dirname, "assets", "brand", "icon.png");
process.env.OPEN_COMPANION_KEYCHAIN_SERVICE = KEYCHAIN_SERVICE;

if (process.platform === "win32") {
  app.setAppUserModelId(APP_USER_MODEL_ID);
}

const KEYCHAIN_ACCOUNT_GROUPS = {
  openai: {
    primary: "openai_api_key",
    aliases: ["openai"],
  },
  anthropic: {
    primary: "anthropic_api_key",
    aliases: ["anthropic"],
  },
  gemini: {
    primary: "gemini_api_key",
    aliases: ["gemini"],
  },
  openrouter: {
    primary: "openrouter_api_key",
    aliases: ["openrouter"],
  },
  qwen: {
    primary: "qwen_api_key",
    aliases: ["qwen", "dashscope_api_key"],
  },
  custom: {
    primary: "custom_api_key",
    aliases: ["custom"],
  },
  custom_url: {
    primary: "custom_url",
    aliases: [],
  },
};

const PROVIDER_VALIDATION_DEFAULT_BASE_URLS = {
  openai: "https://api.openai.com/v1",
  anthropic: "https://api.anthropic.com/v1",
  gemini: "https://generativelanguage.googleapis.com/v1beta",
  openrouter: "https://openrouter.ai/api/v1",
  qwen_cloud: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
  custom: "http://localhost:11434/v1",
};

const PROJECT_ROOT = runtimePaths.PROJECT_ROOT;
const MEMORY_DIR = runtimePaths.MEMORY_DIR;
const SOUL_ACTIVE_DIR = runtimePaths.SOUL_ACTIVE_DIR;
const VAULT_DIR = runtimePaths.VAULT_DIR;
const TEST_OLLAMA_STATE_PATH = path.join(PROFILE_ROOT, "ollama-test-state.json");
const BACKEND_ENTRY = runtimePaths.BACKEND_PYTHON_ENTRY;
const AUTO_CLOSE_MS = Number.parseInt(process.env.OPEN_COMPANION_AUTOCLOSE_MS || "", 10);
const IMPORT_SECTION_ORDER = ["memory", "soul_companion"];
const TEST_EVENT_HISTORY_LIMIT = 80;
const LOGS_DIR = path.join(PROFILE_ROOT, "logs");
const MAIN_LOG_PATH = path.join(LOGS_DIR, "main.log");
const DEBUG_LOGS_DIR = path.join(PROJECT_ROOT, "logs");
const DEBUG_LOG_PATH = path.join(DEBUG_LOGS_DIR, "debug.log");
const DEBUG_LOG_ROTATE_BYTES = 10 * 1024 * 1024;
const DEBUG_LOG_ENABLED = process.env.OPENCOMPANION_DEBUG === "1";
const SETTINGS_PREWARM_DELAY_MS = Math.max(0, Number.parseInt(process.env.OPEN_COMPANION_SETTINGS_PREWARM_DELAY_MS || "1200", 10) || 0);
const SETTINGS_PREWARM_ENABLED = process.env.OPEN_COMPANION_PREWARM_SETTINGS !== "0";
let pullAbortController = null;
let pythonUserSiteCache = null;
const ollamaRuntimeCache = createOllamaRuntimeCache({ fetchImpl: (url, options) => fetch(url, options) });
const DEFAULT_MEMORY_TOPICS = {
  "memory.md": "# Memory\n",
};
const MEMORY_RESET_FILE_NAMES = new Set([
  "memory.md",
  ".dream_state.json",
  ".dream_lock",
  ".last_session_screens.jsonl",
]);

function normalizeMainLogDetail(detail) {
  if (detail == null) {
    return "";
  }
  if (detail instanceof Error) {
    return JSON.stringify({
      name: detail.name,
      message: detail.message,
      stack: detail.stack,
    });
  }
  if (typeof detail === "string") {
    return detail.replace(/\r?\n/g, "\\n");
  }
  try {
    return JSON.stringify(detail);
  } catch {
    return String(detail).replace(/\r?\n/g, "\\n");
  }
}

function logMain(level, message, detail = null) {
  try {
    fs.mkdirSync(LOGS_DIR, { recursive: true });
    const suffix = detail == null ? "" : ` ${normalizeMainLogDetail(detail)}`;
    fs.appendFileSync(MAIN_LOG_PATH, `[${new Date().toISOString()}] [${level}] ${message}${suffix}\n`, "utf8");
  } catch {
    // Never crash the app because the diagnostic logger is unavailable.
  }
}

function normalizeDebugPayload(payload) {
  if (typeof payload === "string") {
    const trimmed = payload.trim();
    if (trimmed) {
      try {
        return JSON.parse(trimmed);
      } catch {
        return payload;
      }
    }
    return payload;
  }
  try {
    JSON.stringify(payload);
    return payload;
  } catch {
    return String(payload);
  }
}

function debugMessageText(value) {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    return value
      .map((item) => {
        if (item && typeof item === "object") return item.text || item.content || "";
        return item ? String(item) : "";
      })
      .filter(Boolean)
      .join("\n");
  }
  if (value && typeof value === "object") {
    return debugMessageText(value.content || value.text || "");
  }
  return "";
}

function extractDebugSystemAndMessage(payload) {
  const body = payload && typeof payload === "object" && payload.body && typeof payload.body === "object"
    ? payload.body
    : payload;
  if (!body || typeof body !== "object") {
    return { system: "", message: "" };
  }
  let system = typeof body.instructions === "string" ? body.instructions : "";
  let message = "";
  if (Array.isArray(body.messages)) {
    for (const item of body.messages) {
      if (!item || typeof item !== "object") continue;
      const role = String(item.role || "").toLowerCase();
      const text = debugMessageText(item.content);
      if (role === "system" && text && !system) system = text;
      if (["user", "assistant", "tool"].includes(role) && text) message = text;
    }
  }
  if (Array.isArray(body.input)) {
    for (const item of body.input) {
      if (!item || typeof item !== "object") continue;
      const role = String(item.role || item.type || "").toLowerCase();
      const text = debugMessageText(item.content || item.output || item.text);
      if (["user", "message", "function_call_output"].includes(role) && text) message = text;
    }
  }
  return { system, message };
}

function appendDebugLog(eventType, payload) {
  if (!DEBUG_LOG_ENABLED) {
    return;
  }
  try {
    fs.mkdirSync(DEBUG_LOGS_DIR, { recursive: true });
    if (fs.existsSync(DEBUG_LOG_PATH) && fs.statSync(DEBUG_LOG_PATH).size >= DEBUG_LOG_ROTATE_BYTES) {
      fs.renameSync(DEBUG_LOG_PATH, path.join(DEBUG_LOGS_DIR, "debug.log.1"));
    }
    const normalizedPayload = normalizeDebugPayload(payload);
    const { system, message } = extractDebugSystemAndMessage(normalizedPayload);
    const entry = {
      timestamp: new Date().toISOString(),
      event: eventType,
      system,
      message,
      payload: normalizedPayload,
    };
    fs.appendFileSync(DEBUG_LOG_PATH, `${JSON.stringify(entry)}\n`, "utf8");
  } catch {
    // Debug logging must never affect normal app behavior.
  }
}

const ANSI_ENABLED = Boolean(process.stderr && process.stderr.isTTY && !process.env.NO_COLOR);
const ANSI = {
  reset: "\x1b[0m",
  dim: "\x1b[2m",
  bold: "\x1b[1m",
  red: "\x1b[31m",
  green: "\x1b[32m",
  yellow: "\x1b[33m",
  blue: "\x1b[34m",
  magenta: "\x1b[35m",
  cyan: "\x1b[36m",
  gray: "\x1b[90m",
};

function colorizeConsole(text, ...styles) {
  if (!ANSI_ENABLED || !styles.length) {
    return text;
  }
  return `${styles.join("")}${text}${ANSI.reset}`;
}

function unquoteConsoleValue(value) {
  const text = String(value || "").trim();
  if (!text) {
    return "";
  }
  if ((text.startsWith("\"") && text.endsWith("\"")) || (text.startsWith("'") && text.endsWith("'"))) {
    try {
      if (text.startsWith("\"")) {
        return JSON.parse(text);
      }
    } catch {
      // Fall through to raw trimming below.
    }
  }
  if ((text.startsWith("'") && text.endsWith("'")) || (text.startsWith("\"") && text.endsWith("\""))) {
    return text.slice(1, -1);
  }
  return text;
}

function abbreviateConsoleText(value, limit = 220) {
  const text = unquoteConsoleValue(value).replace(/\\n/g, " ").replace(/\s+/g, " ").trim();
  if (text.length <= limit) {
    return text;
  }
  return `${text.slice(0, limit - 3)}...`;
}

function parseConsoleKeyValueString(text) {
  const result = {};
  const pattern = /(\w+)=('(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|[^\s]+)/g;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    result[match[1]] = match[2];
  }
  return result;
}

function formatBackendStderrForConsole(line) {
  const raw = String(line || "").trim();
  if (!raw) {
    return "";
  }

  const tagged = raw.match(/^\[([^\]]+)\]\s*(.*)$/);
  if (!tagged) {
    return colorizeConsole(`[backend] ${raw}`, ANSI.gray);
  }

  const tag = tagged[1];
  const rest = tagged[2] || "";
  const values = parseConsoleKeyValueString(rest);
  const layer = values.layer ? unquoteConsoleValue(values.layer) : "";
  const role = values.role ? unquoteConsoleValue(values.role) : "";

  const label = (() => {
    if (["USER", "ASSISTANT"].includes(tag)) {
      return colorizeConsole(`[${tag}]`, ANSI.bold, tag === "USER" ? ANSI.green : ANSI.cyan);
    }
    if (tag.startsWith("TOOL")) {
      return colorizeConsole(`[${tag}]`, ANSI.bold, ANSI.yellow);
    }
    if (tag.startsWith("BRAIN IN")) {
      return colorizeConsole(`[${tag}]`, ANSI.bold, ANSI.cyan);
    }
    if (tag.startsWith("BRAIN OUT")) {
      return colorizeConsole(`[${tag}]`, ANSI.bold, ANSI.blue);
    }
    if (tag.startsWith("OLLAMA REQUEST")) {
      return colorizeConsole(`[${tag}]`, ANSI.bold, ANSI.magenta);
    }
    if (tag.startsWith("BRAIN BUDGET")) {
      return colorizeConsole(`[${tag}]`, ANSI.bold, ANSI.gray);
    }
    if (tag.includes("WARNING") || tag.includes("ERROR")) {
      return colorizeConsole(`[${tag}]`, ANSI.bold, ANSI.red);
    }
    return colorizeConsole(`[${tag}]`, ANSI.bold, ANSI.gray);
  })();

  if (tag === "USER" || tag === "ASSISTANT") {
    return `\n${label} ${abbreviateConsoleText(rest, 320)}`;
  }

  if (tag === "OLLAMA REQUEST") {
    return `\n${label} ${colorizeConsole(layer || "?", ANSI.bold)} ${abbreviateConsoleText(rest, 320)}`;
  }

  if (tag === "BRAIN BUDGET") {
    return `${label} ${colorizeConsole(layer || "?", ANSI.bold)} ${abbreviateConsoleText(rest, 320)}`;
  }

  if (tag === "BRAIN OUT" || tag === "BRAIN IN" || tag === "HISTORY") {
    return `${label} ${layer ? colorizeConsole(layer, ANSI.bold) : ""}${role ? `/${role}` : ""}\n  ${colorizeConsole("content:", ANSI.dim)} ${abbreviateConsoleText(values.content || rest, 320)}`;
  }

  if (tag === "BRAIN OUT META") {
    return `${label} ${layer ? colorizeConsole(layer, ANSI.bold) : ""} ${abbreviateConsoleText(rest, 240)}`;
  }

  if (tag.endsWith("TOOL_CALL") || tag === "TOOL CALL") {
    const name = abbreviateConsoleText(values.name || "", 80);
    const argumentsText = abbreviateConsoleText(values.arguments || rest, 260);
    return `${label} ${layer ? colorizeConsole(layer, ANSI.bold) : ""} ${name ? colorizeConsole(name, ANSI.yellow) : ""}\n  ${colorizeConsole("args:", ANSI.dim)} ${argumentsText}`;
  }

  if (tag.endsWith("TOOL_RESPONSE") || tag === "TOOL RESULT") {
    const name = abbreviateConsoleText(values.name || "", 80);
    const responseText = abbreviateConsoleText(values.response || values.content || rest, 320);
    return `${label} ${layer ? colorizeConsole(layer, ANSI.bold) : ""} ${name ? colorizeConsole(name, ANSI.yellow) : ""}\n  ${colorizeConsole("result:", ANSI.dim)} ${responseText}`;
  }

  return `${label} ${abbreviateConsoleText(rest, 320)}`;
}

function getPythonCommand() {
  return process.platform === "win32" ? "python" : "python3";
}

function normalizeKeychainAccountName(account) {
  const value = String(account || "").trim().toLowerCase();
  if (!value) {
    return null;
  }
  if (value === "openai" || value === "openai_api_key") {
    return "openai";
  }
  if (value === "anthropic" || value === "anthropic_api_key") {
    return "anthropic";
  }
  if (value === "gemini" || value === "gemini_api_key") {
    return "gemini";
  }
  if (value === "openrouter" || value === "openrouter_api_key") {
    return "openrouter";
  }
  if (value === "gemma") {
    return "gemma";
  }
  if (value === "ollama") {
    return "ollama";
  }
  if (value === "qwen" || value === "qwen_cloud" || value === "qwen_api_key" || value === "dashscope_api_key") {
    return "qwen";
  }
  if (value === "custom" || value === "custom_api_key") {
    return "custom";
  }
  if (value === "custom_url") {
    return "custom_url";
  }
  return null;
}

function getKeychainGroup(groupName) {
  const group = KEYCHAIN_ACCOUNT_GROUPS[normalizeKeychainAccountName(groupName)];
  if (!group) {
    return null;
  }
  return {
    primary: group.primary,
    accounts: [group.primary, ...group.aliases],
  };
}

function normalizeProviderName(provider) {
  const value = String(provider || "").trim().toLowerCase();
  if (!value) {
    return null;
  }
  if (value === "openai" || value === "openai_api_key") {
    return "openai";
  }
  if (value === "anthropic" || value === "anthropic_api_key") {
    return "anthropic";
  }
  if (value === "gemini" || value === "gemini_api_key") {
    return "gemini";
  }
  if (value === "openrouter" || value === "openrouter_api_key") {
    return "openrouter";
  }
  if (value === "gemma") {
    return "gemma";
  }
  if (value === "qwen") {
    return "qwen";
  }
  if (value === "qwen_cloud" || value === "qwen_api_key" || value === "dashscope_api_key") {
    return "qwen_cloud";
  }
  if (value === "custom" || value === "custom_api_key") {
    return "custom";
  }
  return null;
}

async function readKeychainPassword(service, account) {
  return keytar.getPassword(service, account).catch(() => null);
}

async function deleteKeychainPassword(service, account) {
  await keytar.deletePassword(service, account).catch(() => {});
}

async function writeKeychainGroupValue(groupName, value) {
  const group = getKeychainGroup(groupName);
  if (!group) {
    throw new Error("Unsupported keychain account.");
  }

  const cleanValue = String(value || "").trim();
  const tasks = group.accounts.map((account) => (
    cleanValue
      ? keytar.setPassword(KEYCHAIN_SERVICE, account, cleanValue)
      : deleteKeychainPassword(KEYCHAIN_SERVICE, account)
  ));

  await Promise.all(tasks);
  return cleanValue ? { ok: true } : { ok: true };
}

async function readKeychainGroupValue(groupName) {
  const group = getKeychainGroup(groupName);
  if (!group) {
    throw new Error("Unsupported keychain account.");
  }

  const searchOrder = [
    { service: KEYCHAIN_SERVICE, accounts: group.accounts },
    { service: LEGACY_KEYCHAIN_SERVICE, accounts: group.accounts },
  ];

  for (const { service, accounts } of searchOrder) {
    for (const account of accounts) {
      const value = await readKeychainPassword(service, account);
      if (value != null && String(value).trim()) {
        if (service !== KEYCHAIN_SERVICE || account !== group.primary) {
          await writeKeychainGroupValue(groupName, value);
        }
        return String(value);
      }
    }
  }

  return null;
}

async function deleteKeychainGroupValue(groupName) {
  const group = getKeychainGroup(groupName);
  if (!group) {
    throw new Error("Unsupported keychain account.");
  }

  await Promise.all([
    ...group.accounts.map((account) => deleteKeychainPassword(KEYCHAIN_SERVICE, account)),
    ...group.accounts.map((account) => deleteKeychainPassword(LEGACY_KEYCHAIN_SERVICE, account)),
  ]);
  return { ok: true };
}

function normalizeValidationOptions(options) {
  if (typeof options === "string") {
    const trimmed = options.trim();
    if (!trimmed) {
      return {};
    }
    if (/^https?:\/\//i.test(trimmed)) {
      return { baseUrl: trimmed };
    }
    return { model: trimmed };
  }

  if (!isPlainObject(options)) {
    return {};
  }

  return {
    model: typeof options.model === "string" ? options.model.trim() : "",
    baseUrl: typeof options.baseUrl === "string" ? options.baseUrl.trim() : "",
  };
}

function sanitizeBaseUrl(baseUrl, fallbackBaseUrl) {
  const clean = String(baseUrl || "").trim();
  const resolved = clean || fallbackBaseUrl;
  return String(resolved || "").replace(/\/+$/, "");
}

function buildModelsUrl(baseUrl, fallbackBaseUrl) {
  return new URL("models", `${sanitizeBaseUrl(baseUrl, fallbackBaseUrl)}/`).toString();
}

function readConfiguredCustomBaseUrl() {
  const config = loadConfig();
  return String(config?.providers?.custom?.base_url || config?.brain?.base_url || config?.brain?.api_url || "").trim();
}

function writeConfiguredCustomBaseUrl(value) {
  const cleanValue = String(value || "").trim();
  return saveConfig(null, {
    brain: {
      base_url: cleanValue,
      api_url: cleanValue,
    },
    providers: {
      custom: {
        base_url: cleanValue,
      },
    },
  });
}

function getProviderDisplayLabel(provider) {
  switch (normalizeProviderName(provider)) {
    case "gemma":
      return "Gemma";
    case "qwen":
      return "Qwen";
    case "qwen_cloud":
      return "Qwen DashScope";
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
    case "custom":
      return "custom endpoint";
    default:
      return "Gemma";
  }
}

function getValidationHeaders(provider, key) {
  if (provider === "anthropic") {
    return {
      "anthropic-version": "2023-06-01",
      "x-api-key": key,
      accept: "application/json",
    };
  }
  if (provider === "gemini") {
    const headers = { accept: "application/json" };
    if (key) {
      headers["x-goog-api-key"] = key;
    }
    return headers;
  }

  const headers = {
    accept: "application/json",
  };

  if (key) {
    headers.authorization = `Bearer ${key}`;
  }

  return headers;
}

function normalizeValidationError(provider, error, response) {
  const status = Number(response?.status || 0);
  const text = String(error?.message || error || "").toLowerCase();
  const bodyText = String(response?.bodyText || "").toLowerCase();
  const combined = `${text} ${bodyText}`.trim();

  if (status === 401 || status === 403 || /invalid api key|incorrect api key|unauthorized|forbidden|authentication failed|not authenticated/.test(combined)) {
    return "Invalid API key";
  }

  if (status === 429 || /rate limit|too many requests|quota|resource exhausted/.test(combined)) {
    return "Rate limited - try again later";
  }

  if (status >= 500 || /failed to fetch|fetch failed|timeout|timed out|econn|enotfound|eai_again|socket|network|aborted|aborterror|cannot connect|connection refused|dns/.test(combined)) {
    return "Cannot reach API";
  }

  if (/invalid url|unsupported protocol|bad url/.test(combined)) {
    return "Cannot reach API";
  }

  return "Cannot reach API";
}

async function validateProviderKey(providerName, options) {
  const provider = normalizeProviderName(providerName);
  if (!provider) {
    throw new Error("Unsupported provider.");
  }

  const normalizedOptions = normalizeValidationOptions(options);
  const fallbackBaseUrl = PROVIDER_VALIDATION_DEFAULT_BASE_URLS[provider] || PROVIDER_VALIDATION_DEFAULT_BASE_URLS.custom;
  const configuredCustomBaseUrl = provider === "custom" ? readConfiguredCustomBaseUrl() : "";
  const baseUrl = sanitizeBaseUrl(normalizedOptions.baseUrl || configuredCustomBaseUrl, fallbackBaseUrl);
  const key = provider === "custom"
    ? String(await readKeychainGroupValue(provider) || "").trim()
    : String(await readKeychainGroupValue(provider) || "").trim();

  if (provider !== "custom" && !key) {
    return { valid: false, error: "API key not set" };
  }
  if (provider === "custom" && !baseUrl) {
    return { valid: false, error: "Custom endpoint URL not set" };
  }
  const controller = new AbortController();
  const timeoutMs = 8000;
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(buildModelsUrl(baseUrl, fallbackBaseUrl), {
      method: "GET",
      headers: getValidationHeaders(provider, key),
      signal: controller.signal,
    });

    if (response.ok) {
      return { valid: true, error: null };
    }

    const bodyText = await response.text().catch(() => "");
    const validationError = normalizeValidationError(provider, null, {
      status: response.status,
      bodyText,
    });

    return { valid: false, error: validationError };
  } catch (error) {
    const validationError = normalizeValidationError(provider, error, null);
    return { valid: false, error: validationError };
  } finally {
    clearTimeout(timeoutId);
  }
}

function extractModelIdsFromPayload(provider, payload) {
  const items = Array.isArray(payload?.data)
    ? payload.data
    : Array.isArray(payload?.models)
      ? payload.models
      : [];

  const models = [];
  for (const item of items) {
    if (!item || typeof item !== "object") {
      continue;
    }
    const rawId = provider === "custom"
      ? (item.name || item.id)
      : (item.id || item.name);
    const modelId = String(rawId || "").trim();
    if (modelId) {
      models.push(modelId);
    }
  }
  return models;
}

async function listProviderModels(providerName, options = {}) {
  const provider = normalizeProviderName(providerName);
  if (!provider) {
    throw new Error("Unsupported provider.");
  }

  const normalizedOptions = normalizeValidationOptions(options);
  const fallbackBaseUrl = PROVIDER_VALIDATION_DEFAULT_BASE_URLS[provider] || PROVIDER_VALIDATION_DEFAULT_BASE_URLS.custom;
  const configuredCustomBaseUrl = provider === "custom" ? readConfiguredCustomBaseUrl() : "";
  const baseUrl = sanitizeBaseUrl(normalizedOptions.baseUrl || configuredCustomBaseUrl, fallbackBaseUrl);
  const key = String(await readKeychainGroupValue(provider) || "").trim();
  const curatedModels = getCuratedProviderModelIds(provider);

  if (provider !== "custom" && !key) {
    return curatedModels;
  }
  if (provider === "custom" && !baseUrl) {
    return [];
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch(buildModelsUrl(baseUrl, fallbackBaseUrl), {
      method: "GET",
      headers: getValidationHeaders(provider, key),
      signal: controller.signal,
    });
    if (!response.ok) {
      return curatedModels;
    }
    const payload = await response.json().catch(() => null);
    return mergeProviderModelIds(provider, extractModelIdsFromPayload(provider, payload));
  } catch {
    return curatedModels;
  } finally {
    clearTimeout(timeoutId);
  }
}

function resolvePythonUserSite(pythonCommand) {
  if (pythonUserSiteCache && pythonUserSiteCache.pythonCommand === pythonCommand) {
    return pythonUserSiteCache.userSite;
  }

  let userSite = null;
  try {
    const probe = spawnSync(pythonCommand, ["-m", "site", "--user-site"], {
      cwd: PROJECT_ROOT,
      windowsHide: true,
      encoding: "utf8",
    });

    if (probe.status === 0) {
      const userSite = String(probe.stdout || "")
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean)
        .pop();
    } else if ((probe.stderr || "").trim()) {
      console.warn("Failed to resolve Python user site:", String(probe.stderr || "").trim());
    }
  } catch (error) {
    console.warn("Failed to build Python subprocess env:", error.message);
  }

  pythonUserSiteCache = { pythonCommand, userSite };
  return userSite;
}

function buildPythonSubprocessEnv() {
  const pythonCommand = getPythonCommand();
  const env = runtimePaths.buildBackendEnvironment();
  env.PYTHONUTF8 = "1";
  env.PYTHONIOENCODING = "utf-8";
  env.OPEN_COMPANION_NODE_PATH = process.execPath;
  env.OPEN_COMPANION_NODE_MODE = "electron";

  const userSite = resolvePythonUserSite(pythonCommand);
  if (userSite) {
    const existing = String(env.PYTHONPATH || "")
      .split(path.delimiter)
      .map((entry) => entry.trim())
      .filter(Boolean);
    env.PYTHONPATH = [userSite, ...existing].filter((entry, index, entries) => entries.indexOf(entry) === index).join(path.delimiter);
  }

  return { pythonCommand, env };
}

function getBackendExecutablePath() {
  return runtimePaths.BACKEND_BUNDLED_ENTRY;
}

function hasBundledBackend() {
  return fs.existsSync(getBackendExecutablePath());
}

function getBackendLaunchSpec(extraArgs = []) {
  const cleanArgs = Array.isArray(extraArgs) ? extraArgs.filter((entry) => typeof entry === "string") : [];
  if (runtimePaths.IS_PACKAGED) {
    return {
      command: getBackendExecutablePath(),
      args: cleanArgs,
      env: runtimePaths.buildBackendEnvironment(),
      cwd: runtimePaths.READONLY_PROJECT_ROOT,
    };
  }

  const { pythonCommand, env } = buildPythonSubprocessEnv();
  return {
    command: pythonCommand,
    args: [BACKEND_ENTRY, ...cleanArgs],
    env,
    cwd: PROJECT_ROOT,
  };
}

function spawnBackendCommand(extraArgs = [], options = {}) {
  const spec = getBackendLaunchSpec(extraArgs);
  return spawn(spec.command, spec.args, {
    cwd: spec.cwd,
    env: spec.env,
    windowsHide: true,
    ...options,
  });
}

function toRepoRelativePath(filePath) {
  const resolved = path.resolve(String(filePath || ""));
  const rootPrefix = `${PROJECT_ROOT}${path.sep}`;
  if (resolved.startsWith(rootPrefix)) {
    return path.relative(PROJECT_ROOT, resolved).replace(/\\/g, "/");
  }
  return resolved;
}

function getCompanionNameFromConfig(config) {
  const companion = isPlainObject(config?.companion) ? config.companion : {};
  const soul = isPlainObject(companion.soul) ? companion.soul : {};
  return String(soul.name || companion.name || "Companion").trim() || "Companion";
}

function getBuiltinRegistryPath() {
  const sourcePath = runtimePaths.BUILTIN_REGISTRY_SOURCE_PATH;
  if (!TEST_MODE) {
    return sourcePath;
  }

  const profilePath = path.join(PROFILE_ROOT, "app", "backend", "tools", "registry.json");
  if (!fs.existsSync(profilePath)) {
    fs.mkdirSync(path.dirname(profilePath), { recursive: true });
    fs.copyFileSync(sourcePath, profilePath);
  }
  return profilePath;
}

function readTestOllamaState() {
  if (!TEST_MODE || !fs.existsSync(TEST_OLLAMA_STATE_PATH)) {
    return null;
  }
  try {
    return JSON.parse(fs.readFileSync(TEST_OLLAMA_STATE_PATH, "utf8"));
  } catch (error) {
    console.warn("Failed to read test Ollama state:", error.message);
    return null;
  }
}

function writeTestOllamaState(state) {
  if (!TEST_MODE) {
    return;
  }
  fs.mkdirSync(path.dirname(TEST_OLLAMA_STATE_PATH), { recursive: true });
  fs.writeFileSync(TEST_OLLAMA_STATE_PATH, `${JSON.stringify(state, null, 2)}\n`, "utf8");
}

function delayWithAbort(ms, signal) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, Math.max(0, Number(ms) || 0));

    const onAbort = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      const error = new Error("Aborted");
      error.name = "AbortError";
      reject(error);
    };

    if (signal?.aborted) {
      onAbort();
      return;
    }

    signal?.addEventListener("abort", onAbort);
  });
}

async function isOllamaInstalled() {
  return await new Promise((resolve) => {
    const child = spawn(runtimePaths.getOllamaExecutablePath(), ["list"], {
      shell: false,
      windowsHide: true,
      env: {
        ...process.env,
        OLLAMA_MODELS: runtimePaths.OLLAMA_MODELS_DIR,
      },
      stdio: "ignore",
    });
    child.on("error", () => resolve(false));
    child.on("exit", () => resolve(true));
  });
}

const testDiagnostics = {
  sentMessages: [],
  emittedEvents: [],
};

function recordTestDiagnostic(bucket, type, details = {}) {
  if (!TEST_MODE) {
    return;
  }

  const target = testDiagnostics[bucket];
  if (!Array.isArray(target)) {
    return;
  }

  target.push({
    type: String(type || "").trim() || "unknown",
    ts: Date.now(),
    ...details,
  });
  if (target.length > TEST_EVENT_HISTORY_LIMIT) {
    target.splice(0, target.length - TEST_EVENT_HISTORY_LIMIT);
  }
}

function refreshBackendFromConfig(updated) {
  const mergedRuntimeConfig = loadConfig();
  backend.latestState = {
    ...backend.latestState,
    companionName: getCompanionNameFromConfig(mergedRuntimeConfig),
    config: mergedRuntimeConfig,
  };

  try {
    backend.send({ type: "config_reload" });
  } catch (error) {
    console.warn("Failed to send backend config_reload:", error.message);
  }

}

function resolveProjectPath(filePath) {
  const raw = String(filePath || "").trim();
  if (!raw) {
    return "";
  }

  if (path.isAbsolute(raw)) {
    return path.resolve(raw);
  }

  const resolveWithinRoot = (rootPath) => {
    const resolved = path.resolve(rootPath, raw);
    const rootPrefix = `${rootPath}${path.sep}`;
    if (resolved !== rootPath && !resolved.startsWith(rootPrefix)) {
      throw new Error("Relative paths must stay inside the project.");
    }
    return resolved;
  };

  const profileResolved = resolveWithinRoot(PROFILE_ROOT);
  if (fs.existsSync(profileResolved)) {
    return profileResolved;
  }

  return resolveWithinRoot(PROJECT_ROOT);
}

function isValidMemoryFileName(fileName) {
  return (
    typeof fileName === "string" &&
    fileName === "memory.md" &&
    !fileName.includes("/") &&
    !fileName.includes("\\")
  );
}

function normalizeMemoryFileContent(content) {
  const normalized = String(content || "")
    .replace(/\r\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .replace(/(?:[ \t]*\n)+$/g, "");

  return normalized ? `${normalized}\n` : "\n";
}

function normalizeMarkdownFileContent(content) {
  const normalized = String(content || "")
    .replace(/\r\n/g, "\n")
    .replace(/[ \t]+\n/g, "\n")
    .trim();

  return normalized ? `${normalized}\n` : "";
}

function resetLongTermMemory() {
  fs.mkdirSync(MEMORY_DIR, { recursive: true });

  for (const entry of fs.readdirSync(MEMORY_DIR, { withFileTypes: true })) {
    if (!entry.isFile()) {
      continue;
    }
    const fileName = String(entry.name || "");
    if (MEMORY_RESET_FILE_NAMES.has(fileName) || isValidMemoryFileName(fileName)) {
      fs.unlinkSync(path.join(MEMORY_DIR, fileName));
    }
  }
  for (const [fileName, content] of Object.entries(DEFAULT_MEMORY_TOPICS)) {
    fs.writeFileSync(path.join(MEMORY_DIR, fileName), content, "utf8");
  }
}

function memoryTimestamp() {
  return new Date().toISOString();
}

function normalizeImportedMemorySection(content) {
  const blocks = String(content || "")
    .replace(/\r\n/g, "\n")
    .split(/\n\s*\n/)
    .map((block) => block.split("\n").map((line) => line.trim()).filter(Boolean).join(" "))
    .filter(Boolean);

  if (!blocks.length) {
    return "";
  }

  return blocks
    .map((block) => {
      const text = block.startsWith("- ") ? block.slice(2).trim() : block;
      return `- [${memoryTimestamp()}] ${text}`;
    })
    .join("\n");
}

function readTestDialogQueue() {
  const raw = String(process.env.OPEN_COMPANION_TEST_DIALOG_QUEUE || "").trim();
  if (!TEST_MODE || !raw) {
    return [];
  }

  try {
    if ((raw.startsWith("[") && raw.endsWith("]")) || (raw.startsWith("{") && raw.endsWith("}"))) {
      const parsedInline = JSON.parse(raw);
      return Array.isArray(parsedInline) ? parsedInline : [parsedInline];
    }

    const queuePath = path.resolve(raw);
    if (!fs.existsSync(queuePath)) {
      return [];
    }
    const parsedFile = JSON.parse(fs.readFileSync(queuePath, "utf8"));
    return Array.isArray(parsedFile) ? parsedFile : [parsedFile];
  } catch (error) {
    console.warn("Failed to parse OPEN_COMPANION_TEST_DIALOG_QUEUE:", error.message);
    return [];
  }
}

function writeTestDialogQueue(queue) {
  const raw = String(process.env.OPEN_COMPANION_TEST_DIALOG_QUEUE || "").trim();
  if (!TEST_MODE || !raw || raw.startsWith("[") || raw.startsWith("{")) {
    return;
  }

  try {
    const queuePath = path.resolve(raw);
    fs.mkdirSync(path.dirname(queuePath), { recursive: true });
    fs.writeFileSync(queuePath, `${JSON.stringify(queue, null, 2)}\n`, "utf8");
  } catch (error) {
    console.warn("Failed to update test dialog queue:", error.message);
  }
}

function consumeTestDialogResult(defaultFilters) {
  const queue = readTestDialogQueue();
  if (!queue.length) {
    return { canceled: true, filePaths: [], filters: defaultFilters };
  }

  const [next, ...rest] = queue;
  writeTestDialogQueue(rest);

  if (typeof next === "string") {
    return { canceled: false, filePaths: [path.resolve(next)], filters: defaultFilters };
  }
  if (next && typeof next === "object") {
    const filePaths = Array.isArray(next.filePaths)
      ? next.filePaths.map((filePath) => path.resolve(String(filePath || ""))).filter(Boolean)
      : [];
    return {
      canceled: Boolean(next.canceled) || !filePaths.length,
      filePaths,
      filters: defaultFilters,
    };
  }
  return { canceled: true, filePaths: [], filters: defaultFilters };
}

function parseOnboardingImport(text) {
  const sections = {
    memory: [],
    soul_companion: [],
  };

  let currentSection = null;
  for (const rawLine of String(text || "").replace(/\r\n/g, "\n").split("\n")) {
    const headingMatch = rawLine.match(/^\s*(?:##\s*)?([A-Za-z_][A-Za-z0-9_-]*)\s*:?\s*$/);
    if (headingMatch) {
      const normalizedHeading = headingMatch[1].trim().toLowerCase().replace(/-/g, "_");
      if (normalizedHeading === "memory_user" || normalizedHeading === "memory_relationship") {
        currentSection = "memory";
      } else {
        currentSection = IMPORT_SECTION_ORDER.includes(normalizedHeading) ? normalizedHeading : null;
      }
      continue;
    }

    if (currentSection) {
      sections[currentSection].push(rawLine);
    }
  }

  return Object.fromEntries(
    IMPORT_SECTION_ORDER.map((sectionName) => [
      sectionName,
      sections[sectionName].join("\n").trim(),
    ])
  );
}

function describeCommunicationStyle(selection) {
  return {
    warm: "Casual and natural.",
    direct: "Clear, concise, and practical.",
    playful: "Light, energetic, and conversational.",
    serious: "Measured, calm, and precise.",
    custom: "Casual and natural.",
  }[selection] || "Casual and natural.";
}

function readOnboardingValue(data, ...keys) {
  for (const key of keys) {
    if (!key || !data || !Object.prototype.hasOwnProperty.call(data, key)) {
      continue;
    }
    const value = data[key];
    if (value !== undefined && value !== null) {
      return value;
    }
  }
  return "";
}

function resolveOnboardingPronouns(data) {
  const selected = String(readOnboardingValue(data, "pronouns", "pronounSelection") || "she/her").trim().toLowerCase();
  if (selected !== "custom") {
    return selected || "she/her";
  }

  const subject = String(readOnboardingValue(data, "pronoun_subject", "pronounSubject")).trim().toLowerCase();
  const object = String(readOnboardingValue(data, "pronoun_object", "pronounObject")).trim().toLowerCase();
  const possessive = String(readOnboardingValue(data, "pronoun_possessive", "pronounPossessive")).trim().toLowerCase();
  if (subject && object && possessive) {
    return `${subject}/${object}/${possessive}`;
  }
  return "they/them/their";
}

function createOnboardingConfigPatch(data) {
  const companionName = String(readOnboardingValue(data, "companion_name", "companionName", "name")).trim() || DEFAULT_CONFIG.companion.name;
  const userName = String(readOnboardingValue(data, "user_name", "userName")).trim() || DEFAULT_CONFIG.companion.user_name;
  const soulIdentity = String(readOnboardingValue(data, "soul_identity", "soulIdentity", "identity")).trim()
    || DEFAULT_CONFIG.companion.soul.identity;
  const soulBackstory = String(readOnboardingValue(data, "soul_backstory", "soulBackstory", "backstory")).trim();
  const soulRelationship = String(readOnboardingValue(data, "soul_relationship", "soulRelationship", "relationship")).trim();
  const soulUserContext = String(readOnboardingValue(data, "soul_user_context", "soulUserContext", "user_context")).trim();
  const voice = String(readOnboardingValue(data, "voice", "voice_id", "voiceId")).trim() || DEFAULT_CONFIG.voice.kokoro_voice;
  const importedSoulCompanion = normalizeMarkdownFileContent(readOnboardingValue(data, "imported_soul_companion", "importedSoulCompanion"));
  const pronouns = resolveOnboardingPronouns(data);

  const patch = {
    companion: {
      name: companionName,
      user_name: userName,
      pronouns,
      soul: {
        name: companionName,
        pronouns,
        identity: soulIdentity,
        backstory: soulBackstory,
        relationship: soulRelationship,
        user_name: userName,
        user_context: soulUserContext,
      },
    },
    voice: {
      kokoro_voice: voice,
    },
  };

  if (importedSoulCompanion) {
    patch.companion.system_prompt_override = importedSoulCompanion;
  }

  const brainProvider = String(readOnboardingValue(data, "brain_provider", "brainProvider") || "").trim();
  const brainProviderMode = String(readOnboardingValue(data, "brain_provider_mode", "brainProviderMode") || "").trim().toLowerCase();
  const brainRemoteProvider = String(readOnboardingValue(data, "brain_remote_provider", "brainRemoteProvider") || "").trim().toLowerCase();
  const brainModel = String(readOnboardingValue(data, "brain_model", "brainModel") || "").trim();
  const memoryMode = String(readOnboardingValue(data, "memory_mode", "memoryMode") || "").trim().toLowerCase();
  const brainBaseUrl = String(
    readOnboardingValue(data, "brain_base_url", "brainBaseUrl", "brain_custom_url", "brainCustomUrl") || ""
  ).trim();
  let resolvedBrainProvider = brainProvider;
  if (brainProviderMode === "local") {
    resolvedBrainProvider = "gemma";
  } else if (brainProviderMode === "custom") {
    resolvedBrainProvider = "custom";
  } else if (brainProviderMode === "cloud" && brainRemoteProvider) {
    resolvedBrainProvider = brainRemoteProvider;
  }
  if (resolvedBrainProvider) {
    patch.brain = { provider: resolvedBrainProvider };
    patch.brain.model = brainModel || "";
    if (resolvedBrainProvider === "custom" && brainBaseUrl) {
      patch.brain.base_url = brainBaseUrl;
      patch.brain.api_url = brainBaseUrl;
    }
  }

  patch.memory = patch.memory || {};
  if (isOllamaBackedProvider(resolvedBrainProvider)) {
    patch.memory.enabled = true;
    patch.memory.embedding_enabled = true;
    patch.memory.extraction_source = "local";
  } else if (memoryMode === "off") {
    patch.memory.enabled = false;
    patch.memory.embedding_enabled = false;
    patch.memory.extraction_source = "brain";
  } else if (memoryMode === "api" || memoryMode === "provider") {
    patch.memory.enabled = true;
    patch.memory.embedding_enabled = false;
    patch.memory.extraction_source = "brain";
  } else {
    patch.memory.enabled = true;
    patch.memory.embedding_enabled = true;
    patch.memory.extraction_source = "local";
  }
  patch.memory.extraction_mode = patch.memory.extraction_source === "local" ? "local" : "provider";

  return patch;
}

function buildRuntimeConfigFromOnboardingPayload(payload) {
  if (!payload || typeof payload !== "object") {
    return loadConfig();
  }
  return mergeConfig(loadConfig(), createOnboardingConfigPatch(payload));
}

async function generateSoulFilesFromConfig(config, { preserveCompanion = false } = {}) {
  const args = ["--generate-soul-json", JSON.stringify(config)];
  if (preserveCompanion) {
    args.push("--preserve-companion");
  }
  await runBackendUtilityCommand(args);
}

function writeOnboardingImportFiles(sections) {
  const memorySections = [
    sections?.memory,
    sections?.memory_user,
    sections?.memory_relationship,
  ]
    .map((value) => String(value || "").trim())
    .filter(Boolean);

  const parsed = {
    memory: memorySections.join("\n\n").trim(),
    soul_companion: String(sections?.soul_companion || "").trim(),
  };

  fs.mkdirSync(MEMORY_DIR, { recursive: true });
  fs.mkdirSync(SOUL_ACTIVE_DIR, { recursive: true });

  if (parsed.memory) {
    fs.writeFileSync(
      path.join(MEMORY_DIR, "memory.md"),
      normalizeMemoryFileContent(`# Memory\n\n${normalizeImportedMemorySection(parsed.memory)}`),
      "utf8"
    );
  }
  if (parsed.soul_companion) {
    fs.writeFileSync(path.join(SOUL_ACTIVE_DIR, "soul_companion.md"), normalizeMarkdownFileContent(parsed.soul_companion), "utf8");
  }

  return parsed;
}

function getDefaultToolOverrides() {
  return {
    companion: {},
    assistant: {},
  };
}

function parseBuiltinToolNames() {
  try {
    const registryPath = getBuiltinRegistryPath();
    const parsed = JSON.parse(fs.readFileSync(registryPath, "utf8"));
    const names = (parsed.tools || [])
      .map((tool) => String(tool?.name || "").trim())
      .filter(Boolean);
    return [...new Set(names)].sort((left, right) => left.localeCompare(right));
  } catch (error) {
    console.warn("Failed to parse builtin tool names:", error.message);
    return [];
  }
}

function parseBuiltinTools() {
  try {
    const registryPath = getBuiltinRegistryPath();
    const parsed = JSON.parse(fs.readFileSync(registryPath, "utf8"));
    const tools = Array.isArray(parsed.tools) ? parsed.tools : [];

    return tools
      .map((tool) => ({
        name: String(tool?.name || "").trim(),
        description: String(tool?.description || "").trim(),
        layer: normalizeCustomToolLayers(tool?.layer),
      }))
      .filter((tool) => tool.name)
      .sort((left, right) => left.name.localeCompare(right.name));
  } catch (error) {
    console.warn("Failed to parse builtin tools:", error.message);
    return [];
  }
}

function normalizeToolOverrides(overrides, builtinNames = null) {
  const normalized = getDefaultToolOverrides();
  if (!isPlainObject(overrides)) {
    return normalized;
  }

  for (const layer of Object.keys(normalized)) {
    const layerOverrides = overrides[layer];
    if (!isPlainObject(layerOverrides)) {
      continue;
    }

    for (const [toolName, enabled] of Object.entries(layerOverrides)) {
      const name = String(toolName || "").trim();
      if (!name || (builtinNames && !builtinNames.has(name))) {
        continue;
      }
      normalized[layer][name] = Boolean(enabled);
    }
  }

  return normalized;
}

function normalizeCustomToolLayers(layers) {
  if (!Array.isArray(layers)) {
    return [];
  }

  const allowed = new Set(["companion", "assistant"]);
  const normalized = [];
  for (const rawLayer of layers) {
    let layer = String(rawLayer || "").trim();
    if (["assistant_low", "assistant_high", "pc_doctor", "pcdoctor"].includes(layer)) {
      layer = "assistant";
    }
    if (!allowed.has(layer) || normalized.includes(layer)) {
      continue;
    }
    normalized.push(layer);
  }
  return normalized;
}

function normalizeCustomToolCommand(command) {
  let raw = String(command || "").trim().replace(/\\/g, "/");
  if (!raw) {
    throw new Error("Tool command is required.");
  }

  if (path.posix.isAbsolute(raw) || path.win32.isAbsolute(raw)) {
    throw new Error("Tool command must be a repo-relative path.");
  }

  if (raw.startsWith("./")) {
    raw = raw.slice(2);
  }
  if (raw === ".." || raw.startsWith("../") || raw.includes("/../") || raw.endsWith("/..")) {
    throw new Error("Tool command cannot traverse directories.");
  }

  const parts = raw.split("/").filter(Boolean);
  if (parts.length < 3) {
    throw new Error("Tool command must live under app/backend/ or app/scripts/.");
  }
  if (parts[0] !== "app" || !new Set(["backend", "scripts"]).has(parts[1])) {
    throw new Error("Tool command must live under app/backend/ or app/scripts/.");
  }

  const ext = path.posix.extname(parts[parts.length - 1]).toLowerCase();
  if (!new Set([".py", ".js"]).has(ext)) {
    throw new Error("Tool command must end with .py or .js.");
  }

  const resolved = path.resolve(PROJECT_ROOT, raw.replace(/\//g, path.sep));
  const rootPrefix = `${PROJECT_ROOT}${path.sep}`;
  if (resolved !== PROJECT_ROOT && !resolved.startsWith(rootPrefix)) {
    throw new Error("Tool command must stay inside the project root.");
  }

  return parts.join("/");
}

function normalizeCustomToolView(tool) {
  if (!isPlainObject(tool)) {
    return {
      name: "",
      description: "",
      command: "",
      layers: [],
    };
  }

  return {
    name: String(tool.name || "").trim(),
    description: String(tool.description || "").trim(),
    command: String(tool.command || "").trim().replace(/\\/g, "/"),
    layers: normalizeCustomToolLayers(tool.layers),
  };
}

function validateCustomToolRecord(tool, builtinNames, existingNames = new Set()) {
  if (!isPlainObject(tool)) {
    throw new Error("Tool must be an object.");
  }

  const name = String(tool.name || "").trim();
  if (!name || !/^[A-Za-z0-9_]+$/.test(name)) {
    throw new Error("Tool name must use only letters, numbers, and underscores.");
  }
  if (builtinNames.has(name) || existingNames.has(name)) {
    throw new Error(`Tool name already exists: ${name}`);
  }

  const description = String(tool.description || "").trim();
  const command = normalizeCustomToolCommand(tool.command);
  const layers = normalizeCustomToolLayers(tool.layers);
  if (!layers.length) {
    throw new Error("Tool must be available in at least one layer.");
  }

  return {
    name,
    description,
    command,
    layers,
  };
}

function normalizeCustomToolList(custom, builtinNames) {
  if (!Array.isArray(custom)) {
    return [];
  }

  const normalized = [];
  const existingNames = new Set();
  for (const tool of custom) {
    const record = validateCustomToolRecord(tool, builtinNames, existingNames);
    existingNames.add(record.name);
    normalized.push(record);
  }
  return normalized;
}

function getToolsConfig(currentConfig, builtinNames = null) {
  const toolsConfig = isPlainObject(currentConfig.tools) ? currentConfig.tools : {};
  const custom = Array.isArray(toolsConfig.custom) ? toolsConfig.custom : [];
  const overrides = normalizeToolOverrides(toolsConfig.overrides, builtinNames);
  return { custom, overrides };
}

function clampBoundsToWorkArea(bounds) {
  const display = screen.getDisplayMatching(bounds);
  const area = display.workArea;
  const margin = 20;
  const maxX = area.x + area.width - bounds.width - margin;
  const maxY = area.y + area.height - bounds.height - margin;

  return {
    ...bounds,
    x: Math.min(Math.max(bounds.x, area.x + margin), Math.max(area.x + margin, maxX)),
    y: Math.min(Math.max(bounds.y, area.y + margin), Math.max(area.y + margin, maxY)),
  };
}

function isBoundsFullyVisible(bounds) {
  const display = screen.getDisplayMatching(bounds);
  const area = display.workArea;

  return (
    bounds.x >= area.x &&
    bounds.y >= area.y &&
    bounds.x + bounds.width <= area.x + area.width &&
    bounds.y + bounds.height <= area.y + area.height
  );
}

function clampBoundsToDisplay(bounds, display) {
  const area = display.workArea;
  const margin = 20;
  const maxX = area.x + area.width - bounds.width - margin;
  const maxY = area.y + area.height - bounds.height - margin;

  return {
    ...bounds,
    x: Math.min(Math.max(bounds.x, area.x + margin), Math.max(area.x + margin, maxX)),
    y: Math.min(Math.max(bounds.y, area.y + margin), Math.max(area.y + margin, maxY)),
  };
}

function normalizePosition(position) {
  const key = String(position || "").trim().toLowerCase().replace(/[\s-]+/g, "_");
  const aliases = {
    left: "center_left",
    right: "center_right",
    top: "top_center",
    up: "top_center",
    bottom: "bottom_center",
    down: "bottom_center",
    middle: "center",
    upper_left: "top_left",
    upper_right: "top_right",
    lower_left: "bottom_left",
    lower_right: "bottom_right",
    full_screen: "fullscreen",
    maximize: "fullscreen",
    maximized: "fullscreen",
    maximised: "fullscreen",
  };
  return aliases[key] || key || "bottom_right";
}

function resolveTargetDisplay(bounds, displaySelector) {
  const displays = screen.getAllDisplays().slice().sort((a, b) => {
    if (a.bounds.x !== b.bounds.x) {
      return a.bounds.x - b.bounds.x;
    }
    return a.bounds.y - b.bounds.y;
  });

  const currentDisplay = screen.getDisplayMatching(bounds);
  const primaryDisplay = screen.getPrimaryDisplay();
  const secondaryDisplay = displays.find((display) => display.id !== primaryDisplay.id) || primaryDisplay;
  const key = String(displaySelector || "current").trim().toLowerCase();
  const numericIndex = Number.parseInt(key, 10);

  if (Number.isFinite(numericIndex) && numericIndex >= 1 && numericIndex <= displays.length) {
    return displays[numericIndex - 1];
  }

  if (key === "primary") {
    return primaryDisplay;
  }
  if (key === "secondary" || key === "second") {
    return secondaryDisplay;
  }
  if (key === "left" || key === "leftmost") {
    return displays[0] || currentDisplay;
  }
  if (key === "right" || key === "rightmost") {
    return displays[displays.length - 1] || currentDisplay;
  }

  return currentDisplay;
}

function resolveWindowPosition(bounds, position, displaySelector) {
  const display = resolveTargetDisplay(bounds, displaySelector);
  const area = display.workArea;
  const margin = 24;
  const centerX = area.x + Math.round((area.width - bounds.width) / 2);
  const centerY = area.y + Math.round((area.height - bounds.height) / 2);
  const normalizedPosition = normalizePosition(position);

  const anchors = {
    top_left: {
      x: area.x + margin,
      y: area.y + margin,
    },
    top_right: {
      x: area.x + area.width - bounds.width - margin,
      y: area.y + margin,
    },
    bottom_left: {
      x: area.x + margin,
      y: area.y + area.height - bounds.height - margin,
    },
    bottom_right: {
      x: area.x + area.width - bounds.width - margin,
      y: area.y + area.height - bounds.height - margin,
    },
    center_left: {
      x: area.x + margin,
      y: centerY,
    },
    center_right: {
      x: area.x + area.width - bounds.width - margin,
      y: centerY,
    },
    center: {
      x: centerX,
      y: centerY,
    },
    top_center: {
      x: centerX,
      y: area.y + margin,
    },
    bottom_center: {
      x: centerX,
      y: area.y + area.height - bounds.height - margin,
    },
  };

  if (normalizedPosition === "fullscreen") {
    return {
      x: display.bounds.x,
      y: display.bounds.y,
      width: display.bounds.width,
      height: display.bounds.height,
    };
  }

  const next = anchors[normalizedPosition] || anchors.bottom_right;
  return clampBoundsToWorkArea({
    ...bounds,
    x: next.x,
    y: next.y,
  });
}

function moveWindow(window, movement = {}) {
  if (!window || window.isDestroyed()) {
    throw new Error("Main window is not available.");
  }

  const bounds = window.getBounds();
  const display = resolveTargetDisplay(bounds, movement.display);

  let nextBounds = null;

  const hasAbsoluteTarget = Number.isFinite(movement.x) || Number.isFinite(movement.y);
  if (hasAbsoluteTarget) {
    nextBounds = clampBoundsToDisplay({
      ...bounds,
      x: Number.isFinite(movement.x) ? Math.round(movement.x) : bounds.x,
      y: Number.isFinite(movement.y) ? Math.round(movement.y) : bounds.y,
    }, display);
  } else {
    const hasRelativeTarget = Number.isFinite(movement.dx) || Number.isFinite(movement.dy);
    if (hasRelativeTarget) {
      nextBounds = clampBoundsToDisplay({
        ...bounds,
        x: bounds.x + Math.round(Number.isFinite(movement.dx) ? movement.dx : 0),
        y: bounds.y + Math.round(Number.isFinite(movement.dy) ? movement.dy : 0),
      }, display);
    } else {
      nextBounds = resolveWindowPosition(bounds, movement.position, movement.display);
    }
  }

  window.setBounds(nextBounds, true);
  return nextBounds;
}

function ensureWindowVisible(window) {
  if (!window || window.isDestroyed()) {
    return;
  }

  const currentBounds = window.getBounds();
  if (isBoundsFullyVisible(currentBounds)) {
    return;
  }

  const safeBounds = resolveWindowPosition(currentBounds, "bottom_right", "current");
  window.setBounds(safeBounds, false);
}

function scheduleVisibilityCheck(window) {
  if (!window || window.isDestroyed()) {
    return;
  }

  setTimeout(() => {
    ensureWindowVisible(window);
  }, 80);
}

function setWindowApprovalElevation(window, active) {
  if (!window || window.isDestroyed()) {
    return;
  }

  const config = loadConfig();
  const keepAlwaysOnTop = (config.ui || {}).always_on_top !== false;

  try {
    if (active) {
      if (window.isMinimized()) {
        window.restore();
      }
      if (!window.isVisible()) {
        window.show();
      }
      ensureWindowVisible(window);
      window.setAlwaysOnTop(true, "screen-saver");
      if (typeof window.moveTop === "function") {
        window.moveTop();
      }
      window.focus();
      window.flashFrame(true);
      return;
    }

    window.flashFrame(false);
    window.setAlwaysOnTop(keepAlwaysOnTop, "normal");
    if (keepAlwaysOnTop && typeof window.moveTop === "function") {
      window.moveTop();
    }
  } catch (error) {
    logMain("WARN", "Could not update window approval elevation.", {
      active: Boolean(active),
      message: error instanceof Error ? error.message : String(error || "unknown"),
    });
  }
}

class BackendBridge {
  constructor() {
    this.child = null;
    this.buffer = "";
    this.stderrBuffer = "";
    this.ready = false;
    this.pendingConfirmation = false;
    this.isShuttingDown = false;
    this.recentStderrLines = [];
    this.latestState = {
      companionName: getCompanionNameFromConfig(loadConfig()),
      state: "starting",
      lastError: "",
      config: loadConfig(),
      voiceCapabilities: {},
    };
    this.window = null;
    this.pendingRequests = new Map();
    this.readyWaiters = [];
  }

  isRunning() {
    return Boolean(this.child && !this.child.killed);
  }

  attachWindow(window) {
    this.window = window;
    logMain("INFO", "Backend bridge attached to window.", {
      hasWindow: Boolean(window && !window.isDestroyed()),
      backendRunning: this.isRunning(),
      backendReady: this.ready,
      latestState: this.latestState.state,
    });
  }

  recordStderr(message) {
    const text = String(message || "").trim();
    if (!text) {
      return;
    }

    this.recentStderrLines.push(text);
    if (this.recentStderrLines.length > 20) {
      this.recentStderrLines = this.recentStderrLines.slice(-20);
    }

    console.warn(formatBackendStderrForConsole(text));
    logMain("WARN", "Backend stderr.", { line: text });
  }

  emit(payload) {
    let outboundPayload = payload;
    appendDebugLog("IPC_RECV", payload);
    logMain("INFO", "Backend payload received.", {
      type: payload?.type || "",
      state: payload?.state || "",
      hasWindow: Boolean(this.window && !this.window.isDestroyed()),
    });
    recordTestDiagnostic("emittedEvents", payload?.type, {
      state: payload?.state || "",
    });

    if (payload.type === "ready" || payload.type === "config_reloaded") {
      if (payload.type === "ready") {
        this.ready = true;
      }
      this.latestState = {
        ...this.latestState,
        companionName: payload.companion_name || this.latestState.companionName,
        state: payload.state || "idle",
        config: mergeConfig(this.latestState.config || {}, payload.config || {}),
        voiceCapabilities: payload.voice_capabilities || {},
        lastError: "",
      };
      outboundPayload = {
        ...payload,
        config: this.latestState.config,
      };
      if (payload.type === "ready") {
        this.resolveReadyWaiters();
      }
    } else if (payload.type === "tool_confirmation_requested") {
      this.pendingConfirmation = true;
      this.latestState = {
        ...this.latestState,
        state: payload.state || "awaiting_approval",
      };
      if (this.window && !this.window.isDestroyed()) {
        setWindowApprovalElevation(this.window, true);
      }
    } else if (payload.type === "assistant_message") {
      this.pendingConfirmation = false;
      this.latestState = {
        ...this.latestState,
        state: payload.state || "idle",
        lastError: "",
      };
      if (this.window && !this.window.isDestroyed()) {
        setWindowApprovalElevation(this.window, false);
      }
    } else if (payload.type === "busy" || payload.type === "idle") {
      this.latestState = {
        ...this.latestState,
        state: payload.state || this.latestState.state,
      };
      if (payload.type === "idle" && this.window && !this.window.isDestroyed() && !this.pendingConfirmation) {
        setWindowApprovalElevation(this.window, false);
      }
    } else if (payload.type === "error") {
      this.latestState = {
        ...this.latestState,
        state: payload.state || "error",
        lastError: payload.message || "Unknown backend error",
      };
      if (this.window && !this.window.isDestroyed()) {
        setWindowApprovalElevation(this.window, false);
      }
    }

    if (payload.type === "pull_progress") {
      const onboardingWin = getOnboardingWindow();
      if (onboardingWin) {
        onboardingWin.webContents.send("onboarding:pull-progress", outboundPayload);
      }
    }

    this.resolvePendingRequest(outboundPayload);

    if (this.window && !this.window.isDestroyed()) {
      this.window.webContents.send("backend:event", outboundPayload);
      logMain("INFO", "Backend payload forwarded to renderer.", {
        type: outboundPayload?.type || "",
        state: outboundPayload?.state || "",
      });
    }

  }

  start() {
    if (this.isRunning()) {
      logMain("INFO", "Backend start skipped because process is already running.", {
        ready: this.ready,
        state: this.latestState.state,
      });
      return;
    }
    this.isShuttingDown = false;
    this.ready = false;
    this.pendingConfirmation = false;
    this.buffer = "";
    this.stderrBuffer = "";
    this.recentStderrLines = [];
    const launchSpec = getBackendLaunchSpec(["--json"]);
    logMain("INFO", "Starting backend process.", {
      command: launchSpec.command,
      args: launchSpec.args,
      cwd: launchSpec.cwd,
      packaged: runtimePaths.IS_PACKAGED,
    });
    this.child = spawnBackendCommand(["--json"], {
      stdio: ["pipe", "pipe", "pipe"],
    });

    this.child.on("error", (error) => {
      logMain("ERROR", "Backend process spawn failed.", error);
      this.emit({
        type: "error",
        message: `Backend process failed to start: ${error?.message || "Unknown spawn error"}`,
        state: "error",
      });
    });

    this.child.stdout.on("data", (chunk) => {
      this.buffer += chunk.toString("utf8");
      const lines = this.buffer.split(/\r?\n/);
      this.buffer = lines.pop();

      for (const line of lines) {
        if (!line.trim()) {
          continue;
        }

        logMain("INFO", "Backend stdout line.", { line });
        try {
          const payload = JSON.parse(line);
          if (payload?.type === "ready") {
            logMain("INFO", "Backend ready payload parsed successfully.", {
              state: payload?.state || "",
              companion_name: payload?.companion_name || "",
            });
          }
          this.emit(payload);
        } catch (error) {
          logMain("ERROR", "Failed to parse backend stdout line as JSON.", {
            line,
            error: error?.message || String(error || ""),
          });
          this.emit({
            type: "error",
            message: `Invalid backend payload: ${line}`,
            state: "error",
          });
        }
      }
    });

    this.child.stderr.on("data", (chunk) => {
      this.stderrBuffer += chunk.toString("utf8");
      const lines = this.stderrBuffer.split(/\r?\n/);
      this.stderrBuffer = lines.pop();

      for (const line of lines) {
        this.recordStderr(line);
        if (this.window && !this.window.isDestroyed()) {
          this.window.webContents.send("backend:stderr", line);
        }
      }
    });

    this.child.on("exit", (code, signal) => {
      this.ready = false;
      this.pendingConfirmation = false;
      this.rejectPendingRequests(new Error(`Backend exited (${signal || code || 0}).`));
      this.rejectReadyWaiters(new Error(`Backend exited (${signal || code || 0}).`));
      logMain("WARN", "Backend process exited.", {
        code,
        signal,
        shuttingDown: this.isShuttingDown,
      });
      if (this.stderrBuffer.trim()) {
        this.recordStderr(this.stderrBuffer);
        this.stderrBuffer = "";
      }
      if (this.isShuttingDown) {
        return;
      }

      const stderrSummary = this.recentStderrLines.length
        ? ` Last stderr: ${this.recentStderrLines[this.recentStderrLines.length - 1]}`
        : "";
      this.emit({
        type: "error",
        message: `Backend exited (${signal || code || 0}).${stderrSummary}`,
        state: "error",
      });
    });
  }

  send(payload) {
    if (!this.child || this.child.stdin === null || this.child.killed) {
      throw new Error("Backend process is not running.");
    }

    const line = JSON.stringify(payload);
    if (!line) {
      return;
    }

    recordTestDiagnostic("sentMessages", payload?.type, {
      layer: payload?.layer || "",
      hasImage: Boolean(payload?.image_base64),
    });
    appendDebugLog("IPC_SEND", payload);
    this.child.stdin.write(Buffer.from(`${line}\n`, "utf8"));
  }

  waitUntilReady(timeoutMs = 15000) {
    if (this.ready) {
      return Promise.resolve();
    }
    if (!this.isRunning()) {
      this.start();
    }
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.readyWaiters = this.readyWaiters.filter((entry) => entry.resolve !== resolve);
        reject(new Error("Backend did not become ready in time."));
      }, timeoutMs);
      this.readyWaiters.push({ resolve, reject, timeout });
    });
  }

  resolveReadyWaiters() {
    const waiters = this.readyWaiters.splice(0);
    for (const waiter of waiters) {
      clearTimeout(waiter.timeout);
      waiter.resolve();
    }
  }

  rejectReadyWaiters(error) {
    const waiters = this.readyWaiters.splice(0);
    for (const waiter of waiters) {
      clearTimeout(waiter.timeout);
      waiter.reject(error);
    }
  }

  resolvePendingRequest(payload) {
    const requestId = String(payload?.request_id || payload?.requestId || "").trim();
    if (!requestId || payload?.type === "pull_progress") {
      return;
    }
    const payloadType = String(payload?.type || "");
    const isFinalResponse = payloadType === "error" || payloadType.endsWith("_result") || payloadType.endsWith(":result");
    if (!isFinalResponse) {
      return;
    }
    const pending = this.pendingRequests.get(requestId);
    if (!pending) {
      return;
    }
    clearTimeout(pending.timeout);
    this.pendingRequests.delete(requestId);
    pending.resolve(payload);
  }

  rejectPendingRequests(error) {
    const requests = Array.from(this.pendingRequests.values());
    this.pendingRequests.clear();
    for (const pending of requests) {
      clearTimeout(pending.timeout);
      pending.reject(error);
    }
  }

  async request(payload, timeoutMs = 120000) {
    await this.waitUntilReady();
    const requestId = crypto.randomUUID();
    const requestPayload = {
      ...payload,
      request_id: requestId,
    };
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.pendingRequests.delete(requestId);
        reject(new Error(`Backend request timed out: ${payload?.type || "unknown"}`));
      }, timeoutMs);
      this.pendingRequests.set(requestId, { resolve, reject, timeout });
      try {
        this.send(requestPayload);
      } catch (error) {
        clearTimeout(timeout);
        this.pendingRequests.delete(requestId);
        reject(error);
      }
    });
  }

  async sendUserMessage(content, imageBase64) {
    if (!this.ready) {
      throw new Error("Backend is still starting.");
    }
    if (this.pendingConfirmation) {
      throw new Error("A tool confirmation is pending.");
    }

    const msg = { type: "user_message", content };
    if (imageBase64) msg.image_base64 = imageBase64;
    this.send(msg);
  }

  async invokeLayer(layer, content, imageBase64) {
    if (!this.ready) {
      throw new Error("Backend is still starting.");
    }
    if (this.pendingConfirmation) {
      throw new Error("A tool confirmation is pending.");
    }
    const payload = { type: "invoke_layer", layer, content };
    if (imageBase64) {
      payload.image_base64 = imageBase64;
    }
    this.send(payload);
  }

  async resetSession(layer, preserveSummary = true) {
    if (!this.ready) {
      throw new Error("Backend is still starting.");
    }
    if (this.pendingConfirmation) {
      throw new Error("A tool confirmation is pending.");
    }
    this.send({
      type: "session_reset",
      layer,
      preserve_summary: Boolean(preserveSummary),
    });
  }

  async resolveToolDecision(approved) {
    if (!this.pendingConfirmation) {
      throw new Error("No tool confirmation is pending.");
    }

    this.pendingConfirmation = false;
    if (this.window && !this.window.isDestroyed()) {
      setWindowApprovalElevation(this.window, false);
    }
    this.send({ type: "tool_decision", approved: Boolean(approved) });
  }

  async runDreamNow() {
    if (!this.ready) {
      throw new Error("Backend is still starting.");
    }
    if (this.pendingConfirmation) {
      throw new Error("A tool confirmation is pending.");
    }
    this.send({ type: "run_dream" });
    return { ok: true };
  }

  async shutdown() {
    if (!this.isRunning()) {
      return;
    }

    this.isShuttingDown = true;
    try {
      this.send({ type: "shutdown" });
    } catch (error) {
      // Ignore write failures during shutdown.
    }

    await new Promise((resolve) => {
      const timeout = setTimeout(() => {
        if (this.child && !this.child.killed) {
          this.child.kill();
        }
        resolve();
      }, 1000);

      this.child.once("exit", () => {
        clearTimeout(timeout);
        resolve();
      });
    });
  }
}

const backend = new BackendBridge();
let isQuitting = false;
const RUNTIME_STATUS_CHANNEL = "runtime:event";

function broadcastRuntimeEvent(payload) {
  for (const win of BrowserWindow.getAllWindows()) {
    if (!win || win.isDestroyed()) {
      continue;
    }
    win.webContents.send(RUNTIME_STATUS_CHANNEL, payload);
  }
}

async function runBackendUtilityCommand(extraArgs, { signal } = {}) {
  await new Promise((resolve, reject) => {
    const child = spawnBackendCommand(extraArgs, {
      stdio: ["ignore", "pipe", "pipe"],
    });

    let stderr = "";
    child.stderr.on("data", (chunk) => {
      stderr += Buffer.isBuffer(chunk) ? chunk.toString("utf8") : String(chunk || "");
    });
    child.on("error", reject);
    child.on("exit", (code) => {
      if (code === 0) {
        resolve();
        return;
      }
      reject(new Error(stderr.trim() || `Backend utility command exited with code ${code}`));
    });

    if (signal) {
      const onAbort = () => {
        if (!child.killed) {
          child.kill();
        }
        const error = new Error("Aborted");
        error.name = "AbortError";
        reject(error);
      };
      if (signal.aborted) {
        onAbort();
        return;
      }
      signal.addEventListener("abort", onAbort, { once: true });
    }
  });
}

const runtimeManager = new RuntimeManager({
  profileRoot: PROFILE_ROOT,
  runtimePaths,
  ensureRuntimeDirectories: runtimePaths.ensureRuntimeDirectories,
  getConfig: () => loadConfig(),
  readTestOllamaState,
  brainModel: String(DEFAULT_CONFIG.brain.model || ""),
  embeddingModel: String(DEFAULT_CONFIG.memory.embedding_model || "nomic-embed-text"),
  whisperModel: String(DEFAULT_CONFIG.voice.whisper_model || "base.en"),
  ollamaZipUrl: process.env.OPEN_COMPANION_OLLAMA_ZIP_URL || "https://ollama.com/download/ollama-windows-amd64.zip",
  ollamaZipSha256: process.env.OPEN_COMPANION_OLLAMA_ZIP_SHA256 || "",
  kokoroModelUrl: process.env.OPEN_COMPANION_KOKORO_MODEL_URL || "https://huggingface.co/fastrtc/kokoro-onnx/resolve/main/kokoro-v1.0.onnx",
  kokoroModelSha256: process.env.OPEN_COMPANION_KOKORO_MODEL_SHA256 || "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
  kokoroVoicesUrl: process.env.OPEN_COMPANION_KOKORO_VOICES_URL || "https://huggingface.co/fastrtc/kokoro-onnx/resolve/main/voices-v1.0.bin",
  kokoroVoicesSha256: process.env.OPEN_COMPANION_KOKORO_VOICES_SHA256 || "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
  openPath: async (targetPath) => shell.openPath(targetPath),
  runBackendCommand: runBackendUtilityCommand,
  onEvent: (payload) => broadcastRuntimeEvent(payload),
});

const runtimeAssetManager = new RuntimeAssetManager({
  profileRoot: PROFILE_ROOT,
  runtimePaths,
  ensureRuntimeDirectories: runtimePaths.ensureRuntimeDirectories,
  kokoroModelVersion: process.env.OPEN_COMPANION_KOKORO_MODEL_VERSION || "1.0.0",
  kokoroModelUrl: process.env.OPEN_COMPANION_KOKORO_MODEL_URL || "https://huggingface.co/fastrtc/kokoro-onnx/resolve/main/kokoro-v1.0.onnx",
  kokoroModelSha256: process.env.OPEN_COMPANION_KOKORO_MODEL_SHA256 || "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
  kokoroVoicesVersion: process.env.OPEN_COMPANION_KOKORO_VOICES_VERSION || "1.0.0",
  kokoroVoicesUrl: process.env.OPEN_COMPANION_KOKORO_VOICES_URL || "https://huggingface.co/fastrtc/kokoro-onnx/resolve/main/voices-v1.0.bin",
  kokoroVoicesSha256: process.env.OPEN_COMPANION_KOKORO_VOICES_SHA256 || "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
  whisperBaseVersion: process.env.OPEN_COMPANION_WHISPER_BASE_VERSION || "",
  whisperBaseUrl: process.env.OPEN_COMPANION_WHISPER_BASE_URL || "",
  whisperBaseSha256: process.env.OPEN_COMPANION_WHISPER_BASE_SHA256 || "",
  whisperBaseLocalPath: process.env.OPEN_COMPANION_WHISPER_BASE_LOCAL_PATH || "whisper/ggml-base.en.bin",
});

const sandboxManager = new SandboxManager({
  appDataRoot: process.env.APPDATA || PROFILE_ROOT,
  vaultPath: VAULT_DIR,
});

// Inject theme CSS variables into a window immediately (before show) to avoid FOUC
function injectThemeIntoWindow(win, config) {
  if (!win || win.isDestroyed()) return;
  const theme = (config.ui || {}).theme || {};
  const [r, g, b] = theme.accent_rgb || [111, 211, 200];
  const themeData = { mode: theme.mode || "dark", accent_rgb: [r, g, b] };
  win.webContents.send("theme:apply", themeData);
}

function injectVersionIntoSettingsWindow(win) {
  if (!win || win.isDestroyed()) {
    return;
  }

  const version = app.getVersion();
  win.webContents.executeJavaScript(`
    document.title = "OpenCompanion - Settings v${version}";
    const titleEl = document.getElementById("settings-version-label");
    if (titleEl) {
      const titleTextEl = titleEl.querySelector(".s-titlebar-text");
      if (titleTextEl) {
        titleTextEl.textContent = "Settings - v${version}";
      } else {
        titleEl.textContent = "Settings - v${version}";
      }
    }
    const footerEl = document.getElementById("settings-version-footer");
    if (footerEl) {
      footerEl.textContent = "v${version}";
    }
  `).catch(() => {});
}

function createWindow() {
  const existingWindow = getMainWindow();
  if (existingWindow) {
    existingWindow.show();
    existingWindow.focus();
    return existingWindow;
  }

  const config = loadConfig();
  const uiConfig = config.ui || {};
  const scale = uiConfig.overlay_scale == null
    ? overlayScaleFromDimensions(uiConfig.window_width, uiConfig.window_height)
    : parseOverlayScaleValue(uiConfig.overlay_scale, DEFAULT_OVERLAY_SCALE);
  const { width, height } = dimensionsFromOverlayScale(scale);
  const primaryDisplay = screen.getPrimaryDisplay();
  const { x, y, width: workWidth, height: workHeight } = primaryDisplay.workArea;

  const mainWindow = new BrowserWindow({
    width,
    height,
    icon: APP_ICON_PATH,
    minWidth: DEFAULT_COMPACT_OVERLAY_WIDTH,
    minHeight: DEFAULT_COMPACT_OVERLAY_HEIGHT,
    maxWidth: 700,
    maxHeight: 1100,
    x: x + workWidth - width - 36,
    y: y + Math.max(24, workHeight - height - 72),
    frame: false,
    transparent: uiConfig.transparent !== false,
    alwaysOnTop: uiConfig.always_on_top !== false,
    skipTaskbar: false,
    resizable: false,
    maximizable: false,
    minimizable: true,
    fullscreenable: false,
    hasShadow: false,
    backgroundColor: "#00000000",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      additionalArguments: TEST_MODE ? ["--open-companion-test-mode=1"] : [],
    },
  });

  mainWindow.setMenuBarVisibility(false);
  mainWindow.loadFile(path.join(__dirname, "overlay.html"));
  mainWindow.webContents.once("did-finish-load", () => {
    logMain("INFO", "Overlay window finished loading.", {
      backendRunning: backend.isRunning(),
      backendReady: backend.ready,
      latestState: backend.latestState.state,
    });
  });
  ensureWindowVisible(mainWindow);
  scheduleVisibilityCheck(mainWindow);
  mainWindowRef = mainWindow;

  mainWindow.on("restore", () => {
    scheduleVisibilityCheck(mainWindow);
  });

  mainWindow.on("show", () => {
    scheduleVisibilityCheck(mainWindow);
  });

  mainWindow.on("ready-to-show", () => {
    scheduleVisibilityCheck(mainWindow);
    injectThemeIntoWindow(mainWindow, config);
  });

  mainWindow.on("closed", () => {
    if (mainWindowRef === mainWindow) {
      mainWindowRef = null;
    }
    if (settingsWindow && !settingsWindow.isDestroyed() && !settingsWindow.isVisible()) {
      settingsWindow.destroy();
    }
  });

  backend.attachWindow(mainWindow);
  return mainWindow;
}

let settingsWindow = null;
let settingsWindowReady = false;
let settingsWindowPendingShow = false;
let mainWindowRef = null;
let onboardingWindow = null;
let updaterStarted = false;
let appIsQuitting = false;

function getMainWindow() {
  return mainWindowRef && !mainWindowRef.isDestroyed() ? mainWindowRef : null;
}

function getOnboardingWindow() {
  return onboardingWindow && !onboardingWindow.isDestroyed() ? onboardingWindow : null;
}

function revealSettingsWindow({ focus = true } = {}) {
  if (!settingsWindow || settingsWindow.isDestroyed()) {
    return;
  }
  settingsWindow.setSkipTaskbar(false);
  settingsWindow.show();
  if (focus) {
    settingsWindow.focus();
  }
}

function createOnboardingWindow() {
  const existingWindow = getOnboardingWindow();
  if (existingWindow) {
    existingWindow.show();
    existingWindow.focus();
    return existingWindow;
  }

  onboardingWindow = new BrowserWindow({
    width: 760,
    height: 600,
    icon: APP_ICON_PATH,
    minWidth: 600,
    minHeight: 500,
    frame: false,
    transparent: false,
    alwaysOnTop: false,
    resizable: true,
    maximizable: false,
    minimizable: true,
    fullscreenable: false,
    backgroundColor: "#0e0e10",
    webPreferences: {
      preload: path.join(__dirname, "onboarding-preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      additionalArguments: TEST_MODE ? ["--open-companion-test-mode=1"] : [],
    },
  });

  onboardingWindow.setMenuBarVisibility(false);
  onboardingWindow.loadFile(path.join(__dirname, "onboarding.html"));
  onboardingWindow.webContents.once("did-finish-load", () => {
    logMain("INFO", "Onboarding window finished loading.");
  });
  onboardingWindow.once("ready-to-show", () => {
    injectThemeIntoWindow(onboardingWindow, loadConfig());
  });
  onboardingWindow.on("closed", () => {
    onboardingWindow = null;
  });
  return onboardingWindow;
}

function startMainOverlay() {
  logMain("INFO", "Starting main overlay.", {
    backendRunning: backend.isRunning(),
    backendReady: backend.ready,
    latestState: backend.latestState.state,
  });
  if (!backend.isRunning()) {
    backend.start();
  }
  const mainWindow = createWindow();
  if (!TEST_MODE && !updaterStarted) {
    setupUpdater(mainWindow, loadConfig());
    // DEV ONLY: simulate update events for manual UI testing.
    if (!app.isPackaged) {
      const { ipcMain } = require("electron");

      ipcMain.handle("dev:simulateUpdateAvailable", () => {
        mainWindow.webContents.send("update:available", {
          version: "99.0.0",
          releaseNotes: "Simulated update for dev testing",
        });
        return { ok: true };
      });

      ipcMain.handle("dev:simulateUpdateReady", () => {
        mainWindow.webContents.send("update:ready", {
          version: "99.0.0",
        });
        return { ok: true };
      });

      ipcMain.handle("dev:simulateUpdateProgress", async () => {
        for (const pct of [10, 30, 60, 90, 100]) {
          mainWindow.webContents.send("update:progress", { percent: pct });
          await new Promise((resolve) => setTimeout(resolve, 400));
        }
        return { ok: true };
      });
    }
    updaterStarted = true;
  }
  if (!TEST_MODE) {
    mainWindow.once("ready-to-show", () => {
      runStartupHealthCheck(mainWindow);
      prewarmSettingsWindow();
    });
  }
  return mainWindow;
}

function createSettingsWindow(options = {}) {
  const shouldShow = options.show !== false;
  const shouldFocus = options.focus !== false;
  if (settingsWindow && !settingsWindow.isDestroyed()) {
    if (shouldShow) {
      settingsWindowPendingShow = true;
      if (settingsWindowReady) {
        revealSettingsWindow({ focus: shouldFocus });
      }
    }
    return settingsWindow;
  }

  settingsWindowReady = false;
  settingsWindowPendingShow = shouldShow;
  settingsWindow = new BrowserWindow({
    width: 760,
    height: 580,
    icon: APP_ICON_PATH,
    minWidth: 680,
    minHeight: 500,
    frame: false,
    transparent: false,
    alwaysOnTop: false,
    resizable: true,
    maximizable: false,
    minimizable: true,
    fullscreenable: false,
    show: false,
    skipTaskbar: !shouldShow,
    backgroundColor: "#0e0e10",
    webPreferences: {
      preload: path.join(__dirname, "settings-preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      additionalArguments: TEST_MODE ? ["--open-companion-test-mode=1"] : [],
    },
  });

  settingsWindow.setMenuBarVisibility(false);
  settingsWindow.loadFile(path.join(__dirname, "settings.html"));
  settingsWindow.once("ready-to-show", () => {
    settingsWindowReady = true;
    if (settingsWindow && !settingsWindow.isDestroyed()) {
      injectThemeIntoWindow(settingsWindow, loadConfig());
      if (settingsWindowPendingShow) {
        revealSettingsWindow({ focus: shouldFocus });
      }
    }
  });
  settingsWindow.webContents.once("dom-ready", () => {
    injectVersionIntoSettingsWindow(settingsWindow);
  });
  settingsWindow.on("close", (event) => {
    if (appIsQuitting || TEST_MODE || settingsWindow?.isDestroyed()) {
      return;
    }
    event.preventDefault();
    settingsWindowPendingShow = false;
    settingsWindow.hide();
    settingsWindow.setSkipTaskbar(true);
  });
  settingsWindow.on("closed", () => {
    settingsWindow = null;
    settingsWindowReady = false;
    settingsWindowPendingShow = false;
  });
  return settingsWindow;
}

function prewarmSettingsWindow() {
  if (!SETTINGS_PREWARM_ENABLED || TEST_MODE || !loadConfig().onboarding_complete) {
    return;
  }
  setTimeout(() => {
    if (appIsQuitting || settingsWindow || !getMainWindow()) {
      return;
    }
    createSettingsWindow({ show: false, focus: false });
  }, SETTINGS_PREWARM_DELAY_MS);
}

async function runStartupHealthCheck(win) {
  const config = loadConfig();
  const brain = config.brain || {};
  const memory = config.memory || {};
  const voice = config.voice || {};
  // Resolve from the companion layer so per-layer provider overrides (e.g. chatgpt_oauth) are respected.
  const resolvedLayer = resolveLayerBrainConfig(config, "companion");
  const provider = normalizeProviderName(resolvedLayer.provider) || "gemma";
  const providerLabel = getProviderDisplayLabel(provider);
  const model = resolvedLayer.model || String(brain.model || "").trim();
  const extractionSource = String(memory.extraction_source || (memory.extraction_mode === "provider" ? "brain" : "local")).trim().toLowerCase() || "local";
  const extractionModel = String(memory.extraction_model || "").trim();
  const extractionProvider = String(memory.extraction_provider || "").trim().toLowerCase();
  const requiresLocalMemory = memory.enabled !== false
    && (
      memory.embedding_enabled !== false
      || extractionSource === "local"
      || (extractionSource === "brain" && isOllamaBackedProvider(provider))
      || (extractionSource === "api" && isOllamaBackedProvider(extractionProvider))
    );
  const results = [];

  // Check 1: ollama_running
  let ollamaOk = false;
  let tagsData = null;
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 2000);
    const resp = await fetch("http://localhost:11434/api/tags", { signal: controller.signal });
    clearTimeout(timeoutId);
    if (resp.ok) {
      ollamaOk = true;
      tagsData = await resp.json();
    }
  } catch {
    // timeout or connection refused
  }

  if (!ollamaOk && (isOllamaBackedProvider(provider) || requiresLocalMemory)) {
    results.push({
      id: "ollama_running",
      ok: false,
      label: "Ollama is not running",
      hint: isOllamaBackedProvider(provider)
        ? "Retry runtime preparation or start Ollama from its app."
        : "OpenCompanion is configured to keep memory local through Ollama. Retry runtime preparation or start Ollama from its app.",
    });
  }

  if (ollamaOk && tagsData && isOllamaBackedProvider(provider)) {
    const modelNames = (tagsData.models || []).map((m) => m.name);

    // Check 2: model_pulled
    const modelFound = model && modelNames.some((n) => n === model || n.startsWith(`${model}:`));
    if (!modelFound) {
      results.push({
        id: "model_pulled",
        ok: false,
        label: `Model "${model}" not found in Ollama`,
        hint: `Retry runtime preparation to install ${model}.`,
      });
    }

    // nomic-embed-text check removed: backend falls back to keyword search gracefully
  }

  // Check 4: kokoro_files
  const kokoroOnnx = runtimePaths.KOKORO_MODEL_PATH;
  const kokoroBin = runtimePaths.KOKORO_VOICES_PATH;
  if (voice.tts_enabled !== false && String(voice.tts_provider || "kokoro").trim().toLowerCase() === "kokoro" && (!fs.existsSync(kokoroOnnx) || !fs.existsSync(kokoroBin))) {
    results.push({
      id: "kokoro_files",
      ok: false,
      label: "Voice synthesis unavailable",
      hint: "Retry runtime preparation to install the Kokoro voice assets.",
    });
  }

  if (!isOllamaBackedProvider(provider) && provider !== "chatgpt_oauth") {
    const baseUrl = String(resolvedLayer.baseUrl || brain.base_url || brain.api_url || "").trim();
    const keyStatus = provider === "custom" ? "optional" : await readKeychainGroupValue(provider);

    if (provider !== "custom" && !String(keyStatus || "").trim()) {
      results.push({
        id: `${provider}_api_key_missing`,
        ok: false,
        label: `Your ${providerLabel} API key is not set.`,
        hint: "Go to Settings -> Model to add it.",
      });
    } else {
      const validation = await validateProviderKey(provider, { model, baseUrl });
      if (!validation.valid) {
        results.push(provider === "custom"
          ? {
              id: "custom_endpoint_invalid",
              ok: false,
              label: validation.error === "Custom endpoint URL not set"
                ? "Your custom endpoint URL is not set."
                : "Your custom endpoint configuration could not be validated.",
              hint: validation.error === "Custom endpoint URL not set"
                ? "Go to Settings -> Model to add the endpoint URL."
                : `${validation.error}. Go to Settings -> Model to review it.`,
            }
          : {
              id: `${provider}_api_key_invalid`,
              ok: false,
              label: `Your ${providerLabel} API key appears invalid.`,
              hint: `${validation.error}. Go to Settings -> Model to update it.`,
            });
      }
    }
  }

  const failures = results.filter((r) => !r.ok);
  if (win && !win.isDestroyed()) {
    win.webContents.send("startup:health", failures);
  }
}

app.whenReady().then(async () => {
  runtimePaths.ensureRuntimeDirectories();
  try {
    await runMigration({
      profileRoot: PROFILE_ROOT,
      configPath: CONFIG_PATH,
      logger: console,
    });
  } catch (error) {
    console.warn("[migration] Startup migration failed:", error.message);
  }

  const config = loadConfig();
  if (config.onboarding_complete) {
    startMainOverlay();
  } else {
    createOnboardingWindow();
  }

  runtimeAssetManager.startupCheck().catch((error) => {
    console.warn("[assets] Runtime asset startup check failed:", error.message);
  });

  // Non-blocking sandbox status check â€” must never delay app startup
  sandboxManager.isSandboxInstalled().then((installed) => {
    if (installed) {
      sandboxManager.verifySandbox().catch((err) => {
        console.warn("[sandbox] Startup verify failed:", err.message);
      });
    }
  }).catch((err) => {
    console.warn("[sandbox] Startup sandbox check failed:", err.message);
  });

  globalShortcut.register("CommandOrControl+,", () => {
    createSettingsWindow();
  });

  screen.on("display-metrics-changed", () => {
    for (const openWindow of BrowserWindow.getAllWindows()) {
      scheduleVisibilityCheck(openWindow);
    }
  });

  screen.on("display-added", () => {
    for (const openWindow of BrowserWindow.getAllWindows()) {
      scheduleVisibilityCheck(openWindow);
    }
  });

  screen.on("display-removed", () => {
    for (const openWindow of BrowserWindow.getAllWindows()) {
      scheduleVisibilityCheck(openWindow);
    }
  });

  if (Number.isFinite(AUTO_CLOSE_MS) && AUTO_CLOSE_MS > 0) {
    setTimeout(() => {
      app.quit();
    }, AUTO_CLOSE_MS);
  }
});

ipcMain.on("startup:dismiss", (_event, _id) => { /* no-op: banners are session-only */ });
ipcMain.on("ui:setIgnoreMouseEvents", (_event, ignore, options) => {
  const win = getMainWindow();
  if (win && !win.isDestroyed()) {
    win.setIgnoreMouseEvents(Boolean(ignore), options || {});
  }
});
ipcMain.handle("backend:getInitialState", async () => {
  logMain("INFO", "Renderer requested backend initial state.", {
    backendRunning: backend.isRunning(),
    backendReady: backend.ready,
    latestState: backend.latestState.state,
    lastError: backend.latestState.lastError || "",
  });
  return backend.latestState;
});
ipcMain.handle("app:version", async () => app.getVersion());

// â”€â”€ Sandbox IPC handlers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
ipcMain.handle("sandbox:status", async () => sandboxManager.getStatus());
ipcMain.handle("sandbox:provision", async () => sandboxManager.provisionSandbox());
ipcMain.handle("sandbox:reset", async () => sandboxManager.resetSandbox());
ipcMain.handle("sandbox:verify", async () => sandboxManager.verifySandbox());
// â”€â”€ End sandbox IPC handlers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

ipcMain.handle("backend:sendUserMessage", async (_event, content, imageBase64) => backend.sendUserMessage(content, imageBase64));
ipcMain.handle("backend:invokeLayer", async (_event, layer, content, imageBase64) => backend.invokeLayer(layer, content, imageBase64));
ipcMain.handle("backend:resetSession", async (_event, layer, preserveSummary = true) => backend.resetSession(layer, preserveSummary));
ipcMain.handle("backend:closeSession", async () => {
  const child = backend?.child;
  if (!child || child.stdin === null || child.killed) {
    return { ok: false, error: "Backend process is not running." };
  }

  try {
    const payload = { type: "config_reload" };
    appendDebugLog("IPC_SEND", payload);
    child.stdin.write(Buffer.from(`${JSON.stringify(payload)}\n`, "utf8"));
    return { ok: true };
  } catch (error) {
    console.warn("Failed to write backend closeSession request:", error.message);
    return { ok: false, error: error.message };
  }
});
ipcMain.handle("backend:resolveToolDecision", async (_event, approved) => backend.resolveToolDecision(approved));
ipcMain.handle("settings:runDreamNow", async () => backend.runDreamNow());
ipcMain.handle("ui:copyText", async (_event, text) => {
  clipboard.writeText(String(text || ""));
});
ipcMain.handle("ui:moveCompanionWindow", async (_event, movement) => {
  const [mainWindow] = BrowserWindow.getAllWindows();
  return moveWindow(mainWindow, movement || {});
});
ipcMain.handle("backend:sendAudioInput", async (_event, audioB64) => {
  backend.send({ type: "audio_input", audio_b64: audioB64 });
});
ipcMain.handle("ui:minimizeWindow", async () => {
  const [mainWindow] = BrowserWindow.getAllWindows();
  if (!mainWindow || mainWindow.isDestroyed()) {
    throw new Error("Main window is not available.");
  }
  mainWindow.minimize();
});

ipcMain.handle("ui:resolveAssetPath", async (_event, assetPath) => resolveProjectPath(assetPath));

ipcMain.handle("ui:openSettings", async () => {
  createSettingsWindow();
});

ipcMain.handle("apikey:set", async (_event, provider, value) => {
  const accountGroup = normalizeKeychainAccountName(provider);
  if (!accountGroup || accountGroup === "custom_url") {
    throw new Error("Unsupported provider.");
  }

  const cleanValue = String(value || "").trim();
  if (!cleanValue) {
    await deleteKeychainGroupValue(accountGroup);
    return { ok: true };
  }

  await writeKeychainGroupValue(accountGroup, cleanValue);
  return { ok: true };
});

ipcMain.handle("apikey:get", async (_event, provider) => {
  const accountGroup = normalizeKeychainAccountName(provider);
  if (!accountGroup || accountGroup === "custom_url") {
    throw new Error("Unsupported provider.");
  }

  const value = await readKeychainGroupValue(accountGroup);
  return value ? "set" : "not-set";
});

ipcMain.handle("apikey:delete", async (_event, provider) => {
  const accountGroup = normalizeKeychainAccountName(provider);
  if (!accountGroup || accountGroup === "custom_url") {
    throw new Error("Unsupported provider.");
  }

  await deleteKeychainGroupValue(accountGroup);
  return { ok: true };
});

ipcMain.handle("apikey:validate", async (_event, provider, options) => {
  return validateProviderKey(provider, options);
});

ipcMain.handle("settings:getApiKeys", async () => {
  const [openai, anthropic, gemini, openrouter, qwen, custom] = await Promise.all([
    readKeychainGroupValue("openai"),
    readKeychainGroupValue("anthropic"),
    readKeychainGroupValue("gemini"),
    readKeychainGroupValue("openrouter"),
    readKeychainGroupValue("qwen"),
    readKeychainGroupValue("custom"),
  ]);
  return {
    openai: Boolean(openai),
    anthropic: Boolean(anthropic),
    gemini: Boolean(gemini),
    openrouter: Boolean(openrouter),
    qwen: Boolean(qwen),
    custom: Boolean(custom),
    custom_url: readConfiguredCustomBaseUrl(),
  };
});

ipcMain.handle("settings:setApiKey", async (_event, { account, value }) => {
  const accountGroup = normalizeKeychainAccountName(account);
  if (!accountGroup) {
    throw new Error("Unsupported keychain account.");
  }

  if (accountGroup === "custom_url") {
    writeConfiguredCustomBaseUrl(value);
    return { ok: true };
  }

  if (value && String(value).trim()) {
    await writeKeychainGroupValue(accountGroup, value);
  } else {
    await deleteKeychainGroupValue(accountGroup);
  }
  return { ok: true };
});

ipcMain.handle("settings:getApiKey", async (_event, { account }) => {
  const accountGroup = normalizeKeychainAccountName(account);
  if (!accountGroup) {
    throw new Error("Unsupported keychain account.");
  }

  if (accountGroup === "custom_url") {
    const value = readConfiguredCustomBaseUrl();
    return { value: value || null };
  }

  const value = await readKeychainGroupValue(accountGroup);
  return { value: value ? "set" : "not-set" };
});

ipcMain.handle("settings:getOllamaModelInfo", async (_event, modelName) => {
  try {
    return await ollamaRuntimeCache.getSettingsModelInfo(modelName, readTestOllamaState());
  } catch {
    return null;
  }
});

ipcMain.handle("brain:contextInfo", async () => {
  // Returns { modelMax, current, isAuto } for the currently configured brain model.
  const cfg = loadConfig() || {};
  const modelName = String(cfg?.brain?.model || "").trim();
  const configured = cfg?.brain?.context_window;
  const isAuto = configured === "auto" || configured == null;

  let modelMax = null;
  if (modelName) {
    try {
      modelMax = await ollamaRuntimeCache.getContextLength(modelName, readTestOllamaState());
      if (modelMax) modelMax = Number(modelMax);
    } catch {
      modelMax = null;
    }
  }

  const current = isAuto ? (modelMax || 32768) : Number(configured);
  return { modelMax, current, isAuto };
});

function normalizeLayerName(layerName) {
  const value = String(layerName || "").trim();
  if (["assistant", "assistant_low", "assistant_high", "pc_doctor", "pcdoctor"].includes(value)) {
    return "assistant";
  }
  return value || "companion";
}

function resolveLayerBrainConfig(config, layerName) {
  const canonical = normalizeLayerName(layerName);
  const brainCfg = isPlainObject(config?.brain) ? config.brain : {};
  const pickValue = (value, fallback) => (value === "" || value == null ? fallback : value);
  const sharedProvider = String(brainCfg.provider || "gemma").trim() || "gemma";
  const sharedModel = String(brainCfg.model || "").trim();
  const sharedBaseUrl = String(brainCfg.base_url || brainCfg.api_url || "").trim();
  const sharedContextWindow = brainCfg.context_window;
  const sharedTemperature = brainCfg.temperature;
  const sharedMaxTokens = brainCfg.max_tokens;
  const sharedReasoning = isPlainObject(brainCfg.reasoning) ? brainCfg.reasoning : {};
  const layers = isPlainObject(brainCfg.layers) ? brainCfg.layers : {};
  const rawOverride = layers[canonical]
    || (canonical === "assistant" ? layers.assistant_low || layers.assistant_high || layers.pc_doctor : null)
    || {};
  const override = isPlainObject(rawOverride) ? rawOverride : {};
  const overrideReasoning = isPlainObject(override.reasoning) ? override.reasoning : {};
  return {
    provider: String(override.provider || sharedProvider || "gemma").trim() || "gemma",
    model: String(override.model || sharedModel || "").trim(),
    baseUrl: String(override.base_url || override.api_url || sharedBaseUrl || "").trim(),
    contextWindow: pickValue(override.context_window, sharedContextWindow ?? "auto"),
    temperature: pickValue(override.temperature, sharedTemperature ?? 0.8),
    maxTokens: pickValue(override.max_tokens, sharedMaxTokens ?? 1024),
    family: String(override.family || brainCfg.family || "").trim(),
    reasoningEffort: String(
      pickValue(
        override.reasoning_effort,
        pickValue(
          overrideReasoning.effort,
          pickValue(brainCfg.reasoning_effort, sharedReasoning.effort)
        )
      )
      ?? ""
    ).trim(),
    reasoningSummary: String(
      pickValue(
        override.reasoning_summary,
        pickValue(
          overrideReasoning.summary,
          pickValue(brainCfg.reasoning_summary, sharedReasoning.summary)
        )
      )
      ?? ""
    ).trim(),
  };
}

async function fetchOllamaShowInfo(modelName) {
  return ollamaRuntimeCache.getRuntimeValidationInfo(modelName, readTestOllamaState());
}

ipcMain.handle("brain:modelInfo", async (_event, modelName) => {
  const cfg = loadConfig() || {};
  const resolvedName = String(modelName || cfg?.brain?.model || "").trim();
  if (!resolvedName) return null;
  try {
    return await ollamaRuntimeCache.getEnrichedModelInfo(resolvedName, readTestOllamaState());
  } catch {
    return null;
  }
});

ipcMain.handle("brain:listPulledModels", async () => {
  try {
    return await ollamaRuntimeCache.listPulledModels(readTestOllamaState());
  } catch {
    return [];
  }
});

ipcMain.handle("brain:systemMemory", async () => {
  const testState = readTestOllamaState();
  if (testState?.system_memory) return testState.system_memory;

  const ramFreeMB = Math.round(os.freemem() / 1048576);
  const ramTotalMB = Math.round(os.totalmem() / 1048576);

  return new Promise((resolve) => {
    execFile(
      "nvidia-smi",
      ["--query-gpu=memory.free,memory.total", "--format=csv,noheader,nounits"],
      { timeout: 5000 },
      (err, stdout) => {
        if (err || !stdout) {
          resolve({ vramFreeMB: null, vramTotalMB: null, ramFreeMB, ramTotalMB });
          return;
        }
        const line = stdout.trim().split("\n")[0] || "";
        const parts = line.split(",").map((s) => parseInt(s.trim(), 10));
        if (parts.length >= 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
          resolve({ vramFreeMB: parts[0], vramTotalMB: parts[1], ramFreeMB, ramTotalMB });
        } else {
          resolve({ vramFreeMB: null, vramTotalMB: null, ramFreeMB, ramTotalMB });
        }
      }
    );
  });
});

ipcMain.handle("brain:searchRegistry", async (_event, query) => {
  const testState = readTestOllamaState();
  if (testState) {
    if (testState.registry_offline === true) return { error: "offline" };
    if (testState.registry) return testState.registry;
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5000);
  try {
    const resp = await fetch(
      `https://ollama.com/api/search?q=${encodeURIComponent(query || "")}&limit=30`,
      { signal: controller.signal }
    );
    clearTimeout(timer);
    return await resp.json();
  } catch {
    clearTimeout(timer);
    return { error: "offline" };
  }
});

ipcMain.handle("brain:activeModelCapabilities", async (_event, layerName) => {
  const cfg = loadConfig() || {};
  const canonical = normalizeLayerName(layerName) || "companion";
  const resolved = resolveLayerBrainConfig(cfg, canonical);
  const modelName = String(resolved?.model || "").trim();
  const emptyResult = {
    capabilities: [],
    supportsImageInput: false,
    provider: String(resolved?.provider || "").trim(),
    model: modelName,
  };

  if (!modelName) {
    return emptyResult;
  }

  if (resolved.provider === "openai") {
    const card = getOpenAiModelCard(modelName);
    return {
      capabilities: card.supportsImageInput === true ? ["image_input"] : [],
      supportsImageInput: card.supportsImageInput === true,
      provider: resolved.provider,
      model: modelName,
    };
  }

  if (resolved.provider === "anthropic") {
    const card = getAnthropicModelCard(modelName);
    return {
      capabilities: card.supportsImageInput === true ? ["image_input"] : [],
      supportsImageInput: card.supportsImageInput === true,
      provider: resolved.provider,
      model: modelName,
    };
  }

  if (!isOllamaBackedProvider(resolved.provider)) {
    return emptyResult;
  }

  const hasImageInputCapability = (capabilities) => capabilities.some((capability) => {
    const value = String(capability || "").trim().toLowerCase();
    return value.includes("vision") || value.includes("image");
  });

  try {
    const ollamaInfo = await fetchOllamaShowInfo(modelName);
    const capabilities = Array.isArray(ollamaInfo?.capabilities) ? ollamaInfo.capabilities : [];
    return {
      capabilities,
      supportsImageInput: hasImageInputCapability(capabilities),
      provider: resolved.provider,
      model: modelName,
    };
  } catch {
    return emptyResult;
  }
});

function buildRuntimeValidationInfo(layerName, resolved) {
  const family = resolved.provider === "openai"
    ? getOpenAiModelCard(resolved.model).family
    : resolved.provider === "anthropic"
      ? getAnthropicModelCard(resolved.model).family
      : detectModelFamily(resolved.model, resolved.family);
  return {
    layer: layerName,
    provider: resolved.provider,
    model: resolved.model,
    family,
    label: "",
    description: "",
    contextWindow: resolved.contextWindow,
    contextLength: null,
    resolvedContextWindow: null,
    maxOutputTokens: null,
    knowledgeCutoff: null,
    transport: isOllamaBackedProvider(resolved.provider)
      ? (family === "google_gemma" ? "ollama_native" : "ollama_chat")
      : resolved.provider,
    temperature: resolved.temperature,
    topP: null,
    topK: null,
    capabilities: [],
    reasoningEfforts: [],
    defaultReasoningEffort: null,
    defaultReasoningSummary: null,
    configuredReasoningEffort: resolved.reasoningEffort || "",
    effectiveReasoningEffort: resolved.reasoningEffort || "",
    supportsTools: null,
    supportsStructuredOutputs: null,
    supportsImageInput: null,
    registryMatch: false,
  };
}

ipcMain.handle("backend:validateLayerRuntime", async (_event, layerName) => {
  const cfg = loadConfig() || {};
  const canonical = normalizeLayerName(layerName);
  const resolved = resolveLayerBrainConfig(cfg, canonical);
  const warnings = [];
  const errors = [];
  const requiresTools = canonical !== "";
  const info = buildRuntimeValidationInfo(canonical, resolved);
  const family = info.family;

  if (!resolved.provider) {
    errors.push("No provider is configured for this layer.");
  }
  if (!resolved.model) {
    errors.push("No model is configured for this layer.");
  }
  if (errors.length) {
    return { ok: false, warnings, errors, resolved: info };
  }

  if (isOllamaBackedProvider(resolved.provider)) {
    try {
      const ollamaInfo = await fetchOllamaShowInfo(resolved.model);
      info.capabilities = ollamaInfo?.capabilities || [];
      info.contextLength = ollamaInfo?.contextLength || null;
      info.resolvedContextWindow = resolved.contextWindow === "auto"
        ? (ollamaInfo?.contextLength || 32768)
        : Number(resolved.contextWindow) || null;
      if (family === "google_gemma") {
        info.temperature = resolved.temperature === 0.8 ? 1.0 : resolved.temperature;
        info.topP = 0.95;
        info.topK = 64;
        warnings.push("Gemma runtime will use native Ollama chat with Gemma sampling defaults.");
      } else if (family === "qwen") {
        warnings.push("Qwen local runtime will use native Ollama chat.");
      }
      if (requiresTools && !info.capabilities.some((cap) => String(cap).toLowerCase().includes("tool"))) {
        errors.push(`Model ${resolved.model} does not advertise tool support.`);
      }
    } catch (error) {
      errors.push(`Could not reach Ollama for ${resolved.model}: ${error.message}`);
    }
  } else if (resolved.provider === "openai") {
    const card = getOpenAiModelCard(resolved.model);
    info.label = card.label || "";
    info.description = card.description || "";
    info.transport = card.preferredApi || "chat_completions";
    info.reasoningEfforts = Array.isArray(card.reasoningEfforts) ? [...card.reasoningEfforts] : [];
    info.defaultReasoningEffort = card.defaultReasoningEffort || null;
    info.defaultReasoningSummary = card.defaultReasoningSummary || null;
    info.effectiveReasoningEffort = resolved.reasoningEffort || card.defaultReasoningEffort || "";
    info.supportsTools = card.supportsTools !== false;
    info.supportsStructuredOutputs = card.supportsStructuredOutputs === true;
    info.supportsImageInput = card.supportsImageInput === true;
    info.contextLength = Number.isFinite(card.contextWindow) ? Number(card.contextWindow) : null;
    info.maxOutputTokens = Number.isFinite(card.maxOutputTokens) ? Number(card.maxOutputTokens) : null;
    info.knowledgeCutoff = card.knowledgeCutoff || null;
    info.registryMatch = card.id !== OPENAI_LEGACY_FALLBACK_CARD.id;
    info.capabilities = [
      info.supportsTools ? "tools" : "",
      info.supportsStructuredOutputs ? "structured_outputs" : "",
      info.supportsImageInput ? "image_input" : "",
      info.reasoningEfforts.length ? "reasoning" : "",
    ].filter(Boolean);
    info.resolvedContextWindow = resolved.contextWindow === "auto"
      ? info.contextLength
      : Number(resolved.contextWindow) || null;
    if (card.preferredApi === "responses") {
      warnings.push("This OpenAI model will use the native Responses API path.");
    }
    if (resolved.reasoningEffort && !info.reasoningEfforts.includes(resolved.reasoningEffort)) {
      warnings.push(`Configured reasoning effort '${resolved.reasoningEffort}' is not supported by ${resolved.model}. Using the model default instead.`);
      info.effectiveReasoningEffort = card.defaultReasoningEffort || "";
    }
    if (!info.registryMatch) {
      warnings.push("This OpenAI model is not in the local registry yet. Using a conservative compatibility profile.");
    }
    if (requiresTools && info.supportsTools === false) {
      errors.push(`Model ${resolved.model} does not advertise tool support.`);
    }
  } else if (resolved.provider === "anthropic") {
    const card = getAnthropicModelCard(resolved.model);
    info.label = card.label || "";
    info.description = card.description || "";
    info.transport = card.preferredApi || "messages";
    info.reasoningEfforts = Array.isArray(card.reasoningEfforts) ? [...card.reasoningEfforts] : [];
    info.defaultReasoningEffort = card.defaultReasoningEffort || null;
    info.effectiveReasoningEffort = resolved.reasoningEffort || card.defaultReasoningEffort || "";
    info.supportsTools = card.supportsTools !== false;
    info.supportsStructuredOutputs = card.supportsStructuredOutputs === true;
    info.supportsImageInput = card.supportsImageInput === true;
    info.contextLength = Number.isFinite(card.contextWindow) ? Number(card.contextWindow) : null;
    info.maxOutputTokens = Number.isFinite(card.maxOutputTokens) ? Number(card.maxOutputTokens) : null;
    info.knowledgeCutoff = card.knowledgeCutoff || null;
    info.registryMatch = card.id !== ANTHROPIC_FALLBACK_CARD.id;
    info.capabilities = [
      info.supportsTools ? "tools" : "",
      info.supportsImageInput ? "image_input" : "",
      info.reasoningEfforts.length ? "thinking" : "",
    ].filter(Boolean);
    info.resolvedContextWindow = resolved.contextWindow === "auto"
      ? info.contextLength
      : Number(resolved.contextWindow) || null;
    if (card.preferredApi === "messages") {
      warnings.push("This Anthropic model will use the native Messages API path.");
    }
    if (resolved.reasoningEffort && !info.reasoningEfforts.includes(resolved.reasoningEffort)) {
      warnings.push(`Configured thinking effort '${resolved.reasoningEffort}' is not supported by ${resolved.model}. Using the model default instead.`);
      info.effectiveReasoningEffort = card.defaultReasoningEffort || "";
    }
    if (!info.registryMatch) {
      warnings.push("This Anthropic model is not in the local registry yet. Using a conservative compatibility profile.");
    }
    if (requiresTools && info.supportsTools === false) {
      errors.push(`Model ${resolved.model} does not advertise tool support.`);
    }
  } else if (resolved.provider === "qwen_cloud") {
    info.label = "Qwen";
    info.description = "DashScope OpenAI-compatible Qwen model.";
    info.transport = "dashscope_chat_completions";
    info.reasoningEfforts = ["none", "low", "medium", "high"];
    info.defaultReasoningEffort = "none";
    info.supportsTools = true;
    info.supportsImageInput = false;
    info.supportsStructuredOutputs = false;
    info.registryMatch = QWEN_CLOUD_MODEL_IDS.includes(resolved.model);
    info.capabilities = ["tools", "thinking"];
    info.resolvedContextWindow = resolved.contextWindow === "auto" ? 1_000_000 : Number(resolved.contextWindow) || null;
    warnings.push("Qwen DashScope uses OpenAI-compatible Chat Completions.");
  } else if (resolved.provider === "chatgpt_oauth") {
    const oauthCard = getChatGptOauthModelCard(resolved.model);
    info.label = `ChatGPT Plus (${oauthCard?.label || resolved.model})`;
    info.description = oauthCard?.description || "ChatGPT Plus via OAuth — stateless Codex endpoint";
    info.transport = "responses";
    info.supportsTools = true;
    info.supportsImageInput = false;
    info.supportsStructuredOutputs = false;
    info.reasoningEfforts = [];
    info.contextLength = Number(oauthCard?.context_window || CHATGPT_OAUTH_MODEL_CARDS[0]?.context_window || 1_050_000);
    info.maxOutputTokens = Number(oauthCard?.max_output_tokens || 0) || null;
    info.resolvedContextWindow = info.contextLength;
    info.capabilities = ["tools"];
    if (!oauthCard && !CHATGPT_OAUTH_MODELS.includes(resolved.model)) {
      warnings.push(`Model '${resolved.model}' is not a known ChatGPT OAuth model. Proceeding anyway.`);
    }
  } else {
    info.resolvedContextWindow = resolved.contextWindow === "auto"
      ? null
      : Number(resolved.contextWindow) || null;
  }

  return { ok: errors.length === 0, warnings, errors, resolved: info };
});

ipcMain.handle("ui:setSelectedTargetLayer", async (_event, layerName) => {
  const canonical = normalizeLayerName(layerName) || "companion";
  const updated = saveConfig(null, {
    ui: {
      selected_target_layer: canonical,
    },
  });
  refreshBackendFromConfig(updated);
  return { ok: true, config: updated };
});

ipcMain.handle("ui:setLayerReasoningEffort", async (_event, layerName, effort) => {
  const canonical = normalizeLayerName(layerName) || "companion";
  const cleanEffort = String(effort || "").trim();
  const config = loadConfig() || {};
  const resolved = resolveLayerBrainConfig(config, canonical);
  const card = resolved.provider === "openai" ? getOpenAiModelCard(resolved.model) : null;
  const supportedEfforts = Array.isArray(card?.reasoningEfforts) ? card.reasoningEfforts : [];

  if (cleanEffort && !supportedEfforts.includes(cleanEffort)) {
    throw new Error(`Reasoning effort '${cleanEffort}' is not supported for ${resolved.model || "this layer"}.`);
  }

  const updated = saveConfig(null, {
    brain: {
      layers: {
        [canonical]: {
          reasoning_effort: cleanEffort,
        },
      },
    },
  });
  const mergedRuntimeConfig = loadConfig();
  backend.latestState = {
    ...backend.latestState,
    config: mergedRuntimeConfig,
  };
  try {
    backend.send({ type: "config_reload" });
  } catch (error) {
    console.warn("Failed to send backend config_reload:", error.message);
  }
  return { ok: true, config: updated };
});

ipcMain.handle("settings:getOllamaRunningModels", async () => {
  try {
    return await ollamaRuntimeCache.listRunningModels(readTestOllamaState());
  } catch {
    return [];
  }
});

ipcMain.handle("settings:getMemoryFiles", async () => {
  try {
    if (!fs.existsSync(MEMORY_DIR)) {
      return { ok: true, files: [] };
    }

    const files = fs.readdirSync(MEMORY_DIR, { withFileTypes: true })
      .filter((entry) => entry.isFile() && entry.name === "memory.md")
      .sort((left, right) => left.name.localeCompare(right.name))
      .map((entry) => {
        const filePath = path.join(MEMORY_DIR, entry.name);
        const lines = fs.readFileSync(filePath, "utf8").split(/\r?\n/);
        const entries = [];

        lines.forEach((line, index) => {
          if (line.startsWith("- ")) {
            entries.push({
              line: index,
              text: line.slice(2),
            });
          }
        });

        return {
          file: entry.name,
          entries,
        };
      });

    return { ok: true, files };
  } catch (error) {
    return { ok: false, files: [], error: error.message };
  }
});

ipcMain.handle("settings:deleteMemoryEntry", async (_event, { file, line }) => {
  try {
    if (!isValidMemoryFileName(file)) {
      throw new Error("Invalid memory file name.");
    }

    const lineIndex = Number(line);
    if (!Number.isInteger(lineIndex) || lineIndex < 0) {
      throw new Error("Invalid memory line index.");
    }

    const filePath = path.join(MEMORY_DIR, file);
    if (!fs.existsSync(filePath)) {
      throw new Error("Memory file not found.");
    }

    const lines = fs.readFileSync(filePath, "utf8").split(/\r?\n/);
    if (lineIndex >= lines.length) {
      throw new Error("Memory line index out of range.");
    }

    if (!lines[lineIndex].startsWith("- ")) {
      throw new Error("Memory line does not contain a deletable entry.");
    }

    lines.splice(lineIndex, 1);
    const updated = normalizeMemoryFileContent(lines.join("\n"));
    fs.writeFileSync(filePath, updated, "utf8");

    return { ok: true };
  } catch (error) {
    return { ok: false, error: error.message };
  }
});

ipcMain.handle("settings:getTools", async () => {
  try {
    const current = readBaseConfig();
    const builtin = parseBuiltinTools();
    const builtinNames = new Set(builtin.map((tool) => tool.name));
    const { custom, overrides } = getToolsConfig(current, builtinNames);
    return {
      ok: true,
      builtin,
      custom: custom.map(normalizeCustomToolView),
      overrides,
    };
  } catch (error) {
    return {
      ok: false,
      builtin: [],
      custom: [],
      overrides: getDefaultToolOverrides(),
      error: error.message,
    };
  }
});

ipcMain.handle("settings:resetMemoryNow", async () => {
  try {
    resetLongTermMemory();
    return { ok: true };
  } catch (error) {
    return { ok: false, error: String(error?.message || error || "Could not reset long-term memory.") };
  }
});

ipcMain.handle("settings:saveToolLayers", async (_event, { builtin, custom }) => {
  try {
    const registryPath = getBuiltinRegistryPath();
    const parsedRegistry = JSON.parse(fs.readFileSync(registryPath, "utf8"));
    const registryTools = Array.isArray(parsedRegistry.tools) ? parsedRegistry.tools : [];
    const builtinByName = new Map(
      (Array.isArray(builtin) ? builtin : [])
        .map((tool) => ({
          name: String(tool?.name || "").trim(),
          layer: normalizeCustomToolLayers(tool?.layer),
        }))
        .filter((tool) => tool.name)
        .map((tool) => [tool.name, tool.layer])
    );

    parsedRegistry.tools = registryTools.map((tool) => {
      const name = String(tool?.name || "").trim();
      if (!builtinByName.has(name)) {
        return tool;
      }
      return {
        ...tool,
        layer: builtinByName.get(name),
      };
    });
    fs.writeFileSync(registryPath, `${JSON.stringify(parsedRegistry, null, 2)}\n`, "utf8");

    const current = readBaseConfig();
    const builtinNames = new Set(parsedRegistry.tools.map((tool) => String(tool?.name || "").trim()).filter(Boolean));
    const normalizedCustom = normalizeCustomToolList(custom, builtinNames);
    const toolsConfig = isPlainObject(current.tools) ? current.tools : {};
    const updated = {
      ...current,
      tools: {
        ...toolsConfig,
        custom: normalizedCustom,
        overrides: getDefaultToolOverrides(),
      },
    };
    writeJsonFile(CONFIG_PATH, updated);
    refreshBackendFromConfig(updated);

    return { ok: true };
  } catch (error) {
    return { ok: false, error: error.message };
  }
});

ipcMain.handle("settings:saveToolOverrides", async (_event, { overrides, custom }) => {
  try {
    const current = readBaseConfig();
    const builtinNames = new Set(parseBuiltinToolNames());
    const normalizedOverrides = normalizeToolOverrides(overrides, builtinNames);
    const normalizedCustom = normalizeCustomToolList(custom, builtinNames);
    const toolsConfig = isPlainObject(current.tools) ? current.tools : {};
    const updated = {
      ...current,
      tools: {
        ...toolsConfig,
        custom: normalizedCustom,
        overrides: normalizedOverrides,
      },
    };
    writeJsonFile(CONFIG_PATH, updated);
    refreshBackendFromConfig(updated);
    return { ok: true };
  } catch (error) {
    return { ok: false, error: error.message };
  }
});

ipcMain.handle("settings:addCustomTool", async (_event, tool) => {
  try {
    const current = readBaseConfig();
    const builtinNames = new Set(parseBuiltinToolNames());
    const toolsConfig = isPlainObject(current.tools) ? current.tools : {};
    const existingCustom = Array.isArray(toolsConfig.custom) ? toolsConfig.custom : [];
    const existingNames = new Set(existingCustom.map((entry) => String(entry && entry.name || "").trim()).filter(Boolean));
    const normalized = validateCustomToolRecord(tool, builtinNames, existingNames);
    const updated = {
      ...current,
      tools: {
        ...toolsConfig,
        custom: [...existingCustom, normalized],
        overrides: normalizeToolOverrides(toolsConfig.overrides, builtinNames),
      },
    };
    writeJsonFile(CONFIG_PATH, updated);
    refreshBackendFromConfig(updated);
    return { ok: true };
  } catch (error) {
    return { ok: false, error: error.message };
  }
});

ipcMain.handle("settings:load", async () => {
  return loadConfig();
});

ipcMain.handle("settings:save", async (_event, _section, data) => {
  const updated = saveConfig(null, data);
  const mergedRuntimeConfig = loadConfig();
  refreshBackendFromConfig(updated);

  if (isPlainObject(data?.ui) && Object.prototype.hasOwnProperty.call(data.ui, "layer_visibility")) {
    const overlayWindow = getMainWindow();
    if (overlayWindow) {
      overlayWindow.webContents.send("ui:layerVisibilityUpdated", Boolean(data.ui.layer_visibility));
    }
  }

  if (isPlainObject(data?.ui) && Object.prototype.hasOwnProperty.call(data.ui, "idle_timeout_seconds")) {
    const overlayWindow = getMainWindow();
    if (overlayWindow) {
      const sec = Number(data.ui.idle_timeout_seconds) || 0;
      overlayWindow.webContents.send("ui:idleTimeoutChanged", sec * 1000);
    }
  }

  if (isPlainObject(data?.ui) && Object.prototype.hasOwnProperty.call(data.ui, "overlay_scale")) {
    const overlayWindow = getMainWindow();
    const scale = parseOverlayScaleValue(data.ui.overlay_scale, DEFAULT_OVERLAY_SCALE);
    const { width, height } = dimensionsFromOverlayScale(scale);
    saveConfig(null, {
      ui: {
        overlay_scale: scale,
        window_width: width,
        window_height: height,
      },
    });
    if (overlayWindow && !overlayWindow.isDestroyed()) {
      const bounds = overlayWindow.getBounds();
      overlayWindow.setBounds({
        x: bounds.x,
        y: bounds.y,
        width,
        height,
      });
      ensureWindowVisible(overlayWindow);
      scheduleVisibilityCheck(overlayWindow);
    }
  }

  if (isPlainObject(data?.companion)) {
    const overlayWindow = getMainWindow();
    if (overlayWindow) {
      overlayWindow.webContents.send("ui:companionNameUpdated", getCompanionNameFromConfig(mergedRuntimeConfig));
    }
  }

  return { ok: true };
});

ipcMain.handle("settings:configReload", async () => {
  try {
    backend.send({ type: "config_reload" });
    return { ok: true };
  } catch (error) {
    console.warn("Failed to send backend config_reload:", error.message);
    return { ok: false, error: error.message };
  }
});

ipcMain.handle("backend:dndStart", async (_event, until) => {
  try {
    const ts = Number(until);
    if (!Number.isFinite(ts) || ts <= 0) {
      throw new Error("dnd_start requires a valid Unix timestamp.");
    }
    backend.send({ type: "dnd_start", until: ts });
    return { ok: true };
  } catch (error) {
    console.warn("Failed to send dnd_start:", error.message);
    return { ok: false, error: error.message };
  }
});

ipcMain.handle("backend:dndCancel", async () => {
  try {
    backend.send({ type: "dnd_cancel" });
    return { ok: true };
  } catch (error) {
    console.warn("Failed to send dnd_cancel:", error.message);
    return { ok: false, error: error.message };
  }
});

ipcMain.handle("settings:getOllamaModels", async () => {
  try {
    return await ollamaRuntimeCache.listModelSummaries(readTestOllamaState());
  } catch {
    return [];
  }
});

ipcMain.handle("settings:getProviderModels", async (_event, providerName, options) => {
  return listProviderModels(providerName, options);
});

ipcMain.handle("settings:pullModel", async (event, modelName) => {
  pullAbortController = new AbortController();

  try {
    const testState = readTestOllamaState();
    if (testState) {
      const cleanName = String(modelName || "").trim();
      const plan = testState.pull?.[cleanName];
      if (!plan) {
        pullAbortController = null;
        return { error: `Model '${cleanName}' was not found.` };
      }
      if (plan.error) {
        pullAbortController = null;
        return { error: plan.error };
      }

      const chunks = Array.isArray(plan.chunks) ? plan.chunks : [];
      for (const chunk of chunks) {
        await delayWithAbort(chunk.delay_ms ?? 30, pullAbortController.signal);
        const percent = chunk.total
          ? Math.round((Number(chunk.completed || 0) / Number(chunk.total)) * 100)
          : null;
        event.sender.send("model:pullProgress", {
          status: chunk.status || "Downloading...",
          percent,
          completed_gb: chunk.completed ? (Number(chunk.completed) / 1e9).toFixed(1) : null,
          total_gb: chunk.total ? (Number(chunk.total) / 1e9).toFixed(1) : null,
        });
      }

      if (plan.final_model) {
        const nextModels = Array.isArray(testState.models) ? [...testState.models] : [];
        const existingIndex = nextModels.findIndex((entry) => entry?.name === plan.final_model.name);
        if (existingIndex >= 0) {
          nextModels[existingIndex] = plan.final_model;
        } else {
          nextModels.push(plan.final_model);
        }
        testState.models = nextModels;
      }
      if (plan.final_info && cleanName) {
        testState.show = isPlainObject(testState.show) ? testState.show : {};
        testState.show[cleanName] = plan.final_info;
      }
      if (plan.final_running_model) {
        const nextRunning = Array.isArray(testState.running_models) ? [...testState.running_models] : [];
        const existingIndex = nextRunning.findIndex((entry) => entry?.name === plan.final_running_model.name);
        if (existingIndex >= 0) {
          nextRunning[existingIndex] = plan.final_running_model;
        } else {
          nextRunning.push(plan.final_running_model);
        }
        testState.running_models = nextRunning;
      }
      writeTestOllamaState(testState);
      pullAbortController = null;
      return { success: true };
    }

    const response = await fetch("http://localhost:11434/api/pull", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: modelName, stream: true }),
      signal: pullAbortController.signal,
    });

    const reader = response.body?.getReader();
    const decoder = new TextDecoder();
    if (!reader) {
      throw new Error("Pull stream is unavailable.");
    }

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const lines = decoder.decode(value, { stream: true }).split("\n").filter(Boolean);
      for (const line of lines) {
        try {
          const data = JSON.parse(line);
          const percent = data.total
            ? Math.round((data.completed / data.total) * 100)
            : null;

          event.sender.send("model:pullProgress", {
            status: data.status,
            percent,
            completed_gb: data.completed ? (data.completed / 1e9).toFixed(1) : null,
            total_gb: data.total ? (data.total / 1e9).toFixed(1) : null,
          });
        } catch {
          // Ignore malformed stream chunks.
        }
      }
    }

    pullAbortController = null;
    return { success: true };
  } catch (error) {
    pullAbortController = null;
    if (error.name === "AbortError") {
      return { cancelled: true };
    }
    return { error: error.message };
  }
});

ipcMain.handle("settings:cancelPull", async () => {
  pullAbortController?.abort();
  pullAbortController = null;
  return { ok: true };
});

ipcMain.handle("settings:deleteModel", async (_event, modelName) => {
  const testState = readTestOllamaState();
  if (testState) {
    const cleanName = String(modelName || "").trim();
    testState.models = Array.isArray(testState.models)
      ? testState.models.filter((entry) => String(entry?.name || "").trim() !== cleanName)
      : [];
    testState.running_models = Array.isArray(testState.running_models)
      ? testState.running_models.filter((entry) => String(entry?.name || "").trim() !== cleanName)
      : [];
    if (isPlainObject(testState.show)) {
      delete testState.show[cleanName];
    }
    writeTestOllamaState(testState);
    return { success: true };
  }
  try {
    const response = await fetch("http://localhost:11434/api/delete", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: modelName }),
    });
    return { success: response.ok };
  } catch (error) {
    return { error: error.message };
  }
});

ipcMain.handle("settings:getWindowsAccent", async () => {
  try {
    const hex8 = systemPreferences.getAccentColor();
    const r = parseInt(hex8.slice(2, 4), 16);
    const g = parseInt(hex8.slice(4, 6), 16);
    const b = parseInt(hex8.slice(6, 8), 16);
    return [r, g, b];
  } catch {
    return null;
  }
});

ipcMain.handle("settings:applyTheme", async (event, themeData) => {
  for (const win of BrowserWindow.getAllWindows()) {
    if (!win.isDestroyed() && win.webContents.id !== event.sender.id) {
      win.webContents.send("theme:apply", themeData);
    }
  }
  return { ok: true };
});

ipcMain.handle("update:download", async () => {
  return downloadAppUpdate();
});

ipcMain.handle("update:install", async () => {
  return installAppUpdate();
});

ipcMain.handle("updater:checkNow", async () => {
  try {
    await checkForAppUpdates();
  } catch (_error) {
    // The updater controller stores the user-facing error message in status.
  }

  const status = getUpdaterStatus();
  const current = readBaseConfig();
  const updaterConfig = isPlainObject(current.updater) ? current.updater : DEFAULT_CONFIG.updater;
  saveConfig("updater", {
    ...updaterConfig,
    last_check_ts: Number(status.lastCheckedAt || Date.now()),
  });
  return getUpdaterStatus();
});

ipcMain.handle("updater:status", async () => {
  return getUpdaterStatus();
});

ipcMain.handle("update:dismiss", async (_event, version) => {
  const status = dismissReadyAppUpdate(version);
  const current = readBaseConfig();
  const updaterConfig = isPlainObject(current.updater) ? current.updater : DEFAULT_CONFIG.updater;
  saveConfig("updater", {
    ...updaterConfig,
    dismissed_version: String(version || status.downloadedVersion || status.latestVersion || ""),
    last_check_ts: Number(status.lastCheckedAt || Date.now()),
  });
  return { ok: true, status };
});

ipcMain.handle("assets:status", async () => {
  return runtimeAssetManager.getStatus();
});

ipcMain.handle("assets:checkNow", async () => {
  return runtimeAssetManager.checkNow();
});

ipcMain.handle("heartbeat:restart", async () => {
  backend.send({ type: "heartbeat_restart" });
  return { ok: true };
});

ipcMain.handle("heartbeat:start", async () => {
  backend.send({ type: "heartbeat_start" });
  return { ok: true };
});

ipcMain.handle("heartbeat:stop", async () => {
  backend.send({ type: "heartbeat_stop" });
  return { ok: true };
});

ipcMain.handle("settings:close", async (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (win && !win.isDestroyed()) {
    if (win === settingsWindow && !TEST_MODE) {
      settingsWindowPendingShow = false;
      win.hide();
      win.setSkipTaskbar(true);
      return;
    }
    win.close();
  }
});

if (TEST_MODE) {
ipcMain.handle("test:getBackendDiagnostics", async () => ({
  latestState: backend.latestState,
  recentStderrLines: [...backend.recentStderrLines],
  sentMessages: [...testDiagnostics.sentMessages],
  emittedEvents: [...testDiagnostics.emittedEvents],
}));

  ipcMain.handle("test:getProfileInfo", async () => ({
    profileRoot: PROFILE_ROOT,
    configPath: CONFIG_PATH,
    localConfigPath: path.join(PROFILE_ROOT, "config.local.json"),
    memoryDir: MEMORY_DIR,
    soulActiveDir: SOUL_ACTIVE_DIR,
    keychainService: KEYCHAIN_SERVICE,
  }));
}

ipcMain.handle("onboarding:close", async (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (win && !win.isDestroyed()) {
    win.close();
  }
});

ipcMain.handle("runtime:status", async (_event, payload) => {
  try {
    return await runtimeManager.refreshStatus(buildRuntimeConfigFromOnboardingPayload(payload));
  } catch (err) {
    console.error("[runtime:status] Unexpected error:", err);
    return runtimeManager.getStatus();
  }
});
ipcMain.handle("runtime:prepare", async (_event, payload) => {
  try {
    return await runtimeManager.prepare(buildRuntimeConfigFromOnboardingPayload(payload));
  } catch (err) {
    console.error("[runtime:prepare] Unexpected error:", err);
    return runtimeManager.getStatus();
  }
});
ipcMain.handle("runtime:cancel", async () => {
  try {
    return await runtimeManager.cancel();
  } catch (err) {
    console.error("[runtime:cancel] Unexpected error:", err);
    return runtimeManager.getStatus();
  }
});
ipcMain.handle("runtime:retry", async (_event, payload) => {
  try {
    return await runtimeManager.retry(buildRuntimeConfigFromOnboardingPayload(payload));
  } catch (err) {
    console.error("[runtime:retry] Unexpected error:", err);
    return runtimeManager.getStatus();
  }
});

ipcMain.handle("onboarding:load", async () => loadConfig());

ipcMain.handle("onboarding:getConfig", async () => loadConfig());

function buildOnboardingSetConfigPatch(keyOrPatch, value) {
  if (keyOrPatch && typeof keyOrPatch === "object" && !Array.isArray(keyOrPatch)) {
    return keyOrPatch;
  }
  const key = String(keyOrPatch || "").trim();
  if (!key) {
    return {};
  }
  if (key === "models.downloaded" || key === "onboarding.modelsDownloaded") {
    return {
      onboarding: {
        modelsDownloaded: Array.isArray(value)
          ? value.map((model) => String(model || "").trim()).filter(Boolean)
          : [],
      },
    };
  }
  if (key === "onboarding.completed") {
    return {
      onboarding: {
        completed: Boolean(value),
      },
      onboarding_complete: Boolean(value),
    };
  }

  const parts = key.split(".").map((part) => part.trim()).filter(Boolean);
  if (!parts.length) {
    return {};
  }
  const patch = {};
  let cursor = patch;
  for (const part of parts.slice(0, -1)) {
    cursor[part] = {};
    cursor = cursor[part];
  }
  cursor[parts[parts.length - 1]] = value;
  return patch;
}

ipcMain.handle("onboarding:setConfig", async (_event, keyOrPatch, value) => {
  const patch = buildOnboardingSetConfigPatch(keyOrPatch, value);
  const updated = saveConfig(null, patch);
  const mergedRuntimeConfig = loadConfig();

  backend.latestState = {
    ...backend.latestState,
    companionName: getCompanionNameFromConfig(mergedRuntimeConfig),
    config: mergedRuntimeConfig,
  };

  try {
    backend.send({ type: "config_reload" });
  } catch (error) {
    console.warn("Failed to send backend config_reload after onboarding config update:", error.message);
  }

  return { ok: true, config: updated };
});

ipcMain.handle("onboarding:save", async (_event, payload) => {
  const patch = createOnboardingConfigPatch(payload);
  const updated = saveConfig(null, patch);
  const mergedRuntimeConfig = loadConfig();

  backend.latestState = {
    ...backend.latestState,
    companionName: getCompanionNameFromConfig(mergedRuntimeConfig),
    config: mergedRuntimeConfig,
  };

  try {
    await generateSoulFilesFromConfig(updated, {
      preserveCompanion: Boolean(
        readOnboardingValue(payload, "preserve_imported_soul", "preserveImportedSoul")
      ),
    });
  } catch (error) {
    console.warn("Failed to generate onboarding soul files:", error.message);
    return { ok: false, error: error.message, config: updated };
  }

  try {
    backend.send({ type: "config_reload" });
  } catch (error) {
    console.warn("Failed to send backend config_reload after onboarding save:", error.message);
  }

  return { ok: true, config: updated };
});

ipcMain.handle("onboarding:resetMemory", async () => {
  try {
    resetLongTermMemory();
    return { ok: true };
  } catch (error) {
    return { ok: false, error: String(error?.message || error || "Could not reset long-term memory.") };
  }
});

ipcMain.handle("onboarding:parseImport", async (_event, text) => {
  return parseOnboardingImport(text);
});

ipcMain.handle("onboarding:checkOllama", async () => {
  const testState = readTestOllamaState();
  if (testState) {
    return {
      available: testState.available !== false,
      models: Array.isArray(testState.models)
        ? testState.models.map((m) => m.name).filter(Boolean)
        : [],
      installed: testState.installed !== false,
    };
  }
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 3000);
    const resp = await fetch("http://localhost:11434/api/tags", { signal: controller.signal });
    clearTimeout(timeoutId);
    if (!resp.ok) return { available: false, models: [], installed: true };
    const json = await resp.json();
    const models = (json.models || []).map((m) => m.name).filter(Boolean);
    return { available: true, models, installed: true };
  } catch {
    const installed = await isOllamaInstalled();
    return { available: false, models: [], installed };
  }
});

ipcMain.handle("onboarding:check-models", async (_event, payload = {}) => {
  try {
    const requestPayload = { type: "check_models" };
    const requestedModels = Array.isArray(payload)
      ? payload
      : Array.isArray(payload?.tags)
        ? payload.tags
        : Array.isArray(payload?.models)
          ? payload.models
          : null;
    if (requestedModels) {
      requestPayload.tags = requestedModels;
    }
    const result = await backend.request(requestPayload, 30000);
    if (!requestedModels && Array.isArray(result?.available_models)) {
      return {
        ...result,
        models: result.available_models,
      };
    }
    return result;
  } catch (error) {
    return { ok: false, error: String(error?.message || error || "Could not check onboarding models.") };
  }
});

ipcMain.handle("onboarding:pull-model", async (_event, payload = {}) => {
  const model = String(payload?.tag || payload?.model || payload?.name || payload || "").trim();
  if (!model) {
    return { ok: false, error: "Model name is required." };
  }
  try {
    await backend.waitUntilReady();
    backend.send({
      type: "pull_model",
      tag: model,
    });
    return { ok: true, started: true, tag: model };
  } catch (error) {
    return { ok: false, error: String(error?.message || error || "Could not pull onboarding model.") };
  }
});

ipcMain.handle("onboarding:writeImport", async (_event, payload) => {
  const parsed = writeOnboardingImportFiles(payload);
  return { ok: true, ...parsed };
});

async function completeOnboarding() {
  const runtimeStatus = await runtimeManager.prepare();
  logMain("INFO", "Completing onboarding.", {
    runtimeReady: runtimeStatus?.ready === true,
    runtimeState: runtimeStatus?.state || "",
  });
  if (runtimeStatus?.ready !== true) {
    return {
      ok: false,
      error: runtimeStatus?.last_error || runtimeStatus?.lastError || "Runtime preparation did not complete.",
    };
  }

  const updated = saveConfig(null, {
    onboarding_complete: true,
    onboarding_reset_memory_on_next_launch: false,
    onboarding: {
      completed: true,
    },
  });
  const mergedRuntimeConfig = loadConfig();

  backend.latestState = {
    ...backend.latestState,
    companionName: getCompanionNameFromConfig(mergedRuntimeConfig),
    config: mergedRuntimeConfig,
  };

  try {
    backend.send({ type: "config_reload" });
  } catch (error) {
    console.warn("Failed to send backend config_reload after onboarding completion:", error.message);
  }

  const onboardingWin = getOnboardingWindow();
  if (onboardingWin) {
    onboardingWin.close();
  }

  if (!backend.isRunning()) {
    backend.start();
  }
  startMainOverlay();
  logMain("INFO", "Onboarding completion handed off to overlay.", {
    backendRunning: backend.isRunning(),
    backendReady: backend.ready,
    latestState: backend.latestState.state,
  });
  return { ok: true };
}

ipcMain.handle("onboarding:complete", async () => completeOnboarding());
ipcMain.handle("onboarding:finishSetup", async () => completeOnboarding());

ipcMain.handle("settings:generateSoulFile", async () => {
  const updated = loadConfig();
  await generateSoulFilesFromConfig(updated);
  return { ok: true };
});

ipcMain.handle("settings:readSoulFile", async () => {
  const filePath = path.join(SOUL_ACTIVE_DIR, "soul_companion.md");
  try {
    if (!fs.existsSync(filePath)) return { ok: true, content: "" };
    const content = fs.readFileSync(filePath, "utf8");
    return { ok: true, content };
  } catch (err) {
    return { ok: false, error: err.message };
  }
});

ipcMain.handle("settings:writeSoulFile", async (_event, { content }) => {
  const filePath = path.join(SOUL_ACTIVE_DIR, "soul_companion.md");
  try {
    fs.mkdirSync(SOUL_ACTIVE_DIR, { recursive: true });
    fs.writeFileSync(filePath, normalizeMarkdownFileContent(String(content || "")), "utf8");
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err.message };
  }
});

ipcMain.handle("settings:readSoulGenerateTemplate", async () => {
  const filePath = path.join(READONLY_PROJECT_ROOT, "companion", "soul", "defaults", "generate.md");
  try {
    if (!fs.existsSync(filePath)) return { ok: true, content: "" };
    const content = fs.readFileSync(filePath, "utf8");
    return { ok: true, content };
  } catch (err) {
    return { ok: false, error: err.message };
  }
});

registerVoicePreviewIpc(
  ipcMain,
  createVoicePreviewService({
    projectRoot: PROJECT_ROOT,
    runtimePaths,
    buildPythonSubprocessEnv,
  })
);

app.on("before-quit", (event) => {
  if (isQuitting) {
    return;
  }

  event.preventDefault();
  isQuitting = true;
  globalShortcut.unregisterAll();
  backend.shutdown().finally(() => {
    app.exit(0);
  });
});

// ─── ChatGPT Plus OAuth ────────────────────────────────────────────────────

const CHATGPT_OAUTH_DEFAULT_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann";
const CHATGPT_OAUTH_CLIENT_ID = String(
  process.env.CHATGPT_OAUTH_CLIENT_ID || CHATGPT_OAUTH_DEFAULT_CLIENT_ID
).trim();
const CHATGPT_OAUTH_AUTH_URL = "https://auth.openai.com/oauth/authorize";
const CHATGPT_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token";
const CHATGPT_OAUTH_REDIRECT_URI = "http://localhost:1455/auth/callback";
const CHATGPT_OAUTH_CALLBACK_PORT = 1455;
const CHATGPT_OAUTH_SCOPE = "openid profile email offline_access";
const CHATGPT_OAUTH_REQUIRED_SCOPES = [];
const CHATGPT_OAUTH_ORIGINATOR = process.env.CHATGPT_OAUTH_ORIGINATOR || "opencompanion";
const CHATGPT_OAUTH_AUTH_CLAIM = "https://api.openai.com/auth";
function buildProfileScopedKeychainService(suffix) {
  const profileRoot = path.resolve(runtimePaths.PROFILE_ROOT || runtimePaths.PROJECT_ROOT || ".");
  const digest = crypto.createHash("sha256").update(profileRoot.toLowerCase()).digest("hex").slice(0, 12);
  return `${KEYCHAIN_SERVICE}:${suffix}:${digest}`;
}

const CHATGPT_KR_SERVICE = String(
  process.env.OPEN_COMPANION_CHATGPT_OAUTH_KEYCHAIN_SERVICE ||
  buildProfileScopedKeychainService("chatgpt-oauth")
).trim();
const CHATGPT_KR_DELETE_SERVICES = Array.from(new Set([
  CHATGPT_KR_SERVICE,
  KEYCHAIN_SERVICE,
  LEGACY_KEYCHAIN_SERVICE,
]));
const CHATGPT_KR_ACCESS = "chatgpt_oauth_access";
const CHATGPT_KR_REFRESH = "chatgpt_oauth_refresh";
const CHATGPT_KR_EXPIRES = "chatgpt_oauth_expires";
const CHATGPT_KR_ACCOUNT = "chatgpt_oauth_account_id";
const CHATGPT_KR_ID_TOKEN = "chatgpt_oauth_id_token";
const CHATGPT_KR_API_KEY = "chatgpt_oauth_api_key";
const CHATGPT_KR_ACCOUNTS = [
  CHATGPT_KR_ACCESS,
  CHATGPT_KR_REFRESH,
  CHATGPT_KR_EXPIRES,
  CHATGPT_KR_ACCOUNT,
  CHATGPT_KR_ID_TOKEN,
  CHATGPT_KR_API_KEY,
];
process.env.OPEN_COMPANION_CHATGPT_OAUTH_KEYCHAIN_SERVICE = CHATGPT_KR_SERVICE;

function requireChatGptOauthClientId() {
  if (CHATGPT_OAUTH_CLIENT_ID) {
    return CHATGPT_OAUTH_CLIENT_ID;
  }
  throw new Error(
    "CHATGPT_OAUTH_CLIENT_ID is not set. Add it to your environment or .env and restart OpenCompanion."
  );
}

function _chatgptBase64url(buf) {
  return buf.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
}

function _chatgptDecodeJwtPayload(token) {
  try {
    const parts = token.split(".");
    if (parts.length < 2) return null;
    const padded = parts[1] + "=".repeat((4 - (parts[1].length % 4)) % 4);
    return JSON.parse(Buffer.from(padded, "base64").toString("utf8"));
  } catch {
    return null;
  }
}

function _chatgptExtractAccountId(accessToken) {
  const payload = _chatgptDecodeJwtPayload(accessToken);
  const authClaims = payload?.[CHATGPT_OAUTH_AUTH_CLAIM];
  const accountId = String(authClaims?.chatgpt_account_id || "").trim();
  if (accountId) {
    return accountId;
  }
  return String(payload?.sub || "").trim();
}

function _chatgptTokenScopes(accessToken) {
  const payload = _chatgptDecodeJwtPayload(accessToken);
  const rawScopes = payload?.scp || payload?.scope || payload?.scopes;
  if (Array.isArray(rawScopes)) {
    return rawScopes.map((scope) => String(scope || "").trim()).filter(Boolean);
  }
  if (typeof rawScopes === "string") {
    return rawScopes.split(/\s+/).map((scope) => scope.trim()).filter(Boolean);
  }
  return [];
}

function _chatgptMissingRequiredScopes(accessToken) {
  const scopes = new Set(_chatgptTokenScopes(accessToken));
  return CHATGPT_OAUTH_REQUIRED_SCOPES.filter((scope) => !scopes.has(scope));
}

function _chatgptEscapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function _chatgptAllowedCallbackHost(req) {
  const rawHost = String(req?.headers?.host || "").trim();
  if (!rawHost) {
    return false;
  }
  try {
    const hostname = new URL(`http://${rawHost}`).hostname.toLowerCase();
    return ["localhost", "127.0.0.1", "::1", "[::1]"].includes(hostname);
  } catch {
    return false;
  }
}

function _chatgptSendCallbackPage(res, ok, message) {
  const title = ok ? "Connected" : "Connection failed";
  const color = ok ? "#2f8f58" : "#a43d3d";
  res.writeHead(ok ? 200 : 400, { "Content-Type": "text/html; charset=utf-8" });
  res.end(
    "<!doctype html><html><head><meta charset='utf-8'><title>OpenCompanion OAuth</title></head>" +
    "<body style='font-family:sans-serif;text-align:center;padding:60px;background:#111;color:#f4f0ff'>" +
    `<h2 style='color:${color}'>${_chatgptEscapeHtml(title)}</h2>` +
    `<p>${_chatgptEscapeHtml(message)}</p>` +
    "<p>You can close this tab and return to OpenCompanion.</p>" +
    "</body></html>"
  );
}

async function _chatgptReadSecret(account) {
  const value = await readKeychainPassword(CHATGPT_KR_SERVICE, account);
  if (value != null && String(value).trim()) {
    return String(value);
  }
  return null;
}

async function _chatgptWriteSecret(account, value) {
  const cleanValue = String(value || "").trim();
  if (cleanValue) {
    await keytar.setPassword(CHATGPT_KR_SERVICE, account, cleanValue);
  } else {
    await deleteKeychainPassword(CHATGPT_KR_SERVICE, account);
  }
  await Promise.all(
    CHATGPT_KR_DELETE_SERVICES
      .filter((service) => service !== CHATGPT_KR_SERVICE)
      .map((service) => deleteKeychainPassword(service, account))
  );
}

async function _chatgptReadTokenStore() {
  const [access, refresh, expires, accountId] = await Promise.all([
    _chatgptReadSecret(CHATGPT_KR_ACCESS),
    _chatgptReadSecret(CHATGPT_KR_REFRESH),
    _chatgptReadSecret(CHATGPT_KR_EXPIRES),
    _chatgptReadSecret(CHATGPT_KR_ACCOUNT),
  ]);
  return { access, refresh, expires, accountId };
}

async function _chatgptWriteTokenStore({ accessToken, refreshToken, expiresMs, accountId }) {
  await Promise.all([
    _chatgptWriteSecret(CHATGPT_KR_ACCESS, accessToken),
    _chatgptWriteSecret(CHATGPT_KR_REFRESH, refreshToken),
    _chatgptWriteSecret(CHATGPT_KR_EXPIRES, String(expiresMs || "")),
    _chatgptWriteSecret(CHATGPT_KR_ACCOUNT, accountId),
    _chatgptWriteSecret(CHATGPT_KR_ID_TOKEN, ""),
    _chatgptWriteSecret(CHATGPT_KR_API_KEY, ""),
  ]);
}

async function _chatgptDeleteTokenStore() {
  await Promise.all(
    CHATGPT_KR_ACCOUNTS.flatMap((account) => (
      CHATGPT_KR_DELETE_SERVICES.map((service) => deleteKeychainPassword(service, account))
    ))
  );
}

async function _chatgptExchangeCode(code, codeVerifier) {
  const clientId = requireChatGptOauthClientId();
  const body = new URLSearchParams({
    grant_type: "authorization_code",
    code,
    redirect_uri: CHATGPT_OAUTH_REDIRECT_URI,
    client_id: clientId,
    code_verifier: codeVerifier,
  });
  const resp = await fetch(CHATGPT_OAUTH_TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    if (resp.status === 403 && /region|territory|country/i.test(text)) {
      throw new Error("ChatGPT OAuth is not available in your region.");
    }
    throw new Error(`Token exchange failed (${resp.status}): ${text.slice(0, 200)}`);
  }
  const tokens = await resp.json();
  const expiresIn = Number(tokens?.expires_in);
  if (!tokens?.access_token || !tokens?.refresh_token || !Number.isFinite(expiresIn) || expiresIn <= 0) {
    throw new Error("Token response missing required Codex OAuth fields.");
  }
  return tokens;
}

ipcMain.handle("oauth:chatgpt:login", async () => {
  console.info("[ChatGPT OAuth] Login requested");
  const clientId = requireChatGptOauthClientId();
  // Generate PKCE pair
  const codeVerifier = _chatgptBase64url(crypto.randomBytes(32));
  const codeChallenge = _chatgptBase64url(
    crypto.createHash("sha256").update(codeVerifier).digest()
  );
  const state = _chatgptBase64url(crypto.randomBytes(16));

  const authUrl = new URL(CHATGPT_OAUTH_AUTH_URL);
  authUrl.searchParams.set("client_id", clientId);
  authUrl.searchParams.set("redirect_uri", CHATGPT_OAUTH_REDIRECT_URI);
  authUrl.searchParams.set("response_type", "code");
  authUrl.searchParams.set("scope", CHATGPT_OAUTH_SCOPE);
  authUrl.searchParams.set("code_challenge", codeChallenge);
  authUrl.searchParams.set("code_challenge_method", "S256");
  authUrl.searchParams.set("state", state);
  authUrl.searchParams.set("id_token_add_organizations", "true");
  authUrl.searchParams.set("codex_cli_simplified_flow", "true");
  authUrl.searchParams.set("originator", CHATGPT_OAUTH_ORIGINATOR);

  return new Promise((resolve) => {
    let server = null;
    let settled = false;
    let timeout = null;

    const settle = (result) => {
      if (settled) {
        return;
      }
      settled = true;
      if (timeout) {
        clearTimeout(timeout);
      }
      try { server?.close(); } catch {}
      resolve(result);
    };

    timeout = setTimeout(() => {
      console.warn("[ChatGPT OAuth] Login timed out waiting for callback");
      settle({ ok: false, error: "OAuth login timed out (2 minutes). Please try again." });
    }, 120_000);

    try {
      server = http.createServer(async (req, res) => {
        console.info(`[ChatGPT OAuth] Callback request received: ${req.url || ""}`);
        if (!_chatgptAllowedCallbackHost(req)) {
          res.writeHead(403, { "Content-Type": "text/plain; charset=utf-8" });
          res.end("Forbidden OAuth callback host.");
          return;
        }

        const url = new URL(req.url || "/", CHATGPT_OAUTH_REDIRECT_URI);
        if (url.pathname !== "/auth/callback") {
          res.writeHead(404);
          res.end();
          return;
        }

        const code = url.searchParams.get("code");
        const returnedState = url.searchParams.get("state");
        const error = url.searchParams.get("error");

        if (error) {
          console.warn(`[ChatGPT OAuth] Provider returned error: ${error}`);
          const message = `OAuth error: ${error}`;
          _chatgptSendCallbackPage(res, false, message);
          settle({ ok: false, error: message });
          return;
        }
        if (!code) {
          console.warn("[ChatGPT OAuth] Callback missing authorization code");
          const message = "No authorization code in callback.";
          _chatgptSendCallbackPage(res, false, message);
          settle({ ok: false, error: message });
          return;
        }
        if (returnedState !== state) {
          console.warn("[ChatGPT OAuth] State mismatch during callback");
          const message = "OAuth state mismatch - possible CSRF. Please try again.";
          _chatgptSendCallbackPage(res, false, message);
          settle({ ok: false, error: message });
          return;
        }
        try {
          console.info("[ChatGPT OAuth] Exchanging authorization code for tokens");
          const tokens = await _chatgptExchangeCode(code, codeVerifier);
          const accessToken = String(tokens.access_token || "");
          const refreshToken = String(tokens.refresh_token || "");
          const expiresIn = Number(tokens.expires_in || 3600);
          const expiresMs = Date.now() + expiresIn * 1000;

          // Codex OAuth stores the stable ChatGPT account id from the access token.
          const accountId = _chatgptExtractAccountId(accessToken);

          await _chatgptWriteTokenStore({ accessToken, refreshToken, expiresMs, accountId });

          console.info(`[ChatGPT OAuth] Tokens stored successfully. refresh=${refreshToken ? "yes" : "no"} account=${accountId ? "yes" : "no"}`);
          _chatgptSendCallbackPage(res, true, "Your ChatGPT account is connected.");
          settle({ ok: true, accountId, expiresMs });
        } catch (err) {
          console.error("[ChatGPT OAuth] Login failed during token exchange or keyring write:", err);
          const message = err.message || "Token exchange failed.";
          _chatgptSendCallbackPage(res, false, message);
          settle({ ok: false, error: message });
        }
      });

      server.on("error", (err) => {
        console.error("[ChatGPT OAuth] Callback server error:", err);
        if (err.code === "EADDRINUSE") {
          settle({ ok: false, error: `Port ${CHATGPT_OAUTH_CALLBACK_PORT} is already in use. Close the conflicting app and try again.` });
        } else {
          settle({ ok: false, error: `OAuth callback server error: ${err.message}` });
        }
      });

      server.listen(CHATGPT_OAUTH_CALLBACK_PORT, () => {
        console.info(`[ChatGPT OAuth] Callback server listening on ${CHATGPT_OAUTH_REDIRECT_URI}`);
        shell.openExternal(authUrl.toString()).catch((err) => {
          console.error("[ChatGPT OAuth] Failed to open browser:", err);
          settle({ ok: false, error: `Failed to open browser: ${err.message}` });
        });
      });
    } catch (err) {
      console.error("[ChatGPT OAuth] Login handler setup failed:", err);
      settle({ ok: false, error: err.message });
    }
  });
});

ipcMain.handle("oauth:chatgpt:logout", async () => {
  try {
    console.info("[ChatGPT OAuth] Logout requested");
    await _chatgptDeleteTokenStore();
    console.info("[ChatGPT OAuth] Credentials cleared from keyring");
    return { ok: true };
  } catch (err) {
    console.error("[ChatGPT OAuth] Logout failed:", err);
    return { ok: false, error: err.message };
  }
});

ipcMain.handle("oauth:chatgpt:status", async () => {
  try {
    const {
      access,
      refresh,
      expires: expiresStr,
      accountId: storedAccountId,
    } = await _chatgptReadTokenStore();

    if (!access && !refresh) {
      console.info("[ChatGPT OAuth] Status requested: no stored credentials");
      return { connected: false };
    }

    // Decode exp from JWT for accurate expiry
    let expiresMs = expiresStr ? Number(expiresStr) : 0;
    if (access) {
      const payload = _chatgptDecodeJwtPayload(access);
      if (payload?.exp) {
        expiresMs = payload.exp * 1000;
      }
    }

    const fresh = expiresMs > Date.now() + 5 * 60 * 1000;
    const missingScopes = access ? _chatgptMissingRequiredScopes(access) : [];
    const reconnectRequired = missingScopes.length > 0;
    const connected = (fresh || !!refresh) && !reconnectRequired;
    console.info(`[ChatGPT OAuth] Status requested: connected=${connected} fresh=${fresh} hasRefresh=${!!refresh} missingScopes=${missingScopes.join(",") || "none"}`);
    return {
      connected,
      fresh,
      expiresMs,
      accountId: String(storedAccountId || _chatgptExtractAccountId(access) || "").trim(),
      hasRefresh: !!refresh,
      reconnectRequired,
      missingScopes,
    };
  } catch (err) {
    console.error("[ChatGPT OAuth] Status read failed:", err);
    return { connected: false, error: err.message };
  }
});

// ─── End ChatGPT Plus OAuth ────────────────────────────────────────────────

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", () => {
  appIsQuitting = true;
});

app.on("activate", () => {
  if (getMainWindow() || getOnboardingWindow()) {
    return;
  }
  if (loadConfig().onboarding_complete) {
    startMainOverlay();
  } else {
    createOnboardingWindow();
  }
});
