const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

function findRcedit(root) {
  const candidates = [
    path.join(root, "node_modules", "electron-winstaller", "vendor", "rcedit.exe"),
    path.join(root, "node_modules", "rcedit", "bin", "rcedit.exe"),
  ];
  return candidates.find((candidate) => fs.existsSync(candidate)) || null;
}

module.exports = async function afterPack(context) {
  if (process.platform !== "win32") {
    return;
  }

  const root = path.resolve(__dirname, "..", "..");
  const rcedit = findRcedit(root);
  if (!rcedit) {
    throw new Error("rcedit.exe was not found; cannot apply Windows executable icon.");
  }

  const iconPath = path.join(root, "app", "frontend", "assets", "brand", "icon.ico");
  const productFilename = context.packager?.appInfo?.productFilename || context.packager?.appInfo?.productName || "OpenCompanion";
  const exePath = path.join(context.appOutDir, `${productFilename}.exe`);

  if (!fs.existsSync(iconPath)) {
    throw new Error(`Windows icon not found: ${iconPath}`);
  }
  if (!fs.existsSync(exePath)) {
    throw new Error(`Packaged executable not found: ${exePath}`);
  }

  const result = spawnSync(rcedit, [exePath, "--set-icon", iconPath], {
    cwd: root,
    stdio: "inherit",
    windowsHide: true,
  });
  if (result.status !== 0) {
    throw new Error(`rcedit failed with exit code ${result.status ?? 1}`);
  }
};
