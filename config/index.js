const fs = require("fs");
const path = require("path");
const { DEFAULT_CONFIG } = require("./defaults");
const { runMigrations } = require("./migrate");

const PROJECT_ROOT = path.join(__dirname, "..");
const PROFILE_ROOT = path.resolve(
  process.env.OPEN_COMPANION_PROFILE_DIR
  || process.env.OPEN_COMPANION_TEST_PROFILE_DIR
  || PROJECT_ROOT
);
const TEST_MODE = process.env.OPEN_COMPANION_TEST_MODE === "1";
const CONFIG_PATH = path.join(PROFILE_ROOT, "config.json");
const LOCAL_CONFIG_PATH = path.join(PROFILE_ROOT, "config.local.json");

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function deepMerge(target, source) {
  if (!isPlainObject(target) || !isPlainObject(source)) {
    if (Array.isArray(source)) {
      return [...source];
    }
    return source;
  }

  const result = { ...target };
  for (const key of Object.keys(source)) {
    const sourceValue = source[key];
    if (isPlainObject(sourceValue)) {
      result[key] = deepMerge(isPlainObject(target[key]) ? target[key] : {}, sourceValue);
    } else if (Array.isArray(sourceValue)) {
      result[key] = [...sourceValue];
    } else {
      result[key] = sourceValue;
    }
  }
  return result;
}

function readJsonFile(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (_error) {
    return {};
  }
}

function writeJsonFile(filePath, data) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const content = JSON.stringify(data, null, 2);
  const tmp = filePath + ".tmp";
  fs.writeFileSync(tmp, content, "utf8");
  try {
    fs.renameSync(tmp, filePath);
  } catch (err) {
    if (err.code === "EPERM" || err.code === "EACCES") {
      // Windows can lock the destination file; fall back to a direct overwrite
      try { fs.unlinkSync(tmp); } catch (_) {}
      fs.writeFileSync(filePath, content, "utf8");
    } else {
      try { fs.unlinkSync(tmp); } catch (_) {}
      throw err;
    }
  }
}

function normalizeLocalOverrides(rawConfig) {
  const config = isPlainObject(rawConfig) ? { ...rawConfig } : {};
  const legacyVision = isPlainObject(config.vision) ? config.vision : {};
  const heartbeat = isPlainObject(config.heartbeat) ? { ...config.heartbeat } : {};
  if (isPlainObject(config.vision)) {
    if (heartbeat.enabled == null && legacyVision.heartbeat_enabled != null) {
      heartbeat.enabled = Boolean(legacyVision.heartbeat_enabled);
    }
    if (heartbeat.interval == null && legacyVision.heartbeat_interval != null) {
      heartbeat.interval = Number(legacyVision.heartbeat_interval) || DEFAULT_CONFIG.heartbeat.interval;
    }
    if (heartbeat.only_when_idle == null && legacyVision.only_when_idle != null) {
      heartbeat.only_when_idle = Boolean(legacyVision.only_when_idle);
    }
    if (heartbeat.idle_threshold_minutes == null && legacyVision.idle_threshold_minutes != null) {
      heartbeat.idle_threshold_minutes = Math.max(1, Number(legacyVision.idle_threshold_minutes) || DEFAULT_CONFIG.heartbeat.idle_threshold_minutes);
    }
  }
  if (Object.keys(heartbeat).length > 0) {
    config.heartbeat = heartbeat;
  }
  if (isPlainObject(config.companion)) {
    config.companion = { ...config.companion };
    delete config.companion.age;
  }
  delete config.vision;
  delete config.tool_rag;
  delete config.animations;
  return config;
}

function readBaseConfig() {
  const current = fs.existsSync(CONFIG_PATH) ? readJsonFile(CONFIG_PATH) : {};
  return runMigrations(current);
}

function loadConfig() {
  let config = deepMerge({}, DEFAULT_CONFIG);

  if (fs.existsSync(CONFIG_PATH)) {
    const rawConfig = readJsonFile(CONFIG_PATH);
    const migrated = runMigrations(rawConfig);
    config = deepMerge(config, migrated);

    if (JSON.stringify(rawConfig) !== JSON.stringify(migrated)) {
      writeJsonFile(CONFIG_PATH, migrated);
    }
  }

  if (fs.existsSync(LOCAL_CONFIG_PATH)) {
    const rawLocalConfig = readJsonFile(LOCAL_CONFIG_PATH);
    const localConfig = normalizeLocalOverrides(rawLocalConfig);
    if (JSON.stringify(rawLocalConfig) !== JSON.stringify(localConfig)) {
      writeJsonFile(LOCAL_CONFIG_PATH, localConfig);
    }
    config = deepMerge(config, localConfig);
  }

  return config;
}

function saveConfig(section, data) {
  const patch = section ? { [section]: data } : data;
  const currentLocal = fs.existsSync(LOCAL_CONFIG_PATH) ? normalizeLocalOverrides(readJsonFile(LOCAL_CONFIG_PATH)) : {};
  const updatedLocal = deepMerge(currentLocal, patch);
  writeJsonFile(LOCAL_CONFIG_PATH, updatedLocal);
  // Return the fully merged view (base + local) so callers see complete config
  const base = readBaseConfig();
  return deepMerge(base, updatedLocal);
}

function updateLocalConfig(patch) {
  const current = fs.existsSync(LOCAL_CONFIG_PATH) ? normalizeLocalOverrides(readJsonFile(LOCAL_CONFIG_PATH)) : {};
  const updated = deepMerge(current, patch);
  writeJsonFile(LOCAL_CONFIG_PATH, updated);
  return updated;
}

module.exports = {
  CONFIG_PATH,
  DEFAULT_CONFIG,
  LOCAL_CONFIG_PATH,
  PROFILE_ROOT,
  PROJECT_ROOT,
  TEST_MODE,
  deepMerge,
  isPlainObject,
  loadConfig,
  readBaseConfig,
  readJsonFile,
  saveConfig,
  updateLocalConfig,
  writeJsonFile,
};
