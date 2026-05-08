from __future__ import annotations

import datetime
import getpass
import platform
import shutil
import subprocess
from pathlib import Path

try:
    import psutil  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    psutil = None

def _clean_subprocess_output(data: str | bytes | None) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace").replace("\ufeff", "")
    return str(data).replace("\ufeff", "")


def get_current_time() -> str:
    now = datetime.datetime.now()
    return now.strftime("%A, %B %d, %Y at %I:%M %p")


def get_system_info() -> str:
    lines = [
        f"OS: {platform.system()} {platform.release()} ({platform.version()})",
        f"Machine: {platform.node()}",
        f"User: {getpass.getuser()}",
        f"Architecture: {platform.machine()}",
        f"Processor: {platform.processor()}",
    ]
    return "\n".join(lines)


def _run_hidden(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def run_windows_terminal(command: str, timeout_seconds: int = 30, shell: str = "powershell") -> str:
    clean_command = str(command or "").strip()
    if not clean_command:
        return "Windows command is required."

    try:
        timeout = max(1, min(120, int(timeout_seconds or 30)))
    except Exception:
        timeout = 30

    shell_name = str(shell or "powershell").strip().lower()
    if shell_name == "cmd":
        args = ["cmd", "/c", clean_command]
    else:
        args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", clean_command]

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        stdout = _clean_subprocess_output(result.stdout)
        stderr = _clean_subprocess_output(result.stderr)
        parts = []
        if stdout.strip():
            parts.append(stdout.strip())
        if stderr.strip():
            parts.append(f"[stderr]: {stderr.strip()}")
        if not parts:
            parts.append(f"Done (exit code {result.returncode})")
        return "\n".join(parts)
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout} seconds."
    except FileNotFoundError:
        return "Error: Windows shell executable not found."
    except Exception as exc:
        return f"Error: {exc}"


def _send_media_key(key_code: int, repeat: int = 1) -> None:
    command = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        + " ".join(f"[System.Windows.Forms.SendKeys]::SendWait([char]{key_code});" for _ in range(max(1, repeat)))
    )
    _run_hidden(command)


def set_volume(level: int) -> str:
    try:
        value = max(0, min(100, int(level)))
    except Exception:
        return "Volume level must be an integer between 0 and 100."

    nircmd = shutil.which("nircmd.exe") or shutil.which("nircmd")
    if nircmd:
        scalar = int((value / 100) * 65535)
        subprocess.run(
            [nircmd, "setsysvolume", str(scalar)],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        return f"Set system volume to {value}%."

    _send_media_key(174, 50)
    if value > 0:
        _send_media_key(175, max(1, round(value / 2)))
    return f"Adjusted system volume to approximately {value}%."


def mute_volume() -> str:
    nircmd = shutil.which("nircmd.exe") or shutil.which("nircmd")
    if nircmd:
        subprocess.run(
            [nircmd, "mutesysvolume", "2"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        return "Toggled system mute."

    _send_media_key(173, 1)
    return "Toggled system mute."


def open_application(name: str) -> str:
    app_name = str(name or "").strip()
    if not app_name:
        return "Application name is required."

    try:
        subprocess.Popen(
            ["cmd", "/c", "start", "", app_name],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return f"Opened: {app_name}"
    except Exception as exc:
        return f"Failed to open {app_name}: {exc}"


def close_application(name: str) -> str:
    app_name = str(name or "").strip()
    if not app_name:
        return "Application name is required."

    image_name = app_name if app_name.lower().endswith(".exe") else f"{app_name}.exe"
    result = subprocess.run(
        ["taskkill", "/IM", image_name, "/F"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    output = (result.stdout or result.stderr or "").strip()
    if result.returncode == 0:
        return output or f"Closed {image_name}."
    return output or f"Failed to close {image_name}."


def _power_command(args: list[str], label: str) -> str:
    subprocess.Popen(args, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return label


def shutdown_pc() -> str:
    return _power_command(["shutdown", "/s", "/t", "0"], "Shutting down the PC.")


def restart_pc() -> str:
    return _power_command(["shutdown", "/r", "/t", "0"], "Restarting the PC.")


def sleep_pc() -> str:
    _run_hidden("Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Application]::SetSuspendState('Suspend',$false,$false)")
    return "Putting the PC to sleep."


def hibernate_pc() -> str:
    _run_hidden("shutdown /h")
    return "Hibernating the PC."


def copy_to_clipboard(text: str) -> str:
    try:
        process = subprocess.Popen(["clip"], stdin=subprocess.PIPE)
        process.communicate(str(text or "").encode("utf-16-le"))
        return "Text copied to clipboard."
    except Exception as exc:
        return f"Failed to copy to clipboard: {exc}"


def read_clipboard() -> str:
    result = _run_hidden("Get-Clipboard")
    output = (result.stdout or result.stderr or "").strip()
    return output or "Clipboard is empty."


def get_cpu_usage() -> str:
    if psutil is None:
        return "psutil is not installed, so CPU usage is unavailable."
    return f"Current CPU usage: {psutil.cpu_percent(interval=0.5):.1f}%"


def get_ram_usage() -> str:
    if psutil is None:
        return "psutil is not installed, so RAM usage is unavailable."
    mem = psutil.virtual_memory()
    used_gb = mem.used / (1024 ** 3)
    total_gb = mem.total / (1024 ** 3)
    return f"RAM usage: {used_gb:.1f} GB / {total_gb:.1f} GB ({mem.percent:.1f}%)"


def get_disk_space() -> str:
    if psutil is None:
        return "psutil is not installed, so disk usage is unavailable."

    lines = []
    seen = set()
    for partition in psutil.disk_partitions(all=False):
        mountpoint = partition.mountpoint
        if mountpoint in seen:
            continue
        seen.add(mountpoint)
        try:
            usage = psutil.disk_usage(mountpoint)
        except Exception:
            continue
        used_gb = usage.used / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        lines.append(f"{mountpoint}: {used_gb:.1f} GB / {total_gb:.1f} GB used ({usage.percent:.1f}%)")
    return "\n".join(lines) if lines else "No disk information available."


def get_battery() -> str:
    if psutil is None or not hasattr(psutil, "sensors_battery"):
        return "Battery information is unavailable on this machine."
    battery = psutil.sensors_battery()
    if battery is None:
        return "No battery detected."
    status = "charging" if battery.power_plugged else "on battery"
    return f"Battery: {battery.percent:.0f}% ({status})"


def get_running_processes() -> str:
    if psutil is None:
        result = subprocess.run(
            ["tasklist"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        return (result.stdout or result.stderr or "").strip()[:2000]

    processes = []
    for proc in psutil.process_iter(["name", "cpu_percent"]):
        try:
            info = proc.info
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        processes.append((float(info.get("cpu_percent") or 0.0), str(info.get("name") or "unknown")))

    top = sorted(processes, key=lambda item: item[0], reverse=True)[:20]
    lines = [f"- {name}: {cpu:.1f}% CPU" for cpu, name in top]
    return "Top running processes:\n" + "\n".join(lines)


def move_companion_window(
    position: str = "center",
    display: str = "current",
    x: int | None = None,
    y: int | None = None,
    dx: int | None = None,
    dy: int | None = None,
) -> str:
    display_text = str(display or "current").replace("_", " ")
    if x is not None or y is not None:
        return f"Companion window moved to absolute position x={x}, y={y} on {display_text} display."
    if dx is not None or dy is not None:
        return f"Companion window moved by dx={dx or 0}, dy={dy or 0} on {display_text} display."
    return f"Companion window moved to {str(position or 'center').replace('_', ' ')} on {display_text} display."


def set_timer(minutes: int | None = None, seconds: int | None = None, label: str = "Timer") -> str:
    duration_seconds = 0
    if minutes is not None:
        duration_seconds += max(0, int(minutes)) * 60
    if seconds is not None:
        duration_seconds += max(0, int(seconds))
    if duration_seconds <= 0:
        return "Timer duration must be greater than zero."

    title = str(label or "Timer").replace("'", "''")
    command = (
        f"Start-Sleep -Seconds {duration_seconds}; "
        "Add-Type -AssemblyName PresentationFramework; "
        f"[System.Windows.MessageBox]::Show('{title} finished.','OpenCompanion Timer') | Out-Null"
    )
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-Command", command],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return f"Set a timer for {duration_seconds} second(s)."
