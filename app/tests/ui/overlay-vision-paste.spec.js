const fs = require("fs");
const os = require("os");
const path = require("path");
const { test, expect, _electron: electron } = require("@playwright/test");
const electronPath = require("electron");
const { DEFAULT_CONFIG } = require("../../../config/defaults");

test.describe.configure({ mode: "serial" });

const ROOT = path.resolve(__dirname, "..", "..", "..");
const PNG_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO5qv6cAAAAASUVORK5CYII=";

function cloneDefaultConfig() {
  return JSON.parse(JSON.stringify(DEFAULT_CONFIG));
}

function writeJson(filePath, data) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(data, null, 2)}\n`, "utf8");
}

function seedProfile(rootDir) {
  const config = cloneDefaultConfig();
  config.onboarding_complete = true;
  config.updater.check_on_startup = false;
  config.companion.name = "Scout";
  config.companion.soul.name = "Scout";
  config.companion.soul.identity = "Warm, curious, and slightly playful.";
  config.companion.soul.relationship = "Trusted companion.";
  config.companion.soul.user_name = "Tester";
  config.ui.avatar_model_path = "";
  config.ui.idle_timeout_seconds = 0;
  config.brain.provider = "gemma";
  config.brain.model = "gemma4:e4b";
  config.brain.layers.companion.provider = "gemma";
  config.brain.layers.companion.model = "gemma4:e4b";
  config.brain.layers.assistant.provider = "gemma";
  config.brain.layers.assistant.model = "gemma4:26b";

  writeJson(path.join(rootDir, "config.json"), config);
  writeJson(path.join(rootDir, "config.local.json"), {
    ui: {
      avatar_image_path: "",
      avatar_model_path: "",
    },
  });

  writeJson(path.join(rootDir, "ollama-test-state.json"), {
    models: [
      { name: "gemma4:e4b", size: 7000000000, modified_at: "2026-03-30T10:00:00Z" },
      { name: "gemma4:26b", size: 18000000000, modified_at: "2026-03-30T10:00:00Z" },
    ],
    running_models: [
      { name: "gemma4:e4b", size_vram: 7000000000, expires_at: "2026-03-30T12:00:00Z" },
      { name: "gemma4:26b", size_vram: 18000000000, expires_at: "2026-03-30T12:00:00Z" },
    ],
    show: {
      "gemma4:e4b": {
        family: "Gemma",
        parameter_size: "4B",
        quantization_level: "Q4_K_M",
        context_length: 32768,
        capabilities: ["tools", "vision"],
      },
      "gemma4:26b": {
        family: "Gemma",
        parameter_size: "26B",
        quantization_level: "Q4_K_M",
        context_length: 32768,
        capabilities: ["tools", "vision"],
      },
    },
  });

  fs.mkdirSync(path.join(rootDir, "companion", "memory"), { recursive: true });
  fs.writeFileSync(path.join(rootDir, "companion", "memory", "memory.md"), "- test user\n", "utf8");
  fs.mkdirSync(path.join(rootDir, "runtime-assets", "kokoro"), { recursive: true });
  fs.writeFileSync(path.join(rootDir, "runtime-assets", "kokoro", "kokoro-v1.0.onnx"), "test", "utf8");
  fs.writeFileSync(path.join(rootDir, "runtime-assets", "kokoro", "voices-v1.0.bin"), "test", "utf8");
  fs.mkdirSync(path.join(rootDir, "companion", "soul", "active"), { recursive: true });
}

async function launchApp(profile) {
  return electron.launch({
    executablePath: electronPath,
    args: ["."],
    cwd: ROOT,
    env: {
      ...process.env,
      ELECTRON_RUN_AS_NODE: undefined,
      OPEN_COMPANION_TEST_MODE: "1",
      OPEN_COMPANION_TEST_PROFILE_DIR: profile,
    },
  });
}

async function waitForOverlayReady(app) {
  const started = Date.now();
  let page = null;
  while (Date.now() - started < 30000) {
    for (const window of app.windows()) {
      if (window.isClosed()) continue;
      const title = await window.title().catch(() => "");
      if (!/Settings|Onboarding/i.test(title)) {
        page = window;
        break;
      }
    }
    if (page) break;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  if (!page) {
    throw new Error("Could not find overlay window");
  }

  await page.waitForLoadState("domcontentloaded");
  await page.waitForFunction(
    () => Boolean(window.openCompanionTest && window.__ocOverlayTest && document.getElementById("settingsButton")),
    { timeout: 30000 },
  );

  await expect.poll(async () => {
    const name = await page.evaluate(() => window.__ocOverlayTest.getCompanionName());
    return name.trim();
  }).toBe("Scout");

  return page;
}

async function dispatchImagePaste(page) {
  await page.evaluate((base64) => {
    const input = document.getElementById("textInput");
    const bytes = Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
    const file = new File([bytes], "pasted-image.png", { type: "image/png" });
    const clipboardData = new DataTransfer();
    clipboardData.items.add(file);
    input.focus();
    input.dispatchEvent(new ClipboardEvent("paste", {
      bubbles: true,
      cancelable: true,
      clipboardData,
    }));
  }, PNG_BASE64);
}

async function getDiagnostics(page) {
  return page.evaluate(() => window.openCompanionTest.getBackendDiagnostics());
}

test.describe("overlay vision paste", () => {
  let electronApp;
  let overlayPage;
  let profileRoot;

  test.afterEach(async () => {
    await electronApp?.close().catch(() => {});
    if (profileRoot) {
      fs.rmSync(profileRoot, { recursive: true, force: true });
    }
  });

  test("paste-to-chat attaches images for companion and assistant", async () => {
    profileRoot = fs.mkdtempSync(path.join(os.tmpdir(), "oc-overlay-vision-"));
    seedProfile(profileRoot);
    electronApp = await launchApp(profileRoot);
    overlayPage = await waitForOverlayReady(electronApp);

    await expect.poll(
      async () => overlayPage.evaluate(() => window.__ocOverlayTest.getVisionCapability()),
      { timeout: 30000 },
    ).toBe(true);

    await dispatchImagePaste(overlayPage);
    await expect(overlayPage.locator("#image-preview-strip")).toBeVisible();
    await expect.poll(
      async () => overlayPage.evaluate(() => window.__ocOverlayTest.getPendingImagePresent()),
      { timeout: 30000 },
    ).toBe(true);
    await overlayPage.click("#image-preview-remove");
    await expect(overlayPage.locator("#image-preview-strip")).toBeHidden();
    await expect.poll(
      async () => overlayPage.evaluate(() => window.__ocOverlayTest.getPendingImagePresent()),
      { timeout: 30000 },
    ).toBe(false);

    const sendCountBeforeCompanion = (await getDiagnostics(overlayPage)).sentMessages.length;
    await dispatchImagePaste(overlayPage);
    await expect(overlayPage.locator("#image-preview-strip")).toBeVisible();
    await expect.poll(
      async () => overlayPage.evaluate(() => window.__ocOverlayTest.getPendingImagePresent()),
      { timeout: 30000 },
    ).toBe(true);
    await overlayPage.fill("#textInput", "Describe this image.");
    await overlayPage.click("#sendButton");
    await expect.poll(async () => (await getDiagnostics(overlayPage)).sentMessages.length).toBeGreaterThan(sendCountBeforeCompanion);
    let diagnostics = await getDiagnostics(overlayPage);
    let lastMessage = diagnostics.sentMessages[diagnostics.sentMessages.length - 1];
    expect(lastMessage).toMatchObject({ layer: "companion", hasImage: true });

    await electronApp.close();

    electronApp = await launchApp(profileRoot);
    overlayPage = await waitForOverlayReady(electronApp);

    await overlayPage.selectOption("#targetLayerSelect", "assistant");
    await expect.poll(
      async () => overlayPage.evaluate(() => window.__ocOverlayTest.getVisionCapability()),
      { timeout: 30000 },
    ).toBe(true);

    const sendCountBeforeAssistant = (await getDiagnostics(overlayPage)).sentMessages.length;
    await dispatchImagePaste(overlayPage);
    await expect(overlayPage.locator("#image-preview-strip")).toBeVisible();
    await overlayPage.fill("#textInput", "Describe this image too.");
    await overlayPage.click("#sendButton");
    await expect.poll(async () => (await getDiagnostics(overlayPage)).sentMessages.length).toBeGreaterThan(sendCountBeforeAssistant);
    diagnostics = await getDiagnostics(overlayPage);
    lastMessage = diagnostics.sentMessages[diagnostics.sentMessages.length - 1];
    expect(lastMessage).toMatchObject({ layer: "assistant", hasImage: true });
  });
});
