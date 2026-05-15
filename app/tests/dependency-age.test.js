const assert = require("assert");
const fs = require("fs");
const path = require("path");

const {
  collectNpmDependencies,
  collectPypiDependencies,
  evaluateDependencyAges,
  newestPypiReleaseUploadTime,
  npmNameFromPackageLockPath,
  parseRequirementPins,
} = require("../scripts/check-dependency-age");

const ROOT = path.resolve(__dirname, "..", "..");
const TEST_RESULTS_ROOT = path.join(ROOT, "app", "tests", "test-results");
fs.mkdirSync(TEST_RESULTS_ROOT, { recursive: true });

async function run() {
  assert.strictEqual(npmNameFromPackageLockPath("node_modules/ws"), "ws");
  assert.strictEqual(npmNameFromPackageLockPath("node_modules/@scope/pkg"), "@scope/pkg");
  assert.strictEqual(npmNameFromPackageLockPath("node_modules/a/node_modules/b"), "b");

  const pins = parseRequirementPins(`
    # generated lock
    anyio==4.13.0 \\
        --hash=sha256:abc
    sample-pkg[extra]==1.2.3 ; python_version >= "3.11"
    unpinned>=1
  `);
  assert.deepStrictEqual(pins, [
    { name: "anyio", version: "4.13.0" },
    { name: "sample-pkg", version: "1.2.3" },
  ]);

  const tempRoot = fs.mkdtempSync(path.join(TEST_RESULTS_ROOT, "oc-dependency-age-"));
  fs.writeFileSync(
    path.join(tempRoot, "package-lock.json"),
    JSON.stringify(
      {
        lockfileVersion: 3,
        packages: {
          "": { name: "fixture", version: "1.0.0" },
          "node_modules/ws": {
            version: "8.20.0",
            resolved: "https://registry.npmjs.org/ws/-/ws-8.20.0.tgz",
          },
          "node_modules/@scope/pkg": {
            version: "1.0.0",
            resolved: "https://registry.npmjs.org/@scope/pkg/-/pkg-1.0.0.tgz",
          },
          "node_modules/local-only": {
            version: "1.0.0",
            resolved: "file:../local-only",
          },
        },
      },
      null,
      2,
    ),
  );
  fs.writeFileSync(
    path.join(tempRoot, "requirements.lock"),
    "requests==2.32.5 \\\n    --hash=sha256:abc\n",
  );

  const npmResult = collectNpmDependencies(tempRoot);
  assert.deepStrictEqual(
    npmResult.dependencies.map((dependency) => `${dependency.name}@${dependency.version}`).sort(),
    ["@scope/pkg@1.0.0", "ws@8.20.0"],
  );
  assert.ok(
    npmResult.warnings.some((warning) => warning.includes("Skipped non-registry npm dependency")),
    "non-registry npm entries should be reported",
  );

  const pypiResult = collectPypiDependencies(tempRoot);
  assert.deepStrictEqual(pypiResult.dependencies, [
    {
      ecosystem: "pypi",
      name: "requests",
      version: "2.32.5",
      source: "requirements.lock",
    },
  ]);

  const newestUpload = newestPypiReleaseUploadTime(
    {
      releases: {
        "1.0.0": [
          { upload_time_iso_8601: "2026-01-01T00:00:00.000Z" },
          { upload_time_iso_8601: "2026-01-03T00:00:00.000Z" },
        ],
      },
    },
    "1.0.0",
  );
  assert.strictEqual(newestUpload, "2026-01-03T00:00:00.000Z");

  const now = new Date("2026-05-15T00:00:00.000Z");
  const evaluation = await evaluateDependencyAges({
    dependencies: [
      { ecosystem: "npm", name: "old", version: "1.0.0" },
      { ecosystem: "npm", name: "fresh", version: "1.0.0" },
      { ecosystem: "pypi", name: "missing", version: "1.0.0" },
      { ecosystem: "pypi", name: "excepted", version: "1.0.0" },
    ],
    minAgeDays: 28,
    now,
    concurrency: 2,
    exceptions: new Map([
      ["pypi:excepted@1.0.0", { reason: "fixture exception", expires: "2026-06-01" }],
    ]),
    fetchPublishedAt: async (dependency) => {
      const dates = {
        "npm:old@1.0.0": "2026-03-01T00:00:00.000Z",
        "npm:fresh@1.0.0": "2026-05-01T00:00:00.000Z",
      };
      return dates[`${dependency.ecosystem}:${dependency.name}@${dependency.version}`] || null;
    },
  });

  assert.deepStrictEqual(
    evaluation.checked.map((dependency) => dependency.name),
    ["old"],
  );
  assert.deepStrictEqual(
    evaluation.allowedByException.map((dependency) => dependency.name),
    ["excepted"],
  );
  assert.deepStrictEqual(
    evaluation.violations.map((dependency) => dependency.name).sort(),
    ["fresh", "missing"],
  );
}

run()
  .then(() => {
    console.log("Dependency age tests passed.");
  })
  .catch((error) => {
    console.error(error.stack || error.message);
    process.exitCode = 1;
  });
