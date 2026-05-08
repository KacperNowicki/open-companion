#!/usr/bin/env node
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");
const LOCKFILE = path.join(ROOT, "package-lock.json");
const OUTPUT = path.join(ROOT, "THIRD_PARTY_NODE_DEPENDENCIES.md");

function readJson(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return null;
  }
}

function packageNameFromLockPath(lockPath) {
  const parts = String(lockPath || "").split("/node_modules/");
  return parts[parts.length - 1] || lockPath;
}

function packageJsonPath(lockPath) {
  return path.join(ROOT, ...String(lockPath || "").split("/"), "package.json");
}

function licenseFromPackageJson(lockPath) {
  const pkg = readJson(packageJsonPath(lockPath));
  if (!pkg) {
    return "";
  }
  if (typeof pkg.license === "string") {
    return pkg.license;
  }
  if (Array.isArray(pkg.licenses)) {
    return pkg.licenses.map((entry) => entry?.type || entry).filter(Boolean).join(" OR ");
  }
  return "";
}

function normalizeLicense(value) {
  return String(value || "").trim().replace(/\s+/g, " ") || "UNKNOWN";
}

function generate() {
  const lock = readJson(LOCKFILE);
  if (!lock?.packages) {
    throw new Error("package-lock.json does not contain a packages map.");
  }

  const rows = Object.entries(lock.packages)
    .filter(([lockPath]) => lockPath.startsWith("node_modules/"))
    .map(([lockPath, meta]) => {
      const name = packageNameFromLockPath(lockPath);
      const version = String(meta.version || "").trim();
      const license = normalizeLicense(meta.license || licenseFromPackageJson(lockPath));
      const resolved = String(meta.resolved || "").trim();
      return { name, version, license, resolved, lockPath };
    })
    .sort((a, b) => a.name.localeCompare(b.name) || a.version.localeCompare(b.version));

  const licenseSet = Array.from(new Set(rows.map((row) => row.license))).sort();
  const lines = [
    "# Third-Party Node Dependency Notices",
    "",
    "Generated from `package-lock.json`. Regenerate with `npm run generate-notices` after dependency changes.",
    "",
    `Package count: ${rows.length}`,
    "",
    "## License Families",
    "",
    ...licenseSet.map((license) => `- ${license}`),
    "",
    "## Packages",
    "",
    "| Package | Lockfile path | Version | License | Resolved |",
    "| --- | --- | --- | --- | --- |",
    ...rows.map((row) => `| \`${row.name}\` | \`${row.lockPath}\` | ${row.version || "UNKNOWN"} | ${row.license} | ${row.resolved || ""} |`),
    "",
  ];

  fs.writeFileSync(OUTPUT, lines.join("\n"), "utf8");
  console.log(`[notices] wrote ${path.relative(ROOT, OUTPUT)} (${rows.length} packages)`);
}

generate();
