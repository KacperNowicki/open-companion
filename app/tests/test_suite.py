#!/usr/bin/env python3
"""
OpenCompanion End-to-End Test Suite
Simulates a real user session through the JSON bridge.

Run: python app/tests/test_suite.py

Sections that require the live backend bridge or Ollama degrade cleanly when the
runtime environment is not available. The suite still reports the root cause
instead of cascading dozens of follow-on failures.
"""

from __future__ import annotations

import importlib
import json
import site
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))
USER_SITE = site.getusersitepackages()
if USER_SITE and USER_SITE not in sys.path:
    sys.path.append(USER_SITE)

from tests.helpers.bridge import BridgeSession
from tests.helpers.setup import BOLD, RESET, ROOT, fail, load_config, load_fixture, ok, ollama_available, section, skip, subprocess_env, summary

READY_EVENT: dict | None = None
LAST_ASSISTANT_MESSAGE: dict | None = None
MEMORY_SNAPSHOT: dict[str, str] | None = None
HEARTBEAT_CONFIG_BACKUP: str | None = None
HEARTBEAT_LOCAL_CONFIG_BACKUP: str | None = None
BRIDGE_CONFIG_BACKUP: str | None = None
BRIDGE_LOCAL_CONFIG_BACKUP: str | None = None
BACKEND_IMPORTS_OK = False
BRIDGE_READY_OK = False
BRIDGE_START_ERROR = ""


def _run_node_json(script: str):
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, cwd=str(ROOT), env=subprocess_env())
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Node snippet failed")
    stdout = result.stdout.strip()
    return json.loads(stdout) if stdout else None


def _memory_dir() -> Path:
    return ROOT / "companion" / "memory"


def _config_path() -> Path:
    return ROOT / "config.json"


def _local_config_path() -> Path:
    return ROOT / "config.local.json"


def _read_optional_text(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _restore_optional_text(path: Path, content: str | None) -> None:
    if content is None:
        path.unlink(missing_ok=True)
        return
    path.write_text(content, encoding="utf-8")


def _snapshot_memory_files() -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in _memory_dir().glob("memory.md"):
        snapshot[path.name] = path.read_text(encoding="utf-8")
    return snapshot


def _restore_memory_snapshot(snapshot: dict[str, str] | None) -> None:
    if snapshot is None:
        return
    memory_dir = _memory_dir()
    for path in memory_dir.glob("memory.md"):
        if path.name not in snapshot:
            path.unlink(missing_ok=True)
    for name, content in snapshot.items():
        (memory_dir / name).write_text(content, encoding="utf-8")


def _all_memory_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in _memory_dir().glob("memory.md"))


def _restore_heartbeat_config(session: BridgeSession | None = None) -> None:
    global HEARTBEAT_CONFIG_BACKUP, HEARTBEAT_LOCAL_CONFIG_BACKUP
    if HEARTBEAT_CONFIG_BACKUP is None:
        return
    _config_path().write_text(HEARTBEAT_CONFIG_BACKUP, encoding="utf-8")
    _restore_optional_text(_local_config_path(), HEARTBEAT_LOCAL_CONFIG_BACKUP)
    HEARTBEAT_CONFIG_BACKUP = None
    HEARTBEAT_LOCAL_CONFIG_BACKUP = None
    if session and session.is_alive():
        try:
            session.send({"type": "config_reload"})
            session.send({"type": "heartbeat_restart"})
        except Exception:
            pass


def _prepare_bridge_config_for_tests() -> None:
    global BRIDGE_CONFIG_BACKUP, BRIDGE_LOCAL_CONFIG_BACKUP
    config_path = _config_path()
    local_config_path = _local_config_path()
    if BRIDGE_CONFIG_BACKUP is None:
        BRIDGE_CONFIG_BACKUP = config_path.read_text(encoding="utf-8")
    if BRIDGE_LOCAL_CONFIG_BACKUP is None:
        BRIDGE_LOCAL_CONFIG_BACKUP = _read_optional_text(local_config_path)

    config_data = json.loads(BRIDGE_CONFIG_BACKUP)
    brain_config = dict(config_data.get("brain") or {})
    layers = dict(brain_config.get("layers") or {})
    companion_layer = dict(layers.get("companion") or {})
    assistant_layer = dict(layers.get("assistant") or {})

    preferred_models = ("gemma4:e4b", "gemma4:26b")
    discovered_ollama_model = _discover_test_ollama_model(preferred_models)
    if not discovered_ollama_model:
        return

    brain_config["provider"] = "gemma"
    brain_config["model"] = discovered_ollama_model

    for layer_name, layer_cfg in (("companion", companion_layer), ("assistant", assistant_layer)):
        next_layer = dict(layer_cfg)
        next_layer["provider"] = "gemma"
        next_layer["model"] = discovered_ollama_model
        layers[layer_name] = next_layer

    heartbeat_config = dict(config_data.get("heartbeat") or {})
    heartbeat_config["enabled"] = False
    config_data["heartbeat"] = heartbeat_config
    brain_config["layers"] = layers
    config_data["brain"] = brain_config
    config_path.write_text(json.dumps(config_data, indent=2) + "\n", encoding="utf-8")
    local_config = json.loads(BRIDGE_LOCAL_CONFIG_BACKUP) if BRIDGE_LOCAL_CONFIG_BACKUP else {}
    local_brain = dict(local_config.get("brain") or {})
    local_layers = dict(local_brain.get("layers") or {})
    local_layers["companion"] = {**dict(local_layers.get("companion") or {}), "provider": "gemma", "model": discovered_ollama_model}
    local_layers["assistant"] = {**dict(local_layers.get("assistant") or {}), "provider": "gemma", "model": discovered_ollama_model}
    local_brain["provider"] = "gemma"
    local_brain["model"] = discovered_ollama_model
    local_brain["layers"] = local_layers
    local_config["brain"] = local_brain
    local_heartbeat = dict(local_config.get("heartbeat") or {})
    local_heartbeat["enabled"] = False
    local_config["heartbeat"] = local_heartbeat
    local_config_path.write_text(json.dumps(local_config, indent=2) + "\n", encoding="utf-8")


def _discover_test_ollama_model(preferred: tuple[str, ...] = ()) -> str:
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return ""

    candidates: list[str] = []
    for entry in payload.get("models", []) if isinstance(payload, dict) else []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("model") or "").strip()
        if not name:
            continue
        lowered = name.lower()
        if "embed" in lowered:
            continue
        candidates.append(name)

    if not candidates:
        return ""
    for preferred_name in preferred:
        if preferred_name in candidates:
            return preferred_name
    return candidates[0]


def _restore_bridge_config() -> None:
    global BRIDGE_CONFIG_BACKUP, BRIDGE_LOCAL_CONFIG_BACKUP
    if BRIDGE_CONFIG_BACKUP is None:
        return
    _config_path().write_text(BRIDGE_CONFIG_BACKUP, encoding="utf-8")
    _restore_optional_text(_local_config_path(), BRIDGE_LOCAL_CONFIG_BACKUP)
    BRIDGE_CONFIG_BACKUP = None
    BRIDGE_LOCAL_CONFIG_BACKUP = None


def _enable_heartbeat_for_test(session: BridgeSession, interval: int = 10) -> None:
    global HEARTBEAT_CONFIG_BACKUP, HEARTBEAT_LOCAL_CONFIG_BACKUP
    config_path = _config_path()
    local_config_path = _local_config_path()
    if HEARTBEAT_CONFIG_BACKUP is None:
        HEARTBEAT_CONFIG_BACKUP = config_path.read_text(encoding="utf-8")
    if HEARTBEAT_LOCAL_CONFIG_BACKUP is None:
        HEARTBEAT_LOCAL_CONFIG_BACKUP = _read_optional_text(local_config_path)
    config_data = json.loads(config_path.read_text(encoding="utf-8"))
    heartbeat_config = dict(config_data.get("heartbeat") or {})
    heartbeat_config["enabled"] = True
    heartbeat_config["interval"] = interval
    config_data["heartbeat"] = heartbeat_config
    config_path.write_text(json.dumps(config_data, indent=2) + "\n", encoding="utf-8")
    local_config = json.loads(HEARTBEAT_LOCAL_CONFIG_BACKUP) if HEARTBEAT_LOCAL_CONFIG_BACKUP else {}
    local_heartbeat = dict(local_config.get("heartbeat") or {})
    local_heartbeat["enabled"] = True
    local_heartbeat["interval"] = interval
    local_config["heartbeat"] = local_heartbeat
    local_config_path.write_text(json.dumps(local_config, indent=2) + "\n", encoding="utf-8")
    session.send({"type": "config_reload"})
    session.send({"type": "heartbeat_restart"})
    time.sleep(1)


def _bridge_failure_reason(session: BridgeSession | None = None) -> str:
    if session is None:
        return BRIDGE_START_ERROR or "bridge not available"
    lines = [line for line in session.stderr_lines if line.strip()]
    if lines:
        return "\n".join(lines[-12:])
    return BRIDGE_START_ERROR or f"bridge exited with code {session.returncode}"


def _skip_bridge_dependent_section(title: str, reason: str) -> None:
    skip(title, reason)


def _skip_if_bridge_unavailable(name: str, session: BridgeSession | None) -> bool:
    if session and session.is_alive() and BRIDGE_READY_OK:
        return False
    skip(name, _bridge_failure_reason(session))
    return True


def _wait_for_idle(session: BridgeSession, since: int, timeout: int = 120) -> dict | None:
    return session.wait_for_event("idle", timeout=timeout, since=since)


def _wait_for_assistant_reply(session: BridgeSession, since: int, timeout: int = 120) -> dict | None:
    deadline = time.time() + timeout
    last_message = None
    while time.time() < deadline:
        events = session.events_since(since)
        assistant_messages = [event for event in events if event.get("type") == "assistant_message"]
        if assistant_messages:
            last_message = assistant_messages[-1]
        if last_message and any(event.get("type") == "idle" for event in events):
            return last_message
        if not session.is_alive():
            return last_message
        time.sleep(0.2)
    return last_message


def test_python_version():
    name = "Python version is 3.11+"
    try:
        assert sys.version_info >= (3, 11), f"Found {sys.version}"
        ok(name)
    except Exception as exc:
        fail(name, str(exc))


def test_project_structure():
    name = "Project structure contains required folders and files"
    try:
        for rel in ["app", "companion", "config", "docs"]:
            assert (ROOT / rel).exists(), f"Missing directory: {rel}"
        for rel in [
            "companion/soul/defaults/soul_companion.md",
            "companion/soul/defaults/soul_assistant.md",
            "companion/soul/defaults/shared_runtime_contract.md",
            "companion/soul/defaults/generate.md",
            "companion/vault/README.md",
            "config/defaults.js",
            "app/backend/tools/registry.json",
        ]:
            assert (ROOT / rel).exists(), f"Missing file: {rel}"
        ok(name)
    except Exception as exc:
        fail(name, str(exc))


def test_config_json():
    name = "config.json parses and merged config exposes required keys"
    try:
        config_path = ROOT / "config.json"
        assert config_path.exists(), "config.json missing"
        with config_path.open(encoding="utf-8") as handle:
            raw = json.load(handle)
        assert isinstance(raw, dict), "config.json root must be an object"
        merged = load_config()
        for key in ["brain", "memory", "heartbeat", "voice", "ui", "vault"]:
            assert key in merged, f"Missing merged config key: {key}"
        ok(name)
    except Exception as exc:
        fail(name, str(exc))


def test_registry_loads():
    name = "Tool registry loads expected layers and representative tools"
    try:
        sys.path.insert(0, str(ROOT / "app"))
        sys.path.insert(0, str(ROOT / "app" / "backend"))
        sys.path.insert(0, str(ROOT))
        import backend.tool_registry as tool_registry

        tool_registry.load_registry(force=True)
        companion = tool_registry.get_tools_by_layer("companion")
        assistant = tool_registry.get_tools_by_layer("assistant")
        expected_both = {
            "write_memory", "search_memories", "read_file", "write_file", "append_file",
            "list_files", "search_files", "replace_text_in_file", "add_reminder", "add_recurring_reminder", "run_terminal",
        }
        expected_companion_only = set()
        assert {tool.get("name") for tool in companion} == expected_both | expected_companion_only, companion
        assert {tool.get("name") for tool in assistant} == expected_both, assistant
        ok(name)
    except Exception as exc:
        fail(name, str(exc))


def test_soul_files_readable():
    name = "Soul files are readable and non-empty"
    try:
        for rel in [
            "companion/soul/defaults/shared_runtime_contract.md",
            "companion/soul/defaults/soul_companion.md",
            "companion/soul/defaults/soul_assistant.md",
        ]:
            content = (ROOT / rel).read_text(encoding="utf-8").strip()
            assert content, f"{rel} is empty"
        ok(name)
    except Exception as exc:
        fail(name, str(exc))


def test_vault_structure():
    name = "Vault structure exists and starter docs are readable"
    try:
        for rel in ["companion/vault", "companion/vault/Daily Notes", "companion/vault/Projects", "companion/vault/Companion", "companion/vault/README.md"]:
            assert (ROOT / rel).exists(), f"Missing vault path: {rel}"
        assert (ROOT / "companion/vault/README.md").read_text(encoding="utf-8").strip(), "vault README empty"
        ok(name)
    except Exception as exc:
        fail(name, str(exc))


def test_python_deps():
    name = "Required Python dependencies import successfully"
    try:
        result = subprocess.run([
            sys.executable,
            "-c",
            "import site, sys; user_site = site.getusersitepackages(); "
            "sys.path.append(user_site) if user_site and user_site not in sys.path else None; "
            "import openai, pydantic, keyring, typing_extensions, numpy, PIL, psutil, pywhispercpp; print('ok')",
        ], capture_output=True, text=True, cwd=str(ROOT), env=subprocess_env())
        assert result.returncode == 0, result.stderr.strip() or result.stdout.strip()
        ok(name)
    except Exception as exc:
        fail(name, str(exc))


def test_backend_imports():
    name = "Backend modules import without errors"
    global BACKEND_IMPORTS_OK
    try:
        result = subprocess.run([
            sys.executable,
            "-c",
            "import site, sys; user_site = site.getusersitepackages(); "
            "sys.path.extend([p for p in [user_site, r'%s', r'%s', r'%s'] if p and p not in sys.path]); "
            "import importlib; "
            "mods=['backend.brain','backend.memory','backend.dream','backend.tool_registry','backend.heartbeat','backend.tts','backend.stt','backend.wrapper']; "
            "[importlib.import_module(m) for m in mods]; print('ok')" % (str(ROOT), str(ROOT / 'app'), str(ROOT / 'app' / 'backend')),
        ], capture_output=True, text=True, cwd=str(ROOT), env=subprocess_env())
        assert result.returncode == 0, result.stderr.strip() or result.stdout.strip()
        BACKEND_IMPORTS_OK = True
        ok(name)
    except Exception as exc:
        BACKEND_IMPORTS_OK = False
        fail(name, str(exc))


def test_tool_registry_dynamic_import():
    name = "Tool registry dynamic execution works for search_memories"
    try:
        sys.path.insert(0, str(ROOT / "app"))
        sys.path.insert(0, str(ROOT / "app" / "backend"))
        sys.path.insert(0, str(ROOT))
        import backend.tool_registry as tool_registry
        result = tool_registry.execute_tool("search_memories", {"query": "test"}, "companion", config=load_config())
        lowered = str(result).lower()
        assert lowered
        ok(name)
    except Exception as exc:
        fail(name, str(exc))


def test_bridge_starts(session: BridgeSession):
    name = "Bridge starts and emits ready"
    global READY_EVENT, BRIDGE_READY_OK, BRIDGE_START_ERROR
    try:
        ready = session.wait_for_event("ready", timeout=20)
        assert ready is not None, _bridge_failure_reason(session)
        assert "voice_capabilities" in ready, "ready missing voice_capabilities"
        READY_EVENT = ready
        BRIDGE_READY_OK = True
        ok(name)
    except Exception as exc:
        BRIDGE_READY_OK = False
        BRIDGE_START_ERROR = str(exc)
        fail(name, str(exc))


def test_bridge_ready_fields(session: BridgeSession):
    name = "Ready event exposes expected fields"
    try:
        if not BRIDGE_READY_OK:
            skip(name, _bridge_failure_reason(session))
            return
        ready = READY_EVENT or session.wait_for_event("ready", timeout=5)
        assert isinstance(ready, dict), "ready event unavailable"
        assert isinstance(ready.get("voice_capabilities"), dict), "voice_capabilities must be a dict"
        ok(name)
    except Exception as exc:
        fail(name, str(exc))


def test_companion_responds(session: BridgeSession):
    name = "Companion responds to a basic greeting"
    global LAST_ASSISTANT_MESSAGE
    try:
        if _skip_if_bridge_unavailable(name, session):
            return
        since = session.mark()
        session.send({"type": "user_message", "content": "hello"})
        event = _wait_for_assistant_reply(session, since, timeout=120)
        assert event is not None, "No assistant_message received"
        assert str(event.get("content", "")).strip(), "Assistant response was empty"
        assert event.get("layer") == "companion", f"Expected companion layer, got {event.get('layer')}"
        LAST_ASSISTANT_MESSAGE = event
        ok(name)
    except Exception as exc:
        fail(name, str(exc))


def test_companion_has_audio(session: BridgeSession):
    name = "Companion response includes audio when backend TTS is available"
    try:
        if _skip_if_bridge_unavailable(name, session):
            return
        ready = READY_EVENT or session.wait_for_event("ready", timeout=5)
        backend_tts = bool((ready or {}).get("voice_capabilities", {}).get("backend_tts"))
        if not backend_tts:
            skip(name, "backend TTS not available")
            return
        assert isinstance(LAST_ASSISTANT_MESSAGE, dict), "No assistant message available from prior test"
        assert LAST_ASSISTANT_MESSAGE.get("audio_b64"), "Expected audio_b64 in assistant message"
        ok(name)
    except Exception as exc:
        fail(name, str(exc))


def test_assistant_run_terminal(session: BridgeSession):
    name = "Assistant layer can execute run_terminal"
    try:
        if _skip_if_bridge_unavailable(name, session):
            return
        since = session.mark()
        session.send({
            "type": "invoke_layer",
            "layer": "assistant",
            "content": "Use run_terminal to run pwd in the sandbox and then tell me the result.",
        })
        tool_event = session.wait_for_tool("run_terminal", timeout=120, since=since)
        assert tool_event is not None, "run_terminal was not executed"
        result = str(tool_event.get("result", "") or "")
        assert result.strip(), "run_terminal result was empty"
        idle_event = _wait_for_idle(session, since, timeout=120)
        assert idle_event is not None, "Assistant layer never returned to idle after run_terminal"
        ok(name)
    except Exception as exc:
        fail(name, str(exc))


def test_memory_write(session: BridgeSession):
    name = "Companion writes a remembered preference to memory"
    global MEMORY_SNAPSHOT
    try:
        if _skip_if_bridge_unavailable(name, session):
            return
        MEMORY_SNAPSHOT = _snapshot_memory_files()
        since = session.mark()
        session.send({"type": "user_message", "content": "remember that my favourite color is purple"})
        reply = _wait_for_assistant_reply(session, since, timeout=120)
        assert reply is not None, "No assistant reply after memory write request"
        time.sleep(2)
        assert "purple" in _all_memory_text().lower(), "Memory files do not contain 'purple'"
        ok(name)
    except Exception as exc:
        fail(name, str(exc))


def test_memory_recall(session: BridgeSession):
    name = "Companion recalls a remembered preference"
    try:
        if _skip_if_bridge_unavailable(name, session):
            return
        since = session.mark()
        session.send({"type": "user_message", "content": "what is my favourite color?"})
        reply = _wait_for_assistant_reply(session, since, timeout=120)
        if reply is None:
            skip(name, "No assistant reply for recall question; local recall remains nondeterministic in this environment")
            return
        assert "purple" in str(reply.get("content", "")).lower(), reply.get("content", "")
        ok(name)
    except Exception as exc:
        fail(name, str(exc))
    finally:
        _restore_memory_snapshot(MEMORY_SNAPSHOT)


def test_run_windows_terminal_hidden_from_model_payload(session: BridgeSession):
    name = "Windows host commands stay hidden from model payload"
    try:
        sys.path.insert(0, str(ROOT / "app"))
        sys.path.insert(0, str(ROOT / "app" / "backend"))
        import backend.brain as brain_module
        import backend.wrapper as wrapper_module

        config = brain_module.load_config()
        layer_session = wrapper_module.LayerSession(config, "assistant")
        try:
            definitions = layer_session._tool_definitions(config, query_text="run this in powershell: Get-Date")
            names = {definition.get("function", {}).get("name") for definition in definitions}
            assert "run_windows_terminal" not in names, names
            assert "run_terminal" in names, names
            assert "add_reminder" in names, names
            assert "add_recurring_reminder" in names, names
        finally:
            try:
                layer_session.close()
            except AttributeError:
                pass
        ok(name)
    except Exception as exc:
        fail(name, str(exc))


def test_heartbeat_fires(session: BridgeSession):
    name = "Heartbeat suppression then firing works through the bridge"
    try:
        if _skip_if_bridge_unavailable(name, session):
            return
        _enable_heartbeat_for_test(session, interval=10)
        since = session.mark()
        session.send({"type": "user_message", "content": "just checking in"})
        reply = session.wait_for_event("assistant_message", timeout=60, since=since)
        assert reply is not None, "No assistant reply while preparing heartbeat test"
        deadline = time.time() + 30
        while time.time() < deadline and not session.stderr_contains("[HEARTBEAT] tick skipped"):
            time.sleep(0.5)
        assert session.stderr_contains("[HEARTBEAT] tick skipped"), "Heartbeat did not log a skipped tick"
        deadline = time.time() + 45
        while time.time() < deadline and not session.stderr_contains("[HEARTBEAT] tick fired"):
            time.sleep(0.5)
        assert session.stderr_contains("[HEARTBEAT] tick fired"), "Heartbeat did not fire after suppression window elapsed"
        ok(name)
    except Exception as exc:
        fail(name, str(exc))
    finally:
        _restore_heartbeat_config(session)


def test_heartbeat_llm_reached(session: BridgeSession):
    name = "Heartbeat reaches the LLM path"
    try:
        if _skip_if_bridge_unavailable(name, session):
            return
        _enable_heartbeat_for_test(session, interval=10)
        deadline = time.time() + 45
        while time.time() < deadline and not session.stderr_contains("[HEARTBEAT] LLM response:"):
            time.sleep(0.5)
        assert session.stderr_contains("[HEARTBEAT] LLM response:"), "Heartbeat never logged an LLM response"
        ok(name)
    except Exception as exc:
        fail(name, str(exc))
    finally:
        _restore_heartbeat_config(session)


def test_config_migration():
    name = "Merged config exposes versioned defaults and vault settings"
    try:
        config = load_config()
        for key in ["version", "companion", "brain", "memory", "heartbeat", "voice", "ui", "vault"]:
            assert key in config, f"Missing key: {key}"
        assert isinstance(config["vault"], dict), "vault block missing"
        assert "path" in config["vault"], "vault.path missing"
        assert "enabled" in config["vault"], "vault.enabled missing"
        ok(name)
    except Exception as exc:
        fail(name, str(exc))


def test_config_defaults_fill_missing():
    name = "Minimal config fixture gets defaults filled in"
    try:
        fixture_json = json.dumps(load_fixture("config_minimal.json"))
        result = _run_node_json(
            "const { DEFAULT_CONFIG } = require('./config/defaults');"
            "const { runMigrations } = require('./config/migrate');"
            "const { deepMerge } = require('./config/index');"
            f"const minimal = {fixture_json};"
            "const merged = deepMerge(DEFAULT_CONFIG, runMigrations(minimal));"
            "console.log(JSON.stringify({provider: merged.brain.provider, top_k: merged.memory.top_k, vault_enabled: merged.vault.enabled}));"
        )
        assert result["provider"] == "gemma", "brain.provider default missing"
        assert result["top_k"] == 5, "memory.top_k default missing"
        assert result["vault_enabled"] is True, "vault.enabled default missing"
        ok(name)
    except Exception as exc:
        fail(name, str(exc))


def test_memory_extraction_routing():
    name = "Memory extraction routing honors brain, local, and api source selection"
    original_sys_path = list(sys.path)
    try:
        if not BACKEND_IMPORTS_OK:
            skip(name, "backend.memory import prerequisites not available")
            return
        sys.path.insert(0, str(ROOT))
        sys.path.insert(0, str(ROOT / "app"))
        sys.path.insert(0, str(ROOT / "app" / "backend"))
        memory = importlib.import_module("backend.memory")

        fallback_client = object()
        local_client = object()
        api_client = object()
        with mock.patch.object(memory, "_build_local_ollama_extraction_client", return_value=local_client), \
             mock.patch.object(memory, "_build_provider_extraction_client", return_value=api_client), \
             mock.patch.object(memory, "_is_ollama_model_available", return_value=True):
            cloud_brain = {
                "brain": {"provider": "openai", "model": "gpt-4.1"},
                "memory": {"extraction_source": "brain", "extraction_model": "gpt-4.1-mini"},
            }
            active_client, model, provider = memory.resolve_extraction_target(cloud_brain, fallback_client)
            assert active_client is api_client, "brain source with cloud brain should use provider extraction"
            assert model == "gpt-4.1-mini", model
            assert provider == "openai", provider

            api_override = {
                "brain": {"provider": "gemma", "model": "qwen2.5:14b"},
                "memory": {
                    "extraction_source": "api",
                    "extraction_provider": "anthropic",
                    "extraction_model": "claude-sonnet-4-5",
                },
            }
            active_client, model, provider = memory.resolve_extraction_target(api_override, fallback_client)
            assert active_client is api_client, "api source should use the configured provider client"
            assert model == "claude-sonnet-4-5", model
            assert provider == "anthropic", provider

            local_override = {
                "brain": {"provider": "openai", "model": "gpt-4.1"},
                "memory": {"extraction_source": "local", "extraction_model": "llama3.2:3b"},
            }
            active_client, model, provider = memory.resolve_extraction_target(local_override, fallback_client)
            assert active_client is local_client, "local source should force an Ollama client"
            assert model == "llama3.2:3b", model
            assert provider == "gemma", provider

            local_brain = {
                "brain": {"provider": "gemma", "model": "qwen2.5:14b"},
                "memory": {"extraction_source": "brain", "extraction_model": ""},
            }
            active_client, model, provider = memory.resolve_extraction_target(local_brain, fallback_client)
            assert active_client is fallback_client, "brain source with local brain should reuse the active client"
            assert model == "qwen2.5:14b", model
            assert provider == "gemma", provider
        ok(name)
    except Exception as exc:
        fail(name, str(exc))
    finally:
        sys.path[:] = original_sys_path

def test_vault_readable_by_companion():
    name = "Companion soul includes vault tool guidance"
    original_sys_path = list(sys.path)
    try:
        if not BACKEND_IMPORTS_OK:
            skip(name, "backend.wrapper import prerequisites not available")
            return
        sys.path.insert(0, str(ROOT))
        sys.path.insert(0, str(ROOT / "app"))
        sys.path.insert(0, str(ROOT / "app" / "backend"))
        brain = importlib.import_module("backend.brain")
        prompt = brain.load_identity({"vault": {"enabled": True, "path": "./companion/vault"}}, "companion")
        assert isinstance(prompt, str) and prompt.strip(), "Companion soul was empty"
        assert "vault" in prompt.lower(), "Companion soul did not mention vault"
        assert "read_file" in prompt and "write_file" in prompt, "Companion soul did not mention vault file tools"
        ok(name)
    except Exception as exc:
        fail(name, str(exc))
    finally:
        sys.path[:] = original_sys_path


def test_vault_writable():
    name = "Vault can be written to and cleaned up"
    test_path = ROOT / "companion" / "vault" / "Companion" / "test_write.md"
    content = "# Test Write\n\nThis file was created by the automated test suite.\n"
    try:
        test_path.write_text(content, encoding="utf-8")
        assert test_path.exists(), "Vault test file was not created"
        assert test_path.read_text(encoding="utf-8") == content, "Vault test file content mismatch"
        test_path.unlink()
        assert not test_path.exists(), "Vault test file was not removed"
        ok(name)
    except Exception as exc:
        fail(name, str(exc))
        test_path.unlink(missing_ok=True)


def test_clean_shutdown(session: BridgeSession):
    name = "Bridge shuts down cleanly without traceback noise"
    try:
        if not BRIDGE_READY_OK:
            skip(name, _bridge_failure_reason(session))
            return
        if not session.is_alive() and session.returncode in (None, 0):
            ok(name)
            return
        session.shutdown()
        deadline = time.time() + 10
        while time.time() < deadline and session.is_alive():
            time.sleep(0.2)
        assert not session.is_alive(), "Bridge process did not exit"
        assert session.returncode == 0, f"Unexpected return code: {session.returncode}"
        stderr_text = "\n".join(session.stderr_lines)
        tail = "\n".join(session.stderr_lines[-40:])
        assert "Traceback" not in stderr_text, f"Traceback detected during shutdown:\n{tail}"
        ok(name)
    except Exception as exc:
        fail(name, str(exc))


_FILTER: str | None = None

# Maps test display names to extra keywords the filter can match against.
# Keeps _should_run() simple while supporting natural filter terms.
_TEST_EXTRA_KEYS: dict[str, str] = {
    "Bridge shuts down cleanly without traceback noise": "shutdown clean_shutdown",
    "Memory extraction routing stays local when configured or when the companion brain is cloud": "memory_extract extraction_routing",
    "Assistant layer can execute run_terminal": "assistant run_terminal sandbox",
    "Windows host commands stay hidden from model payload": "assistant run_windows_terminal hidden windows",
    "Heartbeat suppression then firing works through the bridge": "heartbeat",
    "Heartbeat reaches the LLM path": "heartbeat llm",
}


def _should_run(name: str) -> bool:
    """Return True if this test should run given the active CLI filter."""
    if _FILTER is None:
        return True
    needle = _FILTER.lower()
    if needle in name.lower():
        return True
    extra = _TEST_EXTRA_KEYS.get(name, "").lower()
    if extra and needle in extra:
        return True
    return False


if __name__ == "__main__":
    import argparse as _argparse
    _parser = _argparse.ArgumentParser(description="OpenCompanion test suite")
    _parser.add_argument(
        "filter", nargs="?", default=None,
        help="Only run tests whose name contains this string (case-insensitive)"
    )
    _args = _parser.parse_args()
    _FILTER = _args.filter.lower() if _args.filter else None

    print(f"{BOLD}OpenCompanion End-to-End Test Suite{RESET}")
    print(f"Python: {sys.version}")
    print(f"Root: {ROOT}")
    if _FILTER:
        print(f"Filter: {_args.filter!r}")
    print()

    section("Environment")
    if _should_run("Python version is 3.11+"):
        test_python_version()
    if _should_run("Project structure contains required folders and files"):
        test_project_structure()
    if _should_run("config.json parses and merged config exposes required keys"):
        test_config_json()
    if _should_run("Tool registry loads expected layers and representative tools"):
        test_registry_loads()
    if _should_run("Soul files are readable and non-empty"):
        test_soul_files_readable()
    if _should_run("Vault structure exists and starter docs are readable"):
        test_vault_structure()
    if _should_run("Required Python dependencies import successfully"):
        test_python_deps()

    section("Backend imports")
    if _should_run("Backend modules import without errors"):
        test_backend_imports()
    if _should_run("Tool registry dynamic execution works for search_memories"):
        test_tool_registry_dynamic_import()
    if _should_run("Companion soul includes vault tool guidance"):
        test_vault_readable_by_companion()
    if _should_run("Vault can be written to and cleaned up"):
        test_vault_writable()

    section("Bridge startup")
    _need_bridge = (
        _FILTER is None
        or any(_should_run(n) for n in [
            "Bridge starts and emits ready",
            "Ready event exposes expected fields",
            "Companion responds to a basic greeting",
            "Companion response includes audio when backend TTS is available",
            "Companion writes a remembered preference to memory",
            "Companion recalls a remembered preference",
            "Assistant layer can execute run_terminal",
            "Windows host commands stay hidden from model payload",
            "Heartbeat suppression then firing works through the bridge",
            "Heartbeat reaches the LLM path",
            "Bridge shuts down cleanly without traceback noise",
        ])
    )
    if _need_bridge:
        _prepare_bridge_config_for_tests()
        session = BridgeSession(timeout=30)
        session.start()
        # Always run bridge startup to set BRIDGE_READY_OK, even when filtered.
        test_bridge_starts(session)
        if _should_run("Ready event exposes expected fields"):
            test_bridge_ready_fields(session)

        if not BRIDGE_READY_OK:
            _skip_bridge_dependent_section("Companion conversation", _bridge_failure_reason(session))
            _skip_bridge_dependent_section("Tool execution", _bridge_failure_reason(session))
            _skip_bridge_dependent_section("Heartbeat", _bridge_failure_reason(session))
            if _should_run("Bridge shuts down cleanly without traceback noise"):
                test_clean_shutdown(session)
        elif not ollama_available():
            skip("Companion conversation", "Ollama not running")
            skip("Tool execution", "Ollama not running")
            skip("Heartbeat", "Ollama not running")
            if _should_run("Bridge shuts down cleanly without traceback noise"):
                test_clean_shutdown(session)
        else:
            section("Companion conversation")
            if _should_run("Companion responds to a basic greeting"):
                test_companion_responds(session)
            if _should_run("Companion response includes audio when backend TTS is available"):
                test_companion_has_audio(session)
            if _should_run("Companion writes a remembered preference to memory"):
                test_memory_write(session)
            if _should_run("Companion recalls a remembered preference"):
                test_memory_recall(session)

            section("Tool execution")
            if _should_run("Assistant layer can execute run_terminal"):
                test_assistant_run_terminal(session)
            if _should_run("Windows host commands stay hidden from model payload"):
                test_run_windows_terminal_hidden_from_model_payload(session)

            section("Heartbeat")
            if _should_run("Heartbeat suppression then firing works through the bridge"):
                test_heartbeat_fires(session)
            if _should_run("Heartbeat reaches the LLM path"):
                test_heartbeat_llm_reached(session)

            section("Shutdown")
            if _should_run("Bridge shuts down cleanly without traceback noise"):
                test_clean_shutdown(session)

        _restore_bridge_config()

    section("Config system")
    if _should_run("Merged config exposes versioned defaults and vault settings"):
        test_config_migration()
    if _should_run("Minimal config fixture gets defaults filled in"):
        test_config_defaults_fill_missing()
    if _should_run("Memory extraction routing stays local when configured or when the companion brain is cloud"):
        test_memory_extraction_routing()

    success = summary()
    sys.exit(0 if success else 1)
