#!/usr/bin/env node
const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");
const PYTHON = process.env.OPEN_COMPANION_PYTHON || process.env.PYTHON || "python";
const REQUIREMENTS_LOCK = path.join(ROOT, "requirements.lock");
const BUILD_ROOT = path.join(ROOT, "build");
const DIST_DIR = path.join(BUILD_ROOT, "backend");
const WORK_DIR = path.join(BUILD_ROOT, "backend-work");
const SPEC_DIR = path.join(BUILD_ROOT, "backend-spec");
const BACKEND_ENTRY = path.join(ROOT, "app", "backend", "wrapper.py");
const BACKEND_EXE = path.join(DIST_DIR, "open-companion-backend.exe");
const BACKEND_MODULE_ROOT = path.join(ROOT, "app", "backend");

function run(command, args, options = {}) {
  return spawnSync(command, args, {
    cwd: ROOT,
    stdio: "inherit",
    windowsHide: true,
    ...options,
  });
}

function ensureSuccess(result, label) {
  if (result.status === 0) {
    return;
  }

  const code = result.status ?? 1;
  console.error(`[build-backend] ${label} failed with exit code ${code}`);
  process.exit(code);
}

if (process.platform !== "win32") {
  console.error("[build-backend] Windows backend packaging is only supported on Windows.");
  process.exit(1);
}

if (!fs.existsSync(REQUIREMENTS_LOCK)) {
  console.error(`[build-backend] Missing requirements lockfile: ${REQUIREMENTS_LOCK}`);
  process.exit(1);
}

const depsInstall = run(PYTHON, ["-m", "pip", "install", "--require-hashes", "-r", REQUIREMENTS_LOCK]);
ensureSuccess(depsInstall, "pip install");

const pyinstallerCheck = run(PYTHON, ["-m", "PyInstaller", "--version"], { stdio: "pipe" });
if (pyinstallerCheck.status !== 0) {
  console.log("[build-backend] PyInstaller not found; installing build tool");
  const pyinstallerInstall = run(PYTHON, ["-m", "pip", "install", "pyinstaller"]);
  ensureSuccess(pyinstallerInstall, "pyinstaller install");
}

fs.rmSync(DIST_DIR, { recursive: true, force: true });
fs.rmSync(WORK_DIR, { recursive: true, force: true });
fs.rmSync(SPEC_DIR, { recursive: true, force: true });
fs.mkdirSync(DIST_DIR, { recursive: true });

// Resolve site-packages directories so we can locate data files that
// PyInstaller does not auto-collect (packages that load data from __file__-
// relative paths at import time, before any hook can intervene).
const sitePackagesResult = spawnSync(PYTHON, [
  "-c",
  "import site, json; print(json.dumps(site.getsitepackages() + [site.getusersitepackages()]))",
], { cwd: ROOT, stdio: "pipe", windowsHide: true });
if (sitePackagesResult.status !== 0) {
  console.error("[build-backend] Could not resolve Python site-packages.");
  process.exit(1);
}
const sitePackagesDirs = JSON.parse(sitePackagesResult.stdout.toString().trim());

// Find the first directory that contains a given package sub-path.
function findDataDir(...segments) {
  for (const sp of sitePackagesDirs) {
    const candidate = path.join(sp, ...segments);
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }
  return null;
}

// Build --add-data arguments for packages whose data files are not
// auto-collected by PyInstaller.  sep is ";" on Windows (PATH separator).
const sep = ";";
const addDataArgs = [];

// language_tags loads language_tags/data/json/index.json at import time via
// language_tags.data.__init__.get().  The whole data/ tree must be present.
const languageTagsData = findDataDir("language_tags", "data");
if (languageTagsData) {
  addDataArgs.push("--add-data", `${languageTagsData}${sep}language_tags/data`);
} else {
  console.warn("[build-backend] WARNING: language_tags/data not found in site-packages; kokoro-onnx TTS may crash at runtime.");
}

// phonemizer ships festival and segments g2p data under phonemizer/share/.
const phonemizerShare = findDataDir("phonemizer", "share");
if (phonemizerShare) {
  addDataArgs.push("--add-data", `${phonemizerShare}${sep}phonemizer/share`);
}

const pyinstallerArgs = [
  "-m",
  "PyInstaller",
  "--noconfirm",
  "--clean",
  "--onefile",
  "--name",
  "open-companion-backend",
  "--distpath",
  DIST_DIR,
  "--workpath",
  WORK_DIR,
  "--specpath",
  SPEC_DIR,
  "--paths",
  BACKEND_MODULE_ROOT,
  "--collect-all",
  "kokoro_onnx",
  "--collect-all",
  "pywhispercpp",
  "--collect-all",
  "PIL",
  "--collect-all",
  "numpy",
  "--collect-all",
  "soundfile",
  "--hidden-import",
  "psutil",
  "--hidden-import",
  "keyring",
  "--hidden-import",
  "runtime_paths",
  ...addDataArgs,
  BACKEND_ENTRY,
];

const build = run(PYTHON, pyinstallerArgs);
ensureSuccess(build, "PyInstaller");

if (!fs.existsSync(BACKEND_EXE)) {
  console.error(`[build-backend] Expected backend exe was not produced: ${BACKEND_EXE}`);
  process.exit(1);
}

console.log(`[build-backend] Backend packaged at ${BACKEND_EXE}`);
