#!/usr/bin/env python3
from __future__ import annotations

import json
import site
import sys
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = ROOT / "app"
BACKEND_DIR = APP_ROOT / "backend"
USER_SITE = site.getusersitepackages()
EXTRA_SITE = ROOT / ".pytest-deps"
for candidate in (str(APP_ROOT), str(BACKEND_DIR), str(EXTRA_SITE) if EXTRA_SITE.exists() else "", USER_SITE):
    if candidate and candidate not in sys.path:
        sys.path.insert(0, candidate)

from tests.helpers.setup import BOLD, RESET, fail, ok, section, summary

import backend.brain as brain_module
import backend.tool_registry as tool_registry
import backend.wrapper as wrapper


class FakeClient:
    def health_check(self):
        return True


class FakeToolCall:
    def __init__(self, name: str, arguments: dict, call_id: str = "call-1") -> None:
        self.id = call_id
        self.function = SimpleNamespace(name=name, arguments=json.dumps(arguments))


def run_test(name: str, fn) -> None:
    try:
        fn()
        ok(name)
    except AssertionError as exc:
        fail(name, str(exc))
    except Exception as exc:
        fail(name, f"{type(exc).__name__}: {exc}")


def base_config() -> dict:
    return {
        "companion": {"name": "Nova"},
        "brain": {
            "provider": "gemma",
            "model": "qwen2.5:14b",
            "layers": {
                "companion": {"provider": "", "model": ""},
                "assistant": {"provider": "gemma", "model": "qwen2.5:14b"},
            },
        },
        "layers": {
            "companion": {"enabled": True, "display_name": "Nova"},
            "assistant": {"enabled": True, "display_name": "Assistant"},
        },
        "heartbeat": {"enabled": False, "interval": 1800, "only_when_idle": False, "idle_threshold_minutes": 5},
        "voice": {"tts_provider": "", "stt_provider": "", "tts_enabled": False, "stt_enabled": False},
        "memory": {
            "enabled": False,
            "layer_budgets": {
                "companion": {"percent": 0.15, "min_tokens": 2048, "max_tokens": 12000},
                "assistant": {"percent": 0.20, "min_tokens": 2048, "max_tokens": 16000},
            },
        },
        "vault": {"enabled": True, "path": "./vault"},
        "ui": {"selected_target_layer": "companion"},
    }


def runtime_patches(config: dict):
    def _noop_refresh_voice(self):
        self.tts = None
        self.stt = None

    return [        patch.object(wrapper.LayeredRuntime, "_warm_companion_runtime_async", lambda self: None),
        patch.object(wrapper.LayeredRuntime, "_refresh_voice_engines", _noop_refresh_voice),
        patch("backend.wrapper.soul_module.ensure_soul_active", lambda _config: None),
        patch("backend.wrapper.memory.init_memory", lambda: None),
        patch(
            "backend.wrapper.memory.build_system_prompt",
            lambda prompt, _config, user_message="", memory_budget_tokens=None, layer_name="companion": prompt,
        ),
        patch("backend.wrapper.dream.maybe_trigger_dream", lambda *_args, **_kwargs: None),
        patch("backend.wrapper.brain.load_config", lambda: config),
        patch("backend.wrapper.brain.create_client", lambda _config, layer_name="companion": FakeClient()),
        patch("backend.wrapper.brain.load_identity", lambda _config, layer_name="companion": f"identity for {layer_name}"),
    ]


def build_runtime(config: dict | None = None) -> wrapper.LayeredRuntime:
    active_config = config or base_config()
    stack = ExitStack()
    for patcher in runtime_patches(active_config):
        stack.enter_context(patcher)
    runtime = wrapper.LayeredRuntime()
    runtime._test_stack = stack
    return runtime


def close_runtime(runtime: wrapper.LayeredRuntime) -> None:
    stack = getattr(runtime, "_test_stack", None)
    if stack is not None:
        stack.close()


def build_session(layer_name: str, config: dict | None = None) -> wrapper.LayerSession:
    active_config = config or base_config()
    stack = ExitStack()
    for patcher in runtime_patches(active_config):
        stack.enter_context(patcher)
    session = wrapper.LayerSession(active_config, layer_name, include_memory=False)
    session._test_stack = stack
    return session


def close_session(session: wrapper.LayerSession) -> None:
    stack = getattr(session, "_test_stack", None)
    if stack is not None:
        stack.close()


def test_companion_permissions() -> None:
    for name in ("run_terminal", "run_windows_terminal", "read_file", "write_file", "append_file", "list_files", "search_files", "replace_text_in_file", "add_reminder", "add_recurring_reminder", "write_memory", "search_memories"):
        assert tool_registry.is_tool_allowed(name, "companion", config={}), name
    for name in ("edit_code_symbol", "edit_file", "help_skill", "list_skills", "write_skill", "delete_skill"):
        assert not tool_registry.is_tool_allowed(name, "companion", config={}), name
    assert not tool_registry.needs_confirmation("run_terminal", "companion", config={})
    assert tool_registry.needs_confirmation("run_windows_terminal", "companion", config={})
    assert not tool_registry.needs_confirmation("read_file", "companion", config={})
    assert not tool_registry.needs_confirmation("write_file", "companion", config={})
    assert not tool_registry.needs_confirmation("replace_text_in_file", "companion", config={})
    assert not tool_registry.needs_confirmation("add_reminder", "companion", config={})
    assert not tool_registry.needs_confirmation("add_recurring_reminder", "companion", config={})
    assert not tool_registry.needs_confirmation("write_memory", "companion", config={})


def test_assistant_permissions() -> None:
    for name in ("run_terminal", "run_windows_terminal", "read_file", "write_file", "append_file", "list_files", "search_files", "replace_text_in_file", "add_reminder", "add_recurring_reminder", "write_memory", "search_memories"):
        assert tool_registry.is_tool_allowed(name, "assistant", config={}), name
    for name in ("edit_code_symbol", "edit_file", "help_skill", "list_skills", "write_skill", "delete_skill"):
        assert not tool_registry.is_tool_allowed(name, "assistant", config={}), name
    assert not tool_registry.needs_confirmation("run_terminal", "assistant", config={})
    assert tool_registry.needs_confirmation("run_windows_terminal", "assistant", config={})
    assert not tool_registry.needs_confirmation("read_file", "assistant", config={})
    assert not tool_registry.needs_confirmation("write_file", "assistant", config={})
    assert not tool_registry.needs_confirmation("replace_text_in_file", "assistant", config={})
    assert not tool_registry.needs_confirmation("add_reminder", "assistant", config={})
    assert not tool_registry.needs_confirmation("add_recurring_reminder", "assistant", config={})


def test_normalize_layer_name_collapses_legacy_workers() -> None:
    assert brain_module.normalize_layer_name("assistant") == "assistant"
    assert brain_module.normalize_layer_name("assistant_low") == "assistant"
    assert brain_module.normalize_layer_name("assistant_high") == "assistant"
    assert brain_module.normalize_layer_name("pc_doctor") == "assistant"
    assert brain_module.normalize_layer_name("pcdoctor") == "assistant"
    assert brain_module.normalize_layer_name("companion") == "companion"


def test_assistant_resolves_legacy_brain_keys() -> None:
    config = {
        "brain": {
            "provider": "gemma",
            "model": "default_model",
            "layers": {
                "assistant_low": {"provider": "gemma", "model": "legacy_model"},
            },
        },
        "layers": {},
    }
    brain_cfg = brain_module.get_layer_brain_config(config, "assistant")
    assert brain_cfg["model"] == "legacy_model"


def test_companion_windows_terminal_is_exposed_with_confirmation_metadata() -> None:
    session = build_session("companion")
    try:
        definitions = session._tool_definitions(base_config(), query_text="run this in powershell: Get-Date")
        names = {definition.get("function", {}).get("name") for definition in definitions}
        assert "run_windows_terminal" in names
        assert "run_terminal" in names
        assert tool_registry.needs_confirmation("run_windows_terminal", "companion", config={})
    finally:
        close_session(session)


def test_direct_assistant_invoke_returns_without_companion_rewrite() -> None:
    runtime = build_runtime()
    try:
        with patch.object(wrapper.LayerSession, "submit_user_message", autospec=True, return_value={"type": "assistant_message", "content": "Assistant handled it", "layer": "assistant", "state": "idle"}):
            events = runtime.invoke_layer("assistant", "write a note")
        event_types = [event["type"] for event in events]
        assert event_types == ["assistant_started", "layer_completed", "assistant_message"], event_types
    finally:
        close_runtime(runtime)


def test_invoke_layer_normalizes_legacy_assistant_aliases() -> None:
    runtime = build_runtime()
    try:
        with patch.object(runtime, "_start_worker_layer", return_value=[]) as mock_start:
            runtime.invoke_layer("assistant_low", "do some work")
            called_layer = mock_start.call_args[0][0] if mock_start.call_args[0] else mock_start.call_args[1].get("layer_name")
            assert called_layer == "assistant"
    finally:
        close_runtime(runtime)


def test_invoke_layer_supports_direct_companion() -> None:
    runtime = build_runtime()
    try:
        with patch.object(runtime, "submit_user_message", return_value=[{"type": "assistant_message", "content": "hi", "layer": "companion"}]) as mock_submit:
            events = runtime.invoke_layer("companion", "hello there")
            assert events and events[0]["layer"] == "companion"
            mock_submit.assert_called_once_with("hello there", image_base64=None)
    finally:
        close_runtime(runtime)


def test_image_payload_is_dropped_for_text_only_model() -> None:
    session = build_session("assistant")
    try:
        fake_response = SimpleNamespace(content="ok", tool_calls=None)
        with patch.object(session, "_call_brain", return_value=fake_response):
            session.submit_user_message("what is this?", image_base64="fake-image")

        user_messages = [message for message in session.conversation if message.get("role") == "user"]
        assert user_messages[-1] == {"role": "user", "content": "what is this?"}
    finally:
        close_session(session)


def test_image_payload_is_kept_for_vision_model() -> None:
    config = base_config()
    config["brain"]["layers"]["assistant"] = {"provider": "gemini", "model": "gemini-2.5-flash"}
    session = build_session("assistant", config=config)
    try:
        fake_response = SimpleNamespace(content="ok", tool_calls=None)
        with patch.object(session, "_call_brain", return_value=fake_response):
            session.submit_user_message("what is this?", image_base64="fake-image")

        user_messages = [message for message in session.conversation if message.get("role") == "user"]
        assert user_messages[-1] == {"role": "user", "content": "what is this?", "images": ["fake-image"]}
    finally:
        close_session(session)


def test_companion_tool_definitions_do_not_include_layer_handoffs() -> None:
    session = build_session("companion")
    try:
        definitions = session._tool_definitions(base_config(), query_text="help me")
        names = {definition.get("function", {}).get("name") for definition in definitions}
        assert "request_assistant" not in names
        assert "request_pc_doctor" not in names
    finally:
        close_session(session)


def test_layer_toolset_is_exposed_with_minimal_special_gates() -> None:
    config = base_config()
    session = build_session("assistant", config=config)
    try:
        definitions = session._tool_definitions(config, query_text="write a note")
        names = {definition.get("function", {}).get("name") for definition in definitions}
        assert "read_file" in names
        assert "run_terminal" in names
        assert "run_windows_terminal" in names
        assert "edit_file" not in names
        assert "edit_code_symbol" not in names
        assert "help_skill" not in names
        assert "list_skills" not in names
        assert "write_skill" not in names
        assert "delete_skill" not in names
    finally:
        close_session(session)


def test_repeated_identical_tool_calls_are_blocked() -> None:
    session = build_session("assistant")
    try:
        session._tool_definitions(base_config(), query_text="run terminal command cat Projects/notes.txt")
        session.pending_tool_calls = [
            FakeToolCall("run_terminal", {"command": "cat Projects/notes.txt"}, call_id="call-1"),
            FakeToolCall("run_terminal", {"command": "cat Projects/notes.txt"}, call_id="call-2"),
        ]
        session.pending_tool_index = 0
        fake_response = SimpleNamespace(content="done", tool_calls=None)
        with patch.object(wrapper, "execute_tool", return_value="note doc") as mock_execute, patch.object(session, "_call_brain", return_value=fake_response):
            event = session._process_tool_queue(tool_config=base_config())
        assert event["type"] == "assistant_message"
        assert mock_execute.call_count == 1, mock_execute.call_count
        serialized = json.dumps(session.conversation, ensure_ascii=False)
        assert "already called this tool with the same arguments" in serialized, serialized
    finally:
        close_session(session)


def test_layer_prompt_is_soul_only() -> None:
    session = build_session("assistant")
    try:
        prompt = session.conversation[0]["content"]
        assert "identity for assistant" in prompt
        assert "Runtime Skills Index" not in prompt
    finally:
        close_session(session)


def main() -> None:
    print(f"{BOLD}OpenCompanion Integration Layer Suite (two-layer runtime){RESET}")
    print(f"Python: {sys.version}")
    print(f"Root: {ROOT}")
    print()

    section("Registry policy")
    run_test("companion layer exposes the current tool kernel", test_companion_permissions)
    run_test("assistant layer exposes the current tool kernel", test_assistant_permissions)

    section("Brain config")
    run_test("normalize_layer_name collapses legacy worker aliases", test_normalize_layer_name_collapses_legacy_workers)
    run_test("assistant inherits legacy worker brain settings", test_assistant_resolves_legacy_brain_keys)

    section("Runtime routing")
    run_test("companion exposes Windows host commands with confirmation metadata", test_companion_windows_terminal_is_exposed_with_confirmation_metadata)
    run_test("direct assistant invoke returns without companion rewrite", test_direct_assistant_invoke_returns_without_companion_rewrite)
    run_test("invoke_layer normalizes legacy assistant aliases", test_invoke_layer_normalizes_legacy_assistant_aliases)
    run_test("invoke_layer supports direct companion sends", test_invoke_layer_supports_direct_companion)
    run_test("image payload is dropped for text-only model", test_image_payload_is_dropped_for_text_only_model)
    run_test("image payload is kept for vision model", test_image_payload_is_kept_for_vision_model)
    run_test("companion tool definitions do not include layer handoffs", test_companion_tool_definitions_do_not_include_layer_handoffs)

    section("Prompting and tools")
    run_test("assistant toolset uses layer exposure plus minimal special gates", test_layer_toolset_is_exposed_with_minimal_special_gates)
    run_test("repeated identical tool calls are blocked in the same turn", test_repeated_identical_tool_calls_are_blocked)
    run_test("layer prompt is soul-only", test_layer_prompt_is_soul_only)

    success = summary()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
