const assert = require("assert");
const fs = require("fs");
const path = require("path");
const { deepMerge, DEFAULT_CONFIG } = require("../../config/index");
const { SETTINGS_OPTIONS } = require("../shared/settings-options");
const { runMigrations } = require("../../config/migrate");
const { RuntimeManager } = require("../frontend/runtime-manager");
const { createVoicePreviewService } = require("../frontend/main/voice-previews");
const {
  DEFAULT_WS_URL,
  buildHeaders: buildChatGptOauthWsHeaders,
  buildPayload: buildChatGptOauthWsPayload,
  sanitizeHeaderValue: sanitizeChatGptOauthWsHeaderValue,
} = require("../backend/chatgpt_oauth_ws_bridge");
const {
  DEFAULT_COMPACT_OVERLAY_HEIGHT,
  DEFAULT_COMPACT_OVERLAY_WIDTH,
  DEFAULT_OVERLAY_SCALE,
  dimensionsFromOverlayScale,
  overlayScaleFromDimensions,
  parseOverlayScaleValue,
} = require("../frontend/overlay-scale");

const ROOT = path.resolve(__dirname, "..", "..");
const TEST_RESULTS_ROOT = path.join(ROOT, "app", "tests", "test-results");
fs.mkdirSync(TEST_RESULTS_ROOT, { recursive: true });

{
  const previewsDir = fs.mkdtempSync(path.join(TEST_RESULTS_ROOT, "oc-voice-previews-"));
  fs.writeFileSync(path.join(previewsDir, "af_nova.wav"), "placeholder", "utf8");
  const service = createVoicePreviewService({
    projectRoot: ROOT,
    runtimePaths: {
      VOICE_PREVIEWS_DIR: previewsDir,
      IS_PACKAGED: false,
      BACKEND_GENERATE_PREVIEW_ENTRY: path.join(ROOT, "app", "backend", "generate_voice_preview.py"),
    },
    buildPythonSubprocessEnv: () => ({ pythonCommand: "python", env: process.env }),
  });
  const url = service.getPreviewUrl("af_nova");
  assert.ok(url.startsWith("file:///"), url);
  assert.ok(!url.startsWith("file://D:"), url);
}

const partial = { brain: { model: "gpt-4o" } };
const merged = deepMerge(DEFAULT_CONFIG, partial);
assert.strictEqual(merged.brain.model, "gpt-4o");
assert.strictEqual(merged.brain.provider, "gemma");
assert.strictEqual(merged.memory.extraction_source, "local");
assert.strictEqual(merged.memory.extraction_model, "gemma4:e4b");

const original = { a: { b: 1 } };
const override = { a: { c: 2 } };
const result = deepMerge(original, override);
assert.strictEqual(original.a.c, undefined);
assert.strictEqual(result.a.b, 1);
assert.strictEqual(result.a.c, 2);

const noVersion = { brain: { model: "qwen2.5:14b", api_url: "http://localhost:11434/v1" } };
const migrated = runMigrations(noVersion);
assert.strictEqual(migrated.version, DEFAULT_CONFIG.version);
assert.strictEqual(migrated.brain.base_url, "http://localhost:11434/v1");
assert.strictEqual(migrated.memory.extraction_source, "local");
assert.strictEqual(migrated.memory.extraction_model, "gemma4:e4b");
assert.strictEqual(migrated.brain.context_window, DEFAULT_CONFIG.brain.context_window);
assert.strictEqual(migrated.context.session_summaries.enabled, true);
assert.strictEqual(migrated.context.skills.index_enabled, true);
assert.strictEqual(migrated.context.skills.max_full_skills, DEFAULT_CONFIG.context.skills.max_full_skills);
assert.ok(migrated.brain.layers.assistant, "migration should create brain.layers.assistant");
assert.ok(migrated.layers.assistant, "migration should create layers.assistant");
assert.ok(migrated.tools.overrides.assistant !== undefined, "migration should create tools.overrides.assistant");

const withExistingCtx = { brain: { model: "x", context_window: 4096 } };
const migratedCtx = runMigrations(withExistingCtx);
assert.strictEqual(migratedCtx.brain.context_window, 4096);
assert.strictEqual(DEFAULT_CONFIG.brain.context_window, "auto");

const withExistingContext = { context: { skills: { max_full_skills: 1 } } };
const migratedExistingContext = runMigrations(withExistingContext);
assert.strictEqual(migratedExistingContext.context.skills.max_full_skills, 1);
assert.strictEqual(migratedExistingContext.context.session_summaries.enabled, true);

const migratedLegacyVision = runMigrations({
  companion: { age: "appears mid-20s" },
  vision: { heartbeat_enabled: true, heartbeat_interval: 600 },
  tool_rag: { enabled: true },
});
assert.deepStrictEqual(migratedLegacyVision.heartbeat, {
  enabled: true,
  interval: 600,
  only_when_idle: false,
  idle_threshold_minutes: 5,
});
assert.ok(!("vision" in migratedLegacyVision), "migration should remove stale vision config");
assert.ok(!("tool_rag" in migratedLegacyVision), "migration should remove stale tool_rag config");
assert.ok(!("age" in migratedLegacyVision.companion), "migration should remove stale companion.age");

const migratedLegacyAnimations = runMigrations({
  animations: { idle_pool: ["Idle_3"], llm_clips: { Talk: "old" }, ignored: [] },
});
assert.ok(!("animations" in migratedLegacyAnimations), "migration should remove stale animations config");

const requiredKeys = ["version", "companion", "brain", "memory", "context", "heartbeat", "voice", "ui", "onboarding_complete", "updater"];
for (const key of requiredKeys) {
  assert.ok(key in DEFAULT_CONFIG, `DEFAULT_CONFIG missing key: ${key}`);
}

assert.deepStrictEqual(DEFAULT_CONFIG.settings_options, SETTINGS_OPTIONS);
assert.ok(DEFAULT_CONFIG.settings_options.voices.some((voice) => voice.id === DEFAULT_CONFIG.voice.kokoro_voice));
assert.ok(DEFAULT_CONFIG.settings_options.model_layers.some((layer) => layer.uiId === "assistant" && layer.configKey === "assistant"));
assert.ok(DEFAULT_CONFIG.settings_options.onboarding_models.some((model) => model.layer === "memory" && model.tag === DEFAULT_CONFIG.memory.embedding_model));

function loadFreshConfigModule() {
  const configPath = require.resolve("../../config/index");
  delete require.cache[configPath];
  return require("../../config/index");
}

function testProfileRootEnvOverride() {
  const originalProfileDir = process.env.OPEN_COMPANION_PROFILE_DIR;
  const originalTestProfileDir = process.env.OPEN_COMPANION_TEST_PROFILE_DIR;
  const profileRoot = fs.mkdtempSync(path.join(TEST_RESULTS_ROOT, "oc-profile-"));
  const testProfileRoot = fs.mkdtempSync(path.join(TEST_RESULTS_ROOT, "oc-test-profile-"));

  try {
    process.env.OPEN_COMPANION_PROFILE_DIR = profileRoot;
    process.env.OPEN_COMPANION_TEST_PROFILE_DIR = testProfileRoot;
    const freshConfig = loadFreshConfigModule();

    assert.strictEqual(freshConfig.PROFILE_ROOT, path.resolve(profileRoot));
    assert.strictEqual(freshConfig.CONFIG_PATH, path.join(path.resolve(profileRoot), "config.json"));
    assert.strictEqual(freshConfig.LOCAL_CONFIG_PATH, path.join(path.resolve(profileRoot), "config.local.json"));
  } finally {
    if (originalProfileDir === undefined) delete process.env.OPEN_COMPANION_PROFILE_DIR;
    else process.env.OPEN_COMPANION_PROFILE_DIR = originalProfileDir;
    if (originalTestProfileDir === undefined) delete process.env.OPEN_COMPANION_TEST_PROFILE_DIR;
    else process.env.OPEN_COMPANION_TEST_PROFILE_DIR = originalTestProfileDir;
    delete require.cache[require.resolve("../../config/index")];
    require("../../config/index");
  }
}

async function testRuntimeManagerStateMachine() {
  const events = [];
  const manager = new RuntimeManager({
    profileRoot: fs.mkdtempSync(path.join(TEST_RESULTS_ROOT, "oc-runtime-")),
    runtimePaths: {
      IS_PACKAGED: false,
      PROJECT_ROOT: ROOT,
      BACKEND_BUNDLED_ENTRY: "unused.exe",
      KOKORO_MODEL_PATH: "kokoro-v1.0.onnx",
      KOKORO_VOICES_PATH: "voices-v1.0.bin",
      INSTALL_CACHE_DIR: fs.mkdtempSync(path.join(TEST_RESULTS_ROOT, "oc-runtime-cache-")),
    },
    readTestOllamaState: () => ({
      runtime: {
        steps: [
          { state: "checking", label: "Checking" },
          { state: "downloading_ollama", label: "Downloading Ollama" },
          { state: "ready", label: "Ready" },
        ],
        components: { backend: true, ollama: true, brain_model: true, embedding_model: true, kokoro: true, whisper: true },
      },
    }),
    ensureRuntimeDirectories: () => {},
    getConfig: () => ({}),
    runBackendCommand: async () => {},
    openPath: async () => {},
    onEvent: (payload) => events.push(payload),
    ollamaInstallerUrl: "https://example.invalid/OllamaSetup.exe",
    ollamaInstallerSha256: "",
    kokoroModelUrl: "",
    kokoroModelSha256: "",
    kokoroVoicesUrl: "",
    kokoroVoicesSha256: "",
  });

  await manager.prepare();
  const snapshot = manager.getStatus();

  assert.strictEqual(snapshot.state, "ready");
  assert.strictEqual(snapshot.ready, true);
  assert.deepStrictEqual(events.map((event) => event.state), ["checking", "checking", "downloading_ollama", "ready", "ready"]);
}

async function testRuntimeManagerRefreshStatusClearsStaleReady() {
  const profileRoot = fs.mkdtempSync(path.join(TEST_RESULTS_ROOT, "oc-runtime-refresh-"));
  fs.writeFileSync(path.join(profileRoot, "runtime-status.json"), JSON.stringify({
    state: "ready",
    ready: true,
    components: { backend: true, ollama: true, brain_model: true, embedding_model: true, kokoro: true, whisper: true },
  }, null, 2));

  const manager = new RuntimeManager({
    profileRoot,
    runtimePaths: {
      IS_PACKAGED: false,
      PROJECT_ROOT: ROOT,
      BACKEND_BUNDLED_ENTRY: "unused.exe",
      KOKORO_MODEL_PATH: path.join(profileRoot, "missing", "kokoro-v1.0.onnx"),
      KOKORO_VOICES_PATH: path.join(profileRoot, "missing", "voices-v1.0.bin"),
      INSTALL_CACHE_DIR: fs.mkdtempSync(path.join(TEST_RESULTS_ROOT, "oc-runtime-refresh-cache-")),
    },
    readTestOllamaState: () => ({ installed: false, available: false, models: [] }),
    ensureRuntimeDirectories: () => {},
    getConfig: () => ({}),
    runBackendCommand: async () => {},
    openPath: async () => {},
    onEvent: () => {},
    ollamaInstallerUrl: "https://example.invalid/OllamaSetup.exe",
    ollamaInstallerSha256: "",
    kokoroModelUrl: "",
    kokoroModelSha256: "",
    kokoroVoicesUrl: "",
    kokoroVoicesSha256: "",
  });

  assert.strictEqual(manager.getStatus().ready, true);
  const refreshed = await manager.refreshStatus();
  assert.strictEqual(refreshed.ready, false);
  assert.strictEqual(refreshed.state, "idle");
  assert.strictEqual(refreshed.components.ollama, false);
  assert.strictEqual(refreshed.components.kokoro, false);
  assert.strictEqual(refreshed.components.whisper, false);
}

function testIpcHandlersRegistered() {
  const mainJsPath = path.join(ROOT, "app", "frontend", "main.js");
  const mainJsContent = fs.readFileSync(mainJsPath, "utf8");
  const requiredHandlers = [
    "backend:resetSession",
    "brain:listPulledModels",
    "brain:systemMemory",
    "brain:searchRegistry",
    "brain:activeModelCapabilities",
    "oauth:chatgpt:login",
    "oauth:chatgpt:logout",
    "oauth:chatgpt:status",
  ];
  for (const handlerName of requiredHandlers) {
    assert.ok(mainJsContent.includes(`ipcMain.handle("${handlerName}"`));
  }
}

function testPreloadExposesSessionReset() {
  const preloadPath = path.join(ROOT, "app", "frontend", "preload.js");
  const preloadContent = fs.readFileSync(preloadPath, "utf8");
  assert.ok(preloadContent.includes("resetSession: (layer, preserveSummary) => ipcRenderer.invoke(\"backend:resetSession\", layer, preserveSummary)"));
}

function testOverlayScaleHelperExistsAndMapsMidpointToLegacySize() {
  assert.strictEqual(DEFAULT_OVERLAY_SCALE, 50);
  assert.deepStrictEqual(dimensionsFromOverlayScale(50), { width: 360, height: 516 });
  assert.deepStrictEqual(dimensionsFromOverlayScale(0), {
    width: DEFAULT_COMPACT_OVERLAY_WIDTH,
    height: DEFAULT_COMPACT_OVERLAY_HEIGHT,
  });
  assert.deepStrictEqual(dimensionsFromOverlayScale(100), { width: 540, height: 774 });
  assert.strictEqual(overlayScaleFromDimensions(360, 516), 50);
}

function testOverlayScaleParserPreservesZeroAndFallsBackOnlyForMissingValues() {
  assert.strictEqual(parseOverlayScaleValue("0"), 0);
  assert.strictEqual(parseOverlayScaleValue(0), 0);
  assert.strictEqual(parseOverlayScaleValue(undefined), DEFAULT_OVERLAY_SCALE);
  assert.strictEqual(parseOverlayScaleValue(null), DEFAULT_OVERLAY_SCALE);
  assert.strictEqual(parseOverlayScaleValue(""), DEFAULT_OVERLAY_SCALE);
}

function testChatGptOauthWebsocketBridgeBuilders() {
  const headers = buildChatGptOauthWsHeaders({
    access_token: " \r\ntoken-\u0000123\t",
    request_id: "request-1",
    session_id: "session-1",
  });
  assert.strictEqual(headers.Authorization, "Bearer token-123");
  assert.strictEqual(headers["OpenAI-Beta"], "responses-websocket=v1");
  assert.strictEqual(headers.originator, "openclaw");
  assert.strictEqual(headers.version, "1.0.0");
  assert.strictEqual(headers["User-Agent"], "openclaw/1.0.0");
  assert.strictEqual(headers["x-client-request-id"], "request-1");
  assert.strictEqual(headers["x-openclaw-session-id"], "session-1");
  assert.strictEqual(sanitizeChatGptOauthWsHeaderValue(" \r\nabc\u0000\t "), "abc");

  const payload = buildChatGptOauthWsPayload({
    model: "gpt-5.4",
    input: [{ type: "message", role: "user", content: "hi" }],
    instructions: "Stay concise.",
    tools: [{ type: "function", name: "demo_tool", parameters: { type: "object", properties: {} } }],
    tool_choice: "auto",
    previous_response_id: "resp_123",
    temperature: 0.5,
    max_output_tokens: 256,
    metadata: { test: "yes" },
  });
  assert.strictEqual(DEFAULT_WS_URL, "wss://api.openai.com/v1/responses");
  assert.strictEqual(payload.type, "response.create");
  assert.strictEqual(payload.model, "gpt-5.4");
  assert.strictEqual(payload.store, false);
  assert.strictEqual(payload.previous_response_id, "resp_123");
  assert.strictEqual(payload.temperature, 0.5);
  assert.strictEqual(payload.max_output_tokens, 256);
  assert.strictEqual(payload.tools[0].name, "demo_tool");
}

function testChatGptOauthLoginFlowMatchesCodexOAuth() {
  const mainJsPath = path.join(ROOT, "app", "frontend", "main.js");
  const mainJsContent = fs.readFileSync(mainJsPath, "utf8");
  assert.ok(mainJsContent.includes('const CHATGPT_OAUTH_SCOPE = "openid profile email offline_access";'));
  assert.ok(mainJsContent.includes('const CHATGPT_OAUTH_ORIGINATOR = process.env.CHATGPT_OAUTH_ORIGINATOR || "openclaw";'));
  assert.ok(mainJsContent.includes('const CHATGPT_OAUTH_AUTH_CLAIM = "https://api.openai.com/auth";'));
  assert.ok(mainJsContent.includes('new URLSearchParams({'));
  assert.ok(mainJsContent.includes('"Content-Type": "application/x-www-form-urlencoded"'));
  assert.ok(mainJsContent.includes('authUrl.searchParams.set("id_token_add_organizations", "true");'));
  assert.ok(mainJsContent.includes('authUrl.searchParams.set("codex_cli_simplified_flow", "true");'));
  assert.ok(mainJsContent.includes('authUrl.searchParams.set("originator", CHATGPT_OAUTH_ORIGINATOR);'));
  assert.ok(mainJsContent.includes('authClaims?.chatgpt_account_id'));
}

function testChatGptOauthClientIdSupportsPublicFallback() {
  const mainJsPath = path.join(ROOT, "app", "frontend", "main.js");
  const mainJsContent = fs.readFileSync(mainJsPath, "utf8");
  const providerPath = path.join(ROOT, "app", "backend", "providers", "chatgpt_oauth.py");
  const providerContent = fs.readFileSync(providerPath, "utf8");
  const envExamplePath = path.join(ROOT, ".env.example");
  const envExampleContent = fs.readFileSync(envExamplePath, "utf8");

  assert.ok(mainJsContent.includes('const CHATGPT_OAUTH_DEFAULT_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann";'));
  assert.ok(mainJsContent.includes("process.env.CHATGPT_OAUTH_CLIENT_ID || CHATGPT_OAUTH_DEFAULT_CLIENT_ID"));
  assert.ok(mainJsContent.includes('function requireChatGptOauthClientId() {'));
  assert.ok(mainJsContent.includes('CHATGPT_OAUTH_CLIENT_ID is not set.'));
  assert.ok(mainJsContent.includes('app_EMoamEEZ73f0CkXaXp7hrann'));

  assert.ok(providerContent.includes('def _get_chatgpt_oauth_client_id() -> str:'));
  assert.ok(providerContent.includes('def _require_chatgpt_oauth_client_id() -> str:'));
  assert.ok(providerContent.includes('DEFAULT_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"'));
  assert.ok(providerContent.includes('CHATGPT_OAUTH_CLIENT_ID'));
  assert.ok(providerContent.includes('or DEFAULT_CLIENT_ID'));

  assert.ok(envExampleContent.includes("Copy this file to `.env`"));
}

function testBackendEnvIncludesNodeBridgeSettings() {
  const mainJsPath = path.join(ROOT, "app", "frontend", "main.js");
  const mainJsContent = fs.readFileSync(mainJsPath, "utf8");
  assert.ok(mainJsContent.includes('env.OPEN_COMPANION_NODE_PATH = process.execPath;'));
  assert.ok(mainJsContent.includes('env.OPEN_COMPANION_NODE_MODE = "electron";'));
}

function testChatGptOauthModelCatalogIsSharedLocally() {
  const catalogPath = path.join(ROOT, "app", "shared", "chatgpt_oauth_models.json");
  const catalog = JSON.parse(fs.readFileSync(catalogPath, "utf8"));
  assert.ok(Array.isArray(catalog));
  assert.deepStrictEqual(
    catalog.map((entry) => entry.id),
    [
      "gpt-5.4",
      "gpt-5.2-codex",
      "gpt-5.1-codex-max",
      "gpt-5.4-mini",
      "gpt-5.3-codex",
      "gpt-5.2",
      "gpt-5.1-codex-mini",
    ]
  );

  const providerModelsContent = fs.readFileSync(path.join(ROOT, "app", "frontend", "main", "provider-models.js"), "utf8");
  const preloadContent = fs.readFileSync(path.join(ROOT, "app", "frontend", "settings-preload.js"), "utf8");
  const settingsRendererContent = fs.readFileSync(path.join(ROOT, "app", "frontend", "settings-renderer.js"), "utf8");

  assert.ok(providerModelsContent.includes('require("../../shared/chatgpt_oauth_models.json")'));
  assert.ok(preloadContent.includes('require("../shared/chatgpt_oauth_models.json")'));
  assert.ok(!settingsRendererContent.includes('const CHATGPT_OAUTH_MODELS = ["gpt-5.4", "gpt-5.3", "gpt-5.2"];'));
  assert.ok(catalog.some((entry) => Array.isArray(entry.aliases) && entry.aliases.includes("gpt-5.3")), "catalog should preserve the stale gpt-5.3 alias for auto-migration");
}

function testSettingsOauthMarkupUsesPlainQuotes() {
  const settingsHtmlPath = path.join(ROOT, "app", "frontend", "settings.html");
  const settingsHtml = fs.readFileSync(settingsHtmlPath, "utf8");
  const requiredSnippets = [
    'id="chatgpt-oauth-card"',
    'id="chatgpt-oauth-connect-btn"',
    'id="chatgpt-oauth-disconnect-btn"',
    'id="model-ollama-section"',
  ];
  for (const snippet of requiredSnippets) {
    assert.ok(settingsHtml.includes(snippet), `settings.html missing expected markup: ${snippet}`);
  }
  assert.ok(!settingsHtml.includes('id=”chatgpt-oauth-card”'), "settings.html should not use curly quotes for OAuth card markup");
  assert.ok(!settingsHtml.includes('id=”chatgpt-oauth-connect-btn”'), "settings.html should not use curly quotes for OAuth connect button");
}

function testOverlayScaleSettingsAndNoCustomResizeGrip() {
  const overlayPath = path.join(ROOT, "app", "frontend", "overlay.html");
  const overlayContent = fs.readFileSync(overlayPath, "utf8");
  const settingsPath = path.join(ROOT, "app", "frontend", "settings.html");
  const settingsContent = fs.readFileSync(settingsPath, "utf8");
  const settingsRendererPath = path.join(ROOT, "app", "frontend", "settings-renderer.js");
  const settingsRendererContent = fs.readFileSync(settingsRendererPath, "utf8");
  const preloadPath = path.join(ROOT, "app", "frontend", "preload.js");
  const preloadContent = fs.readFileSync(preloadPath, "utf8");
  const mainPath = path.join(ROOT, "app", "frontend", "main.js");
  const mainContent = fs.readFileSync(mainPath, "utf8");

  assert.ok(overlayContent.includes('class="header-btn dnd-button" id="dndButton"'), "DND button should keep the legacy class used by its state styles");
  assert.ok(overlayContent.includes('class="header-btn history-toggle-button" id="historyToggleButton"'), "history button should keep the legacy class used by its state styles");
  assert.ok(overlayContent.includes('class="header-actions"'), "header actions pill should remain present");
  assert.ok(!overlayContent.includes('id="resize-grip"'), "overlay should not expose a custom resize grip anymore");
  assert.ok(settingsContent.includes('id="s-overlay-scale"'), "settings should expose an overlay scale slider");
  assert.ok(settingsContent.includes('id="s-overlay-scale-val"'), "settings should expose an overlay scale value label");
  assert.ok(settingsRendererContent.includes('wireSlider("s-overlay-scale", "s-overlay-scale-val"'), "settings renderer should wire the overlay scale slider");
  assert.ok(settingsRendererContent.includes("overlay_scale"), "settings renderer should persist overlay scale");
  assert.ok(settingsRendererContent.includes("parseOverlayScaleValue"), "settings renderer should use the shared overlay scale parser");
  assert.ok(!settingsRendererContent.includes('Number.parseInt($("s-overlay-scale").value, 10) || 50'), "settings save path should not collapse 0 back to the default scale");
  assert.ok(!settingsRendererContent.includes('Number.parseInt(config.ui?.overlay_scale, 10) || 50'), "settings load path should not collapse a persisted 0 back to the default scale");
  assert.ok(!preloadContent.includes('startResizeDrag:'), "overlay preload should not expose custom resize start IPC");
  assert.ok(!preloadContent.includes('updateResizeDrag:'), "overlay preload should not expose custom resize update IPC");
  assert.ok(!preloadContent.includes('stopResizeDrag:'), "overlay preload should not expose custom resize stop IPC");
  assert.ok(mainContent.includes('resizable: false'), "overlay window should be fixed-size again");
  assert.ok(!mainContent.includes('ipcMain.on("ui:startResizeDrag"'), "main should not handle custom resize start anymore");
  assert.ok(!mainContent.includes('ipcMain.on("ui:updateResizeDrag"'), "main should not handle custom resize updates anymore");
  assert.ok(!mainContent.includes('ipcMain.on("ui:stopResizeDrag"'), "main should not handle custom resize stop anymore");
}

function testOverlayCompactLayoutBreakpointsStayInsideTheWindow() {
  const overlayPath = path.join(ROOT, "app", "frontend", "overlay.html");
  const overlayContent = fs.readFileSync(overlayPath, "utf8");
  const scenePath = path.join(ROOT, "app", "frontend", "talkinghead-scene.js");
  const sceneContent = fs.readFileSync(scenePath, "utf8");
  const preloadPath = path.join(ROOT, "app", "frontend", "preload.js");
  const preloadContent = fs.readFileSync(preloadPath, "utf8");
  const rendererPath = path.join(ROOT, "app", "frontend", "renderer.js");
  const rendererContent = fs.readFileSync(rendererPath, "utf8");

  assert.ok(overlayContent.includes('class="shell-viewport"'), "overlay should render inside a fixed viewport wrapper");
  assert.ok(overlayContent.includes("transform: scale(var(--overlay-ui-scale));"), "shell should scale uniformly instead of reflowing at smaller slider sizes");
  assert.ok(overlayContent.includes("transform-origin: top left;"), "shell scaling should anchor from the top-left corner");
  assert.ok(!overlayContent.includes("@media (max-width: 560px)"), "slider-driven scaling should not rely on medium compact breakpoints anymore");
  assert.ok(!overlayContent.includes("@media (max-width: 460px)"), "slider-driven scaling should not rely on narrow compact breakpoints anymore");
  assert.ok(overlayContent.includes("#avatar-host canvas"), "avatar canvas should be explicitly constrained to the scene bounds");
  assert.ok(sceneContent.includes('import { TalkingHead } from "@met4citizen/talkinghead";'), "scene controller should use the TalkingHead renderer");
  assert.ok(rendererContent.includes('createTalkingHeadScene'), "renderer should use the TalkingHead scene controller");
  assert.ok(preloadContent.includes("getOverlayScaleFactor"), "overlay preload should expose the shared overlay scale factor helper");
  assert.ok(rendererContent.includes("--overlay-ui-scale"), "renderer should apply the overlay scale factor to the document");
}

function testReminderEventsPreserveBackendTtsAudio() {
  const wrapperContent = fs.readFileSync(path.join(ROOT, "app", "backend", "wrapper.py"), "utf8");
  const rendererContent = fs.readFileSync(path.join(ROOT, "app", "frontend", "renderer.js"), "utf8");

  assert.ok(wrapperContent.includes('event.get("type") == "reminder_fired"'), "wrapper should normalize fired reminder events");
  assert.ok(wrapperContent.includes('event.get("type") == "reminders_missed_batch"'), "wrapper should normalize missed reminder batches");
  assert.ok(wrapperContent.includes("event = self.runtime._attach_tts_to_payload(event)"), "background events should pass through backend TTS");
  assert.ok(rendererContent.includes("audio_b64: payload.audio_b64"), "renderer reminder handlers should preserve backend Kokoro audio");
}

function testRuntimeManagerMemoryComponentResolution() {
  const manager = new RuntimeManager({
    profileRoot: fs.mkdtempSync(path.join(TEST_RESULTS_ROOT, "oc-runtime-memory-")),
    runtimePaths: {
      IS_PACKAGED: false,
      PROJECT_ROOT: ROOT,
      BACKEND_BUNDLED_ENTRY: "unused.exe",
      KOKORO_MODEL_PATH: "kokoro-v1.0.onnx",
      KOKORO_VOICES_PATH: "voices-v1.0.bin",
      INSTALL_CACHE_DIR: fs.mkdtempSync(path.join(TEST_RESULTS_ROOT, "oc-runtime-memory-cache-")),
    },
    readTestOllamaState: () => ({ installed: true, available: true, models: [] }),
    ensureRuntimeDirectories: () => {},
    getConfig: () => ({}),
    runBackendCommand: async () => {},
    openPath: async () => {},
    onEvent: () => {},
    ollamaInstallerUrl: "https://example.invalid/OllamaSetup.exe",
    ollamaInstallerSha256: "",
    kokoroModelUrl: "",
    kokoroModelSha256: "",
    kokoroVoicesUrl: "",
    kokoroVoicesSha256: "",
  });

  assert.strictEqual(
    manager._requiredComponents({
      brain: { provider: "openai", model: "gpt-4.1" },
      memory: { enabled: true, embedding_enabled: false, extraction_source: "api", extraction_provider: "openai" },
    }).ollama,
    false,
    "cloud extraction without embeddings should not require Ollama"
  );
  assert.strictEqual(manager._requiredComponents({}).kokoro, true, "setup should require Kokoro assets");
  assert.strictEqual(manager._requiredComponents({}).whisper, true, "setup should require Whisper prefetch");

  assert.strictEqual(
    manager._requiredComponents({
      brain: { provider: "openai", model: "gpt-4.1" },
      memory: { enabled: true, embedding_enabled: false, extraction_source: "local", extraction_model: "llama3.2:3b" },
    }).ollama,
    true,
    "local extraction should require Ollama"
  );

  assert.strictEqual(
    manager._requiredComponents({
      brain: { provider: "gemma", model: "qwen2.5:14b" },
      memory: { enabled: true, embedding_enabled: false, extraction_source: "brain" },
    }).ollama,
    true,
    "brain extraction should require Ollama when the active brain is local"
  );
}

const legacyLayerConfig = {
  version: "1.1.0",
  brain: {
    layers: {
      companion: { provider: "", model: "" },
      assistant_low: { provider: "gemma", model: "fast_model" },
      assistant_high: { provider: "gemma", model: "strong_model" },
      pc_doctor: { provider: "gemma", model: "doctor_model" },
    },
  },
  layers: {
    assistant_low: { enabled: true, display_name: "Assistant" },
    pc_doctor: { enabled: true, display_name: "PC Doctor" },
  },
  tools: { overrides: { companion: {}, assistant_low: { run_terminal: false }, pc_doctor: { run_windows_terminal: true } }, custom: [] },
};
const collapsed = runMigrations(legacyLayerConfig);
assert.strictEqual(collapsed.brain.layers.assistant.model, "fast_model", "legacy worker config should collapse to assistant");
assert.ok(!collapsed.brain.layers.assistant_low, "legacy worker keys should be removed from migrated config");
assert.ok(collapsed.layers.assistant, "layers.assistant should exist after collapse");
assert.ok(collapsed.tools.overrides.assistant !== undefined, "assistant tool overrides should exist after collapse");

const freshMigrated = runMigrations({});
assert.ok(freshMigrated.brain.layers.assistant, "fresh migration should have assistant brain config");
assert.ok(freshMigrated.layers.assistant, "fresh migration should have assistant layer config");

const idempotentMigrated = runMigrations({
  version: "1.1.0",
  brain: { layers: { companion: { provider: "", model: "" }, assistant: { provider: "gemma", model: "steady_model" } } },
  layers: { assistant: { enabled: true } },
  tools: { overrides: { companion: {}, assistant: {} }, custom: [] },
});
assert.strictEqual(idempotentMigrated.brain.layers.assistant.model, "steady_model");

(async () => {
  testProfileRootEnvOverride();
  await testRuntimeManagerStateMachine();
  await testRuntimeManagerRefreshStatusClearsStaleReady();
  testRuntimeManagerMemoryComponentResolution();
testIpcHandlersRegistered();
testPreloadExposesSessionReset();
testOverlayScaleHelperExistsAndMapsMidpointToLegacySize();
testOverlayScaleParserPreservesZeroAndFallsBackOnlyForMissingValues();
testChatGptOauthWebsocketBridgeBuilders();
testChatGptOauthLoginFlowMatchesCodexOAuth();
testChatGptOauthClientIdSupportsPublicFallback();
  testBackendEnvIncludesNodeBridgeSettings();
  testChatGptOauthModelCatalogIsSharedLocally();
  testSettingsOauthMarkupUsesPlainQuotes();
  testOverlayScaleSettingsAndNoCustomResizeGrip();
  testOverlayCompactLayoutBreakpointsStayInsideTheWindow();
  testReminderEventsPreserveBackendTtsAudio();
  console.log("All config tests passed.");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
