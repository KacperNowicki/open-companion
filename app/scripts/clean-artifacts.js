const childProcess = require("child_process");
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");

const CLEAN_GROUPS = {
  "build-output": {
    description: "Electron Builder and backend packaging output",
    default: true,
    targets: ["build", "release"],
  },
  "installer-cache": {
    description: "Downloaded installer/runtime cache files",
    default: true,
    targets: ["install-cache"],
  },
  logs: {
    description: "Local debug and runtime logs",
    default: true,
    targets: ["logs"],
  },
  "legacy-memory": {
    description: "Legacy root-level generated memory folder",
    default: true,
    targets: ["memory"],
  },
  "test-output": {
    description: "Root and app test result directories",
    default: true,
    targets: [
      "test-results",
      path.join("app", "tests", "test-results"),
      path.join("app", "tests", "test-results-playwright"),
    ],
  },
  "runtime-assets": {
    description: "Downloaded runtime assets such as Kokoro voice files",
    default: false,
    targets: ["runtime-assets"],
  },
};

const GROUP_ALIASES = {
  all: Object.keys(CLEAN_GROUPS),
  default: Object.entries(CLEAN_GROUPS)
    .filter(([, group]) => group.default)
    .map(([name]) => name),
};

function usage() {
  console.log("Usage: node app/scripts/clean-artifacts.js [--dry-run] [--list] [group ...]");
  console.log("");
  console.log("No groups means the default cleanup set:");
  for (const groupName of GROUP_ALIASES.default) {
    const group = CLEAN_GROUPS[groupName];
    console.log(`- ${groupName}: ${group.description}`);
  }
  console.log("");
  console.log("Optional groups:");
  for (const [groupName, group] of Object.entries(CLEAN_GROUPS)) {
    if (!group.default) {
      console.log(`- ${groupName}: ${group.description}`);
    }
  }
}

function listGroups() {
  console.log("Cleanup groups:");
  for (const [groupName, group] of Object.entries(CLEAN_GROUPS)) {
    const marker = group.default ? "default" : "optional";
    console.log(`- ${groupName} (${marker}): ${group.description}`);
    for (const target of group.targets) {
      console.log(`  - ${target}`);
    }
  }
}

function parseArgs(argv) {
  const options = {
    dryRun: false,
    list: false,
    help: false,
    groups: [],
  };

  for (const arg of argv) {
    if (arg === "--dry-run" || arg === "--check") {
      options.dryRun = true;
    } else if (arg === "--list") {
      options.list = true;
    } else if (arg === "--help" || arg === "-h") {
      options.help = true;
    } else {
      options.groups.push(arg);
    }
  }

  return options;
}

function resolveGroupNames(requestedGroups) {
  const rawGroupNames = requestedGroups.length > 0 ? requestedGroups : ["default"];
  const groupNames = [];
  const unknown = [];

  for (const groupName of rawGroupNames) {
    const aliases = GROUP_ALIASES[groupName];
    if (aliases) {
      for (const alias of aliases) {
        if (!groupNames.includes(alias)) {
          groupNames.push(alias);
        }
      }
    } else if (CLEAN_GROUPS[groupName]) {
      if (!groupNames.includes(groupName)) {
        groupNames.push(groupName);
      }
    } else {
      unknown.push(groupName);
    }
  }

  return { groupNames, unknown };
}

function resolveTarget(relativePath) {
  if (path.isAbsolute(relativePath)) {
    throw new Error(`absolute cleanup target is not allowed: ${relativePath}`);
  }

  const fullPath = path.resolve(ROOT, relativePath);
  const relativeToRoot = path.relative(ROOT, fullPath);
  if (relativeToRoot === "" || relativeToRoot.startsWith("..") || path.isAbsolute(relativeToRoot)) {
    throw new Error(`cleanup target escapes repository root: ${relativePath}`);
  }

  return fullPath;
}

function removeWithPowerShell(fullPath) {
  const escapedPath = fullPath.replace(/'/g, "''");
  childProcess.execFileSync(
    "powershell.exe",
    [
      "-NoProfile",
      "-NonInteractive",
      "-ExecutionPolicy",
      "Bypass",
      "-Command",
      `if ((Test-Path -LiteralPath '${escapedPath}' -PathType Container)) { Remove-Item -LiteralPath '${escapedPath}' -Recurse -Force -ErrorAction Stop }`,
    ],
    { stdio: "pipe" },
  );
}

function removeTarget(target, dryRun) {
  const fullPath = resolveTarget(target.relativePath);
  if (!fs.existsSync(fullPath)) {
    return { ...target, removed: false, method: "missing" };
  }

  const stat = fs.lstatSync(fullPath);
  if (!stat.isDirectory()) {
    return { ...target, removed: false, method: "not-directory" };
  }

  if (dryRun) {
    return { ...target, removed: false, method: "dry-run" };
  }

  try {
    fs.rmSync(fullPath, { recursive: true, force: true });
    return { ...target, removed: true, method: "node" };
  } catch (error) {
    if (process.platform === "win32") {
      removeWithPowerShell(fullPath);
      return { ...target, removed: true, method: "powershell" };
    }
    throw error;
  }
}

function collectTargets(groupNames) {
  const targets = [];
  const seen = new Set();

  for (const groupName of groupNames) {
    for (const relativePath of CLEAN_GROUPS[groupName].targets) {
      const key = path.normalize(relativePath);
      if (!seen.has(key)) {
        targets.push({ groupName, relativePath });
        seen.add(key);
      }
    }
  }

  return targets;
}

function printResults(results, failures, dryRun) {
  console.log(dryRun ? "Cleanup dry run:" : "Cleaned installer/runtime artifacts:");

  for (const result of results) {
    if (result.method === "missing") {
      console.log(`- missing ${result.relativePath} (${result.groupName})`);
    } else if (result.method === "not-directory") {
      console.log(`- skipped ${result.relativePath} (${result.groupName}; not a directory)`);
    } else if (result.method === "dry-run") {
      console.log(`- would remove ${result.relativePath} (${result.groupName})`);
    } else if (result.removed) {
      console.log(`- removed ${result.relativePath} (${result.groupName}; ${result.method})`);
    }
  }

  for (const failure of failures) {
    const message = failure.error && failure.error.message ? failure.error.message : String(failure.error);
    console.log(`- failed ${failure.relativePath} (${failure.groupName}): ${message}`);
  }
}

function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    usage();
    return;
  }
  if (options.list) {
    listGroups();
    return;
  }

  const { groupNames, unknown } = resolveGroupNames(options.groups);
  if (unknown.length > 0) {
    console.error(`Unknown cleanup group: ${unknown.join(", ")}`);
    console.error("Run with --list to see available groups.");
    process.exitCode = 1;
    return;
  }

  const results = [];
  const failures = [];
  for (const target of collectTargets(groupNames)) {
    try {
      results.push(removeTarget(target, options.dryRun));
    } catch (error) {
      failures.push({ ...target, error });
    }
  }

  printResults(results, failures, options.dryRun);

  if (failures.length > 0) {
    process.exitCode = 1;
  }
}

main();
