#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = ROOT / "app"
BACKEND_DIR = APP_ROOT / "backend"

for candidate in (str(APP_ROOT), str(BACKEND_DIR), str(ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

if "keyring" not in sys.modules:
    sys.modules["keyring"] = types.SimpleNamespace(get_password=lambda *args, **kwargs: None)
if "openai" not in sys.modules:
    sys.modules["openai"] = types.SimpleNamespace(OpenAI=lambda *args, **kwargs: None)

from tests.helpers.setup import fail, ok, summary

import heartbeat


def _config() -> dict:
    return {
        "brain": {"provider": "gemma", "model": "qwen2.5:14b"},
        "heartbeat": {
            "enabled": False,
            "interval": 30,
            "only_when_idle": False,
            "idle_threshold_minutes": 5,
        },
    }


def _run(coro):
    return asyncio.run(coro)


def test_heartbeat_fires_after_interval() -> None:
    name = "heartbeat: stale interaction reaches the inference path"
    config = _config()
    wrapper_state = {"last_interaction_ts": 100.0, "companion_system_prompt": "Stay warm."}
    touched = {"called": False}

    async def fake_inference(description, cfg, emit_fn, state):
        touched["called"] = True
        assert description is None

    with mock.patch.object(heartbeat, "_heartbeat_inference", side_effect=fake_inference), mock.patch.object(heartbeat.time, "time", return_value=200.0):
        _run(heartbeat._run_tick(config, lambda payload: None, wrapper_state))

    assert touched["called"], "heartbeat did not fire for stale interaction"
    ok(name)


def test_heartbeat_is_suppressed_when_user_is_recently_active() -> None:
    name = "heartbeat: recent interaction suppresses the tick"
    config = _config()
    wrapper_state = {"last_interaction_ts": 180.0, "companion_system_prompt": "Stay warm."}
    touched = {"called": False}

    async def fake_inference(description, cfg, emit_fn, state):
        touched["called"] = True

    with mock.patch.object(heartbeat, "_heartbeat_inference", side_effect=fake_inference), mock.patch.object(heartbeat.time, "time", return_value=200.0):
        _run(heartbeat._run_tick(config, lambda payload: None, wrapper_state))

    assert not touched["called"], "heartbeat fired despite recent activity"
    ok(name)


def test_heartbeat_without_screen_context_still_reaches_llm_path() -> None:
    name = "heartbeat: no screen context still reaches the LLM path"
    config = _config()
    wrapper_state = {"last_interaction_ts": 0.0, "companion_system_prompt": "Stay warm."}
    emitted = []
    calls = []

    def fake_chat(client, config, messages, tools=None, layer_name="companion", stream_handler=None):
        calls.append({"layer": layer_name, "messages": messages})
        return SimpleNamespace(content="Checking in.", tool_calls=[])

    with mock.patch.object(heartbeat.brain, "create_client", return_value=object()), mock.patch.object(heartbeat.brain, "chat", side_effect=fake_chat), mock.patch.object(heartbeat.memory, "load_all_memories", return_value=""), mock.patch.object(heartbeat.time, "strftime", side_effect=["12:00", "Thursday"]), mock.patch.object(heartbeat.time, "time", return_value=3600.0):
        heartbeat._heartbeat_silent_streak = 0
        _run(heartbeat._heartbeat_inference(None, config, emitted.append, wrapper_state))

    assert calls, "LLM path was not reached"
    assert emitted and emitted[-1]["type"] == "assistant_message", emitted
    ok(name)


def test_silent_streak_adds_nudge_guidance() -> None:
    name = "heartbeat: silent streak adds nudge guidance after the threshold"
    config = _config()
    wrapper_state = {"last_interaction_ts": 0.0, "companion_system_prompt": "Stay warm."}
    prompts = []

    def fake_chat(client, config, messages, tools=None, layer_name="companion", stream_handler=None):
        prompts.append(messages[-1]["content"])
        return SimpleNamespace(content="SILENT", tool_calls=[])

    with mock.patch.object(heartbeat.brain, "create_client", return_value=object()), mock.patch.object(heartbeat.brain, "chat", side_effect=fake_chat), mock.patch.object(heartbeat.memory, "load_all_memories", return_value=""), mock.patch.object(heartbeat.time, "strftime", side_effect=["12:00", "Thursday"]), mock.patch.object(heartbeat.time, "time", return_value=3600.0):
        heartbeat._heartbeat_silent_streak = 2
        _run(heartbeat._heartbeat_inference(None, config, lambda payload: None, wrapper_state))

    assert prompts, "heartbeat prompt was never sent"
    assert "already answered silent for 2 heartbeat tick" in prompts[0].lower(), prompts[0]
    ok(name)


def test_start_stop_restart_lifecycle() -> None:
    name = "heartbeat: start stop restart manages the background thread lifecycle"
    config = _config()
    wrapper_state = {"last_interaction_ts": time.time(), "companion_system_prompt": "Stay warm."}
    started = []
    joined = []

    class FakeThread:
        def __init__(self, target=None, args=(), name=None, daemon=None):
            self.target = target
            self.args = args
            self.name = name
            self.daemon = daemon

        def start(self):
            started.append((self.name, self.daemon))

        def join(self, timeout=None):
            joined.append(timeout)

        def is_alive(self):
            return True

    original_active = heartbeat._heartbeat_active
    original_thread = heartbeat._heartbeat_thread
    try:
        heartbeat._heartbeat_active = False
        heartbeat._heartbeat_thread = None
        with mock.patch.object(heartbeat.threading, "Thread", FakeThread):
            heartbeat.start_heartbeat(config, lambda payload: None, wrapper_state)
            assert started and started[-1] == ("heartbeat-loop", True), started
            assert heartbeat._heartbeat_active is True
            heartbeat.restart_heartbeat(config, lambda payload: None, wrapper_state)
            assert len(started) == 2, started
            heartbeat.stop_heartbeat()
            assert joined, "stop_heartbeat did not join the previous thread"
            assert heartbeat._heartbeat_active is False
    finally:
        heartbeat._heartbeat_active = original_active
        heartbeat._heartbeat_thread = original_thread
    ok(name)


def run_all() -> bool:
    tests = [test_heartbeat_fires_after_interval, test_heartbeat_is_suppressed_when_user_is_recently_active, test_heartbeat_without_screen_context_still_reaches_llm_path, test_silent_streak_adds_nudge_guidance, test_start_stop_restart_lifecycle]
    for test in tests:
        try:
            test()
        except Exception as exc:
            fail(test.__name__, str(exc))
    return summary()


def main() -> int:
    return 0 if run_all() else 1


if __name__ == "__main__":
    sys.exit(main())
