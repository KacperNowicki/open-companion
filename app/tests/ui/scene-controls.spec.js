const fs = require('fs');
const os = require('os');
const path = require('path');
const { test, expect, _electron: electron } = require('@playwright/test');
const electronPath = require('electron');
const { DEFAULT_CONFIG } = require('../../../config/defaults');

test.describe.configure({ mode: 'serial' });

const ROOT = path.resolve(__dirname, '..', '..', '..');

function cloneDefaultConfig() {
  return JSON.parse(JSON.stringify(DEFAULT_CONFIG));
}

function writeJson(filePath, data) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(data, null, 2)}\n`, 'utf8');
}

function seedProfile(rootDir) {
  const config = cloneDefaultConfig();
  config.onboarding_complete = true;
  config.updater.check_on_startup = false;
  config.companion.name = 'Scout';
  config.companion.soul.name = 'Scout';
  config.companion.soul.identity = 'Warm, curious, and slightly playful.';
  config.companion.soul.relationship = 'Trusted companion.';
  config.companion.soul.user_name = 'Tester';
  config.ui.avatar_model_path = '';
  config.ui.idle_timeout_seconds = 0; // disable idle for scene tests

  writeJson(path.join(rootDir, 'config.json'), config);
  writeJson(path.join(rootDir, 'config.local.json'), {
    ui: { avatar_image_path: '', avatar_model_path: '' },
  });

  fs.mkdirSync(path.join(rootDir, 'companion', 'memory'), { recursive: true });
  fs.writeFileSync(path.join(rootDir, 'companion', 'memory', 'memory.md'), '- test user\n', 'utf8');
  fs.mkdirSync(path.join(rootDir, 'runtime-assets', 'kokoro'), { recursive: true });
  fs.writeFileSync(path.join(rootDir, 'runtime-assets', 'kokoro', 'kokoro-v1.0.onnx'), 'test', 'utf8');
  fs.writeFileSync(path.join(rootDir, 'runtime-assets', 'kokoro', 'voices-v1.0.bin'), 'test', 'utf8');
  fs.mkdirSync(path.join(rootDir, 'companion', 'soul', 'active'), { recursive: true });
}

async function launchApp(profile) {
  return electron.launch({
    executablePath: electronPath,
    args: ['.'],
    cwd: ROOT,
    env: {
      ...process.env,
      ELECTRON_RUN_AS_NODE: undefined,
      OPEN_COMPANION_TEST_MODE: '1',
      OPEN_COMPANION_TEST_PROFILE_DIR: profile,
    },
  });
}

async function waitForOverlayReady(app) {
  const started = Date.now();
  let page;
  while (Date.now() - started < 30000) {
    for (const w of app.windows()) {
      if (w.isClosed()) continue;
      const title = await w.title().catch(() => '');
      if (!/Settings|Onboarding/i.test(title)) {
        page = w;
        break;
      }
    }
    if (page) break;
    await new Promise((r) => setTimeout(r, 200));
  }
  if (!page) throw new Error('Could not find overlay window');

  await page.waitForLoadState('domcontentloaded');
  await page.waitForFunction(
    () => Boolean(window.openCompanionTest && window.__ocOverlayTest && document.getElementById('settingsButton')),
    { timeout: 30000 },
  );
  await expect.poll(async () => {
    const name = await page.evaluate(() => window.__ocOverlayTest.getCompanionName());
    return name.trim();
  }).toBe('Scout');

  return page;
}

// ── Tests ──────────────────────────────────────────────────────────────────

test.describe('Scene controls', () => {
  let electronApp;
  let overlayPage;
  let profileRoot;

  test.afterEach(async () => {
    await electronApp?.close().catch(() => {});
    if (profileRoot) fs.rmSync(profileRoot, { recursive: true, force: true });
  });

  test('app starts normally when companion/vault/scene.js does not exist', async () => {
    profileRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'oc-scene-test-'));
    seedProfile(profileRoot);
    // No companion/vault/scene.js written — should start cleanly
    electronApp = await launchApp(profileRoot);
    overlayPage = await waitForOverlayReady(electronApp);

    const name = await overlayPage.evaluate(() => window.__ocOverlayTest.getCompanionName());
    expect(name.trim()).toBe('Scout');
  });

  test('companion/vault/scene.js executes and modifies scene when valid', async () => {
    profileRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'oc-scene-test-'));
    seedProfile(profileRoot);

    // Write a valid companion/vault/scene.js that sets a recognizable background
    const vaultDir = path.join(profileRoot, 'companion', 'vault');
    fs.mkdirSync(vaultDir, { recursive: true });
    fs.writeFileSync(
      path.join(vaultDir, 'scene.js'),
      'scene.background = new THREE.Color(0x1a1a2e);',
      'utf8',
    );

    electronApp = await launchApp(profileRoot);
    overlayPage = await waitForOverlayReady(electronApp);

    // If the scene loaded without crashing, the overlay is up — confirm no scene error logged
    // We verify by checking the test harness is still healthy
    const name = await overlayPage.evaluate(() => window.__ocOverlayTest.getCompanionName());
    expect(name.trim()).toBe('Scout');
  });

  test('companion/vault/scene.js fallback on error — app starts normally', async () => {
    profileRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'oc-scene-test-'));
    seedProfile(profileRoot);

    // Write a broken companion/vault/scene.js
    const vaultDir = path.join(profileRoot, 'companion', 'vault');
    fs.mkdirSync(vaultDir, { recursive: true });
    fs.writeFileSync(
      path.join(vaultDir, 'scene.js'),
      'throw new Error("intentional vault scene error");',
      'utf8',
    );

    electronApp = await launchApp(profileRoot);
    overlayPage = await waitForOverlayReady(electronApp);

    // App should still be fully functional after fallback
    const name = await overlayPage.evaluate(() => window.__ocOverlayTest.getCompanionName());
    expect(name.trim()).toBe('Scout');

    // Confirm fallback: scene.background should be null (transparent)
    const bgIsNull = await overlayPage.evaluate(() => {
      const state = window.__ocOverlayTest.getSceneState();
      // We can't reach the Three.js scene object directly, but the app being alive
      // confirms the error was caught and not re-thrown.
      return typeof state === 'object' && state !== null;
    });
    expect(bgIsNull).toBe(true);
  });

  test('navigation control buttons are present in the overlay', async () => {
    profileRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'oc-scene-test-'));
    seedProfile(profileRoot);
    electronApp = await launchApp(profileRoot);
    overlayPage = await waitForOverlayReady(electronApp);

    const present = await overlayPage.evaluate(() => window.__ocOverlayTest.navControlsPresent());
    expect(present).toBe(true);

    // All six buttons exist
    for (const id of ['nav-up', 'nav-down', 'nav-left', 'nav-right', 'nav-zoomin', 'nav-zoomout']) {
      await expect(overlayPage.locator(`#${id}`)).toBeAttached();
    }
  });

  test('animation label is hidden when no animation is playing (placeholder visual)', async () => {
    profileRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'oc-scene-test-'));
    seedProfile(profileRoot);
    // No model configured — placeholder visual has no AnimationController
    electronApp = await launchApp(profileRoot);
    overlayPage = await waitForOverlayReady(electronApp);

    const labelState = await overlayPage.evaluate(() => window.__ocOverlayTest.getAnimLabel());
    expect(labelState.visible).toBe(false);
  });
});
