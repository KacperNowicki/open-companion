"""
BridgeSession - manages a wrapper.py --json subprocess for testing.
"""

from __future__ import annotations

import json
import os
import site
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PYTHON = sys.executable
WRAPPER = ROOT / "app" / "backend" / "wrapper.py"
USER_SITE = site.getusersitepackages()
LOCAL_DEPS = ROOT / ".pytest-deps"


def _env() -> dict[str, str]:
    env = os.environ.copy()
    existing = [entry for entry in env.get("PYTHONPATH", "").split(os.pathsep) if entry]
    extra = []
    if LOCAL_DEPS.exists():
        extra.append(str(LOCAL_DEPS))
    if USER_SITE:
        extra.append(USER_SITE)
    for entry in reversed(extra):
        if entry not in existing:
            existing.insert(0, entry)
    if existing:
        env["PYTHONPATH"] = os.pathsep.join(existing)
    # Prevent side-effects like Spotify opening during automated test runs.
    env["OC_TEST_MODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


class BridgeSession:
    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.events: list[dict] = []
        self.stderr_lines: list[str] = []
        self.proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._started = False

    @property
    def returncode(self) -> int | None:
        return self.proc.poll() if self.proc else None

    def start(self) -> None:
        if self._started and self.is_alive():
            return
        self.proc = subprocess.Popen(
            [PYTHON, str(WRAPPER), "--json"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(ROOT),
            env=_env(),
        )
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()
        self._started = True

    def mark(self) -> int:
        with self._lock:
            return len(self.events)

    def _pump_stdout(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        for line in self.proc.stdout:
            text = line.strip()
            if not text:
                continue
            try:
                event = json.loads(text)
            except Exception:
                continue
            with self._lock:
                self.events.append(event)

    def _pump_stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        for line in self.proc.stderr:
            with self._lock:
                self.stderr_lines.append(line.rstrip())

    def send(self, obj: dict) -> None:
        if not self._started or not self.proc or self.proc.poll() is not None or not self.proc.stdin:
            raise RuntimeError("Bridge not running")
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def wait_for_event(self, event_type: str, timeout: int | None = None, since: int = 0) -> dict | None:
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            with self._lock:
                for event in self.events[since:]:
                    if event.get("type") == event_type:
                        return event
            if self.proc and self.proc.poll() is not None:
                return None
            time.sleep(0.2)
        return None

    def wait_for_any(self, event_types: list[str], timeout: int | None = None, since: int = 0) -> dict | None:
        deadline = time.time() + (timeout or self.timeout)
        wanted = set(event_types)
        while time.time() < deadline:
            with self._lock:
                for event in self.events[since:]:
                    if event.get("type") in wanted:
                        return event
            if self.proc and self.proc.poll() is not None:
                return None
            time.sleep(0.2)
        return None

    def wait_for_tool(self, tool_name: str, timeout: int | None = None, since: int = 0) -> dict | None:
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            with self._lock:
                for event in self.events[since:]:
                    if event.get("type") == "tool_executed" and event.get("tool_name") == tool_name:
                        return event
            if self.proc and self.proc.poll() is not None:
                return None
            time.sleep(0.2)
        return None

    def events_of_type(self, event_type: str) -> list[dict]:
        with self._lock:
            return [event for event in self.events if event.get("type") == event_type]

    def events_since(self, since: int = 0) -> list[dict]:
        with self._lock:
            return list(self.events[since:])

    def stderr_contains(self, text: str) -> bool:
        with self._lock:
            return any(text in line for line in self.stderr_lines)

    def clear_events(self) -> None:
        with self._lock:
            self.events.clear()

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def shutdown(self) -> None:
        try:
            if self.is_alive():
                self.send({"type": "shutdown"})
                if self.proc and self.proc.stdin and not self.proc.stdin.closed:
                    self.proc.stdin.close()
                assert self.proc is not None
                self.proc.wait(timeout=20)
        except Exception:
            pass
        finally:
            if self.proc and self.proc.poll() is None:
                self.proc.kill()
