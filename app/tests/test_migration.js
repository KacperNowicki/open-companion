const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");
const MIGRATION_MODULE_PATH = path.join(ROOT, "app", "frontend", "profile-migration.js");
const RUNTIME_PATHS_MODULE_PATH = path.join(ROOT, "app", "frontend", "runtime-paths.js");
const CONFIG_INDEX_MODULE_PATH = path.join(ROOT, "config", "index.js");
const DEFAULTS_MODULE_PATH = path.join(ROOT, "config", "defaults.js");
const PACKAGE_JSON_PATH = path.join(ROOT, "package.json");

const PACKAGE_VERSION = JSON.parse(fs.readFileSync(PACKAGE_JSON_PATH, "utf8")).version;
const REAL_DEFAULT_CONFIG = JSON.parse(JSON.stringify(require(DEFAULTS_MODULE_PATH).DEFAULT_CONFIG));
const SOUL_FILES = [
  "shared_runtime_contract.md",
  "soul_companion.md",
  "soul_assistant.md",
];

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function writeJson(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(value, null, 2), "utf8");
}

function makeTempProfile() {
  const profileDir = fs.mkdtempSync(path.join(os.tmpdir(), "oc-migration-"));
  fs.mkdirSync(path.join(profileDir, "companion", "soul", "active"), { recursive: true });
  return profileDir;
}

function clearModule(resolvedPath) {
  try {
    delete require.cache[require.resolve(resolvedPath)];
  } catch (_error) {
    // Ignore modules that have not been loaded yet.
  }
}

function installModuleOverride(resolvedPath, exportsValue) {
  const previous = require.cache[resolvedPath];
  require.cache[resolvedPath] = {
    id: resolvedPath,
    filename: resolvedPath,
    loaded: true,
    exports: exportsValue,
  };
  return () => {
    if (previous) {
      require.cache[resolvedPath] = previous;
    } else {
      delete require.cache[resolvedPath];
    }
  };
}

async function runMigration(profileDir, options = {}) {
  const originalEnv = {
    OPEN_COMPANION_PROFILE_DIR: process.env.OPEN_COMPANION_PROFILE_DIR,
    OPEN_COMPANION_TEST_PROFILE_DIR: process.env.OPEN_COMPANION_TEST_PROFILE_DIR,
    OPEN_COMPANION_TEST_MODE: process.env.OPEN_COMPANION_TEST_MODE,
    OPEN_COMPANION_FORCE_DEV: process.env.OPEN_COMPANION_FORCE_DEV,
  };

  const restoreOverrides = [];
  const defaultsResolved = require.resolve(DEFAULTS_MODULE_PATH);

  process.env.OPEN_COMPANION_PROFILE_DIR = profileDir;
  delete process.env.OPEN_COMPANION_TEST_PROFILE_DIR;
  process.env.OPEN_COMPANION_TEST_MODE = "1";
  process.env.OPEN_COMPANION_FORCE_DEV = "1";

  clearModule(MIGRATION_MODULE_PATH);
  clearModule(RUNTIME_PATHS_MODULE_PATH);
  clearModule(CONFIG_INDEX_MODULE_PATH);
  clearModule(DEFAULTS_MODULE_PATH);

  if (options.defaultsOverride) {
    restoreOverrides.push(installModuleOverride(defaultsResolved, { DEFAULT_CONFIG: options.defaultsOverride }));
  }

  try {
    const migrationModule = require(MIGRATION_MODULE_PATH);
    assert.strictEqual(
      typeof migrationModule.runMigration,
      "function",
      "app/frontend/profile-migration.js must export runMigration()"
    );
    await migrationModule.runMigration();
  } finally {
    while (restoreOverrides.length) {
      restoreOverrides.pop()();
    }
    clearModule(MIGRATION_MODULE_PATH);
    clearModule(RUNTIME_PATHS_MODULE_PATH);
    clearModule(CONFIG_INDEX_MODULE_PATH);
    clearModule(DEFAULTS_MODULE_PATH);

    if (originalEnv.OPEN_COMPANION_PROFILE_DIR === undefined) {
      delete process.env.OPEN_COMPANION_PROFILE_DIR;
    } else {
      process.env.OPEN_COMPANION_PROFILE_DIR = originalEnv.OPEN_COMPANION_PROFILE_DIR;
    }

    if (originalEnv.OPEN_COMPANION_TEST_PROFILE_DIR === undefined) {
      delete process.env.OPEN_COMPANION_TEST_PROFILE_DIR;
    } else {
      process.env.OPEN_COMPANION_TEST_PROFILE_DIR = originalEnv.OPEN_COMPANION_TEST_PROFILE_DIR;
    }

    if (originalEnv.OPEN_COMPANION_TEST_MODE === undefined) {
      delete process.env.OPEN_COMPANION_TEST_MODE;
    } else {
      process.env.OPEN_COMPANION_TEST_MODE = originalEnv.OPEN_COMPANION_TEST_MODE;
    }

    if (originalEnv.OPEN_COMPANION_FORCE_DEV === undefined) {
      delete process.env.OPEN_COMPANION_FORCE_DEV;
    } else {
      process.env.OPEN_COMPANION_FORCE_DEV = originalEnv.OPEN_COMPANION_FORCE_DEV;
    }
  }
}

async function withTempProfile(testFn) {
  const profileDir = makeTempProfile();
  try {
    await testFn(profileDir);
  } finally {
    fs.rmSync(profileDir, { recursive: true, force: true });
  }
}

async function testConfigMigrationAddsMissingKeysAndPreservesExistingValues() {
  await withTempProfile(async (profileDir) => {
    const configPath = path.join(profileDir, "config.json");
    writeJson(configPath, {
      version: "0.9.0",
      brain: {
        model: "my-preserved-model",
      },
      companion: {
        name: "KeptName",
      },
    });

    await runMigration(profileDir);

    const migrated = readJson(configPath);
     assert.strictEqual(migrated.brain.model, "my-preserved-model", "existing model should be preserved");
     assert.strictEqual(migrated.companion.name, "KeptName", "existing companion name should be preserved");
     assert.strictEqual(migrated.brain.provider, REAL_DEFAULT_CONFIG.brain.provider, "missing provider should be backfilled");
      assert.strictEqual(
        migrated.context.session_summaries.enabled,
        REAL_DEFAULT_CONFIG.context.session_summaries.enabled,
        "missing session summary defaults should be added"
      );
      assert.strictEqual(
        migrated.context.skills.index_enabled,
        REAL_DEFAULT_CONFIG.context.skills.index_enabled,
        "missing skill context defaults should be added"
      );
      assert.strictEqual(
        migrated.updater.check_on_startup,
        REAL_DEFAULT_CONFIG.updater.check_on_startup,
      "missing updater defaults should be added"
    );
  });
}

async function testConfigMigrationDoesNotOverwriteUserValueWhenDefaultChanges() {
  await withTempProfile(async (profileDir) => {
    const configPath = path.join(profileDir, "config.json");
    writeJson(configPath, {
      version: "0.9.0",
      brain: {
        model: "user-owned-model",
      },
    });

    const changedDefaults = clone(REAL_DEFAULT_CONFIG);
    changedDefaults.brain.model = "new-shipped-default";
    changedDefaults.companion.name = "DifferentDefaultName";

    await runMigration(profileDir, { defaultsOverride: changedDefaults });

    const migrated = readJson(configPath);
    assert.strictEqual(migrated.brain.model, "user-owned-model", "migration must not replace a user-set model");
  });
}

async function testMigrationIsIdempotent() {
  await withTempProfile(async (profileDir) => {
    const configPath = path.join(profileDir, "config.json");
    const markerPath = path.join(profileDir, "migration-version.json");
    writeJson(configPath, {
      version: "0.8.0",
      brain: {
        model: "stable-model",
      },
    });

    await runMigration(profileDir);
    const firstConfig = readJson(configPath);
    const firstMarker = readJson(markerPath);

    await runMigration(profileDir);
    const secondConfig = readJson(configPath);
    const secondMarker = readJson(markerPath);

    assert.deepStrictEqual(secondConfig, firstConfig, "running migration twice should not change config again");
    assert.deepStrictEqual(secondMarker, firstMarker, "migration marker should remain stable on repeat runs");
  });
}

async function testMigrationSkipsWhenAlreadyMigratedToCurrentVersion() {
  await withTempProfile(async (profileDir) => {
    const configPath = path.join(profileDir, "config.json");
    const markerPath = path.join(profileDir, "migration-version.json");
    const originalConfig = {
      version: "0.8.0",
      brain: {
        model: "leave-me-alone",
      },
    };

    writeJson(configPath, originalConfig);
    writeJson(markerPath, { migrated_to: PACKAGE_VERSION });

    await runMigration(profileDir);

    assert.deepStrictEqual(readJson(configPath), originalConfig, "config should remain untouched when migration is skipped");
    assert.deepStrictEqual(readJson(markerPath), { migrated_to: PACKAGE_VERSION }, "marker should remain unchanged");
  });
}

async function testSoulActiveFilesAreGeneratedWhenMissing() {
  await withTempProfile(async (profileDir) => {
    const configPath = path.join(profileDir, "config.json");
    writeJson(configPath, {
      version: "0.8.0",
      companion: {
        name: "Mira",
        soul: {
          name: "Mira",
        },
      },
    });

    await runMigration(profileDir);

    for (const fileName of SOUL_FILES) {
      const activePath = path.join(profileDir, "companion", "soul", "active", fileName);
      assert.ok(fs.existsSync(activePath), `missing generated active soul file: ${fileName}`);
      const contents = fs.readFileSync(activePath, "utf8").trim();
      assert.ok(contents.length > 0, `${fileName} should not be empty after generation`);
    }

    const sharedContract = fs.readFileSync(path.join(profileDir, "companion", "soul", "active", "shared_runtime_contract.md"), "utf8");
    const companionSoul = fs.readFileSync(path.join(profileDir, "companion", "soul", "active", "soul_companion.md"), "utf8");
    assert.ok(sharedContract.includes("## Tool Use"), "generated shared_runtime_contract.md should copy the shipped runtime contract");
    assert.ok(companionSoul.includes("local AI companion"), "generated soul_companion.md should copy the anonymized shipped default");
    for (const blocked of ["PrivateUserName", "PrivateCompanionName", "PrivateProfileName"]) {
      assert.ok(!sharedContract.includes(blocked), `shared runtime default should not include ${blocked}`);
      assert.ok(!companionSoul.includes(blocked), `companion default should not include ${blocked}`);
    }
  });
}

async function testSoulActiveFilesAreNotOverwrittenWhenPresent() {
  await withTempProfile(async (profileDir) => {
    const configPath = path.join(profileDir, "config.json");
    const soulActiveDir = path.join(profileDir, "companion", "soul", "active");
    const preservedPath = path.join(soulActiveDir, "soul_companion.md");
    const preservedContents = "# Custom Soul\n\nDo not overwrite this file.\n";

    writeJson(configPath, {
      version: "0.8.0",
      companion: {
        name: "Mira",
        soul: {
          name: "Mira",
        },
      },
    });
    fs.mkdirSync(soulActiveDir, { recursive: true });
    fs.writeFileSync(preservedPath, preservedContents, "utf8");

    await runMigration(profileDir);

    assert.strictEqual(
      fs.readFileSync(preservedPath, "utf8"),
      preservedContents,
      "existing active soul files must be preserved exactly"
    );
    assert.ok(
      fs.existsSync(path.join(soulActiveDir, "soul_assistant.md")),
      "migration should still generate other missing active soul files"
    );
  });
}

const TESTS = [
  ["config migration adds missing keys and preserves existing values", testConfigMigrationAddsMissingKeysAndPreservesExistingValues],
  ["config migration preserves user values when shipped defaults change", testConfigMigrationDoesNotOverwriteUserValueWhenDefaultChanges],
  ["migration is idempotent", testMigrationIsIdempotent],
  ["migration skips when migrated_to is current or newer", testMigrationSkipsWhenAlreadyMigratedToCurrentVersion],
  ["missing active soul files are generated", testSoulActiveFilesAreGeneratedWhenMissing],
  ["existing active soul files are not overwritten", testSoulActiveFilesAreNotOverwrittenWhenPresent],
];

(async () => {
  let passed = 0;
  const failures = [];

  for (const [name, testFn] of TESTS) {
    try {
      await testFn();
      passed += 1;
      console.log(`[PASS] ${name}`);
    } catch (error) {
      failures.push({ name, error });
      console.error(`[FAIL] ${name}`);
      console.error(error && error.stack ? error.stack : String(error));
    }
  }

  if (failures.length) {
    console.error(`Migration tests failed: ${passed} passed, ${failures.length} failed.`);
    process.exit(1);
  }

  console.log(`All migration tests passed (${passed}/${TESTS.length}).`);
})().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
