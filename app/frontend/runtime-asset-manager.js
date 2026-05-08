const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const DEFAULT_SCHEMA = 1;
const DEFAULT_KOKORO_VERSION = "1.0.0";
const DEFAULT_WHISPER_VERSION = "1.0.0";
const DEFAULT_KOKORO_MODEL_URL = "https://huggingface.co/fastrtc/kokoro-onnx/resolve/main/kokoro-v1.0.onnx";
const DEFAULT_KOKORO_MODEL_SHA256 = "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5";
const DEFAULT_KOKORO_VOICES_URL = "https://huggingface.co/fastrtc/kokoro-onnx/resolve/main/voices-v1.0.bin";
const DEFAULT_KOKORO_VOICES_SHA256 = "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d";

function toUnixPath(value) {
  return String(value || "").replace(/\\/g, "/");
}

function deepClone(value) {
  return JSON.parse(JSON.stringify(value));
}

function sanitizeVersion(value) {
  return String(value || "0.0.0").trim().replace(/[^a-zA-Z0-9._-]+/g, "_") || "0.0.0";
}

function ensureRelativeLocalPath(localPath) {
  const normalized = toUnixPath(localPath).replace(/^\/+/, "");
  if (!normalized || normalized.startsWith("..") || normalized.includes("/../")) {
    throw new Error(`Invalid runtime asset local_path: ${localPath}`);
  }
  return normalized;
}

function parseVersion(value) {
  return String(value || "")
    .split(/[^a-zA-Z0-9]+/)
    .filter(Boolean)
    .map((part) => (/^\d+$/.test(part) ? Number(part) : String(part).toLowerCase()));
}

function compareVersions(left, right) {
  const a = parseVersion(left);
  const b = parseVersion(right);
  const max = Math.max(a.length, b.length);
  for (let index = 0; index < max; index += 1) {
    const leftPart = a[index];
    const rightPart = b[index];
    if (leftPart === undefined && rightPart === undefined) {
      return 0;
    }
    if (leftPart === undefined) {
      return -1;
    }
    if (rightPart === undefined) {
      return 1;
    }
    if (typeof leftPart === "number" && typeof rightPart === "number") {
      if (leftPart !== rightPart) {
        return leftPart < rightPart ? -1 : 1;
      }
      continue;
    }
    const leftText = String(leftPart);
    const rightText = String(rightPart);
    if (leftText !== rightText) {
      return leftText < rightText ? -1 : 1;
    }
  }
  return 0;
}

function defaultAssetCatalog(options = {}) {
  const runtimePaths = options.runtimePaths || {};
  const runtimeAssetsDir = options.runtimeAssetsDir
    || runtimePaths.RUNTIME_ASSETS_DIR
    || path.join(options.profileRoot || process.cwd(), "runtime-assets");
  // Use stable profile-relative paths rather than computing via path.relative() from
  // KOKORO_MODEL_PATH, which may resolve to a legacy dev path (../models/) when the
  // source models/ directory exists on the build machine — producing a ../ path that
  // fails ensureRelativeLocalPath().
  const kokoroModelLocalPath = options.kokoroModelLocalPath
    || path.join("kokoro", "kokoro-v1.0.onnx");
  const kokoroVoicesLocalPath = options.kokoroVoicesLocalPath
    || path.join("kokoro", "voices-v1.0.bin");
  const whisperLocalPath = options.whisperBaseLocalPath
    || path.join("whisper", "ggml-base.en.bin");

  const assets = {
    "kokoro-model": {
      version: String(options.kokoroModelVersion || process.env.OPEN_COMPANION_KOKORO_MODEL_VERSION || DEFAULT_KOKORO_VERSION),
      url: String(options.kokoroModelUrl || process.env.OPEN_COMPANION_KOKORO_MODEL_URL || DEFAULT_KOKORO_MODEL_URL),
      sha256: String(options.kokoroModelSha256 || process.env.OPEN_COMPANION_KOKORO_MODEL_SHA256 || DEFAULT_KOKORO_MODEL_SHA256),
      local_path: ensureRelativeLocalPath(kokoroModelLocalPath),
      status: "pending",
    },
    "kokoro-voices": {
      version: String(options.kokoroVoicesVersion || process.env.OPEN_COMPANION_KOKORO_VOICES_VERSION || DEFAULT_KOKORO_VERSION),
      url: String(options.kokoroVoicesUrl || process.env.OPEN_COMPANION_KOKORO_VOICES_URL || DEFAULT_KOKORO_VOICES_URL),
      sha256: String(options.kokoroVoicesSha256 || process.env.OPEN_COMPANION_KOKORO_VOICES_SHA256 || DEFAULT_KOKORO_VOICES_SHA256),
      local_path: ensureRelativeLocalPath(kokoroVoicesLocalPath),
      status: "pending",
    },
  };

  const whisperUrl = String(options.whisperBaseUrl || process.env.OPEN_COMPANION_WHISPER_BASE_URL || "");
  const whisperSha256 = String(options.whisperBaseSha256 || process.env.OPEN_COMPANION_WHISPER_BASE_SHA256 || "");
  if (whisperUrl || whisperSha256 || options.includeDefaultWhisperAsset) {
    assets["whisper-base"] = {
      version: String(options.whisperBaseVersion || process.env.OPEN_COMPANION_WHISPER_BASE_VERSION || DEFAULT_WHISPER_VERSION),
      url: whisperUrl,
      sha256: whisperSha256,
      local_path: ensureRelativeLocalPath(whisperLocalPath),
      status: "pending",
    };
  }

  return assets;
}

class RuntimeAssetManager {
  constructor(options = {}) {
    if (!options.profileRoot && !options.runtimePaths?.PROFILE_ROOT) {
      throw new Error("RuntimeAssetManager requires profileRoot or runtimePaths.PROFILE_ROOT.");
    }

    this.options = options;
    this.profileRoot = path.resolve(options.profileRoot || options.runtimePaths.PROFILE_ROOT);
    this.runtimeAssetsDir = path.resolve(
      options.runtimeAssetsDir
      || options.runtimePaths?.RUNTIME_ASSETS_DIR
      || path.join(this.profileRoot, "runtime-assets")
    );
    this.cacheDir = path.join(this.runtimeAssetsDir, "cache");
    this.manifestPath = path.join(this.runtimeAssetsDir, "manifest.json");
    this.fetchImpl = options.fetch || global.fetch;
    this.logger = options.logger || console;
    this.onEvent = typeof options.onEvent === "function" ? options.onEvent : null;
    this.status = this._emptyStatus();
    this.currentRun = null;
  }

  _emptyStatus() {
    return {
      checking: false,
      updating: false,
      ready: false,
      upToDate: false,
      last_checked_ts: 0,
      last_error: "",
      manifest_path: this.manifestPath,
      cache_dir: this.cacheDir,
      assets: {},
    };
  }

  _emit(patch = {}) {
    this.status = {
      ...this.status,
      ...patch,
      assets: patch.assets ? deepClone(patch.assets) : this.status.assets,
    };
    if (this.onEvent) {
      this.onEvent(this.getStatus());
    }
  }

  getStatus() {
    return deepClone(this.status);
  }

  async startupCheck() {
    return await this.ensureAssets({ reason: "startup" });
  }

  async checkNow() {
    return await this.ensureAssets({ reason: "manual" });
  }

  async refreshStatus() {
    this._emit({ checking: true, last_error: "" });
    try {
      await this._ensureDirectories();
      const manifest = await this._readManifest();
      const desiredAssets = this._getDesiredAssets();
      const assets = await this._buildStatusAssets(manifest, desiredAssets);
      const ready = Object.values(assets).every((entry) => entry.status === "installed");
      const upToDate = ready && Object.values(assets).every((entry) => !entry.needs_update);
      this._emit({
        checking: false,
        updating: false,
        ready,
        upToDate,
        last_checked_ts: Date.now(),
        assets,
      });
      return this.getStatus();
    } catch (error) {
      this._emit({
        checking: false,
        updating: false,
        ready: false,
        upToDate: false,
        last_checked_ts: Date.now(),
        last_error: String(error?.message || error || "Runtime asset refresh failed."),
      });
      return this.getStatus();
    }
  }

  async ensureAssets({ reason = "startup" } = {}) {
    if (this.currentRun) {
      return await this.currentRun;
    }

    this.currentRun = this._ensureAssetsInternal(reason)
      .catch((error) => {
        this._emit({
          checking: false,
          updating: false,
          ready: false,
          upToDate: false,
          last_checked_ts: Date.now(),
          last_error: String(error?.message || error || "Runtime asset update failed."),
        });
        return this.getStatus();
      })
      .finally(() => {
        this.currentRun = null;
      });

    return await this.currentRun;
  }

  async _ensureAssetsInternal(reason) {
    this._emit({ checking: true, updating: false, last_error: "" });
    await this._ensureDirectories();

    const manifest = await this._readManifest();
    const desiredAssets = this._getDesiredAssets();
    const assets = await this._buildStatusAssets(manifest, desiredAssets);
    this._emit({
      checking: false,
      updating: true,
      last_checked_ts: Date.now(),
      assets,
    });

    for (const [assetName, desiredAsset] of Object.entries(desiredAssets)) {
      const assetStatus = assets[assetName];
      if (!assetStatus || !assetStatus.needs_update) {
        continue;
      }

      if (assetStatus.can_adopt_existing) {
        manifest.assets[assetName] = this._manifestEntryFromDesired(desiredAsset, "installed");
        continue;
      }

      if (!desiredAsset.url || !desiredAsset.sha256) {
        throw new Error(`Runtime asset ${assetName} is missing a download source.`);
      }

      const cachePath = this._cachePathForAsset(assetName, desiredAsset);
      assets[assetName] = {
        ...assets[assetName],
        status: "downloading",
        last_action: reason,
        cache_path: cachePath,
      };
      this._emit({ assets });

      await this._downloadToCache(desiredAsset, cachePath, (progressPatch) => {
        assets[assetName] = {
          ...assets[assetName],
          status: "downloading",
          progress: progressPatch,
        };
        this._emit({ assets });
      });

      assets[assetName] = {
        ...assets[assetName],
        status: "verifying",
        progress: null,
      };
      this._emit({ assets });
      await this._verifyFileHash(cachePath, desiredAsset.sha256);

      const livePath = this._resolveLivePath(desiredAsset.local_path);
      assets[assetName] = {
        ...assets[assetName],
        status: "swapping",
        live_path: livePath,
      };
      this._emit({ assets });

      await this._activateCacheFile(cachePath, livePath);
      manifest.assets[assetName] = this._manifestEntryFromDesired(desiredAsset, "installed");

      assets[assetName] = await this._buildStatusAsset(
        assetName,
        manifest.assets[assetName],
        desiredAsset
      );
      this._emit({ assets });
    }

    await this._writeManifest(manifest);
    return await this.refreshStatus();
  }

  _getDesiredAssets() {
    const fromCallback = typeof this.options.getDesiredAssets === "function"
      ? this.options.getDesiredAssets()
      : null;
    const source = fromCallback || this.options.assets || defaultAssetCatalog(this.options);
    const assets = {};
    for (const [assetName, rawAsset] of Object.entries(source || {})) {
      assets[assetName] = this._normalizeDesiredAsset(assetName, rawAsset);
    }
    return assets;
  }

  _normalizeDesiredAsset(assetName, rawAsset) {
    if (!rawAsset || typeof rawAsset !== "object") {
      throw new Error(`Runtime asset ${assetName} is not configured.`);
    }
    const version = String(rawAsset.version || "").trim();
    const localPath = ensureRelativeLocalPath(rawAsset.local_path || rawAsset.localPath || "");
    if (!version) {
      throw new Error(`Runtime asset ${assetName} is missing a version.`);
    }
    return {
      version,
      url: String(rawAsset.url || "").trim(),
      sha256: String(rawAsset.sha256 || "").trim().toLowerCase(),
      local_path: localPath,
      status: "pending",
    };
  }

  async _ensureDirectories() {
    this.options.ensureRuntimeDirectories?.();
    await fs.promises.mkdir(this.runtimeAssetsDir, { recursive: true });
    await fs.promises.mkdir(this.cacheDir, { recursive: true });
  }

  async _readManifest() {
    try {
      const raw = await fs.promises.readFile(this.manifestPath, "utf8");
      const parsed = JSON.parse(raw);
      return {
        schema: Number(parsed?.schema || DEFAULT_SCHEMA),
        assets: parsed?.assets && typeof parsed.assets === "object" ? parsed.assets : {},
      };
    } catch (error) {
      if (error && error.code === "ENOENT") {
        return { schema: DEFAULT_SCHEMA, assets: {} };
      }
      throw new Error(`Could not read runtime asset manifest: ${error.message}`);
    }
  }

  async _writeManifest(manifest) {
    const next = {
      schema: DEFAULT_SCHEMA,
      assets: {},
    };

    for (const [assetName, assetValue] of Object.entries(manifest.assets || {})) {
      if (!assetValue || typeof assetValue !== "object") {
        continue;
      }
      next.assets[assetName] = {
        version: String(assetValue.version || ""),
        url: String(assetValue.url || ""),
        sha256: String(assetValue.sha256 || "").toLowerCase(),
        local_path: ensureRelativeLocalPath(assetValue.local_path || ""),
        status: String(assetValue.status || "installed"),
      };
    }

    const tempPath = `${this.manifestPath}.tmp`;
    await fs.promises.writeFile(tempPath, `${JSON.stringify(next, null, 2)}\n`, "utf8");
    await fs.promises.rename(tempPath, this.manifestPath);
  }

  _manifestEntryFromDesired(desiredAsset, status) {
    return {
      version: desiredAsset.version,
      url: desiredAsset.url,
      sha256: desiredAsset.sha256,
      local_path: desiredAsset.local_path,
      status: status || "installed",
    };
  }

  async _buildStatusAssets(manifest, desiredAssets) {
    const assets = {};
    for (const [assetName, desiredAsset] of Object.entries(desiredAssets)) {
      assets[assetName] = await this._buildStatusAsset(
        assetName,
        manifest.assets[assetName] || null,
        desiredAsset
      );
    }
    return assets;
  }

  async _buildStatusAsset(assetName, manifestEntry, desiredAsset) {
    const livePath = this._resolveLivePath(desiredAsset.local_path);
    const backupPath = `${livePath}.bak`;
    const liveExists = await this._fileExists(livePath);
    const backupExists = await this._fileExists(backupPath);
    const installedVersion = String(manifestEntry?.version || "");
    const versionCompare = installedVersion ? compareVersions(installedVersion, desiredAsset.version) : -1;
    const hashMatchesDesired = liveExists && desiredAsset.sha256
      ? await this._hashMatches(livePath, desiredAsset.sha256)
      : false;

    let status = liveExists ? "installed" : "missing";
    let needsUpdate = !liveExists;
    let canAdoptExisting = false;

    if (liveExists && !installedVersion && hashMatchesDesired) {
      canAdoptExisting = true;
      needsUpdate = true;
      status = "adopt-existing";
    } else if (liveExists && versionCompare < 0) {
      needsUpdate = true;
      status = "update-available";
    } else if (liveExists && installedVersion && compareVersions(installedVersion, desiredAsset.version) >= 0) {
      needsUpdate = false;
      status = "installed";
    }

    if (liveExists && manifestEntry?.sha256 && desiredAsset.sha256 && manifestEntry.sha256 !== desiredAsset.sha256) {
      needsUpdate = true;
      status = "update-available";
    }

    if (liveExists && desiredAsset.sha256 && !hashMatchesDesired && compareVersions(installedVersion || "0.0.0", desiredAsset.version) >= 0) {
      status = "checksum-mismatch";
      needsUpdate = true;
    }

    if (needsUpdate && (!desiredAsset.url || !desiredAsset.sha256)) {
      status = liveExists ? "update-blocked" : "source-missing";
    }

    return {
      asset_name: assetName,
      status,
      desired_version: desiredAsset.version,
      installed_version: installedVersion,
      local_path: desiredAsset.local_path,
      live_path: livePath,
      backup_path: backupPath,
      has_backup: backupExists,
      exists: liveExists,
      needs_update: needsUpdate,
      can_adopt_existing: canAdoptExisting,
      manifest_entry: manifestEntry ? deepClone(manifestEntry) : null,
      url: desiredAsset.url,
      sha256: desiredAsset.sha256,
      progress: null,
    };
  }

  _resolveLivePath(localPath) {
    const normalized = ensureRelativeLocalPath(localPath);
    return path.join(this.runtimeAssetsDir, normalized.split("/").join(path.sep));
  }

  _cachePathForAsset(assetName, desiredAsset) {
    const extension = path.extname(desiredAsset.local_path) || ".asset";
    return path.join(this.cacheDir, `${assetName}-${sanitizeVersion(desiredAsset.version)}${extension}`);
  }

  async _downloadToCache(desiredAsset, destinationPath, onProgress) {
    if (typeof this.fetchImpl !== "function") {
      throw new Error("Runtime asset download requires fetch in the main process.");
    }

    await fs.promises.mkdir(path.dirname(destinationPath), { recursive: true });
    if (await this._fileExists(destinationPath)) {
      try {
        await this._verifyFileHash(destinationPath, desiredAsset.sha256);
        return;
      } catch {
        await fs.promises.rm(destinationPath, { force: true });
      }
    }

    const tempPath = `${destinationPath}.download`;
    const response = await this.fetchImpl(desiredAsset.url);
    if (!response || !response.ok || !response.body) {
      throw new Error(`Could not download runtime asset ${path.basename(desiredAsset.local_path)}.`);
    }

    const total = Number(response.headers.get("content-length") || 0) || null;
    const reader = response.body.getReader();
    const writer = fs.createWriteStream(tempPath);
    let downloaded = 0;

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }
        const chunk = Buffer.from(value);
        writer.write(chunk);
        downloaded += chunk.byteLength;
        onProgress?.({
          downloaded_bytes: downloaded,
          total_bytes: total,
          percent: total ? Math.round((downloaded / total) * 100) : null,
        });
      }

      await new Promise((resolve, reject) => {
        writer.on("error", reject);
        writer.on("finish", resolve);
        writer.end();
      });
      await fs.promises.rename(tempPath, destinationPath);
    } catch (error) {
      writer.destroy();
      await fs.promises.rm(tempPath, { force: true }).catch(() => {});
      throw error;
    }
  }

  async _activateCacheFile(cachePath, livePath) {
    await fs.promises.mkdir(path.dirname(livePath), { recursive: true });
    const backupPath = `${livePath}.bak`;
    const hadLiveFile = await this._fileExists(livePath);

    if (await this._fileExists(backupPath)) {
      await fs.promises.rm(backupPath, { force: true });
    }

    if (hadLiveFile) {
      await fs.promises.rename(livePath, backupPath);
    }

    try {
      await fs.promises.rename(cachePath, livePath);
      if (!await this._fileExists(livePath)) {
        throw new Error(`Atomic asset swap failed for ${path.basename(livePath)}.`);
      }
    } catch (error) {
      if (hadLiveFile && await this._fileExists(backupPath) && !await this._fileExists(livePath)) {
        await fs.promises.rename(backupPath, livePath).catch(() => {});
      }
      throw error;
    }
  }

  async _verifyFileHash(filePath, expectedHash) {
    const normalized = String(expectedHash || "").trim().toLowerCase();
    if (!normalized) {
      throw new Error(`Checksum is required for ${path.basename(filePath)}.`);
    }

    const actualHash = await this._computeSha256(filePath);
    if (actualHash !== normalized) {
      throw new Error(`Checksum mismatch for ${path.basename(filePath)}.`);
    }
  }

  async _hashMatches(filePath, expectedHash) {
    try {
      await this._verifyFileHash(filePath, expectedHash);
      return true;
    } catch {
      return false;
    }
  }

  async _computeSha256(filePath) {
    return await new Promise((resolve, reject) => {
      const hash = crypto.createHash("sha256");
      const stream = fs.createReadStream(filePath);
      stream.on("data", (chunk) => hash.update(chunk));
      stream.on("error", reject);
      stream.on("end", () => resolve(hash.digest("hex").toLowerCase()));
    });
  }

  async _fileExists(targetPath) {
    try {
      await fs.promises.access(targetPath, fs.constants.F_OK);
      return true;
    } catch {
      return false;
    }
  }
}

module.exports = {
  DEFAULT_RUNTIME_ASSET_SCHEMA: DEFAULT_SCHEMA,
  RuntimeAssetManager,
  compareAssetVersions: compareVersions,
  defaultAssetCatalog,
};
