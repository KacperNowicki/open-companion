const fs = require('fs');

const os = require('os');

const path = require('path');

const keytar = require('keytar');

const { test, expect, _electron: electron } = require('@playwright/test');

const electronPath = require('electron');

const { DEFAULT_CONFIG } = require('../../../config/defaults');

const coverage = require('../fixtures/settings_coverage.json');



test.describe.configure({ mode: 'serial' });



const ROOT = path.resolve(__dirname, '..', '..', '..');

const KEYCHAIN_ACCOUNTS = ['openai_api_key', 'anthropic_api_key', 'gemini_api_key', 'openrouter_api_key', 'custom_api_key'];

const MASKED = '\u2022'.repeat(8);

const TESTED_CONTRACTS = new Set([

  'companion_name', 'pronouns', 'soul_identity', 'backstory', 'relationship', 'user_name', 'user_context',

  'save_regenerates_soul', 'save_triggers_config_reload', 'live_name_reload',

  'default_provider', 'default_model', 'companion_provider', 'companion_model', 'assistant_provider', 'assistant_model', 'temperature', 'max_tokens', 'companion_temperature', 'companion_max_tokens', 'assistant_temperature', 'assistant_max_tokens', 'context_window', 'stream', 'fallback_cpu',

  'openai_api_key_blur_save', 'anthropic_api_key_blur_save', 'custom_url_blur_save', 'gemini_provider_option', 'brain_provider_immediate_save', 'cloud_model_dropdown', 'custom_model_free_text', 'connection_test_button', 'enriched_ollama_labels', 'status_card', 'context_window_warning', 'pull_model_progress', 'pull_model_cancel', 'delete_model',

  'write_back_enabled', 'embedding_enabled', 'max_context_memories', 'extraction_source', 'extraction_provider', 'extraction_base_url', 'extraction_model', 'dream_enabled', 'dream_schedule', 'max_entries',

  'viewer_refresh', 'viewer_delete_entry', 'dream_now', 'reset_memory_confirmation', 'reset_memory_now',

  'heartbeat_enabled', 'heartbeat_interval', 'only_when_idle', 'idle_threshold_minutes',

  'tts_enabled', 'kokoro_voice', 'volume', 'tts_speed', 'stt_enabled', 'whisper_model', 'push_to_talk_key', 'auto_send_on_silence',

  'voice_preview_listen_stop', 'save_live_reload',
  'mode', 'accent_rgb', 'layer_visibility', 'preset_chip', 'windows_accent', 'live_apply'

]);



let electronApp;

let overlayPage;

let profileRoot;

let dialogQueuePath;

let keychainService;



function cloneDefaultConfig() {

  return JSON.parse(JSON.stringify(DEFAULT_CONFIG));

}



function readJson(filePath) {

  return JSON.parse(fs.readFileSync(filePath, 'utf8'));

}



function isPlainObject(value) {

  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);

}



function mergeDeep(target, source) {

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
      result[key] = mergeDeep(isPlainObject(target[key]) ? target[key] : {}, sourceValue);
    } else if (Array.isArray(sourceValue)) {
      result[key] = [...sourceValue];
    } else {
      result[key] = sourceValue;
    }
  }
  return result;

}



function writeJson(filePath, data) {

  fs.mkdirSync(path.dirname(filePath), { recursive: true });

  fs.writeFileSync(filePath, `${JSON.stringify(data, null, 2)}\n`, 'utf8');

}



function configPath() {

  return path.join(profileRoot, 'config.json');

}



function ollamaStatePath() {

  return path.join(profileRoot, 'ollama-test-state.json');

}



function localConfigPath() {

  return path.join(profileRoot, 'config.local.json');

}



function readRuntimeConfig() {

  return mergeDeep(readJson(configPath()), readJson(localConfigPath()));

}



function soulCompanionPath() {

  return path.join(profileRoot, 'companion', 'soul', 'active', 'soul_companion.md');

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

  config.companion.soul.user_context = 'Prefers concise updates.';

  config.ui.avatar_model_path = '';

  config.heartbeat.enabled = false;

  config.voice.tts_enabled = false;

  config.voice.stt_enabled = false;

  config.voice.kokoro_voice = 'af_nova';



  writeJson(path.join(rootDir, 'config.json'), config);

  writeJson(path.join(rootDir, 'config.local.json'), {

    ui: {

      avatar_image_path: '',

      avatar_model_path: 'companion/assets/avatar/default.glb',

    },

  });



  writeJson(path.join(rootDir, 'ollama-test-state.json'), {

    models: [

      {

        name: 'qwen2.5:14b',

        size: 8700000000,

        modified_at: '2026-03-30T10:00:00Z',

      },

      {

        name: 'llama3.2:3b',

        size: 2100000000,

        modified_at: '2026-03-29T09:00:00Z',

      },

      {

        name: 'nomic-embed-text',

        size: 300000000,

        modified_at: '2026-03-28T08:00:00Z',

      },

    ],

    running_models: [

      {

        name: 'qwen2.5:14b',

        size_vram: 8600000000,

        expires_at: '2026-03-30T12:00:00Z',

      },

    ],

    show: {

      'qwen2.5:14b': {

        family: 'Qwen2',

        parameter_size: '14B',

        quantization_level: 'Q4_K_M',

        context_length: 8192,

      },

      'llama3.2:3b': {

        family: 'Llama',

        parameter_size: '3B',

        quantization_level: 'Q4_K_M',

        context_length: 32768,

      },

      'nomic-embed-text': {

        family: 'Nomic',

        parameter_size: '137M',

        quantization_level: 'F16',

        context_length: 2048,

      },

    },

    pull: {

      'cancel-me': {

        chunks: [

          { status: 'Pulling manifest', delay_ms: 25 },

          { status: 'Downloading layers', completed: 200000000, total: 800000000, delay_ms: 250 },

          { status: 'Downloading layers', completed: 500000000, total: 800000000, delay_ms: 250 },

        ],

        final_model: {

          name: 'cancel-me',

          size: 800000000,

          modified_at: '2026-03-30T13:00:00Z',

        },

        final_info: {

          family: 'TinyLlama',

          parameter_size: '1.1B',

          quantization_level: 'Q4_K_M',

          context_length: 4096,

        },

      },

      tinyllama: {

        chunks: [

          { status: 'Pulling manifest', delay_ms: 25 },

          { status: 'Downloading', completed: 300000000, total: 1000000000, delay_ms: 40 },

          { status: 'Verifying', completed: 1000000000, total: 1000000000, delay_ms: 40 },

        ],

        final_model: {

          name: 'tinyllama',

          size: 1000000000,

          modified_at: '2026-03-30T13:05:00Z',

        },

        final_info: {

          family: 'TinyLlama',

          parameter_size: '1.1B',

          quantization_level: 'Q4_K_M',

          context_length: 4096,

        },

      },

      'bad-model': {

        error: 'manifest for bad-model not found',

      },

    },

  });



  fs.mkdirSync(path.join(rootDir, 'companion', 'memory'), { recursive: true });

  fs.writeFileSync(path.join(rootDir, 'companion', 'memory', 'memory.md'), '# Memory\n\n- likes concise updates\n- uses dark mode\n- asks for direct tradeoffs\n- wants verification before sign-off\n', 'utf8');

  fs.mkdirSync(path.join(rootDir, 'runtime-assets', 'kokoro'), { recursive: true });
  fs.writeFileSync(path.join(rootDir, 'runtime-assets', 'kokoro', 'kokoro-v1.0.onnx'), 'test-kokoro-model', 'utf8');
  fs.writeFileSync(path.join(rootDir, 'runtime-assets', 'kokoro', 'voices-v1.0.bin'), 'test-kokoro-voices', 'utf8');

  fs.mkdirSync(path.join(rootDir, 'companion', 'soul', 'active'), { recursive: true });

}



async function clearKeychain(service) {

  for (const account of KEYCHAIN_ACCOUNTS) {

    await keytar.deletePassword(service, account).catch(() => {});

  }

}



async function waitForWindowByTitle(pattern, timeoutMs = 20000) {

  const started = Date.now();

  while (Date.now() - started < timeoutMs) {

    for (const page of electronApp.windows()) {

      if (page.isClosed()) {

        continue;

      }

      const title = await page.title().catch(() => '');

      if (pattern.test(title)) {

        return page;

      }

    }

    await new Promise((resolve) => setTimeout(resolve, 200));

  }

  throw new Error(`Timed out waiting for window title ${pattern}`);

}

async function isSettingsWindow(page) {
  if (!page || page.isClosed()) {
    return false;
  }
  const title = await page.title().catch(() => '');
  if (!/Settings/.test(title)) {
    return false;
  }
  return page.evaluate(() => Boolean(window.ocSettingsTest && document.querySelector('.oc-nav-btn[data-tab="persona"]')))
    .catch(() => false);
}



async function getDiagnostics(page = overlayPage) {

  return page.evaluate(async () => {

    if (window.ocSettingsTest) {

      return window.ocSettingsTest.getBackendDiagnostics();

    }

    if (window.openCompanionTest) {

      return window.openCompanionTest.getBackendDiagnostics();

    }

    throw new Error('No test diagnostics bridge is available in this window.');

  });

}



async function diagnosticCount(page, bucket, type) {

  const diagnostics = await getDiagnostics(page);

  return diagnostics[bucket].filter((entry) => entry.type === type).length;

}



async function waitForDiagnosticCount(page, bucket, type, previousCount) {

  await expect.poll(async () => diagnosticCount(page, bucket, type)).toBeGreaterThan(previousCount);

}



async function waitForOverlayReady() {

  await overlayPage.waitForLoadState('domcontentloaded');

  await overlayPage.waitForFunction(() => Boolean(window.openCompanionTest && window.__ocOverlayTest && document.getElementById('settingsButton')));

  await expect.poll(async () => {

    const companionName = await overlayPage.evaluate(() => window.__ocOverlayTest.getCompanionName());

    return companionName.trim();

  }).toBe('Scout');

}



async function launchTestApp() {

  electronApp = await electron.launch({

    executablePath: electronPath,

    args: ['.'],

    cwd: ROOT,

    env: {

      ...process.env,

      ELECTRON_RUN_AS_NODE: undefined,

      OPEN_COMPANION_TEST_MODE: '1',

      OPEN_COMPANION_TEST_PROFILE_DIR: profileRoot,

      OPEN_COMPANION_TEST_DIALOG_QUEUE: dialogQueuePath,

      OPEN_COMPANION_KEYCHAIN_SERVICE: keychainService,

    },

  });

  return electronApp;

}



async function waitForOnboardingReady() {

  const page = await waitForWindowByTitle(/Onboarding|First Run/);

  await page.waitForLoadState('domcontentloaded');

  await page.waitForFunction(() => Boolean(document.getElementById('companion-name') && document.getElementById('next-button')));

  return page;

}

async function completeOnboardingWithImport(page, {
  companionName = 'Scout',
  importText = '',
} = {}) {
  await page.click('#next-button');
  await page.fill('#companion-name', companionName);
  await page.click('#next-button');
  await page.click('#next-button');
  if (!(await page.locator('#next-button').isEnabled())) {
    await page.click('#runtime-prepare');
  }
  await expect(page.locator('#next-button')).toBeEnabled();
  await page.click('#next-button');
  await page.click('#next-button');
  await page.click('#next-button');

  if (importText) {
    await page.fill('#import-result', importText);
    await page.click('#apply-import');
    await expect(page.locator('#wizard-status')).toContainText('Import saved.');
  }

  await page.click('#next-button');
}



async function openSettingsWindow() {

  const maybeExisting = electronApp.windows().find((page) => !page.isClosed());

  if (maybeExisting) {

    for (const page of electronApp.windows()) {

      if (page.isClosed()) {

        continue;

      }

      if (await isSettingsWindow(page)) {

        return page;

      }

    }

  }



  await overlayPage.click('#settingsButton');

  const started = Date.now();
  while (Date.now() - started < 20000) {
    const page = await waitForWindowByTitle(/Settings/, 2000).catch(() => null);
    if (!page) {
      continue;
    }
    await page.waitForLoadState('domcontentloaded').catch(() => null);
    if (await isSettingsWindow(page)) {
      return page;
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }

  throw new Error('Timed out waiting for the settings window preload.');

}



async function closeSettingsWindow(page) {

  if (!page || page.isClosed()) {

    return;

  }



  const closed = page.waitForEvent('close').catch(() => null);

  try {

    await page.click('#s-close');

  } catch (error) {

    if (!page.isClosed()) {

      throw error;

    }

  }

  await closed;

}



async function openTab(page, tab) {

  await page.click(`.oc-nav-btn[data-tab="${tab}"]`);

  await expect(page.locator(`#page-${tab}`)).toHaveClass(/active/);

}



async function setRangeValue(page, selector, value) {

  await page.locator(selector).evaluate((element, nextValue) => {

    element.value = String(nextValue);

    element.dispatchEvent(new Event('input', { bubbles: true }));

    element.dispatchEvent(new Event('change', { bubbles: true }));

  }, value);

}



async function setChecked(page, selector, checked) {

  await page.locator(selector).evaluate((element, nextValue) => {

    element.checked = Boolean(nextValue);

    element.dispatchEvent(new Event('input', { bubbles: true }));

    element.dispatchEvent(new Event('change', { bubbles: true }));

  }, checked);

}



function queueDialogResults(results) {

  fs.writeFileSync(dialogQueuePath, JSON.stringify(results, null, 2), 'utf8');

}



async function clickModelOption(page, targetId, modelName) {

  await page.locator(`#${targetId}-model-listbox .oc-model-option-main[data-model="${modelName}"]`).click();

}



async function deleteModelOption(page, targetId, modelName) {

  await page.evaluate(() => {
    window.__ocPreviousConfirm = window.confirm;
    window.confirm = () => true;
  });

  await page.locator(`#${targetId}-model-listbox .oc-model-option[data-model="${modelName}"] .oc-btn-danger`).click();

  await page.evaluate(() => {
    if (window.__ocPreviousConfirm) {
      window.confirm = window.__ocPreviousConfirm;
      delete window.__ocPreviousConfirm;
    }
  });

}



function allManifestContractIds() {

  return Object.values(coverage.sections).flatMap((section) => [

    ...section.persisted,

    ...section.actions,

  ]);

}



test.beforeAll(async () => {

  profileRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'open-companion-settings-ui-'));

  dialogQueuePath = path.join(profileRoot, 'dialog-queue.json');

  keychainService = `OpenCompanion-SettingsUITest-${Date.now()}`;

  seedProfile(profileRoot);

  queueDialogResults([]);

  await clearKeychain(keychainService);



  await launchTestApp();



  overlayPage = await electronApp.firstWindow();

  await waitForOverlayReady();

});



test.afterAll(async () => {

  await clearKeychain(keychainService);

  if (electronApp) {

    await electronApp.close();

  }

  if (profileRoot) {

    fs.rmSync(profileRoot, { recursive: true, force: true });

  }

});



test('settings coverage manifest is fully mapped by the UI suite', async () => {

  const manifestIds = allManifestContractIds();

  const missing = manifestIds.filter((id) => !TESTED_CONTRACTS.has(id));

  expect(missing).toEqual([]);

});

test('settings window no longer exposes manual save buttons for heartbeat', async () => {

  const page = await openSettingsWindow();

  await expect(page.locator('[data-save]')).toHaveCount(0);

  await closeSettingsWindow(page);

});



test('persona settings save, regenerate the active soul, and hot-reload the overlay name', async () => {

  const page = await openSettingsWindow();

  await openTab(page, 'persona');

  const reloadCount = await diagnosticCount(page, 'sentMessages', 'config_reload');

  await page.fill('#persona-name', 'Cipher');
  await page.selectOption('#persona-pronouns', 'they/them');
  await page.fill('#persona-user-name', 'Morgan');

  // Write a raw soul file value via the soul textarea
  const rawSoul = '# Soul\n\nName: Cipher\n\nCipher is precise, dry, and quietly protective.';
  await page.fill('#persona-soul-raw', rawSoul);
  await page.locator('#persona-soul-raw').blur();

  await expect(page.locator('#status-persona')).toContainText('Saved');

  await waitForDiagnosticCount(page, 'sentMessages', 'config_reload', reloadCount);
  await expect.poll(async () => overlayPage.evaluate(() => window.__ocOverlayTest.getSceneState())).toMatchObject({
    kind: 'model',
    assetPath: 'app/frontend/assets/talkinghead/avatar.glb',
  });

  await expect.poll(async () => overlayPage.evaluate(() => window.__ocOverlayTest.getCompanionName())).toBe('Cipher');

  await expect.poll(() => readRuntimeConfig().companion.soul).toMatchObject({
    name: 'Cipher',
    pronouns: 'they/them',
    user_name: 'Morgan',
  });

  // Persona identity fields should also sync into the raw soul file.
  await expect.poll(() => fs.existsSync(soulCompanionPath())).toBe(true);
  await expect.poll(() => fs.readFileSync(soulCompanionPath(), 'utf8')).toContain('## OpenCompanion Identity');
  await expect.poll(() => fs.readFileSync(soulCompanionPath(), 'utf8')).toContain('Companion name: Cipher');
  await expect.poll(() => fs.readFileSync(soulCompanionPath(), 'utf8')).toContain('Pronouns: they/them');
  await expect.poll(() => fs.readFileSync(soulCompanionPath(), 'utf8')).toContain('What to call the user: Morgan');
  await closeSettingsWindow(page);

  const reopened = await openSettingsWindow();
  await openTab(reopened, 'persona');

  await expect(reopened.locator('#persona-name')).toHaveValue('Cipher');
  await expect(reopened.locator('#persona-pronouns')).toHaveValue('they/them');
  await expect(reopened.locator('#persona-user-name')).toHaveValue('Morgan');

  // The raw soul editor should reflect the active soul file with the synced identity block.
  await expect.poll(() => reopened.locator('#persona-soul-raw').inputValue()).toContain('## OpenCompanion Identity');
  await expect.poll(() => reopened.locator('#persona-soul-raw').inputValue()).toContain('Companion name: Cipher');

  await closeSettingsWindow(reopened);

});



test('model settings persist through the real UI and API keys save on blur', async () => {
  const page = await openSettingsWindow();
  await openTab(page, 'model');

  await page.selectOption('#brain-provider', 'openai');
  await page.fill('#apikey-openai', 'sk-openai-settings-ui');
  await page.locator('#apikey-openai').blur();
  await expect(page.locator('#confirm-openai')).toBeVisible();

  await page.selectOption('#brain-provider', 'anthropic');
  await page.fill('#apikey-anthropic', 'sk-ant-settings-ui');
  await page.locator('#apikey-anthropic').blur();
  await expect(page.locator('#confirm-anthropic')).toBeVisible();

  await page.selectOption('#brain-provider', 'gemini');
  await page.fill('#apikey-gemini', 'AIza-settings-ui');
  await page.locator('#apikey-gemini').blur();
  await expect(page.locator('#confirm-gemini')).toBeVisible();

  await page.selectOption('#brain-provider', 'custom');
  await page.fill('#apikey-custom-url', 'https://settings-ui.example/v1');
  await page.locator('#apikey-custom-url').blur();

  await page.fill('#apikey-custom', 'sk-custom-settings-ui');
  await page.locator('#apikey-custom').blur();
  await expect(page.locator('#confirm-custom')).toBeVisible();

  await expect.poll(async () => page.evaluate(() => window.ocSettings.getApiKeys())).toMatchObject({
    openai: true,
    anthropic: true,
    gemini: true,
    custom: true,
    custom_url: 'https://settings-ui.example/v1',
  });

  const providerReloadCount = await diagnosticCount(page, 'sentMessages', 'config_reload');
  await page.selectOption('#brain-provider', 'openai');
  await waitForDiagnosticCount(page, 'sentMessages', 'config_reload', providerReloadCount);
  await expect.poll(() => readRuntimeConfig().brain.provider).toBe('openai');
  await expect.poll(() => page.inputValue('#brain-model')).toBe('gpt-4.1');
  await expect(page.locator('#model-section2-title')).toContainText('Active Layers');
  await expect(page.locator('#al-companion-model')).toContainText('gpt-4.1');
  await expect(page.locator('#al-companion-provider')).not.toHaveText('');
  await expect(page.locator('#al-companion-params')).not.toHaveText('');
  await expect(page.locator('#al-companion-tools')).not.toHaveText('');

  const providerReloadCountAfterCloud = await diagnosticCount(page, 'sentMessages', 'config_reload');
  await page.selectOption('#brain-provider', 'gemma');
  await waitForDiagnosticCount(page, 'sentMessages', 'config_reload', providerReloadCountAfterCloud);
  await expect(page.locator('#brain-model-listbox .oc-model-option')).toHaveCount(2);
  await expect(page.locator('#brain-model-listbox')).toContainText('qwen2.5:14b');
  await expect(page.locator('#brain-model-listbox')).toContainText('14B');
  await expect(page.locator('#brain-model-listbox')).not.toContainText('nomic-embed-text');
  await expect(page.locator('#brain-model-listbox .oc-model-option[data-model="qwen2.5:14b"]')).toHaveAttribute('title', /Quantization: Q4_K_M/);
  await expect(page.locator('#al-companion-badge')).toContainText('In VRAM');
  await expect(page.locator('#al-companion-embed')).toContainText('Embedding: ready');

  await closeSettingsWindow(page);
});

test('model context window can switch from auto to a custom value through the token budget field', async () => {
  const page = await openSettingsWindow();
  await openTab(page, 'model');

  await expect(page.locator('#s-ctx-auto-btn')).toHaveClass(/active/);
  await expect(page.locator('#s-ctx')).toHaveJSProperty('readOnly', true);

  await page.click('#s-ctx');

  await expect(page.locator('#s-ctx-auto-btn')).not.toHaveClass(/active/);
  await expect(page.locator('#s-ctx')).toBeEnabled();
  await expect(page.locator('#s-ctx')).toHaveValue('32768');

  await expect.poll(() => readRuntimeConfig().brain.context_window).toBe(32768);

  await page.fill('#s-ctx', '8192');
  await page.locator('#s-ctx').blur();

  await expect.poll(() => readRuntimeConfig().brain.context_window).toBe(8192);

  await closeSettingsWindow(page);

  const reopened = await openSettingsWindow();
  await openTab(reopened, 'model');

  await expect(reopened.locator('#s-ctx-auto-btn')).not.toHaveClass(/active/);
  await expect(reopened.locator('#s-ctx')).toHaveValue('8192');

  await closeSettingsWindow(reopened);
});

test('memory settings persist, refresh, delete entries, and hot-reload after save', async () => {

  const page = await openSettingsWindow();

  await openTab(page, 'memory');



  await page.click('#memory-refresh-btn');

  await expect(page.locator('.memory-section[data-file="memory.md"]')).toBeVisible();

  await expect(page.locator('.memory-entry-delete[data-file="memory.md"]').first()).toBeVisible();

  await page.locator('.memory-entry-delete[data-file="memory.md"]').first().click();

  await expect(page.locator('.memory-section[data-file="memory.md"] .memory-entry')).toHaveCount(3);

  expect(fs.readFileSync(path.join(profileRoot, 'companion', 'memory', 'memory.md'), 'utf8')).not.toContain('likes concise updates');



  const reloadCount = await diagnosticCount(page, 'sentMessages', 'config_reload');



  await setChecked(page, '#s-mem-writeback', false);

  await setChecked(page, '#s-mem-embed', false);

  await setRangeValue(page, '#s-topk', 7);

  await page.selectOption('#s-extraction-source', 'api');

  await page.selectOption('#s-extraction-provider', 'openai');

  await page.fill('#s-extraction-model-remote', 'gpt-4.1-mini');

  await setChecked(page, '#s-dream-enabled', false);

  await page.selectOption('#s-dream-schedule', { label: 'Manual only' });

  await page.fill('#s-max-entries', '321');
  await page.locator('#s-max-entries').blur();

  await expect(page.locator('#status-memory')).toContainText('Saved');

  await waitForDiagnosticCount(page, 'sentMessages', 'config_reload', reloadCount);



  const config = readRuntimeConfig();

  expect(config.memory).toMatchObject({

    enabled: false,

    embedding_enabled: false,

    max_context_memories: 7,

    extraction_source: 'api',

    extraction_provider: 'openai',

    extraction_model: 'gpt-4.1-mini',

    dream_enabled: false,

    dream_schedule: 'Manual only',

    max_entries: 321,

  });



  await closeSettingsWindow(page);

  const reopened = await openSettingsWindow();

  await openTab(reopened, 'memory');

  await expect(reopened.locator('#s-mem-writeback')).not.toBeChecked();

  await expect(reopened.locator('#s-mem-embed')).not.toBeChecked();

  await expect(reopened.locator('#s-extraction-source')).toHaveValue('api');
  await expect(reopened.locator('#s-extraction-provider')).toHaveValue('openai');
  await expect(reopened.locator('#s-extraction-model-remote')).toHaveValue('gpt-4.1-mini');

  await expect(reopened.locator('#s-dream-enabled')).not.toBeChecked();

  await expect(reopened.locator('#s-dream-schedule')).toHaveValue('Manual only');

  await expect(reopened.locator('#s-max-entries')).toHaveValue('321');

  const reloadCountAfterReopen = await diagnosticCount(reopened, 'sentMessages', 'config_reload');
  await reopened.selectOption('#s-extraction-source', 'brain');
  await reopened.fill('#s-extraction-model-remote', '');
  await reopened.locator('#s-extraction-model-remote').blur();
  await expect(reopened.locator('#status-memory')).toContainText('Saved');
  await waitForDiagnosticCount(reopened, 'sentMessages', 'config_reload', reloadCountAfterReopen);

  await expect.poll(() => readRuntimeConfig().memory).toMatchObject({
    extraction_source: 'brain',
    extraction_model: '',
  });

  await expect(reopened.locator('.memory-section[data-file="memory.md"] .memory-entry')).toHaveCount(3);

  await closeSettingsWindow(reopened);

});

test('memory actions can run dream now and reset memory with confirmation', async () => {

  const page = await openSettingsWindow();

  await openTab(page, 'memory');

  await expect(page.locator('#memory-dream-now-btn')).toBeVisible();
  await expect(page.locator('#memory-reset-now-btn')).toBeVisible();
  await expect(page.locator('#rerun-onboarding-btn')).toHaveCount(0);
  await expect(page.locator('#rerun-onboarding-reset-memory-btn')).toHaveCount(0);

  const dreamCount = await diagnosticCount(page, 'sentMessages', 'run_dream');
  await page.click('#memory-dream-now-btn');
  await waitForDiagnosticCount(page, 'sentMessages', 'run_dream', dreamCount);
  await expect(page.locator('#status-memory')).toContainText('Dream');
  await expect(page.locator('#memory-dream-now-btn')).toBeEnabled({ timeout: 30000 });
  await expect(page.locator('#memory-reset-now-btn')).toBeEnabled({ timeout: 30000 });

  const originalMemory = fs.readFileSync(path.join(profileRoot, 'companion', 'memory', 'memory.md'), 'utf8');

  await page.click('#memory-reset-now-btn');
  await expect(page.locator('#memory-reset-modal')).toBeVisible();
  await expect(page.locator('#memory-reset-confirm-copy')).toContainText('Are you sure you want to reset memory?');

  await page.click('#memory-reset-cancel');
  await expect(page.locator('#memory-reset-modal')).toBeHidden();
  expect(fs.readFileSync(path.join(profileRoot, 'companion', 'memory', 'memory.md'), 'utf8')).toBe(originalMemory);

  await page.click('#memory-reset-now-btn');
  await expect(page.locator('#memory-reset-modal')).toBeVisible();
  await page.click('#memory-reset-confirm-btn');

  await expect(page.locator('#memory-reset-modal')).toBeHidden();
  await expect(page.locator('#status-memory')).toContainText('Memory reset');
  await expect(page.locator('#memory-viewer-content')).toContainText('No memories stored yet.');
  expect(fs.readFileSync(path.join(profileRoot, 'companion', 'memory', 'memory.md'), 'utf8')).toBe('# Memory\n');

  await closeSettingsWindow(page);

});



test('heartbeat settings render the slim control set and persist the retained fields', async () => {

  const page = await openSettingsWindow();

  await openTab(page, 'heartbeat');

  await expect(page.locator('#heartbeat-provider')).toHaveCount(0);
  await expect(page.locator('#heartbeat-enable-banner')).toHaveCount(0);
  await expect(page.locator('#heartbeat-blocklist-row')).toHaveCount(0);
  await expect(page.locator('#heartbeat-inject-context')).toHaveCount(0);
  await expect(page.locator('#heartbeat-feed-memory')).toHaveCount(0);

  await setChecked(page, '#heartbeat-enabled', true);
  await page.click('[data-heartbeat-interval="custom"]');
  await expect(page.locator('#heartbeat-custom-interval-row')).toBeVisible();
  await page.fill('#heartbeat-custom-minutes', '45');
  await setChecked(page, '#heartbeat-only-idle', true);
  await expect(page.locator('#heartbeat-idle-threshold-row')).toBeVisible();
  await page.fill('#heartbeat-idle-threshold', '7');

  await expect.poll(async () => {
    const config = readJson(path.join(profileRoot, 'config.local.json'));
    const heartbeat = config.heartbeat || {};
    return {
      enabled: heartbeat.enabled,
      interval: heartbeat.interval,
      onlyIdle: heartbeat.only_when_idle,
      threshold: heartbeat.idle_threshold_minutes,
    };
  }).toMatchObject({
    enabled: true,
    interval: 2700,
    onlyIdle: true,
    threshold: 7,
  });

  await closeSettingsWindow(page);

});



test('audio settings persist, preview switching works, and the live runtime voice updates without restart', async () => {

  const page = await openSettingsWindow();

  await openTab(page, 'audio');



  const firstPreview = page.locator('.voice-card-listen[data-voice-id="af_nova"]');

  const secondPreview = page.locator('.voice-card-listen[data-voice-id="bf_emma"]');

  // Expand the British English / Female group (collapsed by default) before clicking
  const britishFemaleTitle = page.locator('.voice-group-title', { hasText: 'British English / Female' });
  await britishFemaleTitle.click();

  await firstPreview.click();

  await expect(firstPreview).toContainText('Playing');

  await secondPreview.click();

  await expect(secondPreview).toContainText('Playing');

  await page.click('.voice-card[data-voice-id="bf_emma"]');

  await expect(page.locator('.voice-card[data-voice-id="bf_emma"]')).toHaveClass(/selected/);



  const reloadCount = await diagnosticCount(page, 'sentMessages', 'config_reload');



  await setChecked(page, '#s-tts-enabled', false);

  await setRangeValue(page, '#s-vol', 65);

  await setRangeValue(page, '#s-speed', 13);

  await setChecked(page, '#s-stt-enabled', false);

  await page.selectOption('#s-whisper', 'small');

  await page.fill('#s-ptt-key', 'F8');

  await setChecked(page, '#s-auto-send', false);

  await expect(page.locator('#status-audio')).toContainText('Saved');

  await waitForDiagnosticCount(page, 'sentMessages', 'config_reload', reloadCount);

  await expect.poll(async () => {

    const diagnostics = await getDiagnostics(page);

    return diagnostics.latestState.config.voice.kokoro_voice;

  }).toBe('bf_emma');



  const config = readRuntimeConfig();

  expect(config.voice).toMatchObject({

    tts_enabled: false,

    kokoro_voice: 'bf_emma',

    volume: 0.65,

    tts_speed: 1.3,

    stt_enabled: false,

    whisper_model: 'small',

    push_to_talk_key: 'F8',

    auto_send_on_silence: false,

  });



  await closeSettingsWindow(page);

  const reopened = await openSettingsWindow();

  await openTab(reopened, 'audio');

  await expect(reopened.locator('#s-tts-enabled')).not.toBeChecked();

  await expect(reopened.locator('.voice-card[data-voice-id="bf_emma"]')).toHaveClass(/selected/);

  await expect(reopened.locator('#s-stt-enabled')).not.toBeChecked();

  await expect(reopened.locator('#s-whisper')).toHaveValue('small');

  await expect(reopened.locator('#s-ptt-key')).toHaveValue('F8');

  await expect(reopened.locator('#s-auto-send')).not.toBeChecked();

  await closeSettingsWindow(reopened);

});



test('theme controls apply live to settings and overlay, including preset chips and the Windows accent button', async () => {

  const page = await openSettingsWindow();

  await openTab(page, 'themes');



  await page.click('.theme-chip[data-r="239"][data-g="159"][data-b="39"]');

  await expect(page.locator('#s-r')).toHaveValue('239');

  await expect(page.locator('#s-g')).toHaveValue('159');

  await expect(page.locator('#s-b')).toHaveValue('39');



  const expectedWindowsAccent = await page.evaluate(() => window.ocSettings.getWindowsAccent());

  await page.click('#btn-day');

  await page.click('#btn-win-accent');

  await expect(page.locator('#s-r')).toHaveValue(String(expectedWindowsAccent[0]));

  await expect(page.locator('#s-g')).toHaveValue(String(expectedWindowsAccent[1]));

  await expect(page.locator('#s-b')).toHaveValue(String(expectedWindowsAccent[2]));

  await expect(page.locator('#status-themes')).toContainText('Saved');
  await expect.poll(() => readRuntimeConfig().ui.theme).toMatchObject({
    mode: 'day',
    accent_rgb: expectedWindowsAccent,
  });



  const settingsTheme = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--oc-accent-rgb').trim());

  const overlayTheme = await overlayPage.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--accent-rgb').trim());

  const expectedThemeString = expectedWindowsAccent.join(', ');

  expect(settingsTheme).toBe(expectedThemeString);

  expect(overlayTheme).toBe(expectedThemeString);



  await closeSettingsWindow(page);

  const reopened = await openSettingsWindow();

  await openTab(reopened, 'themes');

  await expect(reopened.locator('#btn-day')).toHaveClass(/selected/);

  await expect(reopened.locator('#s-r')).toHaveValue(String(expectedWindowsAccent[0]));

  await expect(reopened.locator('#s-g')).toHaveValue(String(expectedWindowsAccent[1]));

  await expect(reopened.locator('#s-b')).toHaveValue(String(expectedWindowsAccent[2]));

  await closeSettingsWindow(reopened);

});
