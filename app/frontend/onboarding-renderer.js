const DEFAULT_SETTINGS_OPTIONS = window.ocOnboarding?.options || {};

let MODELS = [
  {
    id: "companion",
    tag: "gemma4:e4b",
    label: "Companion Model",
    desc: "Lightweight conversational model. Powers the persistent companion layer.",
    size: "~4 GB",
    layer: "companion",
  },
  {
    id: "assistant",
    tag: "gemma4:26b",
    label: "Assistant Model",
    desc: "Larger execution model. Handles complex tasks, file operations, and tool use.",
    size: "~17 GB",
    layer: "assistant",
  },
  {
    id: "embedding",
    tag: "nomic-embed-text",
    label: "Embedding Model",
    desc: "Tiny vector model for memory retrieval. Required for long-term memory.",
    size: "~270 MB",
    layer: "memory",
  },
];

let VOICE_GROUP_ORDER = [
  "US English / Female",
  "US English / Male",
  "British English / Female",
  "British English / Male",
];

let VOICES = [
  { id: "af_alloy", label: "Alloy", tag: "US English", group: "US English / Female" },
  { id: "af_aoede", label: "Aoede", tag: "US English", group: "US English / Female" },
  { id: "af_bella", label: "Bella", tag: "US English", group: "US English / Female" },
  { id: "af_heart", label: "Heart", tag: "US English", group: "US English / Female" },
  { id: "af_jessica", label: "Jessica", tag: "US English", group: "US English / Female" },
  { id: "af_kore", label: "Kore", tag: "US English", group: "US English / Female" },
  { id: "af_nicole", label: "Nicole", tag: "US English", group: "US English / Female" },
  { id: "af_nova", label: "Nova", tag: "US English", group: "US English / Female" },
  { id: "af_river", label: "River", tag: "US English", group: "US English / Female" },
  { id: "af_sarah", label: "Sarah", tag: "US English", group: "US English / Female" },
  { id: "af_sky", label: "Sky", tag: "US English", group: "US English / Female" },
  { id: "am_adam", label: "Adam", tag: "US English", group: "US English / Male" },
  { id: "am_echo", label: "Echo", tag: "US English", group: "US English / Male" },
  { id: "am_eric", label: "Eric", tag: "US English", group: "US English / Male" },
  { id: "am_fenrir", label: "Fenrir", tag: "US English", group: "US English / Male" },
  { id: "am_liam", label: "Liam", tag: "US English", group: "US English / Male" },
  { id: "am_michael", label: "Michael", tag: "US English", group: "US English / Male" },
  { id: "am_onyx", label: "Onyx", tag: "US English", group: "US English / Male" },
  { id: "am_puck", label: "Puck", tag: "US English", group: "US English / Male" },
  { id: "am_santa", label: "Santa", tag: "US English", group: "US English / Male" },
  { id: "bf_alice", label: "Alice", tag: "British English", group: "British English / Female" },
  { id: "bf_emma", label: "Emma", tag: "British English", group: "British English / Female" },
  { id: "bf_isabella", label: "Isabella", tag: "British English", group: "British English / Female" },
  { id: "bf_lily", label: "Lily", tag: "British English", group: "British English / Female" },
  { id: "bm_daniel", label: "Daniel", tag: "British English", group: "British English / Male" },
  { id: "bm_fable", label: "Fable", tag: "British English", group: "British English / Male" },
  { id: "bm_george", label: "George", tag: "British English", group: "British English / Male" },
  { id: "bm_lewis", label: "Lewis", tag: "British English", group: "British English / Male" },
];
let CLOUD_PROVIDER_ORDER = ["anthropic", "gemma", "gemini", "openai", "openrouter", "qwen_cloud"];
let CLOUD_PROVIDER_SPECS = {};

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function cloneConfigValue(value) {
  if (Array.isArray(value)) {
    return value.map((item) => cloneConfigValue(item));
  }
  if (isPlainObject(value)) {
    const cloned = {};
    for (const [key, item] of Object.entries(value)) {
      cloned[key] = cloneConfigValue(item);
    }
    return cloned;
  }
  return value;
}

function configuredArray(options, key, fallback) {
  return Array.isArray(options?.[key]) ? cloneConfigValue(options[key]) : cloneConfigValue(fallback);
}

function applySettingsOptions(options) {
  if (!isPlainObject(options)) {
    return;
  }
  MODELS = configuredArray(options, "onboarding_models", MODELS);
  VOICE_GROUP_ORDER = configuredArray(options, "voice_group_order", VOICE_GROUP_ORDER);
  VOICES = configuredArray(options, "voices", VOICES);
  CLOUD_PROVIDER_ORDER = ["gemma", ...configuredArray(options, "cloud_provider_order", CLOUD_PROVIDER_ORDER)]
    .filter((provider, index, all) => provider !== "chatgpt_oauth" && all.indexOf(provider) === index);
  CLOUD_PROVIDER_SPECS = isPlainObject(options.cloud_provider_specs)
    ? cloneConfigValue(options.cloud_provider_specs)
    : CLOUD_PROVIDER_SPECS;
}

applySettingsOptions(DEFAULT_SETTINGS_OPTIONS);

const STEPS = [
  { id: "welcome", title: "Welcome", eyebrow: "Setup", render: renderWelcome },
  { id: "identity", title: "Your Companion", eyebrow: "Step 1", render: renderIdentity },
  { id: "provider", title: "Brain Provider", eyebrow: "Step 2", render: renderProvider },
  { id: "models", title: "Model Downloads", eyebrow: "Step 3", render: renderModels },
  { id: "memory", title: "Memory", eyebrow: "Step 4", render: renderMemory },
  { id: "voice", title: "Voice", eyebrow: "Step 5", render: renderVoice },
  { id: "finish", title: "All Set", eyebrow: "Ready", render: renderFinish },
];

const $ = (id) => document.getElementById(id);

const state = {
  currentStep: 0,
  config: {},
  companionName: "Nova",
  pronouns: "she/her",
  userName: "User",
  soulIdentity: "Warm, curious, and slightly playful.",
  soulBackstory: "",
  soulRelationship: "Trusted companion.",
  soulUserContext: "",
  provider: "gemma",
  cloudProvider: "openai",
  apiKey: "",
  apiKeyStored: false,
  memoryMode: "local",
  selectedVoice: "af_nova",
  openVoiceGroups: new Set(["US English / Female"]),
  missingPreviews: [],
  currentAudio: null,
  currentPlayingVoice: "",
  selectedModelTag: MODELS[0].tag,
  downloadedModels: new Set(),
  presentModels: new Set(),
  skippedRemainingModels: false,
  pull: {
    busy: false,
    tag: "",
    status: "",
    percent: 0,
    completed: 0,
    total: 0,
    error: "",
    finishedTag: "",
  },
  isSubmitting: false,
};

function api() {
  return window.ocOnboarding || {};
}

function normalizeProvider(value) {
  const clean = String(value || "").trim().toLowerCase();
  return ["openai", "anthropic", "gemini", "qwen_cloud"].includes(clean) ? clean : "gemma";
}

function selectedProvider() {
  return state.provider === "gemma" ? "gemma" : state.cloudProvider;
}

function shouldSkipModelsStep() {
  return state.provider !== "gemma";
}

function visibleSteps() {
  return STEPS.filter((step) => step.id !== "models" || !shouldSkipModelsStep());
}

function currentStep() {
  const steps = visibleSteps();
  if (state.currentStep >= steps.length) {
    state.currentStep = Math.max(0, steps.length - 1);
  }
  return steps[state.currentStep] || steps[0];
}

function setStatus(message = "", isError = false) {
  const el = $("wizard-status");
  if (!el) {
    return;
  }
  el.textContent = message;
  el.classList.toggle("error", Boolean(isError));
}

function header(panel, step) {
  const eyebrow = document.createElement("div");
  eyebrow.className = "step-eyebrow";
  eyebrow.textContent = step.eyebrow;

  const title = document.createElement("div");
  title.className = "step-title";
  title.textContent = step.title;

  panel.appendChild(eyebrow);
  panel.appendChild(title);
}

function body(text) {
  const el = document.createElement("div");
  el.className = "step-body";
  el.textContent = text;
  return el;
}

function renderShell() {
  const panels = $("step-panels");
  panels.replaceChildren();
  for (const step of STEPS) {
    const panel = document.createElement("section");
    panel.className = "step-panel";
    panel.dataset.step = step.id;
    header(panel, step);
    step.render(panel);
    panels.appendChild(panel);
  }
}

function renderDots() {
  const dots = $("step-dots");
  dots.replaceChildren();
  visibleSteps().forEach((step, index) => {
    const dot = document.createElement("div");
    dot.className = "step-dot";
    dot.classList.toggle("active", index === state.currentStep);
    dot.classList.toggle("done", index < state.currentStep);
    dot.title = step.title;
    dots.appendChild(dot);
  });
}

function renderButtons() {
  const step = currentStep();
  const back = $("btn-back");
  const next = $("btn-next");
  back.classList.toggle("hidden", state.currentStep === 0);
  back.disabled = state.pull.busy || state.isSubmitting;
  next.textContent = step.id === "finish" ? "Launch" : "Continue";
  next.disabled = state.pull.busy || state.isSubmitting;
}

function renderActiveStep() {
  const step = currentStep();
  document.querySelectorAll(".step-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.step === step.id);
  });
  renderDots();
  renderButtons();
  setStatus("");
}

function goTo(index) {
  const steps = visibleSteps();
  state.currentStep = Math.max(0, Math.min(steps.length - 1, Number(index) || 0));
  renderActiveStep();
  if (currentStep().id === "models") {
    void refreshModelPresence();
  }
}

function goToStepId(stepId) {
  const index = visibleSteps().findIndex((step) => step.id === stepId);
  goTo(index >= 0 ? index : 0);
}

function renderWelcome(panel) {
  panel.appendChild(body("Set up your companion once, then launch straight into the desktop overlay."));
}

function field(labelText, input) {
  const wrap = document.createElement("div");
  wrap.className = "field";
  const label = document.createElement("label");
  label.textContent = labelText;
  label.htmlFor = input.id;
  wrap.appendChild(label);
  wrap.appendChild(input);
  return wrap;
}

function textInput(id, value, placeholder, onInput) {
  const input = document.createElement("input");
  input.className = "oc-input";
  input.id = id;
  input.type = "text";
  input.value = value || "";
  input.placeholder = placeholder || "";
  input.addEventListener("input", () => onInput(input.value));
  input.addEventListener("blur", () => persistIdentityFields());
  return input;
}

function renderIdentity(panel) {
  panel.appendChild(body("Define the basics: her name, her pronouns, and what she should call you."));

  const grid = document.createElement("div");
  grid.className = "field-grid";
  grid.appendChild(field("Companion name", textInput("companion-name", state.companionName, "Nova", (value) => {
    state.companionName = value;
  })));
  grid.appendChild(field("Pronouns", textInput("companion-pronouns", state.pronouns, "she/her", (value) => {
    state.pronouns = value;
  })));

  const userField = field("What should she call you?", textInput("user-name", state.userName, "Your name", (value) => {
    state.userName = value;
  }));
  userField.classList.add("span-2");
  grid.appendChild(userField);

  panel.appendChild(grid);
}

function providerCard(id, title, description) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "select-card";
  button.classList.toggle("selected", selectedProvider() === id);
  button.innerHTML = `<div class="select-card-title">${title}</div><div class="select-card-body">${description}</div>`;
  button.addEventListener("click", () => {
    if (id === "gemma") {
      state.provider = "gemma";
    } else {
      state.provider = id;
      state.cloudProvider = id;
      state.memoryMode = state.memoryMode === "local" ? "api" : state.memoryMode;
    }
    renderShell();
    renderActiveStep();
  });
  return button;
}

function providerTitle(id) {
  if (id === "gemma") {
    return "Gemma (Local)";
  }
  return CLOUD_PROVIDER_SPECS[id]?.label || id;
}

function providerDescription(id) {
  if (id === "gemma") {
    return "Keeps the companion and assistant models on this machine through Ollama.";
  }
  if (id === "openrouter") {
    return "Requires an API key. Uses OpenRouter-hosted models for both runtime layers.";
  }
  if (id === "qwen_cloud") {
    return "Requires a DashScope API key. Uses Qwen cloud models for both runtime layers.";
  }
  const label = providerTitle(id);
  return `Requires an API key. Uses ${label} models for both runtime layers.`;
}

function renderProvider(panel) {
  panel.appendChild(body("Choose whether the brain stays local through Ollama or uses a cloud provider."));

  const grid = document.createElement("div");
  grid.className = "select-grid two";
  for (const provider of CLOUD_PROVIDER_ORDER) {
    grid.appendChild(providerCard(provider, providerTitle(provider), providerDescription(provider)));
  }
  panel.appendChild(grid);

  if (state.provider !== "gemma") {
    const key = document.createElement("input");
    key.className = "oc-input";
    key.id = "brain-api-key";
    key.type = "password";
    key.autocomplete = "off";
    key.placeholder = state.apiKeyStored ? "Key stored in your OS keychain" : `Enter your ${state.cloudProvider} API key`;
    key.value = state.apiKey;
    key.addEventListener("input", () => {
      state.apiKey = key.value;
      state.apiKeyStored = false;
    });
    key.addEventListener("blur", () => persistProviderSettings());

    const apiKeyField = field("API key", key);
    apiKeyField.classList.add("span-2");
    apiKeyField.style.marginTop = "16px";
    panel.appendChild(apiKeyField);
  }
}

function renderModels(panel) {
  panel.appendChild(body("Select a model to download first. You can pull the others after setup."));

  const list = document.createElement("div");
  list.id = "model-list";
  for (const model of MODELS) {
    list.appendChild(modelRow(model));
  }
  panel.appendChild(list);

  const allReady = MODELS.every((model) => isDownloaded(model.tag));
  if (allReady) {
    panel.appendChild(body("All models are ready. Nothing to download."));
    return;
  }

  const button = document.createElement("button");
  button.id = "download-selected";
  button.type = "button";
  button.className = "btn-accent btn-wide";
  button.textContent = state.pull.error ? "Retry Download" : "Download Selected";
  button.disabled = state.pull.busy || isDownloaded(state.selectedModelTag);
  button.addEventListener("click", () => {
    void downloadSelectedModel();
  });
  panel.appendChild(button);

  if (state.pull.busy || state.pull.status || state.pull.error || state.pull.finishedTag) {
    panel.appendChild(progressBlock());
  }

  if (state.pull.finishedTag && !state.pull.busy && nextUndownloadedModel()) {
    const next = document.createElement("button");
    next.type = "button";
    next.className = "btn-ghost btn-wide";
    next.style.marginTop = "12px";
    next.textContent = "Download next model";
    next.addEventListener("click", () => {
      const model = nextUndownloadedModel();
      if (model) {
        state.selectedModelTag = model.tag;
        state.pull.finishedTag = "";
        renderShell();
        renderActiveStep();
      }
    });
    panel.appendChild(next);
  }

  const skip = document.createElement("button");
  skip.type = "button";
  skip.className = "link-ghost";
  skip.textContent = "Skip remaining";
  skip.addEventListener("click", () => {
    state.skippedRemainingModels = true;
    goTo(state.currentStep + 1);
  });
  panel.appendChild(skip);
}

function modelRow(model) {
  const downloaded = isDownloaded(model.tag);
  const row = document.createElement("div");
  row.className = "model-row";
  row.classList.toggle("selected", state.selectedModelTag === model.tag);
  row.classList.toggle("disabled", downloaded || state.pull.busy);
  row.addEventListener("click", () => {
    if (downloaded || state.pull.busy) {
      return;
    }
    state.selectedModelTag = model.tag;
    renderShell();
    renderActiveStep();
  });

  if (!downloaded) {
    const radio = document.createElement("div");
    radio.className = "model-row-radio";
    row.appendChild(radio);
  }

  const info = document.createElement("div");
  info.className = "model-row-info";
  const name = document.createElement("div");
  name.className = "model-row-name";
  name.textContent = `${model.label} / ${model.tag}`;
  const desc = document.createElement("div");
  desc.className = "model-row-desc";
  desc.textContent = model.desc;
  info.appendChild(name);
  info.appendChild(desc);
  row.appendChild(info);

  const badge = document.createElement("div");
  badge.className = `model-row-badge${downloaded ? " already-downloaded" : ""}`;
  badge.textContent = downloaded ? "Ready" : model.size;
  row.appendChild(badge);

  return row;
}

function progressBlock() {
  const wrap = document.createElement("div");
  wrap.className = "download-progress-wrap";
  const pct = Math.max(0, Math.min(100, Number(state.pull.percent) || 0));
  const model = MODELS.find((candidate) => candidate.tag === state.pull.tag) || MODELS.find((candidate) => candidate.tag === state.selectedModelTag);
  const statusClass = state.pull.error ? "error" : state.pull.finishedTag ? "done" : "";
  const statusText = state.pull.error || state.pull.status || (state.pull.finishedTag ? "Download complete." : "Waiting.");
  wrap.innerHTML = `
    <div class="download-progress-label">
      <div class="download-progress-name">${model?.tag || state.selectedModelTag}</div>
      <div class="download-progress-pct">${pct}%</div>
    </div>
    <div class="download-progress-track">
      <div class="download-progress-fill" style="width: ${pct}%"></div>
    </div>
    <div class="download-status-chip">
      <span class="download-status-dot ${statusClass}"></span>
      <span>${statusText}</span>
    </div>
  `;
  return wrap;
}

function memoryCard(id, title, description) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "select-card";
  button.classList.toggle("selected", state.memoryMode === id);
  button.innerHTML = `<div class="select-card-title">${title}</div><div class="select-card-body">${description}</div>`;
  button.addEventListener("click", () => {
    state.memoryMode = id;
    renderShell();
    renderActiveStep();
  });
  return button;
}

function renderMemory(panel) {
  panel.appendChild(body("Choose how long-term memory is extracted and embedded."));

  const grid = document.createElement("div");
  grid.className = "select-grid";
  grid.appendChild(memoryCard("local", "Local", "Default. Extraction via Ollama, embeddings local."));
  grid.appendChild(memoryCard("api", "Cloud-assisted", "Uses the selected brain provider for extraction."));
  panel.appendChild(grid);
}

function renderVoice(panel) {
  panel.appendChild(body("Choose a Kokoro voice for your companion. Preview each one before you decide."));
  const grid = document.createElement("div");
  grid.id = "voice-grid";
  grid.className = "voice-grid";
  panel.appendChild(grid);
  renderVoiceGrid(grid);
}

function renderFinish(panel) {
  panel.appendChild(body("OpenCompanion is ready to launch. Your companion will greet you on the overlay."));
}

function isDownloaded(tag) {
  return state.downloadedModels.has(tag) || state.presentModels.has(tag);
}

function nextUndownloadedModel() {
  return MODELS.find((model) => !isDownloaded(model.tag)) || null;
}

function normalizeCheckResult(result) {
  const rows = Array.isArray(result)
    ? result
    : Array.isArray(result?.models)
      ? result.models
      : [];
  return rows.map((row) => {
    if (typeof row === "string") {
      return { tag: row, present: true };
    }
    return {
      tag: String(row?.tag || row?.name || row?.model || "").trim(),
      present: Boolean(row?.present),
    };
  }).filter((row) => row.tag);
}

async function refreshModelPresence() {
  if (state.provider !== "gemma" || typeof api().checkOllamaModels !== "function") {
    return;
  }
  try {
    const result = await api().checkOllamaModels(MODELS.map((model) => model.tag));
    state.presentModels = new Set(normalizeCheckResult(result).filter((row) => row.present).map((row) => row.tag));
    const next = nextUndownloadedModel();
    if (isDownloaded(state.selectedModelTag) && next) {
      state.selectedModelTag = next.tag;
    }
    renderShell();
    renderActiveStep();
  } catch (error) {
    state.pull.error = error?.message || "Could not check Ollama models.";
    renderShell();
    renderActiveStep();
  }
}

function normalizeProgress(data) {
  const total = Number(data?.total || data?.bytesTotal || 0);
  const completed = Number(data?.completed || data?.current || data?.downloaded || 0);
  let percent = Number(data?.percent);
  if (!Number.isFinite(percent) && total > 0) {
    percent = Math.round((completed / total) * 100);
  }
  return {
    tag: String(data?.tag || data?.model || data?.name || "").trim(),
    status: String(data?.status || data?.message || "").trim(),
    total: Number.isFinite(total) ? total : 0,
    completed: Number.isFinite(completed) ? completed : 0,
    percent: Number.isFinite(percent) ? Math.max(0, Math.min(100, percent)) : 0,
    error: String(data?.error || "").trim(),
  };
}

async function downloadSelectedModel() {
  const tag = state.selectedModelTag;
  if (!tag || state.pull.busy || isDownloaded(tag) || typeof api().pullModel !== "function") {
    return;
  }

  state.pull = {
    busy: true,
    tag,
    status: `Pulling ${tag}...`,
    percent: 0,
    completed: 0,
    total: 0,
    error: "",
    finishedTag: "",
  };
  renderShell();
  renderActiveStep();

  try {
    const result = await api().pullModel(tag);
    if (result && result.ok === false) {
      throw new Error(result.error || "Could not start model download.");
    }
  } catch (error) {
    state.pull = {
      busy: false,
      tag,
      status: "",
      percent: 0,
      completed: 0,
      total: 0,
      error: error?.message || "Could not start model download.",
      finishedTag: "",
    };
    renderShell();
    renderActiveStep();
  }
}

function handlePullProgress(data) {
  const progress = normalizeProgress(data || {});
  if (!progress.tag || progress.tag !== state.pull.tag) {
    return;
  }

  if (progress.status === "success") {
    state.downloadedModels.add(progress.tag);
    state.presentModels.add(progress.tag);
    state.pull = {
      busy: false,
      tag: progress.tag,
      status: "Download complete.",
      percent: 100,
      completed: progress.completed,
      total: progress.total,
      error: "",
      finishedTag: progress.tag,
    };
    void persistDownloadedModels();
    const next = nextUndownloadedModel();
    if (next) {
      state.selectedModelTag = next.tag;
    }
    renderShell();
    renderActiveStep();
    return;
  }

  if (progress.status === "error" || progress.error) {
    state.pull = {
      busy: false,
      tag: progress.tag,
      status: "",
      percent: progress.percent,
      completed: progress.completed,
      total: progress.total,
      error: progress.error || "Model download failed.",
      finishedTag: "",
    };
    renderShell();
    renderActiveStep();
    return;
  }

  state.pull = {
    ...state.pull,
    status: progress.status || `Pulling ${progress.tag}...`,
    percent: progress.percent,
    completed: progress.completed,
    total: progress.total,
  };
  renderShell();
  renderActiveStep();
}

async function persistDownloadedModels() {
  if (typeof api().setConfig !== "function") {
    return;
  }
  await api().setConfig("models.downloaded", Array.from(state.downloadedModels));
}

async function persistIdentityFields() {
  if (typeof api().setConfig !== "function") {
    return;
  }
  await api().setConfig({
    companion: {
      name: state.companionName || "Nova",
      pronouns: state.pronouns || "she/her",
      user_name: state.userName || "User",
      soul: {
        name: state.companionName || "Nova",
        pronouns: state.pronouns || "she/her",
        user_name: state.userName || "User",
      },
    },
  });
}

async function persistProviderSettings() {
  if (state.provider !== "gemma" && state.apiKey && api().apikey?.set) {
    await api().apikey.set(state.cloudProvider, state.apiKey);
    state.apiKeyStored = true;
    state.apiKey = "";
  }
}

async function persistSetup() {
  if (typeof api().save !== "function") {
    return { ok: true };
  }
  await persistProviderSettings();
  return api().save({
    companion_name: state.companionName,
    user_name: state.userName,
    soul_identity: state.soulIdentity,
    soul_backstory: state.soulBackstory,
    soul_relationship: state.soulRelationship,
    soul_user_context: state.soulUserContext,
    pronouns: state.pronouns,
    voice: state.selectedVoice,
    brain_provider: selectedProvider(),
    brain_model: state.provider === "gemma" ? MODELS[0].tag : "",
    brain_provider_mode: state.provider === "gemma" ? "local" : "cloud",
    brain_remote_provider: state.provider === "gemma" ? "" : state.cloudProvider,
    memory_mode: state.memoryMode,
  });
}

function validateCurrentStep() {
  const step = currentStep().id;
  if (step === "identity") {
    if (!state.companionName.trim()) {
      return "Enter a companion name.";
    }
    if (!state.pronouns.trim()) {
      return "Enter companion pronouns.";
    }
    if (!state.userName.trim()) {
      return "Enter what your companion should call you.";
    }
  }
  if (step === "provider" && state.provider !== "gemma" && !state.apiKey && !state.apiKeyStored) {
    return `Enter your ${state.cloudProvider} API key.`;
  }
  if (step === "models" && state.pull.busy) {
    return "Wait for the model download to finish before continuing.";
  }
  return "";
}

async function handleNext() {
  const step = currentStep();
  const error = validateCurrentStep();
  if (error) {
    setStatus(error, true);
    return;
  }

  if (step.id === "finish") {
    await finishSetup();
    return;
  }

  if (step.id === "identity") {
    await persistIdentityFields();
  }
  if (step.id === "provider") {
    await persistProviderSettings();
  }

  goTo(state.currentStep + 1);
}

async function finishSetup() {
  try {
    state.isSubmitting = true;
    renderButtons();
    setStatus("Saving setup...");
    const saveResult = await persistSetup();
    if (saveResult && saveResult.ok === false) {
      throw new Error(saveResult.error || "Could not save onboarding settings.");
    }
    setStatus("Launching your companion...");
    const finish = api().finishSetup || api().complete;
    const result = typeof finish === "function" ? await finish() : { ok: true };
    if (result && result.ok === false) {
      throw new Error(result.error || "Could not complete onboarding.");
    }
  } catch (error) {
    state.isSubmitting = false;
    renderButtons();
    setStatus(error?.message || "Could not complete setup.", true);
  }
}

function renderVoiceGrid(target = $("voice-grid")) {
  if (!target) {
    return;
  }
  target.replaceChildren();

  for (const groupName of VOICE_GROUP_ORDER) {
    const voices = VOICES.filter((voice) => voice.group === groupName);
    if (!voices.length) {
      continue;
    }

    if (voices.some((voice) => voice.id === state.selectedVoice)) {
      state.openVoiceGroups.add(groupName);
    }

    const group = document.createElement("section");
    group.className = "voice-group";
    group.classList.toggle("expanded", state.openVoiceGroups.has(groupName));

    const headerButton = document.createElement("button");
    headerButton.type = "button";
    headerButton.className = "voice-group-header";
    headerButton.innerHTML = `<span>${groupName}</span><span>${state.openVoiceGroups.has(groupName) ? "^" : "v"}</span>`;
    headerButton.addEventListener("click", () => {
      if (state.openVoiceGroups.has(groupName)) {
        state.openVoiceGroups.delete(groupName);
      } else {
        state.openVoiceGroups.add(groupName);
      }
      renderShell();
      renderActiveStep();
    });
    group.appendChild(headerButton);

    const grid = document.createElement("div");
    grid.className = "voice-group-grid";
    for (const voice of voices) {
      grid.appendChild(voiceCard(voice));
    }
    group.appendChild(grid);
    target.appendChild(group);
  }
}

function voiceCard(voice) {
  const card = document.createElement("div");
  card.className = "select-card voice-card";
  card.classList.toggle("selected", voice.id === state.selectedVoice);
  card.addEventListener("click", () => {
    state.selectedVoice = voice.id;
    renderShell();
    renderActiveStep();
  });

  const name = document.createElement("div");
  name.className = "voice-card-name";
  name.textContent = voice.label;
  const tag = document.createElement("div");
  tag.className = "voice-card-tag";
  tag.textContent = `${voice.tag} / ${voice.id}`;
  card.appendChild(name);
  card.appendChild(tag);

  if (state.missingPreviews.includes(voice.id)) {
    const loading = document.createElement("div");
    loading.className = "voice-card-spinner";
    loading.textContent = "Generating...";
    card.appendChild(loading);
  } else {
    const listen = document.createElement("button");
    listen.type = "button";
    listen.className = "voice-card-listen";
    listen.textContent = state.currentPlayingVoice === voice.id ? "Stop" : "Listen";
    listen.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      void playVoicePreview(voice.id);
    });
    card.appendChild(listen);
  }

  return card;
}

function stopCurrentAudio() {
  if (!state.currentAudio) {
    return;
  }
  state.currentAudio.pause();
  state.currentAudio.currentTime = 0;
  state.currentAudio = null;
  state.currentPlayingVoice = "";
}

async function playVoicePreview(voiceId) {
  if (state.currentPlayingVoice === voiceId) {
    stopCurrentAudio();
    renderShell();
    renderActiveStep();
    return;
  }

  stopCurrentAudio();
  if (typeof api().getVoicePreviewUrl !== "function") {
    return;
  }

  const url = await api().getVoicePreviewUrl(voiceId);
  if (!url) {
    return;
  }

  const audio = new Audio(url);
  const cleanup = () => {
    audio.removeEventListener("ended", cleanup);
    audio.removeEventListener("error", cleanup);
    if (state.currentAudio === audio) {
      state.currentAudio = null;
      state.currentPlayingVoice = "";
      renderShell();
      renderActiveStep();
    }
  };
  state.currentAudio = audio;
  state.currentPlayingVoice = voiceId;
  renderShell();
  renderActiveStep();
  audio.addEventListener("ended", cleanup);
  audio.addEventListener("error", cleanup);
  try {
    await audio.play();
  } catch (_error) {
    cleanup();
  }
}

async function initVoicePreviews() {
  try {
    if (typeof api().checkVoicePreviews !== "function") {
      return;
    }
    state.missingPreviews = await api().checkVoicePreviews();
    renderShell();
    renderActiveStep();

    if (!Array.isArray(state.missingPreviews) || !state.missingPreviews.length || typeof api().generateVoicePreview !== "function") {
      return;
    }
    for (const voiceId of [...state.missingPreviews]) {
      const result = await api().generateVoicePreview(voiceId);
      if (result && result.ok !== false) {
        state.missingPreviews = state.missingPreviews.filter((candidate) => candidate !== voiceId);
        renderShell();
        renderActiveStep();
      }
    }
  } catch (_error) {
    state.missingPreviews = [];
    renderShell();
    renderActiveStep();
  }
}

function applyTheme(data) {
  if (!data || !Array.isArray(data.accent_rgb) || data.accent_rgb.length !== 3) {
    return;
  }
  document.documentElement.style.setProperty("--oc-accent-rgb", data.accent_rgb.join(", "));
}

function populateFromConfig(config) {
  state.config = config || {};
  const companion = state.config.companion || {};
  const soul = companion.soul || {};
  const brain = state.config.brain || {};
  const memory = state.config.memory || {};
  const provider = normalizeProvider(brain.provider);

  state.companionName = String(soul.name || companion.name || "Nova").trim() || "Nova";
  state.pronouns = String(soul.pronouns || companion.pronouns || "she/her").trim() || "she/her";
  state.userName = String(soul.user_name || companion.user_name || "User").trim() || "User";
  state.soulIdentity = String(soul.identity || companion.personality || state.soulIdentity).trim();
  state.soulBackstory = String(soul.backstory || "").trim();
  state.soulRelationship = String(soul.relationship || state.soulRelationship).trim();
  state.soulUserContext = String(soul.user_context || soul.user_description || companion.user_description || "").trim();
  state.provider = provider === "gemma" ? "gemma" : provider;
  state.cloudProvider = provider === "gemma" ? "openai" : provider;
  state.memoryMode = memory.enabled === false
    ? "off"
    : memory.embedding_enabled === false || String(memory.extraction_source || "").toLowerCase() === "brain"
      ? "api"
      : "local";
  state.selectedVoice = VOICES.some((voice) => voice.id === state.config.voice?.kokoro_voice)
    ? state.config.voice.kokoro_voice
    : "af_nova";
  const downloaded = state.config.onboarding?.modelsDownloaded || state.config.models?.downloaded || [];
  state.downloadedModels = new Set(Array.isArray(downloaded) ? downloaded.map((tag) => String(tag || "").trim()).filter(Boolean) : []);
}

async function hydrateApiKeyState() {
  if (state.provider === "gemma" || !api().apikey?.get) {
    return;
  }
  try {
    const result = await api().apikey.get(state.cloudProvider);
    state.apiKeyStored = String(result || "").trim() === "set";
  } catch (_error) {
    state.apiKeyStored = false;
  }
}

async function init() {
  $("close-btn").addEventListener("click", () => {
    if (typeof api().closeWindow === "function") {
      void api().closeWindow();
    }
  });
  $("btn-back").addEventListener("click", () => {
    goTo(state.currentStep - 1);
  });
  $("btn-next").addEventListener("click", () => {
    void handleNext();
  });

  if (typeof api().onThemeApply === "function") {
    api().onThemeApply((data) => applyTheme(data));
  }
  if (typeof api().onPullProgress === "function") {
    api().onPullProgress((data) => handlePullProgress(data));
  }

  const load = api().getConfig || api().load;
  const config = typeof load === "function" ? await load() : {};
  applySettingsOptions(config?.settings_options);
  populateFromConfig(config || {});
  await hydrateApiKeyState();
  renderShell();
  renderActiveStep();
  void initVoicePreviews();
}

init().catch((error) => {
  setStatus(error?.message || "Could not load onboarding.", true);
});
