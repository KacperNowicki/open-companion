from __future__ import annotations

import shutil
import subprocess
import shlex

DISTRO_NAME = "OpenCompanion-Sandbox"
SANDBOX_USER = "companion"
VAULT_PATH = "/home/companion/vault"
PYTHON_BIN = "/home/companion/env/bin/python3"
PIP_BIN = "/home/companion/env/bin/pip"
MAX_TIMEOUT = 120  # upper bound; package installs can take up to 2 minutes
DEFAULT_TIMEOUT = 10
MAX_OUTPUT_CHARS = 12000
FORBIDDEN_PATH_PREFIXES = ("/mnt", "/etc", "/usr", "/bin", "/sbin", "/var", "/root", "/home/companion/..")
_LAST_COMMAND: str = ""


def _clean_wsl_output(data: str | bytes | None) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace").replace("\ufeff", "")
    return str(data).replace("\ufeff", "")


def _decode_wsl_probe_output(data: bytes | None) -> str:
    raw = data or b""
    if not raw:
        return ""
    encodings = ["utf-16-le", "utf-16", "utf-8-sig", "utf-8", "cp1250", "cp1252", "cp850", "cp437"]
    for encoding in encodings:
        try:
            return raw.decode(encoding).replace("\ufeff", "")
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace").replace("\ufeff", "")


def _probe_sandbox() -> tuple[bool, str]:
    result = subprocess.run(
        ["wsl", "-l", "-v"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    stdout = _clean_wsl_output(result.stdout)
    stderr = _clean_wsl_output(result.stderr)
    combined = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part).strip()
    if DISTRO_NAME not in combined:
        retry = subprocess.run(
            ["wsl", "-l", "-v"],
            capture_output=True,
            timeout=10,
        )
        retry_stdout = _decode_wsl_probe_output(retry.stdout)
        retry_stderr = _decode_wsl_probe_output(retry.stderr)
        retry_combined = "\n".join(part for part in (retry_stdout.strip(), retry_stderr.strip()) if part).strip()
        if retry_combined:
            result = retry
            combined = retry_combined
    if result.returncode != 0:
        return False, combined or f"WSL returned exit code {result.returncode}."
    return DISTRO_NAME in combined, combined


def _probe_vault_mount() -> tuple[bool, str]:
    result = subprocess.run(
        [
            "wsl", "-d", DISTRO_NAME,
            "-u", SANDBOX_USER,
            "--", "bash", "-lc", f"grep -qs ' {VAULT_PATH} ' /proc/mounts",
        ],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    stdout = _clean_wsl_output(result.stdout)
    stderr = _clean_wsl_output(result.stderr)
    combined = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part).strip()
    if result.returncode == 0:
        return True, combined
    if result.returncode == 1:
        return False, combined
    return False, combined or f"mount probe exited with code {result.returncode}."


def _truncate_output(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + "\n[Truncated: output size cap reached]"


def _looks_like_forbidden_command(command: str) -> str:
    raw = str(command or "")
    if "\x00" in raw:
        return "Command contains a NUL byte."
    lowered = raw.lower()
    if " --daemon" in lowered or " nohup " in lowered or " &" in lowered or "disown" in lowered:
        return "Persistent/background commands are not supported in the sandbox tool."
    try:
        tokens = shlex.split(raw, posix=True)
    except ValueError as exc:
        return f"Command could not be parsed safely: {exc}"
    for token in tokens:
        if token in {"..", "../"} or token.startswith("../") or "/../" in token:
            return f"Command attempts parent-directory traversal: {token}"
        if token.startswith("/"):
            if token == VAULT_PATH or token.startswith(VAULT_PATH + "/"):
                continue
            if token.startswith(FORBIDDEN_PATH_PREFIXES) or not token.startswith(VAULT_PATH + "/"):
                return f"Command attempts to access outside the sandbox root: {token}"
    return ""


def run_terminal(command: str, timeout_seconds: int = DEFAULT_TIMEOUT) -> str:
    """Run a shell command in the companion's isolated WSL sandbox."""
    command = str(command or "").strip()
    if not command:
        return "Error: command is required."
    forbidden = _looks_like_forbidden_command(command)
    if forbidden:
        return f"Error: {forbidden}"
    global _LAST_COMMAND
    if command == _LAST_COMMAND:
        return "Error: Duplicate sandbox command blocked for this process."
    _LAST_COMMAND = command

    if not shutil.which("wsl"):
        return "Error: WSL is not available on this system."

    try:
        installed, details = _probe_sandbox()
    except Exception as exc:  # noqa: BLE001
        return f"Error: Failed to inspect WSL sandbox: {exc}"

    if not installed:
        if details:
            return f"Error: WSL sandbox unavailable: {details}"
        return (
            "My sandbox isn't set up yet. "
            "Ask me to set up the sandbox first, or check Settings -> Sandbox."
        )

    try:
        mounted, mount_details = _probe_vault_mount()
    except Exception as exc:  # noqa: BLE001
        return f"Error: Failed to verify WSL sandbox vault mount: {exc}"

    if not mounted:
        details_text = f" {mount_details}" if mount_details else ""
        return (
            f"Error: WSL sandbox unavailable: {VAULT_PATH} is not mounted."
            f" Reset the sandbox from Settings -> Sandbox to refresh the vault mount.{details_text}"
        )

    timeout = min(int(timeout_seconds or DEFAULT_TIMEOUT), MAX_TIMEOUT)

    full_command = (
        "export LANG=C.UTF-8 LC_ALL=C.UTF-8 >/dev/null 2>&1; "
        f"cd {VAULT_PATH} && "
        "root=$(realpath -e .) && [ \"$root\" = \"{vault}\" ] && "
        "{cmd}"
    ).format(vault=VAULT_PATH, cmd=command)

    try:
        result = subprocess.run(
            [
                "wsl", "-d", DISTRO_NAME,
                "-u", SANDBOX_USER,
                "--", "bash", "-c", full_command,
            ],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        stdout = _clean_wsl_output(result.stdout)
        stderr = _clean_wsl_output(result.stderr)
        parts = []
        if stdout.strip():
            parts.append(stdout.strip())
        if stderr.strip():
            parts.append(f"[stderr]: {stderr.strip()}")
        if not parts:
            parts.append(f"Done (exit code {result.returncode})")
        return _truncate_output("\n".join(parts))

    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout} seconds."
    except FileNotFoundError:
        return "Error: WSL executable not found."
    except Exception as exc:  # noqa: BLE001
        return f"Error: {exc}"


def check_sandbox_status() -> dict:
    """Check if sandbox distro is installed and the vault mount is accessible."""
    if not shutil.which("wsl"):
        return {"available": False, "installed": False, "vault_mounted": False, "reason": "WSL not found"}
    try:
        installed, details = _probe_sandbox()
        payload = {"available": True, "installed": installed, "vault_mounted": False}
        if details:
            payload["details"] = details
        if installed:
            mounted, mount_details = _probe_vault_mount()
            payload["vault_mounted"] = mounted
            if mount_details:
                payload["mount_details"] = mount_details
            if not mounted:
                payload["reason"] = f"{VAULT_PATH} is not mounted"
        return payload
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "installed": False, "vault_mounted": False, "reason": str(exc)}
