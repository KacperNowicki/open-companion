const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");
const { _electron: electron } = require("@playwright/test");
const electronPath = require("electron");
const { DEFAULT_CONFIG } = require("../../config/defaults");

const ROOT = path.resolve(__dirname, "..", "..");
const ASSET_DIR = path.join(ROOT, "docs", "assets");
const FRAMES_DIR = path.join(ASSET_DIR, "readme-demo-frames");
const OUT_GIF = path.join(ASSET_DIR, "opencompanion-demo.gif");

function writeJson(filePath, data) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(data, null, 2)}\n`, "utf8");
}

function cloneConfig() {
  return JSON.parse(JSON.stringify(DEFAULT_CONFIG));
}

function seedProfile(profileRoot) {
  const config = cloneConfig();
  config.onboarding_complete = true;
  config.updater.check_on_startup = false;
  config.companion.name = "Scout";
  config.companion.user_name = "Reader";
  config.companion.soul.name = "Scout";
  config.companion.soul.user_name = "Reader";
  config.companion.soul.identity = "Warm, curious, and helpful.";
  config.companion.soul.user_context = "";
  config.ui.avatar_model_path = "";
  config.ui.overlay_scale = 70;
  config.brain.provider = "gemma";
  config.brain.model = "qwen2.5:14b";
  config.heartbeat.enabled = false;
  config.voice.tts_enabled = false;
  config.voice.stt_enabled = false;
  config.voice.kokoro_voice = "af_nova";
  writeJson(path.join(profileRoot, "config.json"), config);
  writeJson(path.join(profileRoot, "config.local.json"), {
    ui: {
      avatar_image_path: "",
      avatar_model_path: "companion/assets/avatar/default.glb",
    },
  });
  writeJson(path.join(profileRoot, "ollama-test-state.json"), {
    models: [
      { name: "qwen2.5:14b", size: 8700000000, modified_at: "2026-03-30T10:00:00Z" },
      { name: "llama3.2:3b", size: 2100000000, modified_at: "2026-03-29T09:00:00Z" },
      { name: "nomic-embed-text", size: 300000000, modified_at: "2026-03-28T08:00:00Z" },
    ],
    running_models: [
      { name: "qwen2.5:14b", size_vram: 8600000000, expires_at: "2026-03-30T12:00:00Z" },
    ],
    show: {
      "qwen2.5:14b": {
        family: "Qwen2",
        parameter_size: "14B",
        quantization_level: "Q4_K_M",
        context_length: 8192,
      },
      "llama3.2:3b": {
        family: "Llama",
        parameter_size: "3B",
        quantization_level: "Q4_K_M",
        context_length: 32768,
      },
      "nomic-embed-text": {
        family: "Nomic",
        parameter_size: "137M",
        quantization_level: "F16",
        context_length: 2048,
      },
    },
  });
}

async function waitForWindow(app, titlePattern, timeout = 20000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    for (const page of app.windows()) {
      if (page.isClosed()) {
        continue;
      }
      const title = await page.title().catch(() => "");
      if (titlePattern.test(title)) {
        await page.waitForLoadState("domcontentloaded").catch(() => null);
        return page;
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Timed out waiting for ${titlePattern}`);
}

async function screenshot(page, index, selector = null) {
  const filePath = path.join(FRAMES_DIR, `frame_${String(index).padStart(2, "0")}.png`);
  if (selector) {
    const locator = page.locator(selector);
    await locator.waitFor({ state: "visible", timeout: 15000 });
    await locator.screenshot({ path: filePath });
  } else {
    await page.screenshot({ path: filePath, fullPage: false });
  }
  return filePath;
}

async function openSettings(app, overlayPage) {
  await overlayPage.locator("#settingsButton").click();
  const page = await waitForWindow(app, /Settings/);
  await page.waitForFunction(() => Boolean(window.ocSettingsTest && document.querySelector(".oc-nav-btn[data-tab='persona']")));
  return page;
}

async function openTab(page, tab) {
  await page.locator(`.oc-nav-btn[data-tab="${tab}"]`).click();
  await page.waitForFunction((tabName) => {
    const el = document.getElementById(`page-${tabName}`);
    return Boolean(el && el.classList.contains("active"));
  }, tab);
}

function encodeGif() {
  const normalizedPattern = path.join(FRAMES_DIR, "normalized_%02d.png");
  const palettePath = path.join(FRAMES_DIR, "palette.png");
  const normalize = spawnSync("ffmpeg", [
    "-y",
    "-framerate", "1",
    "-i", path.join(FRAMES_DIR, "frame_%02d.png"),
    "-vf", "scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2:color=0x0d111c,setsar=1",
    normalizedPattern,
  ], {
    cwd: ROOT,
    stdio: "inherit",
  });
  if (normalize.status !== 0) {
    throw new Error(`ffmpeg frame normalization failed with exit code ${normalize.status}`);
  }

  const palette = spawnSync("ffmpeg", [
    "-y",
    "-framerate", "1",
    "-i", normalizedPattern,
    "-vf", "palettegen",
    palettePath,
  ], {
    cwd: ROOT,
    stdio: "inherit",
  });
  if (palette.status !== 0) {
    throw new Error(`ffmpeg palette generation failed with exit code ${palette.status}`);
  }

  const gif = spawnSync("ffmpeg", [
    "-y",
    "-framerate", "1",
    "-i", normalizedPattern,
    "-i", palettePath,
    "-lavfi", "paletteuse=dither=bayer",
    "-loop", "0",
    OUT_GIF,
  ], {
    cwd: ROOT,
    stdio: "inherit",
  });
  if (gif.status !== 0) {
    throw new Error(`ffmpeg gif encode failed with exit code ${gif.status}`);
  }
}

async function main() {
  fs.rmSync(FRAMES_DIR, { recursive: true, force: true });
  fs.mkdirSync(FRAMES_DIR, { recursive: true });
  fs.mkdirSync(ASSET_DIR, { recursive: true });

  const profileRoot = fs.mkdtempSync(path.join(os.tmpdir(), "open-companion-readme-gif-"));
  const dialogQueuePath = path.join(profileRoot, "dialog-queue.json");
  writeJson(dialogQueuePath, []);
  seedProfile(profileRoot);

  let app = null;
  try {
    app = await electron.launch({
      executablePath: electronPath,
      args: ["."],
      cwd: ROOT,
      env: {
        ...process.env,
        ELECTRON_RUN_AS_NODE: undefined,
        OPEN_COMPANION_TEST_MODE: "1",
        OPEN_COMPANION_TEST_PROFILE_DIR: profileRoot,
        OPEN_COMPANION_TEST_DIALOG_QUEUE: dialogQueuePath,
        OPEN_COMPANION_KEYCHAIN_SERVICE: `OpenCompanion-ReadmeGif-${Date.now()}`,
      },
    });

    const overlayPage = await app.firstWindow();
    await overlayPage.waitForLoadState("domcontentloaded");
    await overlayPage.waitForFunction(() => Boolean(window.openCompanionTest && window.__ocOverlayTest && document.getElementById("settingsButton")));
    await overlayPage.waitForTimeout(1000);
    await screenshot(overlayPage, 0);
    await screenshot(overlayPage, 1);

    const settingsPage = await openSettings(app, overlayPage);
    await openTab(settingsPage, "model");
    await settingsPage.waitForTimeout(500);
    await screenshot(settingsPage, 2);
    await openTab(settingsPage, "audio");
    await settingsPage.waitForTimeout(500);
    await screenshot(settingsPage, 3);
    await openTab(settingsPage, "memory");
    await settingsPage.waitForTimeout(500);
    await screenshot(settingsPage, 4);
    await settingsPage.close();

    await overlayPage.bringToFront();
    await overlayPage.waitForTimeout(500);
    await screenshot(overlayPage, 5);
  } finally {
    if (app) {
      await app.close().catch(() => null);
    }
    fs.rmSync(profileRoot, { recursive: true, force: true });
  }

  encodeGif();
  fs.rmSync(FRAMES_DIR, { recursive: true, force: true });
  console.log(`Wrote ${path.relative(ROOT, OUT_GIF)}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
