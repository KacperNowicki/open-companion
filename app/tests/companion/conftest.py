#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import site
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND_WRAPPER = ROOT / "app" / "backend" / "wrapper.py"
USER_SITE = site.getusersitepackages()
OLLAMA_URL = "http://localhost:11434"
TEMP_PROFILE_ROOT = ROOT / "app" / "tests" / "companion" / ".tmp-profiles"
DEFAULT_TIMEOUT = 120

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"
BOLD = "\033[1m"

passed: list[str] = []
failed: list[str] = []
skipped: list[str] = []


class SuiteSkip(RuntimeError):
    pass


def ok(name: str) -> None:
    passed.append(name)
    print(f"{GREEN}[PASS]{RESET} {name}")


def fail(name: str, reason: str = "") -> None:
    failed.append(name)
    print(f"{RED}[FAIL]{RESET} {name}")
    if reason:
        print(f"  {RED}{reason}{RESET}")


def skip(name: str, reason: str = "") -> None:
    skipped.append(name)
    print(f"{YELLOW}[SKIP]{RESET} {name} (skipped: {reason})")


def section(title: str) -> None:
    print(f"\n{BOLD}-- {title} --{RESET}")


def summary() -> bool:
    print(f"\n{BOLD}{'=' * 40}{RESET}")
    print(f"{GREEN}{len(passed)} passed{RESET}  {RED}{len(failed)} failed{RESET}  {YELLOW}{len(skipped)} skipped{RESET}")
    if failed:
        print(f"\n{RED}Failed:{RESET}")
        for name in failed:
            print(f"  - {name}")
    print()
    return not failed


def run_test(name: str, fn) -> None:
    t0 = time.monotonic()
    try:
        fn()
        elapsed = time.monotonic() - t0
        ok(f"{name}  ({elapsed:.1f}s)")
    except SuiteSkip as exc:
        elapsed = time.monotonic() - t0
        skip(f"{name}  ({elapsed:.1f}s)", str(exc))
    except AssertionError as exc:
        elapsed = time.monotonic() - t0
        fail(f"{name}  ({elapsed:.1f}s)", str(exc))
    except Exception as exc:
        elapsed = time.monotonic() - t0
        fail(f"{name}  ({elapsed:.1f}s)", f"{type(exc).__name__}: {exc}")


def _deep_merge(base, override):
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = _deep_merge(merged.get(key), value)
        return merged
    if isinstance(override, list):
        return list(override)
    return override


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _subprocess_env(profile_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["OPEN_COMPANION_TEST_MODE"] = "1"
    env["OPEN_COMPANION_TEST_PROFILE_DIR"] = str(profile_root)
    env["OPEN_COMPANION_KEYCHAIN_SERVICE"] = "open-companion"
    local_deps = ROOT / ".pytest-deps"
    bundled_deps = ROOT / ".python-deps"
    pythonpath_parts = [entry for entry in env.get("PYTHONPATH", "").split(os.pathsep) if entry]
    if local_deps.exists() and str(local_deps) not in pythonpath_parts:
        pythonpath_parts.insert(0, str(local_deps))
    if bundled_deps.exists() and str(bundled_deps) not in pythonpath_parts:
        pythonpath_parts.insert(0, str(bundled_deps))
    if USER_SITE and USER_SITE not in pythonpath_parts:
        pythonpath_parts.insert(0, USER_SITE)
    if pythonpath_parts:
        env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
        env["PYTHONIOENCODING"] = "utf-8"
    return env


def _model_matches(candidate: str, available: set[str]) -> bool:
    clean = str(candidate or "").strip().lower()
    if not clean:
        return False
    if clean in available:
        return True
    return any(name.startswith(clean) or clean.startswith(name) for name in available)


def _is_embedding_only_model(name: str) -> bool:
    clean = str(name or "").strip().lower()
    return not clean or "embed" in clean or clean.startswith("nomic-embed")


def _ollama_models(timeout: int = 5) -> list[str]:
    request = urllib.request.Request(f"{OLLAMA_URL}/api/tags", method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8") or "{}")
    return [str(item.get("name") or "").strip() for item in data.get("models", []) if str(item.get("name") or "").strip()]


def resolve_chat_model(preferred: str | None = None) -> str:
    try:
        models = _ollama_models(timeout=6)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SuiteSkip(f"Ollama is not reachable at {OLLAMA_URL}: {exc}") from exc
    if not models:
        raise SuiteSkip("Ollama is reachable, but no models are installed.")

    available = {model.lower() for model in models}
    candidates = [preferred, "gemma4:26b", "gemma4:e4b", "qwen3:8b", "qwen2.5:14b", "qwen2.5:7b", "qwen2.5:3b", "llama3.1:8b", "llama3.2:3b"]
    for candidate in candidates:
        if candidate and _model_matches(candidate, available):
            return candidate
    for model in models:
        if not _is_embedding_only_model(model):
            return model
    raise SuiteSkip(f"No chat-capable Ollama model found. Installed models: {', '.join(models)}")


def skip_if_ollama_down() -> str:
    return resolve_chat_model()


def build_profile_config(config_patch: dict | None = None, chat_model: str | None = None) -> dict:
    config = _deep_merge({}, _read_json(ROOT / "config.json"))
    if config_patch:
        config = _deep_merge(config, config_patch)

    brain = config.setdefault("brain", {})
    requested_provider = str(brain.get("provider") or "ollama").strip().lower() or "ollama"
    preferred = chat_model or str(brain.get("model") or "").strip() or None
    resolved_model = resolve_chat_model(preferred) if requested_provider == "ollama" else str(preferred or "").strip()

    if requested_provider == "ollama":
        brain["provider"] = "ollama"
        brain["base_url"] = "http://localhost:11434/v1"
        brain["api_url"] = "http://localhost:11434/v1"
        brain["model"] = resolved_model
    elif requested_provider == "openai":
        brain["provider"] = "openai"
        brain["base_url"] = "https://api.openai.com/v1"
        brain["api_url"] = "https://api.openai.com/v1"
        if resolved_model:
            brain["model"] = resolved_model
    try:
        brain["max_tokens"] = max(int(brain.get("max_tokens", 1024) or 1024), 512)
    except (TypeError, ValueError):
        brain["max_tokens"] = 1024

    layers = brain.setdefault("layers", {})
    assistant_layer = layers.setdefault("assistant", {})
    env_openai_key = (
        os.environ.get("OPEN_COMPANION_ASSISTANT_OPENAI_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    ).strip()
    if str(assistant_layer.get("provider") or "").strip().lower() == "openai" and env_openai_key:
        assistant_layer["api_key"] = env_openai_key

    memory = config.setdefault("memory", {})
    memory["enabled"] = bool(memory.get("enabled", True))
    memory["write_back_enabled"] = bool(memory.get("write_back_enabled", True))
    memory["embedding_enabled"] = bool(memory.get("embedding_enabled", False))
    if requested_provider == "ollama":
        memory["extraction_model"] = resolved_model

    heartbeat = config.setdefault("heartbeat", {})
    heartbeat.setdefault("enabled", False)

    voice = config.setdefault("voice", {})
    voice["tts_enabled"] = False
    voice["stt_enabled"] = False


    companion = config.setdefault("companion", {})
    companion["user_name"] = ""
    soul = companion.setdefault("soul", {})
    soul["user_name"] = ""

    return config


def _prepare_profile(profile_root: Path, config: dict, wipe: bool = False) -> None:
    if wipe and profile_root.exists():
        shutil.rmtree(profile_root, ignore_errors=True)
    profile_root.mkdir(parents=True, exist_ok=True)
    if profile_root.resolve() == ROOT.resolve() and not wipe:
        _write_json(profile_root / "config.local.json", config)
    else:
        _write_json(profile_root / "config.json", config)
    (profile_root / "companion" / "memory").mkdir(parents=True, exist_ok=True)
    (profile_root / "companion" / "soul" / "active").mkdir(parents=True, exist_ok=True)


def wait_until(predicate, timeout: int = DEFAULT_TIMEOUT, interval: float = 0.25) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class Companion:
    def __init__(self, profile_root: Path, config_patch: dict | None = None, timeout: int = DEFAULT_TIMEOUT, wipe_on_start: bool = True):
        self.profile_root = profile_root.resolve()
        self.timeout = timeout
        self._config_patch = _deep_merge({}, config_patch or {})
        self._base_config = build_profile_config(self._config_patch)
        self._wipe_on_start = wipe_on_start
        self._events: list[dict] = []
        self._stderr_lines: list[str] = []
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._started = False
        self._last_turn_events: list[dict] = []
        self._prepare_and_start(wipe=self._wipe_on_start)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    @property
    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def returncode(self) -> int | None:
        return self._proc.poll() if self._proc else None

    def _prepare_and_start(self, wipe: bool = False) -> None:
        _prepare_profile(self.profile_root, self._base_config, wipe=wipe)
        if not wipe:
            _write_json(self.profile_root / "config.local.json", self._base_config)
        self.start()

    def start(self) -> None:
        if self._started and self.is_alive:
            return
        self._proc = subprocess.Popen(
            [sys.executable, str(BACKEND_WRAPPER), "--json"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(ROOT),
            env=_subprocess_env(self.profile_root),
        )
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()
        self._started = True
        ready = self.wait_for_event("ready", timeout=min(self.timeout, 90))
        if ready is None:
            reason = self._stderr_tail() or f"wrapper exited with code {self.returncode}"
            self.close()
            raise SuiteSkip(reason or "wrapper did not become ready")

    def _pump_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except Exception:
                continue
            with self._lock:
                self._events.append(payload)

    def _pump_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        for line in self._proc.stderr:
            with self._lock:
                self._stderr_lines.append(line.rstrip())

    def _stderr_tail(self, count: int = 12) -> str:
        with self._lock:
            lines = [line for line in self._stderr_lines if line.strip()]
        return "\n".join(lines[-count:]) if lines else ""

    def mark(self) -> int:
        with self._lock:
            return len(self._events)

    def mark_stderr(self) -> int:
        with self._lock:
            return len(self._stderr_lines)

    def events_since(self, index: int = 0) -> list[dict]:
        with self._lock:
            return list(self._events[index:])

    def stderr_since(self, index: int = 0) -> list[str]:
        with self._lock:
            return list(self._stderr_lines[index:])

    def send(self, payload: dict) -> None:
        if not self.is_alive or not self._proc or not self._proc.stdin:
            raise RuntimeError("Companion bridge is not running")
        self._proc.stdin.write(json.dumps(payload) + "\n")
        self._proc.stdin.flush()

    def wait_for_event(self, event_type: str, timeout: int | None = None, since: int = 0) -> dict | None:
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            for event in self.events_since(since):
                if event.get("type") == event_type:
                    return event
            if self._proc and self._proc.poll() is not None:
                return None
            time.sleep(0.2)
        return None

    def wait_for_any(self, event_types: list[str], timeout: int | None = None, since: int = 0) -> dict | None:
        wanted = set(event_types)
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            for event in self.events_since(since):
                if event.get("type") in wanted:
                    return event
            if self._proc and self._proc.poll() is not None:
                return None
            time.sleep(0.2)
        return None

    def _run_payload(self, payload: dict, wait_for: list[str], timeout: int | None = None) -> tuple[dict | None, list[dict]]:
        start = self.mark()
        self.send(payload)
        outcome = self.wait_for_any(wait_for, timeout=timeout, since=start)
        self._last_turn_events = self.events_since(start)
        if outcome is None:
            raise SuiteSkip(self._stderr_tail() or "companion turn timed out")
        error_event = next((event for event in self._last_turn_events if event.get("type") == "error"), None)
        if error_event:
            raise RuntimeError(str(error_event.get("message") or "companion returned an error"))
        return outcome, self._last_turn_events

    def say(self, message: str, timeout: int | None = None) -> str:
        _, events = self._run_payload({"type": "user_message", "content": str(message or "")}, ["idle", "error"], timeout=timeout)
        assistant_events = [event for event in events if event.get("type") == "assistant_message"]
        return str(assistant_events[-1].get("content") or "") if assistant_events else ""

    def start_user_turn(self, message: str, timeout: int | None = None) -> list[dict]:
        _, events = self._run_payload({"type": "user_message", "content": str(message or "")}, ["tool_confirmation_requested", "idle", "error"], timeout=timeout)
        return events

    def invoke_layer(self, layer: str, content: str, timeout: int | None = None) -> list[dict]:
        _, events = self._run_payload({"type": "invoke_layer", "layer": layer, "content": content}, ["tool_confirmation_requested", "idle", "error"], timeout=timeout)
        return events

    def last_events(self) -> list[dict]:
        return list(self._last_turn_events)

    def last_tool_calls(self) -> list[dict]:
        return [event for event in self._last_turn_events if event.get("type") == "tool_executed"]

    def wait_for_confirmation(self, timeout: int | None = None, since: int | None = None) -> dict:
        start = self.mark() if since is None else since
        event = self.wait_for_event("tool_confirmation_requested", timeout=timeout, since=start)
        if event is None:
            raise SuiteSkip(self._stderr_tail() or "tool confirmation never arrived")
        return event

    def resolve_confirmation(self, approved: bool, timeout: int | None = None) -> list[dict]:
        _, events = self._run_payload({"type": "tool_decision", "approved": approved}, ["tool_confirmation_requested", "idle", "error"], timeout=timeout)
        return events

    def wait_for_heartbeat(self, timeout: int | None = None) -> dict | None:
        start_events = self.mark()
        start_stderr = self.mark_stderr()
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            for line in self.stderr_since(start_stderr):
                if "[HEARTBEAT] tick fired" in line:
                    return {"type": "heartbeat_tick", "line": line}
            for event in self.events_since(start_events):
                if event.get("type") == "assistant_message" and event.get("layer") == "companion":
                    return event
            if self._proc and self._proc.poll() is not None:
                return None
            time.sleep(0.2)
        return None

    def reset(self) -> None:
        self.close(restart=False)
        self._events.clear()
        self._stderr_lines.clear()
        self._last_turn_events = []
        self.start()

    def reset_all(self) -> None:
        self.close(restart=False)
        _prepare_profile(self.profile_root, self._base_config, wipe=True)
        self._events.clear()
        self._stderr_lines.clear()
        self._last_turn_events = []
        self.start()

    def close(self, restart: bool = False) -> None:
        try:
            if self.is_alive and self._proc and self._proc.stdin:
                self.send({"type": "shutdown"})
                self._proc.wait(timeout=10)
        except Exception:
            pass
        finally:
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            if not restart:
                self._proc = None
                self._started = False


@contextmanager
def companion_session(config_patch: dict | None = None, timeout: int = DEFAULT_TIMEOUT):
    use_main_profile = os.environ.get("OPEN_COMPANION_TEST_USE_MAIN_PROFILE") == "1"
    if use_main_profile:
        profile_root = ROOT
        preserved = {
            "config.json": (ROOT / "config.json").read_text(encoding="utf-8") if (ROOT / "config.json").exists() else None,
            "config.local.json": (ROOT / "config.local.json").read_text(encoding="utf-8") if (ROOT / "config.local.json").exists() else None,
        }
    else:
        TEMP_PROFILE_ROOT.mkdir(parents=True, exist_ok=True)
        profile_root = TEMP_PROFILE_ROOT / f"open-companion-companion-{uuid.uuid4().hex[:8]}"
        profile_root.mkdir(parents=True, exist_ok=False)
        preserved = {}
    session: Companion | None = None
    try:
        session = Companion(profile_root, config_patch=config_patch, timeout=timeout, wipe_on_start=not use_main_profile)
        yield session
    finally:
        if session is not None:
            session.close()
        if use_main_profile:
            for name, content in preserved.items():
                path = ROOT / name
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(content, encoding="utf-8")
        else:
            shutil.rmtree(profile_root, ignore_errors=True)


def companion_profile_root(session: Companion) -> Path:
    return session.profile_root


def memory_snapshot(profile_root: Path) -> str:
    memory_dir = profile_root / "companion" / "memory"
    if not memory_dir.exists():
        return ""
    parts = []
    for path in sorted(memory_dir.glob("memory.md")):
        try:
            parts.append(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return "\n".join(parts)
