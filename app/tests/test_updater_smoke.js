/**
 * Updater smoke test - verifies status transitions in isolation (no Electron, no GitHub).
 * Tests the updaterController state machine from app/scripts/updater.js directly.
 *
 * Run: node app/tests/test_updater_smoke.js
 */

const EventEmitter = require("events");

const autoUpdaterStub = new EventEmitter();
autoUpdaterStub.autoDownload = false;
autoUpdaterStub.autoInstallOnAppQuit = false;
autoUpdaterStub.checkForUpdates = async () => {};
autoUpdaterStub.downloadUpdate = async () => {};
autoUpdaterStub.quitAndInstall = () => {};

require.cache[require.resolve("electron-updater")] = {
  exports: { autoUpdater: autoUpdaterStub },
};

const { createUpdaterController } = require("../scripts/updater.js");

let passed = 0;
let failed = 0;
let testsPassed = 0;
let testsFailed = 0;

function assert(condition, label) {
  if (condition) {
    console.log(`  OK ${label}`);
    passed += 1;
  } else {
    console.error(`  FAIL ${label}`);
    failed += 1;
  }
}

function finishTest(startFailedCount) {
  if (failed === startFailedCount) {
    testsPassed += 1;
  } else {
    testsFailed += 1;
  }
}

async function createBoundController(config) {
  autoUpdaterStub.removeAllListeners();
  const ctrl = createUpdaterController();
  ctrl.configure(config || { updater: { check_on_startup: false } });
  await ctrl.checkNow();
  return ctrl;
}

async function run() {
  console.log("Test 1: initial status");
  {
    const startFailedCount = failed;
    const ctrl = createUpdaterController();
    const s = ctrl.getStatus();
    assert(s.checking === false, "checking starts false");
    assert(s.available === false, "available starts false");
    assert(s.ready === false, "ready starts false");
    assert(s.error === "", "error starts empty");
    finishTest(startFailedCount);
  }

  console.log("Test 2: configure");
  {
    const startFailedCount = failed;
    const ctrl = createUpdaterController();
    ctrl.configure({ updater: { dismissed_version: "1.2.3", check_on_startup: false } });
    const s = ctrl.getStatus();
    assert(s.dismissed === true, "dismissed is true after configure with dismissed_version");
    finishTest(startFailedCount);
  }

  console.log("Test 3: update-available event");
  {
    const startFailedCount = failed;
    const ctrl = await createBoundController({ updater: { check_on_startup: false } });
    autoUpdaterStub.emit("update-available", { version: "2.0.0", releaseNotes: "" });
    const s = ctrl.getStatus();
    assert(s.available === true, "available becomes true");
    assert(s.latestVersion === "2.0.0", "latestVersion is set");
    assert(s.downloading === true, "downloading starts automatically");
    assert(s.dismissed === false, "not dismissed");
    finishTest(startFailedCount);
  }

  console.log("Test 4: suppressed version");
  {
    const startFailedCount = failed;
    const ctrl = await createBoundController({
      updater: { dismissed_version: "2.0.0", check_on_startup: false },
    });
    autoUpdaterStub.emit("update-available", { version: "2.0.0" });
    const s = ctrl.getStatus();
    assert(s.available === false, "available stays false for dismissed version");
    assert(s.dismissed === true, "dismissed is true");
    finishTest(startFailedCount);
  }

  console.log("Test 5: update-downloaded");
  {
    const startFailedCount = failed;
    const ctrl = await createBoundController({ updater: { check_on_startup: false } });
    autoUpdaterStub.emit("update-available", { version: "3.0.0" });
    autoUpdaterStub.emit("update-downloaded", { version: "3.0.0" });
    const s = ctrl.getStatus();
    assert(s.ready === true, "ready becomes true after download");
    assert(s.downloading === false, "downloading clears");
    assert(s.downloadedVersion === "3.0.0", "downloadedVersion is set");
    finishTest(startFailedCount);
  }

  console.log("Test 6: dismissReady");
  {
    const startFailedCount = failed;
    const ctrl = await createBoundController({ updater: { check_on_startup: false } });
    autoUpdaterStub.emit("update-available", { version: "4.0.0" });
    autoUpdaterStub.emit("update-downloaded", { version: "4.0.0" });
    ctrl.dismissReady("4.0.0");
    const s = ctrl.getStatus();
    assert(s.ready === false, "ready cleared after dismiss");
    assert(s.dismissed === true, "dismissed is true");
    finishTest(startFailedCount);
  }

  console.log("Test 7: error event");
  {
    const startFailedCount = failed;
    const ctrl = await createBoundController({ updater: { check_on_startup: false } });
    const originalConsoleError = console.error;
    console.error = () => {};
    autoUpdaterStub.emit("error", new Error("Network failure"));
    console.error = originalConsoleError;
    const s = ctrl.getStatus();
    assert(s.error === "Network failure", "error message captured");
    assert(s.checking === false, "checking cleared on error");
    finishTest(startFailedCount);
  }

  console.log(`\nAssertions: ${passed} passed, ${failed} failed`);
  console.log(`Results: ${testsPassed} passed, ${testsFailed} failed`);
  if (failed > 0 || testsFailed > 0) {
    process.exit(1);
  }
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});
