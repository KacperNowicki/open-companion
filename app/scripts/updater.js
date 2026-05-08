const { autoUpdater } = require("electron-updater");

const DEFAULT_STATUS = Object.freeze({
  checking: false,
  available: false,
  downloading: false,
  ready: false,
  upToDate: false,
  dismissed: false,
  error: "",
  currentVersion: "",
  latestVersion: "",
  downloadedVersion: "",
  lastCheckedAt: 0,
  downloadProgressPercent: 0,
});

function cloneStatus(status) {
  return { ...status };
}

function normalizeConfig(config) {
  const updater = config && typeof config === "object" && config.updater && typeof config.updater === "object"
    ? config.updater
    : {};

  return {
    checkOnStartup: updater.check_on_startup !== false,
    dismissedVersion: String(updater.dismissed_version || ""),
    lastCheckTs: Number(updater.last_check_ts || 0),
  };
}

function createUpdaterController() {
  let mainWindow = null;
  let configured = false;
  let listenersBound = false;
  let startupCheckTimer = null;
  let configState = normalizeConfig(null);
  let status = {
    ...cloneStatus(DEFAULT_STATUS),
    lastCheckedAt: 0,
  };

  function emit(channel, payload) {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send(channel, payload);
    }
  }

  function setStatus(patch) {
    status = {
      ...status,
      ...patch,
    };
    return getStatus();
  }

  function resetTransientStatus() {
    setStatus({
      checking: false,
      available: false,
      downloading: false,
      upToDate: false,
      error: "",
      latestVersion: "",
      downloadedVersion: "",
      downloadProgressPercent: 0,
    });
  }

  function shouldSuppressVersion(version) {
    return Boolean(version && configState.dismissedVersion && configState.dismissedVersion === version);
  }

  function bindAutoUpdaterEvents() {
    if (listenersBound) {
      return;
    }
    listenersBound = true;

    autoUpdater.autoDownload = true;
    autoUpdater.autoInstallOnAppQuit = false;

    autoUpdater.removeAllListeners("checking-for-update");
    autoUpdater.removeAllListeners("update-available");
    autoUpdater.removeAllListeners("update-not-available");
    autoUpdater.removeAllListeners("download-progress");
    autoUpdater.removeAllListeners("update-downloaded");
    autoUpdater.removeAllListeners("error");

    autoUpdater.on("checking-for-update", () => {
      resetTransientStatus();
      setStatus({
        checking: true,
        dismissed: false,
      });
    });

    autoUpdater.on("update-available", (info = {}) => {
      const version = String(info.version || "");
      const suppressed = shouldSuppressVersion(version);
      setStatus({
        checking: false,
        available: !suppressed,
        downloading: !suppressed,
        ready: false,
        upToDate: false,
        dismissed: suppressed,
        error: "",
        latestVersion: version,
        downloadedVersion: "",
        lastCheckedAt: Date.now(),
      });

      if (!suppressed) {
        emit("update:available", {
          version,
          releaseNotes: info.releaseNotes || "",
        });
      }
    });

    autoUpdater.on("update-not-available", () => {
      setStatus({
        checking: false,
        available: false,
        downloading: false,
        ready: false,
        upToDate: true,
        dismissed: false,
        error: "",
        latestVersion: "",
        downloadedVersion: "",
        downloadProgressPercent: 0,
        lastCheckedAt: Date.now(),
      });
    });

    autoUpdater.on("download-progress", (progress = {}) => {
      setStatus({
        checking: false,
        available: true,
        downloading: true,
        ready: false,
        upToDate: false,
        error: "",
        downloadProgressPercent: Math.round(progress.percent || 0),
      });

      emit("update:progress", {
        percent: Math.round(progress.percent || 0),
      });
    });

    autoUpdater.on("update-downloaded", (info = {}) => {
      const version = String(info.version || status.latestVersion || "");
      const suppressed = shouldSuppressVersion(version);
      setStatus({
        checking: false,
        available: false,
        downloading: false,
        ready: !suppressed,
        upToDate: false,
        dismissed: suppressed,
        error: "",
        latestVersion: version,
        downloadedVersion: version,
        downloadProgressPercent: 100,
        lastCheckedAt: Date.now(),
      });

      if (!suppressed) {
        emit("update:ready", {
          version,
        });
      }
    });

    autoUpdater.on("error", (err) => {
      const message = err && err.message ? err.message : "Update check failed";
      setStatus({
        checking: false,
        available: false,
        downloading: false,
        error: message,
        upToDate: false,
      });
      console.error("[updater] Error:", message);
    });
  }

  function attachWindow(windowRef) {
    mainWindow = windowRef || null;
    return controller;
  }

  function configure(config) {
    configState = normalizeConfig(config);
    setStatus({
      dismissed: Boolean(configState.dismissedVersion),
      lastCheckedAt: configState.lastCheckTs,
    });
    return controller;
  }

  async function checkNow() {
    bindAutoUpdaterEvents();
    setStatus({
      checking: true,
      upToDate: false,
      error: "",
    });

    try {
      const result = await autoUpdater.checkForUpdates();
      setStatus({
        lastCheckedAt: Date.now(),
      });
      return result;
    } catch (error) {
      const message = error && error.message ? error.message : "Update check failed";
      setStatus({
        checking: false,
        error: message,
        lastCheckedAt: Date.now(),
      });
      throw error;
    }
  }

  async function downloadUpdate() {
    bindAutoUpdaterEvents();

    if (status.ready) {
      return { ok: true, alreadyReady: true, status: getStatus() };
    }

    setStatus({
      downloading: true,
      error: "",
    });

    await autoUpdater.downloadUpdate();
    return { ok: true, status: getStatus() };
  }

  function installUpdate() {
    autoUpdater.quitAndInstall();
    return { ok: true };
  }

  function dismissReady(version) {
    const dismissedVersion = String(version || status.downloadedVersion || status.latestVersion || "");
    configState = {
      ...configState,
      dismissedVersion,
    };

    if (dismissedVersion && (dismissedVersion === status.downloadedVersion || dismissedVersion === status.latestVersion)) {
      setStatus({
        ready: false,
        available: false,
        downloading: false,
        dismissed: true,
      });
    }

    return getStatus();
  }

  function getStatus() {
    return cloneStatus(status);
  }

  function scheduleStartupCheck(delayMs = 5000) {
    if (!configState.checkOnStartup) {
      return controller;
    }

    if (startupCheckTimer) {
      clearTimeout(startupCheckTimer);
    }

    startupCheckTimer = setTimeout(() => {
      checkNow().catch(() => {});
    }, delayMs);

    return controller;
  }

  function setup(windowRef, config) {
    attachWindow(windowRef);
    configure(config);
    bindAutoUpdaterEvents();
    configured = true;
    scheduleStartupCheck();
    return controller;
  }

  function isConfigured() {
    return configured;
  }

  const controller = {
    setup,
    attachWindow,
    configure,
    checkNow,
    downloadUpdate,
    installUpdate,
    dismissReady,
    getStatus,
    scheduleStartupCheck,
    isConfigured,
  };

  return controller;
}

const updaterController = createUpdaterController();

function setupUpdater(mainWindow, config) {
  return updaterController.setup(mainWindow, config);
}

module.exports = {
  createUpdaterController,
  updaterController,
  setupUpdater,
  checkForAppUpdates: () => updaterController.checkNow(),
  downloadAppUpdate: () => updaterController.downloadUpdate(),
  installAppUpdate: () => updaterController.installUpdate(),
  dismissReadyAppUpdate: (version) => updaterController.dismissReady(version),
  getUpdaterStatus: () => updaterController.getStatus(),
};
