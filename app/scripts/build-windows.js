#!/usr/bin/env node
const { spawnSync } = require("child_process");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");
const NODE = process.execPath;

function run(command, args, options = {}) {
  return spawnSync(command, args, {
    cwd: ROOT,
    stdio: "inherit",
    windowsHide: true,
    ...options,
  });
}

function runCmd(commandLine) {
  return run(commandLine, [], { shell: true });
}

function ensureSuccess(result, label) {
  if (result.status === 0) {
    return;
  }

  const code = result.status ?? 1;
  console.error(`[build-windows] ${label} failed with exit code ${code}`);
  process.exit(code);
}

function runNodeScript(scriptName) {
  const scriptPath = path.join(ROOT, "app", "scripts", scriptName);
  const result = run(NODE, [scriptPath]);
  ensureSuccess(result, scriptName);
}

function getArgValue(flag, fallback) {
  const index = process.argv.indexOf(flag);
  if (index >= 0 && process.argv[index + 1] && !String(process.argv[index + 1]).startsWith("--")) {
    return process.argv[index + 1];
  }
  return fallback;
}

if (process.platform !== "win32") {
  console.error("[build-windows] Windows packaging is only supported on Windows.");
  process.exit(1);
}

const publish = getArgValue("--publish", "onTagOrDraft");
const shouldBuildDir = process.argv.includes("--dir");

runNodeScript("build-backend.js");
runNodeScript("generate-voices.js");
runNodeScript("generate-node-license-notice.js");

const builderCommand = shouldBuildDir
  ? `npx.cmd electron-builder --win --publish ${publish} --dir`
  : `npx.cmd electron-builder --win nsis --publish ${publish}`;
const builder = runCmd(builderCommand);
ensureSuccess(builder, "electron-builder");

console.log(`[build-windows] Windows package completed (${shouldBuildDir ? "directory build" : "installer build"})`);
