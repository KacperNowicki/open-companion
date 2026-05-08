const { spawnSync } = require("child_process");
const path = require("path");

module.exports = async function beforePack() {
  const root = path.resolve(__dirname, "..", "..");
  const node = process.execPath;
  const scripts = [
    path.join(root, "app", "scripts", "build-backend.js"),
    path.join(root, "app", "scripts", "generate-node-license-notice.js"),
  ];

  for (const scriptPath of scripts) {
    const result = spawnSync(node, [scriptPath], {
      cwd: root,
      stdio: "inherit",
      windowsHide: true,
    });

    if (result.status !== 0) {
      const code = result.status ?? 1;
      throw new Error(`${path.basename(scriptPath)} failed with exit code ${code}`);
    }
  }
};
