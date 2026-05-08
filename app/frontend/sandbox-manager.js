const { execFile } = require("child_process");
const fs = require("fs");
const https = require("https");
const path = require("path");

const DISTRO_NAME = "OpenCompanion-Sandbox";
const SANDBOX_USER = "companion";
const ROOTFS_URL = "https://cloud-images.ubuntu.com/minimal/releases/jammy/release/ubuntu-22.04-minimal-cloudimg-amd64-root.tar.xz";

function run(cmd, args, opts = {}) {
  return new Promise((resolve, reject) => {
    execFile(cmd, args, { encoding: "utf8", ...opts }, (err, stdout, stderr) => {
      if (err) {
        err.stdout = String(stdout || "");
        err.stderr = String(stderr || "");
        reject(err);
      } else {
        resolve({ stdout: String(stdout || ""), stderr: String(stderr || "") });
      }
    });
  });
}

// wsl -l -v outputs UTF-16LE on Windows; must be read as raw bytes then decoded.
function runWslList(timeoutMs = 10000) {
  return new Promise((resolve, reject) => {
    execFile("wsl", ["-l", "-v"], { encoding: "buffer", timeout: timeoutMs }, (err, stdout) => {
      if (err) {
        reject(err);
      } else {
        resolve(stdout.toString("utf16le"));
      }
    });
  });
}

function downloadFile(url, destPath) {
  return new Promise((resolve, reject) => {
    const file = fs.createWriteStream(destPath);
    https.get(url, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        file.close();
        fs.unlink(destPath, () => {});
        downloadFile(res.headers.location, destPath).then(resolve).catch(reject);
        return;
      }
      if (res.statusCode !== 200) {
        file.close();
        fs.unlink(destPath, () => {});
        reject(new Error(`Download failed with status ${res.statusCode}`));
        return;
      }
      res.pipe(file);
      file.on("finish", () => { file.close(); resolve(); });
    }).on("error", (err) => {
      file.close();
      fs.unlink(destPath, () => {});
      reject(err);
    });
  });
}

class SandboxManager {
  constructor({ appDataRoot, vaultPath }) {
    this.sandboxDir = path.join(appDataRoot, "OpenCompanion", "sandbox");
    this.distroDir = path.join(this.sandboxDir, "distro");
    this.rootfsCachePath = path.join(this.sandboxDir, "rootfs.tar.xz");
    this.statusPath = path.join(appDataRoot, "OpenCompanion", "sandbox-status.json");
    this.vaultPath = vaultPath;
  }

  async isWSLAvailable() {
    try {
      await run("wsl", ["--status"], { timeout: 8000 });
      return true;
    } catch (_err) {
      // Also try --version for older WSL installs
      try {
        await run("wsl", ["--version"], { timeout: 8000 });
        return true;
      } catch (_err2) {
        return false;
      }
    }
  }

  async isSandboxInstalled() {
    try {
      const output = await runWslList(10000);
      return output.includes(DISTRO_NAME);
    } catch (_err) {
      return false;
    }
  }

  async isVaultMounted() {
    try {
      await run("wsl", [
        "-d", DISTRO_NAME,
        "-u", SANDBOX_USER,
        "--", "bash", "-lc", "grep -qs ' /home/companion/vault ' /proc/mounts",
      ], { timeout: 15000 });
      return true;
    } catch (_err) {
      return false;
    }
  }

  async _buildProvisionScript() {
    // Escape backslashes for use inside a bash string
    const vaultWin = this.vaultPath.replace(/\\/g, "\\\\");
    return `#!/bin/bash
set -e

# 1. Create companion user — no password, no sudo by default
useradd -m -s /bin/bash companion 2>/dev/null || true

# 2. sudoers — ONLY apt update and apt install
# NOTE: apt with NOPASSWD has a known privilege escalation vector via Pre-Invoke hooks.
# This is an accepted trade-off for a convenience sandbox, not a security product.
# Documented in companion/vault/SANDBOX_README.md.
cat > /etc/sudoers.d/companion << 'EOF'
companion ALL=(ALL) NOPASSWD: /usr/bin/apt update, /usr/bin/apt install *
EOF
chmod 440 /etc/sudoers.d/companion

# 3. wsl.conf — disable all Windows drive mounts and interop
cat > /etc/wsl.conf << 'EOF'
[automount]
enabled = false
mountFsTab = true

[interop]
enabled = false
appendWindowsPath = false

[user]
default = companion
EOF

# 4. fstab — mount ONLY vault, nothing else
echo "${vaultWin} /home/companion/vault drvfs defaults 0 0" > /etc/fstab

# 5. Create vault mountpoint owned by companion
mkdir -p /home/companion/vault
chown companion:companion /home/companion/vault

# 6. Install system packages
apt update -qq
apt install -y -qq python3 python3-pip python3-venv curl git

# 7. Create Python venv for companion — no sudo needed for pip inside venv
su - companion -c "python3 -m venv /home/companion/env"
`;
  }

  async provisionSandbox() {
    if (!await this.isWSLAvailable()) {
      throw new Error("WSL is not available on this system. Install WSL 2 first.");
    }

    if (await this.isSandboxInstalled()) {
      return { ok: true, message: "Sandbox already installed." };
    }

    // Ensure directories
    fs.mkdirSync(this.sandboxDir, { recursive: true });
    fs.mkdirSync(this.distroDir, { recursive: true });

    // Download rootfs if not cached
    if (!fs.existsSync(this.rootfsCachePath)) {
      await downloadFile(ROOTFS_URL, this.rootfsCachePath);
    }

    // Import distro
    await run("wsl", [
      "--import", DISTRO_NAME,
      this.distroDir,
      this.rootfsCachePath,
    ], { timeout: 120000 });

    // Run provisioning script as root
    const script = await this._buildProvisionScript();
    await run("wsl", [
      "-d", DISTRO_NAME,
      "-u", "root",
      "--", "bash", "-c", script,
    ], { timeout: 300000 });

    // Terminate distro to allow wsl.conf to take effect
    try { await run("wsl", ["--terminate", DISTRO_NAME], { timeout: 15000 }); } catch (_e) {}

    // Wait for termination
    await new Promise((r) => setTimeout(r, 2000));

    // Verify running user is companion
    const { stdout: whoami } = await run("wsl", [
      "-d", DISTRO_NAME,
      "-u", SANDBOX_USER,
      "--", "whoami",
    ], { timeout: 20000 });

    if (whoami.trim() !== SANDBOX_USER) {
      throw new Error(`Provisioning verification failed: expected user '${SANDBOX_USER}', got '${whoami.trim()}'`);
    }

    const vaultMounted = await this.isVaultMounted();
    if (!vaultMounted) {
      throw new Error("Provisioning verification failed: /home/companion/vault is not mounted.");
    }

    // Save status
    this._saveStatus({ installed: true, vaultMounted: true });

    return { ok: true, message: "Sandbox provisioned successfully." };
  }

  async resetSandbox() {
    try {
      await run("wsl", ["--unregister", DISTRO_NAME], { timeout: 30000 });
    } catch (_err) {
      // Distro may not exist — that's fine
    }
    return this.provisionSandbox();
  }

  async verifySandbox() {
    if (!await this.isSandboxInstalled()) {
      return { ok: false, reason: "Distro not installed." };
    }

    try {
      const { stdout: whoami } = await run("wsl", [
        "-d", DISTRO_NAME, "-u", SANDBOX_USER, "--", "whoami",
      ], { timeout: 15000 });

      if (whoami.trim() !== SANDBOX_USER) {
        return { ok: false, reason: `Wrong user: ${whoami.trim()}` };
      }

      const vaultMounted = await this.isVaultMounted();
      if (!vaultMounted) {
        this._saveStatus({ installed: true, vaultMounted: false });
        return { ok: false, reason: "/home/companion/vault is not mounted. Reset the sandbox to refresh the vault path." };
      }

      this._saveStatus({ installed: true, vaultMounted: true });
      return { ok: true };
    } catch (err) {
      return { ok: false, reason: err.message };
    }
  }

  async getStatus() {
    const available = await this.isWSLAvailable();
    const installed = available ? await this.isSandboxInstalled() : false;

    let vaultMounted = false;
    let sizeBytes = 0;

    if (installed) {
      try {
        await run("wsl", [
          "-d", DISTRO_NAME, "-u", SANDBOX_USER, "--", "ls", "/home/companion/vault",
        ], { timeout: 10000 });
        vaultMounted = true;
      } catch (_err) {}

      if (fs.existsSync(this.distroDir)) {
        sizeBytes = _directorySize(this.distroDir);
      }
    }

    return { available, installed, vaultMounted, sizeBytes };
  }

  _saveStatus(patch = {}) {
    let current = {};
    try {
      if (fs.existsSync(this.statusPath)) {
        current = JSON.parse(fs.readFileSync(this.statusPath, "utf8"));
      }
    } catch (_e) {}
    const next = { ...current, sandbox: { ...(current.sandbox || {}), ...patch } };
    fs.mkdirSync(path.dirname(this.statusPath), { recursive: true });
    fs.writeFileSync(this.statusPath, `${JSON.stringify(next, null, 2)}\n`, "utf8");
  }
}

function _directorySize(dirPath) {
  let total = 0;
  try {
    for (const entry of fs.readdirSync(dirPath, { withFileTypes: true })) {
      const child = path.join(dirPath, entry.name);
      if (entry.isDirectory()) {
        total += _directorySize(child);
      } else {
        try { total += fs.statSync(child).size; } catch (_e) {}
      }
    }
  } catch (_e) {}
  return total;
}

module.exports = { SandboxManager };
