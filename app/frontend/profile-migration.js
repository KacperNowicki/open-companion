const fs = require("fs");
const path = require("path");

const { DEFAULT_CONFIG } = require("../../config/defaults");
const { runMigrations: runConfigMigrations } = require("../../config/migrate");
const { PROFILE_ROOT, READONLY_PROJECT_ROOT } = require("./runtime-paths");

const PACKAGE_JSON_PATH = path.resolve(__dirname, "..", "..", "package.json");
const DEFAULT_SOUL_DEFAULTS_DIR = path.join(READONLY_PROJECT_ROOT, "companion", "soul", "defaults");
const SOUL_FILES = [
  "shared_runtime_contract.md",
  "soul_companion.md",
  "soul_assistant.md",
];
const DEPRECATED_SOUL_FILES = [
  "system.md",
  "system_worker.md",
  "soul_default.md",
  "soul_pc_doctor.md",
  "pc_doctor.md",
  "assistant.md",
  "companion.md",
];
const ROOT_ONLY_DEPRECATED_SOUL_FILES = [
  "soul_companion.md",
  "soul_assistant.md",
];

const DEPRECATED_KEY_RENAMES = [
  { from: "brain.api_url", to: "brain.base_url" },
];

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function cloneValue(value) {
  if (Array.isArray(value)) {
    return value.map(cloneValue);
  }
  if (isPlainObject(value)) {
    const output = {};
    for (const [key, nested] of Object.entries(value)) {
      output[key] = cloneValue(nested);
    }
    return output;
  }
  return value;
}

function mergeMissingKeys(target, defaults) {
  if (!isPlainObject(defaults)) {
    return target;
  }

  const output = isPlainObject(target) ? { ...target } : {};
  for (const [key, defaultValue] of Object.entries(defaults)) {
    const currentValue = output[key];
    if (typeof currentValue === "undefined") {
      output[key] = cloneValue(defaultValue);
      continue;
    }
    if (isPlainObject(defaultValue) && isPlainObject(currentValue)) {
      output[key] = mergeMissingKeys(currentValue, defaultValue);
    }
  }
  return output;
}

function pathSegments(input) {
  return String(input || "")
    .split(".")
    .map((segment) => segment.trim())
    .filter(Boolean);
}

function hasPath(object, dottedPath) {
  let current = object;
  for (const segment of pathSegments(dottedPath)) {
    if (!isPlainObject(current) || !Object.prototype.hasOwnProperty.call(current, segment)) {
      return false;
    }
    current = current[segment];
  }
  return true;
}

function getPath(object, dottedPath) {
  let current = object;
  for (const segment of pathSegments(dottedPath)) {
    if (!isPlainObject(current) || !Object.prototype.hasOwnProperty.call(current, segment)) {
      return undefined;
    }
    current = current[segment];
  }
  return current;
}

function setPath(object, dottedPath, value) {
  const segments = pathSegments(dottedPath);
  if (!segments.length) {
    return object;
  }

  let current = object;
  for (let index = 0; index < segments.length - 1; index += 1) {
    const segment = segments[index];
    if (!isPlainObject(current[segment])) {
      current[segment] = {};
    }
    current = current[segment];
  }
  current[segments[segments.length - 1]] = value;
  return object;
}

function deletePath(object, dottedPath) {
  const segments = pathSegments(dottedPath);
  if (!segments.length) {
    return;
  }

  let current = object;
  for (let index = 0; index < segments.length - 1; index += 1) {
    const segment = segments[index];
    if (!isPlainObject(current[segment])) {
      return;
    }
    current = current[segment];
  }

  delete current[segments[segments.length - 1]];
}

function applyDeprecatedRenames(config, renames = DEPRECATED_KEY_RENAMES) {
  const next = cloneValue(isPlainObject(config) ? config : {});
  const applied = [];

  for (const entry of renames) {
    if (!entry || !entry.from || !entry.to) {
      continue;
    }
    if (!hasPath(next, entry.from) || hasPath(next, entry.to)) {
      continue;
    }
    setPath(next, entry.to, cloneValue(getPath(next, entry.from)));
    deletePath(next, entry.from);
    applied.push({ from: entry.from, to: entry.to });
  }

  return { config: next, applied };
}

function parseVersion(version) {
  return String(version || "0.0.0")
    .split(".")
    .map((part) => {
      const match = part.match(/^(\d+)/);
      return match ? Number.parseInt(match[1], 10) : 0;
    });
}

function compareVersions(left, right) {
  const a = parseVersion(left);
  const b = parseVersion(right);
  const length = Math.max(a.length, b.length);
  for (let index = 0; index < length; index += 1) {
    const delta = (a[index] || 0) - (b[index] || 0);
    if (delta !== 0) {
      return delta < 0 ? -1 : 1;
    }
  }
  return 0;
}

function readJsonFileSafe(filePath, fallback = {}) {
  try {
    if (!fs.existsSync(filePath)) {
      return cloneValue(fallback);
    }
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (_error) {
    return cloneValue(fallback);
  }
}

function readJsonFileStrict(filePath, fallback) {
  if (!fs.existsSync(filePath)) {
    return cloneValue(typeof fallback === "undefined" ? {} : fallback);
  }
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function atomicWriteJson(filePath, payload) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const tempPath = `${filePath}.tmp`;
  fs.writeFileSync(tempPath, JSON.stringify(payload, null, 2), "utf8");
  if (fs.existsSync(filePath)) {
    fs.rmSync(filePath, { force: true });
  }
  fs.renameSync(tempPath, filePath);
}

function getCurrentAppVersion(overrideVersion) {
  if (overrideVersion) {
    return String(overrideVersion);
  }
  try {
    const pkg = JSON.parse(fs.readFileSync(PACKAGE_JSON_PATH, "utf8"));
    return String(pkg.version || DEFAULT_CONFIG.version || "0.0.0");
  } catch (_error) {
    return String(DEFAULT_CONFIG.version || "0.0.0");
  }
}

function normalizeConfig(config, defaultsConfig = DEFAULT_CONFIG) {
  const migrated = runConfigMigrations(isPlainObject(config) ? config : {});
  const { config: renamedConfig, applied } = applyDeprecatedRenames(migrated);
  return {
    config: mergeMissingKeys(renamedConfig, defaultsConfig),
    renamed: applied,
  };
}

function cleanupDeprecatedSoulFiles(activeDir, profileRoot) {
  const soulRootDir = path.join(profileRoot, "companion", "soul");
  for (const filename of DEPRECATED_SOUL_FILES) {
    for (const directory of [activeDir, soulRootDir]) {
      const targetPath = path.join(directory, filename);
      if (fs.existsSync(targetPath)) {
        fs.rmSync(targetPath, { force: true });
      }
    }
  }
  for (const filename of ROOT_ONLY_DEPRECATED_SOUL_FILES) {
    const targetPath = path.join(soulRootDir, filename);
    if (fs.existsSync(targetPath)) {
      fs.rmSync(targetPath, { force: true });
    }
  }
}

function ensureMissingSoulFiles(_config, options = {}) {
  const defaultsDir = options.soulDefaultsDir || DEFAULT_SOUL_DEFAULTS_DIR;
  const activeDir = options.soulActiveDir || path.join(PROFILE_ROOT, "companion", "soul", "active");
  const profileRoot = options.profileRoot || PROFILE_ROOT;
  const logger = options.logger || console;
  const created = [];

  fs.mkdirSync(activeDir, { recursive: true });
  cleanupDeprecatedSoulFiles(activeDir, profileRoot);

  for (const filename of SOUL_FILES) {
    const targetPath = path.join(activeDir, filename);
    if (fs.existsSync(targetPath)) {
      continue;
    }

    try {
      const templatePath = path.join(defaultsDir, filename);
      fs.writeFileSync(targetPath, fs.readFileSync(templatePath, "utf8"), "utf8");
      created.push(filename);
    } catch (error) {
      logger.warn?.(`[migration] Failed copying soul file ${filename}: ${error.message}`);
    }
  }

  return created;
}

function ensureMigrationDirectories(profileRoot, soulActiveDir) {
  fs.mkdirSync(profileRoot, { recursive: true });
  fs.mkdirSync(path.dirname(soulActiveDir), { recursive: true });
  fs.mkdirSync(soulActiveDir, { recursive: true });
}

async function runMigration(options = {}) {
  const logger = options.logger || console;
  const profileRoot = options.profileRoot || PROFILE_ROOT;
  const currentVersion = getCurrentAppVersion(options.currentVersion);
  const defaultsConfig = options.defaultsConfig || DEFAULT_CONFIG;
  const configPath = options.configPath || path.join(profileRoot, "config.json");
  const markerPath = options.markerPath || path.join(profileRoot, "migration-version.json");
  const soulActiveDir = options.soulActiveDir || path.join(profileRoot, "companion", "soul", "active");
  const soulDefaultsDir = options.soulDefaultsDir || DEFAULT_SOUL_DEFAULTS_DIR;

  ensureMigrationDirectories(profileRoot, soulActiveDir);

  const marker = readJsonFileSafe(markerPath, { migrated_to: "0.0.0" });
  const previousVersion = String(marker.migrated_to || "0.0.0");

  if (compareVersions(previousVersion, currentVersion) >= 0) {
    return {
      migrated: false,
      skipped: true,
      fromVersion: previousVersion,
      toVersion: currentVersion,
      configUpdated: false,
      createdSoulFiles: [],
      renamedKeys: [],
      errors: [],
    };
  }

  const errors = [];
  let configUpdated = false;
  let createdSoulFiles = [];
  let renamedKeys = [];
  let effectiveConfig = cloneValue(defaultsConfig);

  try {
    const rawConfig = readJsonFileStrict(configPath, {});
    const normalized = normalizeConfig(rawConfig, defaultsConfig);
    renamedKeys = normalized.renamed;
    const nextConfig = normalized.config;
    effectiveConfig = nextConfig;
    if (JSON.stringify(rawConfig) !== JSON.stringify(nextConfig)) {
      atomicWriteJson(configPath, nextConfig);
      configUpdated = true;
    }
  } catch (error) {
    errors.push({ step: "config", message: error.message });
    logger.warn?.(`[migration] Config migration failed: ${error.message}`);
  }

  try {
    createdSoulFiles = ensureMissingSoulFiles(effectiveConfig, {
      soulDefaultsDir,
      soulActiveDir,
      profileRoot,
      logger,
    });
  } catch (error) {
    errors.push({ step: "soul", message: error.message });
    logger.warn?.(`[migration] Soul migration failed: ${error.message}`);
  }

  try {
    atomicWriteJson(markerPath, { migrated_to: currentVersion });
  } catch (error) {
    errors.push({ step: "marker", message: error.message });
    logger.warn?.(`[migration] Failed writing migration marker: ${error.message}`);
  }

  return {
    migrated: true,
    skipped: false,
    fromVersion: previousVersion,
    toVersion: currentVersion,
    configUpdated,
    createdSoulFiles,
    renamedKeys,
    errors,
  };
}

module.exports = {
  DEPRECATED_KEY_RENAMES,
  applyDeprecatedRenames,
  atomicWriteJson,
  compareVersions,
  cleanupDeprecatedSoulFiles,
  ensureMissingSoulFiles,
  mergeMissingKeys,
  normalizeConfig,
  runMigration,
};
