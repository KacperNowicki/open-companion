const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");

function defaultComponents() {
  return {
    backend: false,
    ollama: false,
    brain_model: false,
    embedding_model: false,
    kokoro: false,
    whisper: false,
  };
}

function defaultRequiredComponents() {
  return {
    backend: true,
    ollama: true,
    brain_model: true,
    embedding_model: true,
    kokoro: true,
    whisper: true,
  };
}

function emptyProgress() {
  return {
    label: "",
    percent: null,
    current: null,
    total: null,
    downloaded_bytes: null,
    total_bytes: null,
  };
}

function quoteCmd(value) {
  return `"${String(value || "").replace(/"/g, '""')}"`;
}

function quotePowerShell(value) {
  return String(value || "").replace(/'/g, "''");
}

function ollamaModelMatches(models, requestedName) {
  const target = String(requestedName || "").trim();
  if (!target) {
    return false;
  }
  return Array.isArray(models) && models.some((entry) => {
    const candidate = String(entry || "").trim();
    return candidate === target || candidate.startsWith(`${target}:`);
  });
}

function safeMarkerName(value, fallback) {
  return (String(value || fallback || "")
    .trim()
    .replace(/[^a-zA-Z0-9._-]+/g, "_")) || String(fallback || "ready");
}

const OLLAMA_BACKED_PROVIDERS = new Set(["gemma", "qwen", "ollama"]);
const MAX_OLLAMA_START_RETRIES = 3;
const MAX_OLLAMA_MODEL_PULL_RETRIES = 3;

function isOllamaBackedProvider(provider) {
  return OLLAMA_BACKED_PROVIDERS.has(String(provider || "").trim().toLowerCase());
}

class RuntimeManager {
  constructor(options) {
    this.options = options;
    this.statusPath = path.join(options.profileRoot, "runtime-status.json");
    this.abortController = null;
    this.currentChild = null;
    this.currentRun = null;
    this.status = this._loadStatus();
  }

  _currentConfig(configOverride = null) {
    if (configOverride && typeof configOverride === "object") {
      return configOverride;
    }
    return this.options.getConfig ? this.options.getConfig() : {};
  }

  _brainModel(configOverride = null) {
    return String(this._currentConfig(configOverride)?.brain?.model || this.options.brainModel || "").trim();
  }

  _brainProvider(configOverride = null) {
    return String(this._currentConfig(configOverride)?.brain?.provider || this.options.brainProvider || "gemma").trim().toLowerCase() || "gemma";
  }

  _usesLocalBrainModel(configOverride = null) {
    return isOllamaBackedProvider(this._brainProvider(configOverride));
  }

  _embeddingModel(configOverride = null) {
    return String(this._currentConfig(configOverride)?.memory?.embedding_model || this.options.embeddingModel || "nomic-embed-text").trim() || "nomic-embed-text";
  }

  _whisperModel(configOverride = null) {
    return String(this._currentConfig(configOverride)?.voice?.whisper_model || this.options.whisperModel || "base.en").trim() || "base.en";
  }

  _runtimeAssetsDir() {
    return this.options.runtimePaths.RUNTIME_ASSETS_DIR || path.join(this.options.profileRoot, "runtime-assets");
  }

  _whisperMarkerPath(configOverride = null) {
    return path.join(this._runtimeAssetsDir(), "whisper", `${safeMarkerName(this._whisperModel(configOverride), "base.en")}.ready.json`);
  }

  _markWhisperPrepared(configOverride = null) {
    const model = this._whisperModel(configOverride);
    const markerPath = this._whisperMarkerPath(configOverride);
    fs.mkdirSync(path.dirname(markerPath), { recursive: true });
    fs.writeFileSync(markerPath, `${JSON.stringify({
      model,
      prepared_at: new Date().toISOString(),
    }, null, 2)}\n`, "utf8");
  }

  _memoryConfig(configOverride = null) {
    return this._currentConfig(configOverride)?.memory || {};
  }

  _requiresLocalMemory(configOverride = null) {
    const memoryConfig = this._memoryConfig(configOverride);
    if (memoryConfig.enabled === false) {
      return false;
    }

    if (memoryConfig.embedding_enabled !== false) {
      return true;
    }

    const extractionSource = String(
      memoryConfig.extraction_source || (memoryConfig.extraction_mode === "provider" ? "brain" : "local")
    ).trim().toLowerCase() || "local";
    const extractionProvider = String(memoryConfig.extraction_provider || "").trim().toLowerCase();
    const brainProvider = String(this._currentConfig(configOverride)?.brain?.provider || "gemma").trim().toLowerCase() || "gemma";

    if (extractionSource === "local") {
      return true;
    }
    if (extractionSource === "brain") {
      return isOllamaBackedProvider(brainProvider);
    }
    if (extractionSource === "api") {
      return isOllamaBackedProvider(extractionProvider);
    }
    return false;
  }

  _requiredComponents(configOverride = null) {
    const usesLocalBrainModel = this._usesLocalBrainModel(configOverride);
    const requiresLocalMemory = this._requiresLocalMemory(configOverride);

    return {
      ...defaultRequiredComponents(),
      ollama: usesLocalBrainModel || requiresLocalMemory,
      brain_model: usesLocalBrainModel,
      embedding_model: requiresLocalMemory && this._memoryConfig(configOverride).embedding_enabled !== false,
    };
  }

  _loadStatus() {
    try {
      if (fs.existsSync(this.statusPath)) {
        const parsed = JSON.parse(fs.readFileSync(this.statusPath, "utf8"));
        return this._normalizeStatus(parsed);
      }
    } catch (error) {
      console.warn("Failed to read runtime status:", error.message);
    }
    return this._normalizeStatus({});
  }

  _normalizeStatus(source) {
    const components = { ...defaultComponents(), ...(source?.components || {}) };
    const requiredComponents = { ...defaultRequiredComponents(), ...(source?.required_components || source?.requiredComponents || {}) };
    const progress = { ...emptyProgress(), ...(source?.progress || {}) };
    const state = String(source?.state || "idle");
    const activeStep = String(source?.activeStep || source?.active_step || state);
    const lastError = String(source?.lastError || source?.last_error || "");
    const sandboxSource = source?.sandbox || {};
    return {
      state,
      active_step: activeStep,
      activeStep,
      ready: Object.entries(requiredComponents).every(([name, required]) => !required || Boolean(components[name])),
      components,
      required_components: requiredComponents,
      requiredComponents,
      progress,
      last_error: lastError,
      lastError,
      guided_installer_path: String(source?.guided_installer_path || ""),
      installer_url: String(source?.installer_url || this.options.ollamaZipUrl || ""),
      can_prepare: !["checking", "downloading_ollama", "installing_ollama", "starting_ollama", "pulling_brain", "pulling_embeddings", "downloading_kokoro", "preparing_whisper"].includes(state),
      can_cancel: ["checking", "downloading_ollama", "installing_ollama", "starting_ollama", "pulling_brain", "pulling_embeddings", "downloading_kokoro", "preparing_whisper"].includes(state),
      can_retry: ["failed", "cancelled"].includes(state),
      sandbox: {
        available: Boolean(sandboxSource.available ?? false),
        installed: Boolean(sandboxSource.installed ?? false),
        vaultMounted: Boolean(sandboxSource.vaultMounted ?? false),
      },
    };
  }

  _saveStatus() {
    fs.mkdirSync(path.dirname(this.statusPath), { recursive: true });
    fs.writeFileSync(this.statusPath, `${JSON.stringify(this.status, null, 2)}\n`, "utf8");
  }

  _emit(patch = {}) {
    this.status = this._normalizeStatus({
      ...this.status,
      ...patch,
      components: patch.components ? { ...this.status.components, ...patch.components } : this.status.components,
      progress: patch.progress ? { ...this.status.progress, ...patch.progress } : this.status.progress,
    });
    this._saveStatus();
    this.options.onEvent?.(this.getStatus());
  }

  _transition(state, patch = {}) {
    this._emit({
      ...patch,
      state,
      active_step: patch.active_step || state,
      last_error: patch.last_error ?? (state === "failed" ? this.status.last_error : ""),
    });
  }

  _setProgress(label, progress = {}) {
    this._emit({ progress: { label, ...progress } });
  }

  _setComponent(name, value) {
    this._emit({ components: { [name]: Boolean(value) } });
  }

  getStatus() {
    return JSON.parse(JSON.stringify(this.status));
  }

  async refreshStatus(configOverride = null) {
    if (this.currentRun) {
      return this.getStatus();
    }

    this.options.ensureRuntimeDirectories?.();
    const probeController = new AbortController();
    const probe = await this._probe(probeController.signal, configOverride);
    const preserveTerminalState = this.status.state === "failed" || this.status.state === "cancelled";
    const nextState = probe.ready
      ? "ready"
      : (preserveTerminalState ? this.status.state : "idle");

    this._emit({
      state: nextState,
      components: probe.components,
      required_components: probe.requiredComponents,
      progress: nextState === "ready" || nextState === "idle" ? emptyProgress() : this.status.progress,
      last_error: nextState === "ready" ? "" : this.status.last_error,
      lastError: nextState === "ready" ? "" : this.status.lastError,
    });
    return this.getStatus();
  }

  async prepare(configOverride = null) {
    if (this.currentRun) {
      return this.getStatus();
    }

    this.abortController = new AbortController();
    this.currentRun = this._run(this.abortController.signal, configOverride)
      .catch((error) => {
        if (error?.name === "AbortError") {
          this._transition("cancelled", {
            progress: emptyProgress(),
            last_error: "",
          });
          return this.getStatus();
        }

        this._transition("failed", {
          progress: emptyProgress(),
          last_error: String(error?.message || error || "Runtime preparation failed."),
        });
        return this.getStatus();
      })
      .finally(() => {
        this.abortController = null;
        this.currentChild = null;
        this.currentRun = null;
      });

    return this.currentRun;
  }

  async cancel() {
    if (!this.abortController) {
      return this.getStatus();
    }
    this.abortController.abort();
    if (this.currentChild && !this.currentChild.killed) {
      this.currentChild.kill();
    }
    return this.getStatus();
  }

  async retry(configOverride = null) {
    this._transition("idle", {
      progress: emptyProgress(),
      last_error: "",
      guided_installer_path: "",
    });
    return this.prepare(configOverride);
  }

  async _run(signal, configOverride = null) {
    const testState = this.options.readTestOllamaState?.();
    if (testState?.runtime?.steps) {
      return this._runTestPlan(testState.runtime, signal);
    }

    this.options.ensureRuntimeDirectories?.();
    this._transition("checking", {
      progress: emptyProgress(),
      guided_installer_path: "",
      last_error: "",
      components: defaultComponents(),
      required_components: this._requiredComponents(configOverride),
    });

    const initial = await this._probe(signal, configOverride);
    if (initial.ready) {
      this._transition("ready", {
        components: initial.components,
        required_components: initial.requiredComponents,
        progress: emptyProgress(),
        last_error: "",
        lastError: "",
      });
      return this.getStatus();
    }

    if (!initial.components.backend) {
      throw new Error("Bundled backend executable is missing.");
    }

    if (initial.requiredComponents.ollama) {
      if (!initial.ollama.installed) {
        await this._downloadAndInstallOllama(signal);
      }
      await this._ensureOllamaAvailable(signal);
    }
    if (initial.requiredComponents.brain_model) {
      await this._ensureOllamaModel(this._brainModel(configOverride), "pulling_brain", signal);
    } else {
      this._setComponent("brain_model", true);
    }
    if (initial.requiredComponents.embedding_model) {
      await this._ensureOllamaModel(this._embeddingModel(configOverride), "pulling_embeddings", signal);
    }
    if (initial.requiredComponents.kokoro) {
      await this._ensureKokoro(signal);
    }
    if (initial.requiredComponents.whisper) {
      await this._prefetchWhisper(signal, configOverride);
    }

    const finalStatus = await this._probe(signal, configOverride);
    if (!finalStatus.ready) {
      throw new Error("Runtime preparation completed with missing components.");
    }

    this._transition("ready", {
      components: finalStatus.components,
      required_components: finalStatus.requiredComponents,
      progress: emptyProgress(),
      last_error: "",
      lastError: "",
    });
    return this.getStatus();
  }

  async _runTestPlan(plan, signal) {
    this._transition("checking", {
      components: defaultComponents(),
      progress: emptyProgress(),
      last_error: "",
    });
    for (const step of plan.steps) {
      signal.throwIfAborted?.();
      this._transition(String(step.state || "checking"), {
        progress: {
          label: String(step.label || step.state || ""),
          percent: Number.isFinite(step.percent) ? Number(step.percent) : null,
          current: Number.isFinite(step.downloaded_bytes) ? Number(step.downloaded_bytes) : null,
          total: Number.isFinite(step.total_bytes) ? Number(step.total_bytes) : null,
          downloaded_bytes: Number.isFinite(step.downloaded_bytes) ? Number(step.downloaded_bytes) : null,
          total_bytes: Number.isFinite(step.total_bytes) ? Number(step.total_bytes) : null,
        },
      });
      await this._delay(step.delay_ms ?? 25, signal);
    }

    if (plan.error) {
      throw new Error(String(plan.error));
    }

    this._transition("ready", {
      components: { ...defaultComponents(), ...(plan.components || {}) },
      progress: emptyProgress(),
      last_error: "",
      lastError: "",
    });
    return this.getStatus();
  }

  async _probe(signal, configOverride = null) {
    signal.throwIfAborted?.();
    const components = defaultComponents();
    const requiredComponents = this._requiredComponents(configOverride);
    components.backend = this.options.runtimePaths.IS_PACKAGED
      ? fs.existsSync(this.options.runtimePaths.BACKEND_BUNDLED_ENTRY)
      : true;
    components.kokoro = fs.existsSync(this.options.runtimePaths.KOKORO_MODEL_PATH)
      && fs.existsSync(this.options.runtimePaths.KOKORO_VOICES_PATH);
    components.whisper = fs.existsSync(this._whisperMarkerPath(configOverride));

    const ollama = await this._checkOllama(signal);
    components.ollama = ollama.available;
    components.brain_model = this._usesLocalBrainModel(configOverride)
      ? ollamaModelMatches(ollama.models, this._brainModel(configOverride))
      : true;
    components.embedding_model = requiredComponents.embedding_model
      ? ollamaModelMatches(ollama.models, this._embeddingModel(configOverride))
      : true;

    this._emit({ components, required_components: requiredComponents });
    return {
      ready: Object.entries(requiredComponents).every(([name, required]) => !required || Boolean(components[name])),
      components,
      requiredComponents,
      ollama,
    };
  }

  async _checkOllama(signal) {
    signal.throwIfAborted?.();
    const testState = this.options.readTestOllamaState?.();
    if (testState) {
      return {
        installed: testState.installed !== false,
        available: testState.available !== false,
        models: Array.isArray(testState.models) ? testState.models.map((entry) => String(entry?.name || "").trim()).filter(Boolean) : [],
      };
    }

    const available = await this._fetchOllamaTags(signal);
    if (available) {
      return {
        installed: true,
        available: true,
        models: available,
      };
    }

    const installed = await this._isOllamaInstalled(signal);
    return {
      installed,
      available: false,
      models: [],
    };
  }

  _getOllamaExecutablePath() {
    if (typeof this.options.runtimePaths.getOllamaExecutablePath === "function") {
      return this.options.runtimePaths.getOllamaExecutablePath();
    }
    return this.options.runtimePaths.OLLAMA_EMBEDDED_ENTRY || "ollama";
  }

  _getOllamaEnvironment() {
    return {
      ...process.env,
      OLLAMA_MODELS: this.options.runtimePaths.OLLAMA_MODELS_DIR || process.env.OLLAMA_MODELS,
    };
  }

  async _isOllamaInstalled(signal) {
    signal?.throwIfAborted?.();
    return new Promise((resolve) => {
      const child = spawn(this._getOllamaExecutablePath(), ["list"], {
        shell: false,
        windowsHide: true,
        env: this._getOllamaEnvironment(),
        stdio: "ignore",
      });
      child.on("error", () => resolve(false)); // ENOENT or any spawn error = not installed
      child.on("exit", () => resolve(true));   // any exit code = binary is present
      if (signal) {
        signal.addEventListener("abort", () => {
          if (!child.killed) child.kill();
          resolve(false);
        }, { once: true });
      }
    });
  }

  async _fetchOllamaTags(signal) {
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 3000);
      signal.addEventListener("abort", () => controller.abort(), { once: true });
      const response = await fetch("http://localhost:11434/api/tags", { signal: controller.signal });
      clearTimeout(timeoutId);
      if (!response.ok) {
        return null;
      }
      const json = await response.json();
      return (json.models || []).map((entry) => String(entry?.name || "").trim()).filter(Boolean);
    } catch {
      return null;
    }
  }

  async _downloadAndInstallOllama(signal) {
    const zipUrl = this.options.ollamaZipUrl;
    if (!zipUrl) {
      throw new Error("Ollama runtime zip URL is not configured.");
    }

    const archivePath = path.join(this.options.runtimePaths.INSTALL_CACHE_DIR, "ollama-windows-amd64.zip");
    const stagingPath = path.join(this.options.runtimePaths.INSTALL_CACHE_DIR, "ollama-extract");
    const targetPath = this.options.runtimePaths.OLLAMA_DIR;

    this._transition("downloading_ollama");
    this._setProgress("Downloading Ollama runtime", { percent: null });
    await this._downloadFile(zipUrl, archivePath, "Downloading Ollama runtime", signal);
    await this._verifyFileHash(archivePath, this.options.ollamaZipSha256);

    this._transition("installing_ollama", { guided_installer_path: archivePath });
    this._setProgress("Installing Ollama runtime", { percent: null });
    fs.rmSync(stagingPath, { recursive: true, force: true });
    fs.mkdirSync(stagingPath, { recursive: true });
    await this._extractZip(archivePath, stagingPath, signal);

    const extractedRoot = this._resolveExtractedOllamaRoot(stagingPath);
    fs.rmSync(targetPath, { recursive: true, force: true });
    fs.renameSync(extractedRoot, targetPath);
    fs.rmSync(stagingPath, { recursive: true, force: true });

    if (!fs.existsSync(this.options.runtimePaths.OLLAMA_EMBEDDED_ENTRY)) {
      throw new Error("Ollama runtime installation completed, but ollama.exe was not found.");
    }
  }

  async _startOllama(signal) {
    this._transition("starting_ollama");
    this._setProgress("Starting Ollama", { percent: null });
    signal?.throwIfAborted?.();
    const child = spawn(this._getOllamaExecutablePath(), ["serve"], {
      shell: false,
      windowsHide: true,
      detached: true,
      env: this._getOllamaEnvironment(),
      stdio: "ignore",
    });
    child.unref();
  }

  async _stopOllama(signal) {
    signal?.throwIfAborted?.();
    await this._runCmd("taskkill /IM ollama.exe /F", {
      signal,
      allowFailure: true,
    });
  }

  async _ensureOllamaAvailable(signal) {
    for (let attempt = 1; attempt <= MAX_OLLAMA_START_RETRIES; attempt += 1) {
      signal?.throwIfAborted?.();
      const probe = await this._checkOllama(signal);
      if (probe.available) {
        this._setComponent("ollama", true);
        return true;
      }

      if (attempt > 1) {
        this._setProgress(`Retrying Ollama startup (${attempt}/${MAX_OLLAMA_START_RETRIES})`, { percent: null });
      }

      await this._startOllama(signal);
      const ready = await this._waitForOllama(signal, { throwOnFailure: false });
      if (ready) {
        return true;
      }

      if (attempt < MAX_OLLAMA_START_RETRIES) {
        await this._stopOllama(signal);
        await this._delay(1000, signal);
      }
    }

    throw new Error("Ollama did not become available after startup.");
  }

  async _waitForOllama(signal, { throwOnFailure = true } = {}) {
    await this._delay(3000, signal);
    for (let attempt = 0; attempt < 120; attempt += 1) {
      signal.throwIfAborted?.();
      const models = await this._fetchOllamaTags(signal);
      if (models) {
        this._setComponent("ollama", true);
        return true;
      }
      await this._delay(1000, signal);
    }
    if (throwOnFailure) {
      throw new Error("Ollama did not become available after startup.");
    }
    return false;
  }

  async _ensureOllamaModel(modelName, stateName, signal) {
    if (!modelName) {
      // No specific model configured — whatever is already installed will be used at runtime
      this._setComponent(stateName === "pulling_brain" ? "brain_model" : "embedding_model", true);
      return;
    }
    for (let attempt = 1; attempt <= MAX_OLLAMA_MODEL_PULL_RETRIES; attempt += 1) {
      const probe = await this._checkOllama(signal);
      if (ollamaModelMatches(probe.models, modelName)) {
        this._setComponent(stateName === "pulling_brain" ? "brain_model" : "embedding_model", true);
        return;
      }

      this._transition(stateName);
      this._setProgress(
        attempt > 1
          ? `Retrying ${modelName} (${attempt}/${MAX_OLLAMA_MODEL_PULL_RETRIES})`
          : `Pulling ${modelName}`,
        { percent: attempt > 1 ? null : 0 }
      );

      try {
        await this._pullOllamaModel(modelName, signal);
        this._setComponent(stateName === "pulling_brain" ? "brain_model" : "embedding_model", true);
        return;
      } catch (error) {
        if (attempt >= MAX_OLLAMA_MODEL_PULL_RETRIES) {
          throw error;
        }
        await this._delay(1000, signal);
      }
    }
  }

  async _pullOllamaModel(modelName, signal) {
    const response = await fetch("http://localhost:11434/api/pull", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: modelName, stream: true }),
      signal,
    });
    if (!response.ok || !response.body) {
      throw new Error(`Could not pull Ollama model ${modelName}.`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) {
          continue;
        }
        try {
          const payload = JSON.parse(line);
          const percent = payload.total ? Math.round((Number(payload.completed || 0) / Number(payload.total)) * 100) : null;
          this._setProgress(`Pulling ${modelName}`, {
            percent,
            current: Number.isFinite(payload.completed) ? Number(payload.completed) : null,
            total: Number.isFinite(payload.total) ? Number(payload.total) : null,
            downloaded_bytes: Number.isFinite(payload.completed) ? Number(payload.completed) : null,
            total_bytes: Number.isFinite(payload.total) ? Number(payload.total) : null,
          });
        } catch {
          // Ignore malformed chunks from the stream.
        }
      }
    }
  }

  async _ensureKokoro(signal) {
    if (fs.existsSync(this.options.runtimePaths.KOKORO_MODEL_PATH) && fs.existsSync(this.options.runtimePaths.KOKORO_VOICES_PATH)) {
      this._setComponent("kokoro", true);
      return;
    }

    this._transition("downloading_kokoro");
    const assets = [
      {
        targetPath: this.options.runtimePaths.KOKORO_MODEL_PATH,
        legacyPath: path.join(this.options.runtimePaths.PROJECT_ROOT, "models", "kokoro-v1.0.onnx"),
        url: this.options.kokoroModelUrl,
        sha256: this.options.kokoroModelSha256,
        label: "Downloading Kokoro model",
      },
      {
        targetPath: this.options.runtimePaths.KOKORO_VOICES_PATH,
        legacyPath: path.join(this.options.runtimePaths.PROJECT_ROOT, "models", "voices-v1.0.bin"),
        url: this.options.kokoroVoicesUrl,
        sha256: this.options.kokoroVoicesSha256,
        label: "Downloading Kokoro voices",
      },
    ];

    for (const asset of assets) {
      if (fs.existsSync(asset.targetPath)) {
        continue;
      }

      fs.mkdirSync(path.dirname(asset.targetPath), { recursive: true });
      if (fs.existsSync(asset.legacyPath)) {
        fs.copyFileSync(asset.legacyPath, asset.targetPath);
      } else if (asset.url) {
        await this._downloadFile(asset.url, asset.targetPath, asset.label, signal);
        await this._verifyFileHash(asset.targetPath, asset.sha256);
      } else {
        throw new Error(`Kokoro asset source is not configured for ${path.basename(asset.targetPath)}.`);
      }
    }

    this._setComponent("kokoro", true);
  }

  async _prefetchWhisper(signal, configOverride = null) {
    this._transition("preparing_whisper");
    this._setProgress(`Preparing Whisper ${this._whisperModel(configOverride)}`, { percent: null });
    if (typeof this.options.runBackendCommand !== "function") {
      throw new Error("Backend command runner is not configured for Whisper preparation.");
    }
    await this.options.runBackendCommand(["--prefetch-stt"], { signal });
    this._markWhisperPrepared(configOverride);
    this._setComponent("whisper", true);
  }

  async _downloadFile(url, destinationPath, label, signal) {
    const response = await fetch(url, { signal });
    if (!response.ok || !response.body) {
      throw new Error(`Could not download ${label.toLowerCase()}.`);
    }

    const total = Number(response.headers.get("content-length") || 0) || null;
    const tempPath = `${destinationPath}.download`;
    const writer = fs.createWriteStream(tempPath);
    const reader = response.body.getReader();
    let downloaded = 0;

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }
        writer.write(Buffer.from(value));
        downloaded += value.byteLength;
        this._setProgress(label, {
          percent: total ? Math.round((downloaded / total) * 100) : null,
          current: downloaded,
          total,
          downloaded_bytes: downloaded,
          total_bytes: total,
        });
      }
      await new Promise((resolve, reject) => {
        writer.on("finish", resolve);
        writer.on("error", reject);
        writer.end();
      });
    } catch (error) {
      writer.destroy();
      fs.rmSync(tempPath, { force: true });
      throw error;
    }

    fs.renameSync(tempPath, destinationPath);
  }

  _resolveExtractedOllamaRoot(stagingPath) {
    const directExePath = path.join(stagingPath, "ollama.exe");
    if (fs.existsSync(directExePath)) {
      return stagingPath;
    }

    const directories = fs.readdirSync(stagingPath, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => path.join(stagingPath, entry.name));
    if (directories.length === 1 && fs.existsSync(path.join(directories[0], "ollama.exe"))) {
      return directories[0];
    }

    throw new Error("Ollama runtime archive did not contain ollama.exe in the expected location.");
  }

  async _extractZip(archivePath, destinationPath, signal) {
    const script = [
      `if (Test-Path -LiteralPath '${quotePowerShell(destinationPath)}') { Remove-Item -LiteralPath '${quotePowerShell(destinationPath)}' -Recurse -Force }`,
      `New-Item -ItemType Directory -Path '${quotePowerShell(destinationPath)}' -Force | Out-Null`,
      `Expand-Archive -LiteralPath '${quotePowerShell(archivePath)}' -DestinationPath '${quotePowerShell(destinationPath)}' -Force`,
    ].join("; ");
    const result = await this._runPowerShell(script, { signal, allowFailure: true });
    if (result.code !== 0) {
      throw new Error("Could not extract the Ollama runtime archive.");
    }
  }

  async _verifyFileHash(filePath, expectedHash) {
    const normalized = String(expectedHash || "").trim().toLowerCase();
    if (!normalized) {
      return;
    }
    const hash = crypto.createHash("sha256");
    hash.update(fs.readFileSync(filePath));
    const actual = hash.digest("hex").toLowerCase();
    if (actual !== normalized) {
      throw new Error(`Checksum mismatch for ${path.basename(filePath)}.`);
    }
  }

  async _runPowerShell(command, { signal, allowFailure = false } = {}) {
    signal?.throwIfAborted?.();
    const workingDirectory = this.options.runtimePaths.READONLY_PROJECT_ROOT || this.options.runtimePaths.PROJECT_ROOT;

    return await new Promise((resolve, reject) => {
      const child = spawn("powershell.exe", ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command], {
        cwd: workingDirectory,
        windowsHide: true,
        env: process.env,
        stdio: ["ignore", "pipe", "pipe"],
      });
      this.currentChild = child;

      let stderr = "";
      child.stderr.on("data", (chunk) => {
        stderr += String(chunk || "");
      });
      child.on("error", reject);
      child.on("exit", (code) => {
        this.currentChild = null;
        if (code === 0 || allowFailure) {
          resolve({ code, stderr: stderr.trim() });
          return;
        }
        reject(new Error(stderr.trim() || `PowerShell failed with code ${code}`));
      });

      if (signal) {
        const onAbort = () => {
          if (!child.killed) {
            child.kill();
          }
          const error = new Error("Aborted");
          error.name = "AbortError";
          reject(error);
        };
        if (signal.aborted) {
          onAbort();
          return;
        }
        signal.addEventListener("abort", onAbort, { once: true });
      }
    });
  }

  async _runCmd(command, { signal, allowFailure = false } = {}) {
    signal?.throwIfAborted?.();
    const workingDirectory = this.options.runtimePaths.READONLY_PROJECT_ROOT || this.options.runtimePaths.PROJECT_ROOT;

    return await new Promise((resolve, reject) => {
      const child = spawn(command, [], {
        shell: true,
        cwd: workingDirectory,
        windowsHide: true,
        env: process.env,
        stdio: ["ignore", "pipe", "pipe"],
      });
      this.currentChild = child;

      let stderr = "";
      child.stderr.on("data", (chunk) => {
        stderr += String(chunk || "");
      });
      child.on("error", reject);
      child.on("exit", (code) => {
        this.currentChild = null;
        if (code === 0 || allowFailure) {
          resolve({ code, stderr: stderr.trim() });
          return;
        }
        reject(new Error(stderr.trim() || `Command failed with code ${code}: ${command}`));
      });

      if (signal) {
        const onAbort = () => {
          if (!child.killed) {
            child.kill();
          }
          const error = new Error("Aborted");
          error.name = "AbortError";
          reject(error);
        };
        if (signal.aborted) {
          onAbort();
          return;
        }
        signal.addEventListener("abort", onAbort, { once: true });
      }
    });
  }

  async _delay(ms, signal) {
    return await new Promise((resolve, reject) => {
      const timer = setTimeout(resolve, Math.max(0, Number(ms) || 0));
      if (signal) {
        const onAbort = () => {
          clearTimeout(timer);
          const error = new Error("Aborted");
          error.name = "AbortError";
          reject(error);
        };
        if (signal.aborted) {
          onAbort();
          return;
        }
        signal.addEventListener("abort", onAbort, { once: true });
      }
    });
  }
}

module.exports = { RuntimeManager };
