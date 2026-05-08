const fs = require("fs");
const path = require("path");
const { app } = require("electron");

const APP_NAME = "OpenCompanion";
const PROJECT_ROOT = path.resolve(__dirname, "..", "..");
const IS_PACKAGED = Boolean(app && app.isPackaged && !process.env.OPEN_COMPANION_FORCE_DEV);

function resolveRuntimeResourcePath(...segments) {
  if (!IS_PACKAGED) {
    return path.join(PROJECT_ROOT, ...segments);
  }
  return path.join(process.resourcesPath, "open-companion-runtime", ...segments);
}

function resolveDefaultProfileRoot() {
  if (process.env.OPEN_COMPANION_PROFILE_DIR) {
    return path.resolve(process.env.OPEN_COMPANION_PROFILE_DIR);
  }
  if (process.env.OPEN_COMPANION_TEST_PROFILE_DIR) {
    return path.resolve(process.env.OPEN_COMPANION_TEST_PROFILE_DIR);
  }
  if (!IS_PACKAGED) {
    return PROJECT_ROOT;
  }
  if (app && typeof app.getPath === "function") {
    try {
      return path.resolve(app.getPath("userData"));
    } catch (_error) {
      // Fall back to environment-based resolution below.
    }
  }

  const appDataRoot = process.env.APPDATA || process.env.LOCALAPPDATA || PROJECT_ROOT;
  return path.resolve(appDataRoot, APP_NAME);
}

const PROFILE_ROOT = resolveDefaultProfileRoot();
const RESOURCE_ROOT = IS_PACKAGED
  ? resolveRuntimeResourcePath()
  : PROJECT_ROOT;
const READONLY_PROJECT_ROOT = IS_PACKAGED
  ? resolveRuntimeResourcePath("project")
  : PROJECT_ROOT;

const RUNTIME_ASSETS_DIR = path.join(PROFILE_ROOT, "runtime-assets");
const INSTALL_CACHE_DIR = path.join(PROFILE_ROOT, "install-cache");
const LOGS_DIR = path.join(PROFILE_ROOT, "logs");
const MEMORY_DIR = path.join(PROFILE_ROOT, "companion", "memory");
const SESSION_SUMMARIES_DIR = path.join(MEMORY_DIR, "session_summaries");
const SOUL_ACTIVE_DIR = path.join(PROFILE_ROOT, "companion", "soul", "active");
const VAULT_DIR = path.join(PROFILE_ROOT, "companion", "vault");
const AVATAR_ASSETS_DIR = path.join(PROFILE_ROOT, "companion", "assets", "avatar");
const KOKORO_DIR = path.join(RUNTIME_ASSETS_DIR, "kokoro");
const OLLAMA_DIR = path.join(RUNTIME_ASSETS_DIR, "ollama");
const OLLAMA_MODELS_DIR = path.join(PROFILE_ROOT, "ollama-models");
const LEGACY_MODELS_DIR = path.join(PROJECT_ROOT, "models");
const VOICE_PREVIEWS_DIR = IS_PACKAGED
  ? resolveRuntimeResourcePath("companion", "assets", "voices")
  : path.join(PROJECT_ROOT, "companion", "assets", "voices");

const BACKEND_PYTHON_ENTRY = path.join(PROJECT_ROOT, "app", "backend", "wrapper.py");
const BACKEND_GENERATE_PREVIEW_ENTRY = path.join(PROJECT_ROOT, "app", "backend", "generate_voice_preview.py");
const BACKEND_BUNDLED_ENTRY = resolveRuntimeResourcePath("backend", "open-companion-backend.exe");
const BUILTIN_REGISTRY_SOURCE_PATH = path.join(READONLY_PROJECT_ROOT, "app", "backend", "tools", "registry.json");
const DEFAULT_KOKORO_MODEL_PATH = path.join(KOKORO_DIR, "kokoro-v1.0.onnx");
const DEFAULT_KOKORO_VOICES_PATH = path.join(KOKORO_DIR, "voices-v1.0.bin");
const OLLAMA_EMBEDDED_ENTRY = path.join(OLLAMA_DIR, "ollama.exe");
const LEGACY_KOKORO_MODEL_PATH = path.join(LEGACY_MODELS_DIR, "kokoro-v1.0.onnx");
const LEGACY_KOKORO_VOICES_PATH = path.join(LEGACY_MODELS_DIR, "voices-v1.0.bin");
const KOKORO_MODEL_PATH = !IS_PACKAGED && fs.existsSync(LEGACY_KOKORO_MODEL_PATH)
  ? LEGACY_KOKORO_MODEL_PATH
  : DEFAULT_KOKORO_MODEL_PATH;
const KOKORO_VOICES_PATH = !IS_PACKAGED && fs.existsSync(LEGACY_KOKORO_VOICES_PATH)
  ? LEGACY_KOKORO_VOICES_PATH
  : DEFAULT_KOKORO_VOICES_PATH;

function ensureRuntimeDirectories() {
  const dirs = [
    PROFILE_ROOT,
    RUNTIME_ASSETS_DIR,
    INSTALL_CACHE_DIR,
    LOGS_DIR,
    MEMORY_DIR,
    SESSION_SUMMARIES_DIR,
    SOUL_ACTIVE_DIR,
    VAULT_DIR,
    AVATAR_ASSETS_DIR,
    KOKORO_DIR,
    OLLAMA_DIR,
    OLLAMA_MODELS_DIR,
  ];
  for (const dir of dirs) {
    fs.mkdirSync(dir, { recursive: true });
  }
}

function getOllamaExecutablePath() {
  return fs.existsSync(OLLAMA_EMBEDDED_ENTRY)
    ? OLLAMA_EMBEDDED_ENTRY
    : "ollama";
}

function buildBackendEnvironment(extra = {}) {
  return {
    ...process.env,
    OPEN_COMPANION_PROFILE_DIR: PROFILE_ROOT,
    OPEN_COMPANION_PROJECT_ROOT: READONLY_PROJECT_ROOT,
    OPEN_COMPANION_RUNTIME_ASSETS_DIR: RUNTIME_ASSETS_DIR,
    OPEN_COMPANION_KOKORO_MODEL_PATH: KOKORO_MODEL_PATH,
    OPEN_COMPANION_KOKORO_VOICES_PATH: KOKORO_VOICES_PATH,
    OLLAMA_MODELS: OLLAMA_MODELS_DIR,
    ...extra,
  };
}

module.exports = {
  APP_NAME,
  BACKEND_BUNDLED_ENTRY,
  BACKEND_GENERATE_PREVIEW_ENTRY,
  BACKEND_PYTHON_ENTRY,
  BUILTIN_REGISTRY_SOURCE_PATH,
  INSTALL_CACHE_DIR,
  IS_PACKAGED,
  KOKORO_DIR,
  KOKORO_MODEL_PATH,
  KOKORO_VOICES_PATH,
  LOGS_DIR,
  MEMORY_DIR,
  SESSION_SUMMARIES_DIR,
  OLLAMA_DIR,
  OLLAMA_EMBEDDED_ENTRY,
  OLLAMA_MODELS_DIR,
  PROFILE_ROOT,
  PROJECT_ROOT,
  READONLY_PROJECT_ROOT,
  RESOURCE_ROOT,
  RUNTIME_ASSETS_DIR,
  SOUL_ACTIVE_DIR,
  VAULT_DIR,
  AVATAR_ASSETS_DIR,
  VOICE_PREVIEWS_DIR,
  buildBackendEnvironment,
  ensureRuntimeDirectories,
  getOllamaExecutablePath,
  resolveRuntimeResourcePath,
};
