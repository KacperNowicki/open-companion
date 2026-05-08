const fs = require('fs');
const os = require('os');
const path = require('path');
const { test, expect, _electron: electron } = require('@playwright/test');
const electronPath = require('electron');
const { DEFAULT_CONFIG } = require('../../../config/defaults');

test.describe.configure({ mode: 'serial' });

const ROOT = path.resolve(__dirname, '..', '..', '..');

let electronApp;
let overlayPage;
let profileRoot;

function cloneDefaultConfig() {
  return JSON.parse(JSON.stringify(DEFAULT_CONFIG));
}

function writeJson(filePath, data) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(data, null, 2)}\n`, 'utf8');
}

function seedProfile(rootDir, overrides = {}) {
  const config = cloneDefaultConfig();
  config.onboarding_complete = true;
  config.updater.check_on_startup = false;
  config.companion.name = 'Scout';
  config.companion.soul.name = 'Scout';
  config.companion.soul.identity = 'Warm, curious, and slightly playful.';
  config.companion.soul.relationship = 'Trusted companion.';
  config.companion.soul.user_name = 'Tester';
  config.ui.avatar_model_path = '';
  // Use 1s timeout by default for fast tests
  config.ui.idle_timeout_seconds = 1;

  Object.assign(config.ui, overrides.ui || {});

  writeJson(path.join(rootDir, 'config.json'), config);
  writeJson(path.join(rootDir, 'config.local.json'), {
    ui: {
      avatar_image_path: '',
      avatar_model_path: 'companion/assets/avatar/default.glb',
    },
  });

  fs.mkdirSync(path.join(rootDir, 'companion', 'memory'), { recursive: true });
  fs.writeFileSync(path.join(rootDir, 'companion', 'memory', 'memory.md'), '- test user\n', 'utf8');
  fs.mkdirSync(path.join(rootDir, 'runtime-assets', 'kokoro'), { recursive: true });
  fs.writeFileSync(path.join(rootDir, 'runtime-assets', 'kokoro', 'kokoro-v1.0.onnx'), 'test', 'utf8');
  fs.writeFileSync(path.join(rootDir, 'runtime-assets', 'kokoro', 'voices-v1.0.bin'), 'test', 'utf8');
  fs.mkdirSync(path.join(rootDir, 'companion', 'soul', 'active'), { recursive: true });
}

async function launchApp(profile) {
  const app = await electron.launch({
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
  return app;
}

async function waitForOverlayReady(app) {
  // Find the overlay window (title contains the companion name or is untitled)
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

test.describe('Idle mode', () => {
  test.beforeEach(async () => {
    profileRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'oc-idle-test-'));
    seedProfile(profileRoot);
    electronApp = await launchApp(profileRoot);
    overlayPage = await waitForOverlayReady(electronApp);
    // Move mouse once to reset the timer that fires at startup
    await overlayPage.mouse.move(200, 200);
  });

  test.afterEach(async () => {
    await electronApp.close().catch(() => {});
    fs.rmSync(profileRoot, { recursive: true, force: true });
  });

  test('body gets class idle and UI chrome is hidden after 1s of inactivity', async () => {
    // Ensure we start without idle
    await expect(overlayPage.locator('body')).not.toHaveClass(/idle/);

    // Wait for the 1s idle timer to fire (with margin)
    await overlayPage.waitForFunction(
      () => document.body.classList.contains('idle'),
      { timeout: 5000 },
    );

    // CSS hides .header, .response, .controls via opacity:0 (150ms transition)
    await expect(overlayPage.locator('body')).toHaveClass(/idle/);

    // Wait for the 150ms CSS transition to complete, then verify computed opacity
    await new Promise((r) => setTimeout(r, 300));
    const headerOpacity = await overlayPage.evaluate(() => {
      const header = document.querySelector('.shell > .header');
      return header ? getComputedStyle(header).opacity : null;
    });
    expect(headerOpacity).toBe('0');
  });

  test('click on the window exits idle (class removed)', async () => {
    // Wait for idle to engage
    await overlayPage.waitForFunction(
      () => document.body.classList.contains('idle'),
      { timeout: 5000 },
    );
    expect(await overlayPage.evaluate(() => document.body.classList.contains('idle'))).toBe(true);

    // Click anywhere — resetIdleTimer() removes the class
    await overlayPage.mouse.click(300, 300);

    await expect(overlayPage.locator('body')).not.toHaveClass(/idle/);
  });

  test('mouse movement resets timer but does NOT exit idle while already idle', async () => {
    // Wait for idle to engage
    await overlayPage.waitForFunction(
      () => document.body.classList.contains('idle'),
      { timeout: 5000 },
    );

    // Move the mouse — handleIdleMouseMove() skips exitIdle() when already idle
    await overlayPage.mouse.move(100, 100);
    await overlayPage.mouse.move(150, 150);

    // Class should still be present immediately after movement
    await expect(overlayPage.locator('body')).toHaveClass(/idle/);

    // Give it 300ms to confirm it stays idle (it's not reset by mousemove while idle)
    await new Promise((r) => setTimeout(r, 300));
    await expect(overlayPage.locator('body')).toHaveClass(/idle/);
  });

  test('idle_timeout_seconds: 0 disables the feature', async () => {
    // This test uses its own profile with idle disabled
    await electronApp.close().catch(() => {});
    fs.rmSync(profileRoot, { recursive: true, force: true });

    profileRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'oc-idle-disabled-'));
    seedProfile(profileRoot, { ui: { idle_timeout_seconds: 0 } });
    electronApp = await launchApp(profileRoot);
    overlayPage = await waitForOverlayReady(electronApp);

    // Move mouse to establish baseline
    await overlayPage.mouse.move(200, 200);

    // Wait well beyond what the timer would be (idle_timeout_seconds default is 5s;
    // our test default is 1s — but this profile has 0). Wait 2s to be sure.
    await new Promise((r) => setTimeout(r, 2000));

    // Body must NOT have idle class
    await expect(overlayPage.locator('body')).not.toHaveClass(/idle/);
  });
});
