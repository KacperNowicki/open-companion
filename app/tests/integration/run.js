const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..", "..");
const PYTHON_RUNNER = path.join(ROOT, "app", "tests", "integration", "run_python.py");

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function runJsChecks() {
  const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, "package.json"), "utf8"));
  const mainJs = fs.readFileSync(path.join(ROOT, "app", "frontend", "main.js"), "utf8");

  assert(pkg.scripts["test:integration"] === "node app/tests/integration/run.js", "package.json is missing test:integration");
  assert(pkg.scripts["test:companion"] === "python app/tests/companion/run.py", "package.json is missing test:companion");
  assert(mainJs.includes('ipcMain.handle("onboarding:resetMemory"'), "main.js is missing onboarding:resetMemory IPC");
  assert(mainJs.includes("function resetLongTermMemory()"), "main.js is missing resetLongTermMemory()");
  assert(mainJs.includes("function writeOnboardingImportFiles(sections)"), "main.js is missing writeOnboardingImportFiles()");

  process.stdout.write("[PASS] js integration: package scripts and onboarding memory hooks\n");
}

function runCommand(command, args, label) {
  const result = spawnSync(command, args, {
    cwd: ROOT,
    env: process.env,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });
  if (result.error) {
    throw new Error(`${label} could not be started (${result.error.code || "ERROR"}): ${result.error.message}`);
  }
  if (result.status !== 0) {
    throw new Error(`${label} failed with exit code ${result.status}\n${result.stdout || ""}${result.stderr ? `\n${result.stderr}` : ""}`);
  }
  process.stdout.write(result.stdout || "");
  process.stderr.write(result.stderr || "");
}

try {
  runJsChecks();
  runCommand(process.env.PYTHON || "python", [PYTHON_RUNNER], "Python integration checks");
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
}
