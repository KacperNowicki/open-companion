#!/usr/bin/env node

const fs = require("fs");
const https = require("https");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");
const DAY_MS = 24 * 60 * 60 * 1000;
const DEFAULT_MIN_AGE_DAYS = 28;
const USER_AGENT = "open-companion-dependency-age-check/1.0";

function usage() {
  console.log("Usage: node app/scripts/check-dependency-age.js [options]");
  console.log("");
  console.log("Checks package-lock.json and requirements.lock against registry publish dates.");
  console.log("");
  console.log("Options:");
  console.log("  --root <path>          Repository root. Defaults to the current repo.");
  console.log("  --min-age-days <days>  Minimum allowed package version age. Default: 28.");
  console.log("  --now <iso-date>       Override the current time, mainly for tests.");
  console.log("  --npm-only             Check only package-lock.json.");
  console.log("  --pypi-only            Check only requirements.lock.");
  console.log("  --json                 Print machine-readable JSON.");
  console.log("  --help                 Show this help.");
}

function parseArgs(argv) {
  const options = {
    root: ROOT,
    minAgeDays: parsePositiveNumber(
      process.env.OPEN_COMPANION_DEPENDENCY_MIN_AGE_DAYS,
      DEFAULT_MIN_AGE_DAYS,
    ),
    now: new Date(process.env.OPEN_COMPANION_DEPENDENCY_AGE_NOW || Date.now()),
    ecosystems: new Set(["npm", "pypi"]),
    json: false,
    help: false,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--help" || arg === "-h") {
      options.help = true;
    } else if (arg === "--json") {
      options.json = true;
    } else if (arg === "--npm-only") {
      options.ecosystems = new Set(["npm"]);
    } else if (arg === "--pypi-only") {
      options.ecosystems = new Set(["pypi"]);
    } else if (arg === "--root") {
      i += 1;
      options.root = path.resolve(requireValue(argv[i], arg));
    } else if (arg === "--min-age-days") {
      i += 1;
      options.minAgeDays = parsePositiveNumber(requireValue(argv[i], arg), null);
    } else if (arg === "--now") {
      i += 1;
      options.now = new Date(requireValue(argv[i], arg));
    } else {
      throw new Error(`Unknown option: ${arg}`);
    }
  }

  if (!Number.isFinite(options.minAgeDays) || options.minAgeDays <= 0) {
    throw new Error("--min-age-days must be a positive number.");
  }
  if (Number.isNaN(options.now.getTime())) {
    throw new Error("--now must be a valid date.");
  }

  return options;
}

function requireValue(value, flag) {
  if (!value || value.startsWith("--")) {
    throw new Error(`${flag} requires a value.`);
  }
  return value;
}

function parsePositiveNumber(value, fallback) {
  if (value === undefined || value === null || value === "") {
    return fallback;
  }
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`Expected a positive number, got ${value}`);
  }
  return parsed;
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function collectDependencies(root, ecosystems = new Set(["npm", "pypi"])) {
  const dependencies = [];
  const warnings = [];

  if (ecosystems.has("npm")) {
    const result = collectNpmDependencies(root);
    dependencies.push(...result.dependencies);
    warnings.push(...result.warnings);
  }

  if (ecosystems.has("pypi")) {
    const result = collectPypiDependencies(root);
    dependencies.push(...result.dependencies);
    warnings.push(...result.warnings);
  }

  dependencies.sort(compareDependency);
  return { dependencies, warnings };
}

function collectNpmDependencies(root) {
  const lockPath = path.join(root, "package-lock.json");
  if (!fs.existsSync(lockPath)) {
    return { dependencies: [], warnings: [`No package-lock.json found at ${lockPath}`] };
  }

  const lock = readJson(lockPath);
  const packages = lock.packages || {};
  const dependencies = new Map();
  const warnings = [];

  for (const [lockPathKey, details] of Object.entries(packages)) {
    if (!lockPathKey || !details || details.link) {
      continue;
    }
    const version = normalizeVersion(details.version);
    if (!version) {
      continue;
    }
    const name = details.name || npmNameFromPackageLockPath(lockPathKey);
    if (!name) {
      warnings.push(`Could not determine npm package name for lock entry ${lockPathKey}`);
      continue;
    }
    if (isNonRegistryNpmEntry(details)) {
      warnings.push(`Skipped non-registry npm dependency ${name}@${version}`);
      continue;
    }
    const key = dependencyKey("npm", name, version);
    dependencies.set(key, { ecosystem: "npm", name, version, source: "package-lock.json" });
  }

  return { dependencies: Array.from(dependencies.values()), warnings };
}

function npmNameFromPackageLockPath(lockPathKey) {
  const parts = lockPathKey.split("node_modules/");
  const packagePath = parts[parts.length - 1];
  if (!packagePath) {
    return null;
  }
  const segments = packagePath.split(/[\\/]/).filter(Boolean);
  if (segments.length === 0) {
    return null;
  }
  if (segments[0].startsWith("@") && segments.length >= 2) {
    return `${segments[0]}/${segments[1]}`;
  }
  return segments[0];
}

function isNonRegistryNpmEntry(details) {
  const resolved = String(details.resolved || "");
  return (
    resolved.startsWith("file:") ||
    resolved.startsWith("git+") ||
    resolved.startsWith("git:") ||
    resolved.startsWith("github:")
  );
}

function collectPypiDependencies(root) {
  const lockPath = path.join(root, "requirements.lock");
  if (!fs.existsSync(lockPath)) {
    return { dependencies: [], warnings: [`No requirements.lock found at ${lockPath}`] };
  }

  const pins = parseRequirementPins(fs.readFileSync(lockPath, "utf8"));
  const dependencies = new Map();
  for (const pin of pins) {
    const key = dependencyKey("pypi", pin.name, pin.version);
    dependencies.set(key, {
      ecosystem: "pypi",
      name: pin.name,
      version: pin.version,
      source: "requirements.lock",
    });
  }

  return { dependencies: Array.from(dependencies.values()), warnings: [] };
}

function parseRequirementPins(text) {
  const pins = [];
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || trimmed.startsWith("-")) {
      continue;
    }
    const match = trimmed.match(
      /^([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[^\]]+\])?\s*==\s*([A-Za-z0-9][A-Za-z0-9_.!+\-]*)/,
    );
    if (match) {
      pins.push({ name: match[1], version: match[2] });
    }
  }
  return pins;
}

function normalizeVersion(version) {
  if (typeof version !== "string") {
    return null;
  }
  const trimmed = version.trim();
  return trimmed || null;
}

function loadExceptions(root, now) {
  const exceptionsPath = path.join(root, "config", "dependency-age-exceptions.json");
  if (!fs.existsSync(exceptionsPath)) {
    return { exceptions: new Map(), errors: [], warnings: [] };
  }

  const data = readJson(exceptionsPath);
  const entries = Array.isArray(data.exceptions) ? data.exceptions : [];
  const exceptions = new Map();
  const errors = [];
  const warnings = [];

  for (const entry of entries) {
    const ecosystem = String(entry.ecosystem || "").toLowerCase();
    const name = String(entry.name || "");
    const version = String(entry.version || "");
    const reason = String(entry.reason || "");
    const expires = entry.expires ? new Date(entry.expires) : null;

    if (!ecosystem || !name || !version || !reason) {
      errors.push("Dependency age exception is missing ecosystem, name, version, or reason.");
      continue;
    }
    if (expires && Number.isNaN(expires.getTime())) {
      errors.push(`Dependency age exception for ${ecosystem}:${name}@${version} has an invalid expiry.`);
      continue;
    }
    if (expires && expires.getTime() < now.getTime()) {
      warnings.push(
        `Dependency age exception for ${ecosystem}:${name}@${version} expired on ${entry.expires} and is no longer applied.`,
      );
      continue;
    }

    exceptions.set(dependencyKey(ecosystem, name, version), {
      reason,
      expires: entry.expires || null,
    });
  }

  return { exceptions, errors, warnings };
}

async function evaluateDependencyAges({
  dependencies,
  minAgeDays,
  now,
  exceptions = new Map(),
  fetchPublishedAt = createRegistryPublishedAtFetcher(),
  concurrency = 8,
}) {
  const results = {
    checked: [],
    allowedByException: [],
    violations: [],
  };

  await mapLimit(dependencies, concurrency, async (dependency) => {
    const exception = exceptions.get(dependencyKey(dependency.ecosystem, dependency.name, dependency.version));
    if (exception) {
      results.allowedByException.push({ ...dependency, exception });
      return;
    }

    let publishedAt;
    try {
      publishedAt = await fetchPublishedAt(dependency);
    } catch (error) {
      results.violations.push({
        ...dependency,
        reason: error.message,
        publishedAt: null,
        ageDays: null,
      });
      return;
    }

    if (!publishedAt) {
      results.violations.push({
        ...dependency,
        reason: "Registry metadata did not include a publish time for this version.",
        publishedAt: null,
        ageDays: null,
      });
      return;
    }

    const publishedTime = new Date(publishedAt);
    if (Number.isNaN(publishedTime.getTime())) {
      results.violations.push({
        ...dependency,
        reason: `Registry returned an invalid publish time: ${publishedAt}`,
        publishedAt,
        ageDays: null,
      });
      return;
    }

    const ageDays = (now.getTime() - publishedTime.getTime()) / DAY_MS;
    if (ageDays < minAgeDays) {
      results.violations.push({
        ...dependency,
        reason: `Version is ${formatAge(ageDays)} old; minimum is ${minAgeDays} days.`,
        publishedAt: publishedTime.toISOString(),
        ageDays,
      });
      return;
    }

    results.checked.push({
      ...dependency,
      publishedAt: publishedTime.toISOString(),
      ageDays,
    });
  });

  results.checked.sort(compareDependency);
  results.allowedByException.sort(compareDependency);
  results.violations.sort(compareDependency);
  return results;
}

function createRegistryPublishedAtFetcher() {
  const npmCache = new Map();
  const pypiCache = new Map();

  return async function fetchPublishedAt(dependency) {
    if (dependency.ecosystem === "npm") {
      let metadata = npmCache.get(dependency.name);
      if (!metadata) {
        metadata = await fetchJson(`https://registry.npmjs.org/${encodeURIComponent(dependency.name)}`);
        npmCache.set(dependency.name, metadata);
      }
      return metadata.time ? metadata.time[dependency.version] : null;
    }

    if (dependency.ecosystem === "pypi") {
      let metadata = pypiCache.get(dependency.name);
      if (!metadata) {
        metadata = await fetchJson(`https://pypi.org/pypi/${encodeURIComponent(dependency.name)}/json`);
        pypiCache.set(dependency.name, metadata);
      }
      return newestPypiReleaseUploadTime(metadata, dependency.version);
    }

    throw new Error(`Unsupported ecosystem: ${dependency.ecosystem}`);
  };
}

function newestPypiReleaseUploadTime(metadata, version) {
  const releases = metadata && metadata.releases ? metadata.releases[version] : null;
  if (!Array.isArray(releases) || releases.length === 0) {
    return null;
  }
  let newest = null;
  for (const release of releases) {
    const value = release.upload_time_iso_8601 || release.upload_time;
    if (!value) {
      continue;
    }
    const timestamp = new Date(value);
    if (!Number.isNaN(timestamp.getTime()) && (!newest || timestamp > newest)) {
      newest = timestamp;
    }
  }
  return newest ? newest.toISOString() : null;
}

function fetchJson(url, redirectCount = 0) {
  return new Promise((resolve, reject) => {
    const request = https.get(
      url,
      {
        headers: {
          Accept: "application/json",
          "User-Agent": USER_AGENT,
        },
      },
      (response) => {
        const status = response.statusCode || 0;
        const location = response.headers.location;
        if (status >= 300 && status < 400 && location) {
          response.resume();
          if (redirectCount >= 5) {
            reject(new Error(`Too many redirects fetching ${url}`));
            return;
          }
          resolve(fetchJson(new URL(location, url).toString(), redirectCount + 1));
          return;
        }

        let body = "";
        response.setEncoding("utf8");
        response.on("data", (chunk) => {
          body += chunk;
        });
        response.on("end", () => {
          if (status < 200 || status >= 300) {
            reject(new Error(`Registry request failed with HTTP ${status} for ${url}`));
            return;
          }
          try {
            resolve(JSON.parse(body));
          } catch (error) {
            reject(new Error(`Registry returned invalid JSON for ${url}: ${error.message}`));
          }
        });
      },
    );
    request.setTimeout(30000, () => {
      request.destroy(new Error(`Registry request timed out for ${url}`));
    });
    request.on("error", reject);
  });
}

async function mapLimit(items, limit, worker) {
  const queue = items.slice();
  const workers = Array.from({ length: Math.min(limit, queue.length) }, async () => {
    while (queue.length > 0) {
      const item = queue.shift();
      await worker(item);
    }
  });
  await Promise.all(workers);
}

function compareDependency(left, right) {
  return (
    left.ecosystem.localeCompare(right.ecosystem) ||
    left.name.localeCompare(right.name) ||
    left.version.localeCompare(right.version)
  );
}

function dependencyKey(ecosystem, name, version) {
  return `${ecosystem}:${String(name).toLowerCase()}@${version}`;
}

function formatAge(ageDays) {
  if (!Number.isFinite(ageDays)) {
    return "unknown";
  }
  return `${ageDays.toFixed(1)} days`;
}

function countByEcosystem(dependencies) {
  const counts = {};
  for (const dependency of dependencies) {
    counts[dependency.ecosystem] = (counts[dependency.ecosystem] || 0) + 1;
  }
  return counts;
}

function printHumanReport({ dependencies, warnings, exceptionErrors, results, minAgeDays }) {
  const counts = countByEcosystem(dependencies);
  const totalChecked = results.checked.length + results.allowedByException.length;
  console.log(
    `Dependency age policy: checked ${totalChecked}/${dependencies.length} locked package versions ` +
      `(minimum age ${minAgeDays} days).`,
  );
  if (counts.npm) {
    console.log(`- npm: ${counts.npm}`);
  }
  if (counts.pypi) {
    console.log(`- PyPI: ${counts.pypi}`);
  }

  for (const warning of warnings) {
    console.warn(`Warning: ${warning}`);
  }
  for (const error of exceptionErrors) {
    console.error(`Exception error: ${error}`);
  }

  if (results.allowedByException.length > 0) {
    console.log("");
    console.log("Allowed by temporary exception:");
    for (const dependency of results.allowedByException) {
      const expiry = dependency.exception.expires ? `, expires ${dependency.exception.expires}` : "";
      console.log(
        `- ${dependency.ecosystem} ${dependency.name}@${dependency.version}: ` +
          `${dependency.exception.reason}${expiry}`,
      );
    }
  }

  if (results.violations.length > 0 || exceptionErrors.length > 0) {
    console.error("");
    console.error("Dependency age policy failed:");
    for (const violation of results.violations) {
      const published = violation.publishedAt ? ` published ${violation.publishedAt}` : "";
      console.error(
        `- ${violation.ecosystem} ${violation.name}@${violation.version}:${published} ${violation.reason}`,
      );
    }
    return;
  }

  console.log("Dependency age policy passed.");
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    usage();
    return;
  }

  const { dependencies, warnings } = collectDependencies(options.root, options.ecosystems);
  const {
    exceptions,
    errors: exceptionErrors,
    warnings: exceptionWarnings,
  } = loadExceptions(options.root, options.now);
  const reportWarnings = warnings.concat(exceptionWarnings);
  const results = await evaluateDependencyAges({
    dependencies,
    minAgeDays: options.minAgeDays,
    now: options.now,
    exceptions,
  });

  if (options.json) {
    console.log(
      JSON.stringify(
        {
          minAgeDays: options.minAgeDays,
          now: options.now.toISOString(),
          dependencyCount: dependencies.length,
          warnings: reportWarnings,
          exceptionErrors,
          results,
        },
        null,
        2,
      ),
    );
  } else {
    printHumanReport({
      dependencies,
      warnings: reportWarnings,
      exceptionErrors,
      results,
      minAgeDays: options.minAgeDays,
    });
  }

  if (exceptionErrors.length > 0 || results.violations.length > 0) {
    process.exitCode = 1;
  }
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}

module.exports = {
  collectDependencies,
  collectNpmDependencies,
  collectPypiDependencies,
  createRegistryPublishedAtFetcher,
  evaluateDependencyAges,
  newestPypiReleaseUploadTime,
  npmNameFromPackageLockPath,
  parseArgs,
  parseRequirementPins,
};
