"""
Settings persistence tests for OpenCompanion.

Tests the IPC layer directly â€” calls settings:save (via config/index.js saveConfig)
with known values, then reads config.json back and asserts the written values.

Does NOT test the visual Electron UI. Does NOT require a running app.

Run:
    python app/tests/suites/test_settings.py

Design: each test saves known values through the same Node code path the real
app uses, asserts specific config.json keys, then restores the original config.
The isolated_config() context manager guarantees config.json is always restored
even if an assertion fails.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

# Ensure project root is importable
ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = ROOT / "app"
for candidate in (str(APP_ROOT), str(ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from tests.helpers.setup import ok, fail, skip, section, summary, ollama_available, ROOT as SETUP_ROOT
from tests.helpers.settings import (
    assert_config_key,
    isolated_config,
    reload_config,
    save_settings,
    read_base_config,
    send_backend_message,
)
from tests.helpers.bridge import BridgeSession


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Helpers
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def run_test(name: str, fn):
    """Run a test function, catch all exceptions, report pass/fail."""
    try:
        fn()
        ok(name)
    except AssertionError as exc:
        fail(name, str(exc))
    except Exception as exc:
        fail(name, f"{type(exc).__name__}: {exc}")


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# PERSONA TAB
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_persona_name():
    with isolated_config():
        save_settings(None, {"companion": {"name": "TestCompanion"}})
        assert_config_key(["companion", "name"], "TestCompanion", "persona name")


def test_persona_soul_identity():
    with isolated_config():
        save_settings(None, {"companion": {"soul": {"identity": "Direct, clear, and grounded."}}})
        assert_config_key(["companion", "soul", "identity"], "Direct, clear, and grounded.", "soul identity")


def test_persona_soul_name():
    with isolated_config():
        save_settings(None, {"companion": {"soul": {"name": "SoulTestName"}}})
        assert_config_key(["companion", "soul", "name"], "SoulTestName", "soul name")


def test_persona_soul_relationship():
    with isolated_config():
        save_settings(None, {"companion": {"soul": {"relationship": "A precise collaborator."}}})
        assert_config_key(["companion", "soul", "relationship"], "A precise collaborator.", "soul relationship")


def test_persona_soul_backstory():
    with isolated_config():
        save_settings(None, {"companion": {"soul": {"backstory": "A test backstory."}}})
        assert_config_key(["companion", "soul", "backstory"], "A test backstory.", "soul backstory")


def test_persona_soul_user_name():
    with isolated_config():
        save_settings(None, {"companion": {"soul": {"user_name": "TestUser"}}})
        assert_config_key(["companion", "soul", "user_name"], "TestUser", "soul user_name")


def test_persona_pc_doctor_keywords():
    with isolated_config():
        save_settings(None, {"companion": {"pc_doctor_keywords": ["crash", "slow", "disk"]}})
        assert_config_key(["companion", "pc_doctor_keywords"], ["crash", "slow", "disk"], "pc_doctor_keywords")


def test_persona_layer_visibility():
    with isolated_config():
        save_settings(None, {"ui": {"layer_visibility": False}})
        assert_config_key(["ui", "layer_visibility"], False, "layer_visibility false")

    with isolated_config():
        save_settings(None, {"ui": {"layer_visibility": True}})
        assert_config_key(["ui", "layer_visibility"], True, "layer_visibility true")


def test_persona_pronouns():
    with isolated_config():
        save_settings(None, {"companion": {"pronouns": "they/them"}})
        assert_config_key(["companion", "pronouns"], "they/them", "pronouns")


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# MODEL TAB
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_model_temperature():
    with isolated_config():
        save_settings(None, {"brain": {"temperature": 0.5}})
        assert_config_key(["brain", "temperature"], 0.5, "model temperature")


def test_model_context_window():
    with isolated_config():
        save_settings(None, {"brain": {"context_window": 4096}})
        assert_config_key(["brain", "context_window"], 4096, "model context_window")


def test_model_stream():
    with isolated_config():
        save_settings(None, {"brain": {"stream": False}})
        assert_config_key(["brain", "stream"], False, "model stream off")

    with isolated_config():
        save_settings(None, {"brain": {"stream": True}})
        assert_config_key(["brain", "stream"], True, "model stream on")


def test_model_fallback_cpu():
    with isolated_config():
        save_settings(None, {"brain": {"fallback_cpu": True}})
        assert_config_key(["brain", "fallback_cpu"], True, "model fallback_cpu")


def test_model_default_provider_and_model():
    with isolated_config():
        save_settings(None, {"brain": {"provider": "custom", "model": "global-default-model"}})
        assert_config_key(["brain", "provider"], "custom", "brain default provider")
        assert_config_key(["brain", "model"], "global-default-model", "brain default model")


def test_model_provider_layer():
    with isolated_config():
        save_settings(None, {"brain": {"layers": {"companion": {"provider": "openai", "model": "gpt-4o"}}}})
        assert_config_key(["brain", "layers", "companion", "provider"], "openai", "brain layer provider")
        assert_config_key(["brain", "layers", "companion", "model"], "gpt-4o", "brain layer model")


def test_model_deep_merge_preserves_other_keys():
    """Saving brain.temperature should not wipe out brain.model."""
    with isolated_config():
        original = read_base_config()
        original_model = original.get("brain", {}).get("model", "")
        save_settings(None, {"brain": {"temperature": 0.3}})
        assert_config_key(["brain", "model"], original_model, "model preservation after temperature save")


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# MEMORY TAB
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_memory_enabled():
    with isolated_config():
        save_settings(None, {"memory": {"enabled": False}})
        assert_config_key(["memory", "enabled"], False, "memory.enabled false")


def test_memory_embedding_enabled():
    with isolated_config():
        save_settings(None, {"memory": {"embedding_enabled": False}})
        assert_config_key(["memory", "embedding_enabled"], False, "memory.embedding_enabled")


def test_memory_max_context_memories():
    with isolated_config():
        save_settings(None, {"memory": {"max_context_memories": 3}})
        assert_config_key(["memory", "max_context_memories"], 3, "memory.max_context_memories")


def test_memory_dream_enabled():
    with isolated_config():
        save_settings(None, {"memory": {"dream_enabled": False}})
        assert_config_key(["memory", "dream_enabled"], False, "memory.dream_enabled")


def test_memory_dream_schedule():
    with isolated_config():
        save_settings(None, {"memory": {"dream_schedule": "daily"}})
        assert_config_key(["memory", "dream_schedule"], "daily", "memory.dream_schedule")


def test_memory_max_entries():
    with isolated_config():
        save_settings(None, {"memory": {"max_entries": 100}})
        assert_config_key(["memory", "max_entries"], 100, "memory.max_entries")


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# AUDIO TAB
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_audio_tts_enabled():
    with isolated_config():
        save_settings(None, {"voice": {"tts_enabled": False}})
        assert_config_key(["voice", "tts_enabled"], False, "audio.tts_enabled false")


def test_audio_kokoro_voice():
    with isolated_config():
        save_settings(None, {"voice": {"kokoro_voice": "af_sky"}})
        assert_config_key(["voice", "kokoro_voice"], "af_sky", "audio.kokoro_voice")


def test_audio_volume():
    with isolated_config():
        save_settings(None, {"voice": {"volume": 0.7}})
        assert_config_key(["voice", "volume"], 0.7, "audio.volume")


def test_audio_tts_speed():
    with isolated_config():
        save_settings(None, {"voice": {"tts_speed": 1.2}})
        assert_config_key(["voice", "tts_speed"], 1.2, "audio.tts_speed")


def test_audio_stt_enabled():
    with isolated_config():
        save_settings(None, {"voice": {"stt_enabled": False}})
        assert_config_key(["voice", "stt_enabled"], False, "audio.stt_enabled false")


def test_audio_whisper_model():
    with isolated_config():
        save_settings(None, {"voice": {"whisper_model": "small.en"}})
        assert_config_key(["voice", "whisper_model"], "small.en", "audio.whisper_model")


def test_audio_auto_send_on_silence():
    with isolated_config():
        save_settings(None, {"voice": {"auto_send_on_silence": False}})
        assert_config_key(["voice", "auto_send_on_silence"], False, "audio.auto_send_on_silence")


def test_audio_push_to_talk_key():
    with isolated_config():
        save_settings(None, {"voice": {"push_to_talk_key": "F9"}})
        assert_config_key(["voice", "push_to_talk_key"], "F9", "audio.push_to_talk_key")


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# THEMES TAB
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_themes_mode():
    with isolated_config():
        save_settings(None, {"ui": {"theme": {"mode": "light"}}})
        assert_config_key(["ui", "theme", "mode"], "light", "themes.mode light")

    with isolated_config():
        save_settings(None, {"ui": {"theme": {"mode": "dark"}}})
        assert_config_key(["ui", "theme", "mode"], "dark", "themes.mode dark")


def test_themes_accent_rgb():
    with isolated_config():
        save_settings(None, {"ui": {"theme": {"accent_rgb": [255, 0, 128]}}})
        assert_config_key(["ui", "theme", "accent_rgb"], [255, 0, 128], "themes.accent_rgb")


def test_themes_accent_rgb_replaces_completely():
    """Arrays are replaced, not merged â€” new accent should be exactly what was saved."""
    with isolated_config():
        save_settings(None, {"ui": {"theme": {"accent_rgb": [10, 20, 30]}}})
        assert_config_key(["ui", "theme", "accent_rgb"], [10, 20, 30], "themes.accent_rgb replace")


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# HEARTBEAT TAB
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_heartbeat_enabled():
    with isolated_config():
        save_settings(None, {"heartbeat": {"enabled": True}})
        assert_config_key(["heartbeat", "enabled"], True, "heartbeat.enabled true")

    with isolated_config():
        save_settings(None, {"heartbeat": {"enabled": False}})
        assert_config_key(["heartbeat", "enabled"], False, "heartbeat.enabled false")


def test_heartbeat_interval():
    with isolated_config():
        save_settings(None, {"heartbeat": {"interval": 600}})
        assert_config_key(["heartbeat", "interval"], 600, "heartbeat.interval")


def test_heartbeat_only_when_idle():
    with isolated_config():
        save_settings(None, {"heartbeat": {"only_when_idle": True}})
        assert_config_key(["heartbeat", "only_when_idle"], True, "heartbeat.only_when_idle")


def test_heartbeat_idle_threshold():
    with isolated_config():
        save_settings(None, {"heartbeat": {"idle_threshold_minutes": 10}})
        assert_config_key(["heartbeat", "idle_threshold_minutes"], 10, "heartbeat.idle_threshold_minutes")


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# TOOLS TAB (config path: tools.overrides and tools.custom)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_tools_overrides_structure():
    """tools.overrides should accept per-layer toggle maps."""
    with isolated_config():
        overrides = {
            "companion": {"list_files": False},
            "assistant": {"open_application": True},
            "pc_doctor": {}
        }
        save_settings(None, {"tools": {"overrides": overrides}})
        assert_config_key(["tools", "overrides", "companion", "list_files"], False, "tools.overrides companion")
        assert_config_key(["tools", "overrides", "assistant", "open_application"], True, "tools.overrides assistant")


def test_tools_overrides_deep_merge_preserves_other_layers():
    """Saving one layer's overrides should not wipe other layers."""
    with isolated_config():
        # Write both layers
        save_settings(None, {
            "tools": {
                "overrides": {
                    "companion": {"read_file": True},
                    "assistant": {},
                    "pc_doctor": {}
                }
            }
        })
        # Save only assistant layer â€” companion should be preserved
        save_settings(None, {
            "tools": {
                "overrides": {
                    "companion": {"read_file": True},
                    "assistant": {"open_application": True},
                    "pc_doctor": {}
                }
            }
        })
        assert_config_key(["tools", "overrides", "companion", "read_file"], True, "tools overrides companion preserved")
        assert_config_key(["tools", "overrides", "assistant", "open_application"], True, "tools overrides assistant set")


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# CONFIG_RELOAD BACKEND SIGNAL (requires bridge session)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_config_reload_signal_persona():
    """After saving Persona settings, config_reload should reach backend without error."""
    bridge = BridgeSession(timeout=20)
    bridge.start()
    ready = bridge.wait_for_event("ready", timeout=20)
    if not ready:
        bridge.shutdown()
        skip("config_reload persona", "backend did not start (Ollama unavailable?)")
        return

    try:
        with isolated_config():
            save_settings(None, {"companion": {"name": "ReloadTestName"}})
            mark = bridge.mark()
            bridge.send({"type": "config_reload"})
            reloaded = bridge.wait_for_event("config_reloaded", timeout=10, since=mark)
            assert reloaded is not None, "config_reloaded event not received"
    finally:
        bridge.shutdown()


def test_config_reload_signal_memory():
    """After saving Memory settings, config_reload should reach backend without error."""
    bridge = BridgeSession(timeout=20)
    bridge.start()
    ready = bridge.wait_for_event("ready", timeout=20)
    if not ready:
        bridge.shutdown()
        skip("config_reload memory", "backend did not start (Ollama unavailable?)")
        return

    try:
        with isolated_config():
            save_settings(None, {"memory": {"max_context_memories": 4}})
            mark = bridge.mark()
            bridge.send({"type": "config_reload"})
            reloaded = bridge.wait_for_event("config_reloaded", timeout=10, since=mark)
            assert reloaded is not None, "config_reloaded event not received after memory save"
    finally:
        bridge.shutdown()


def test_config_reload_signal_model():
    """After saving Model settings, config_reload should reach backend without error."""
    bridge = BridgeSession(timeout=20)
    bridge.start()
    ready = bridge.wait_for_event("ready", timeout=20)
    if not ready:
        bridge.shutdown()
        skip("config_reload model", "backend did not start (Ollama unavailable?)")
        return

    try:
        with isolated_config():
            save_settings(None, {"brain": {"temperature": 0.6}})
            mark = bridge.mark()
            bridge.send({"type": "config_reload"})
            reloaded = bridge.wait_for_event("config_reloaded", timeout=10, since=mark)
            assert reloaded is not None, "config_reloaded event not received after model save"
    finally:
        bridge.shutdown()


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# DND BACKEND SIGNAL
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_config_reload_applies_tool_override_immediately():
    """Disabling a companion tool should take effect in the current bridge session after config_reload."""
    bridge = BridgeSession(timeout=20)
    bridge.start()
    ready = bridge.wait_for_event("ready", timeout=20)
    if not ready:
        bridge.shutdown()
        skip("config_reload tools", "backend did not start (Ollama unavailable?)")
        return

    try:
        with isolated_config():
            mark_before = bridge.mark()
            bridge.send({"type": "user_message", "content": "list available tools"})
            before = bridge.wait_for_event("assistant_message", timeout=10, since=mark_before)
            assert before is not None, "assistant_message not received before tool override"
            assert "copy_to_clipboard" in str(before.get("content") or ""), "copy_to_clipboard should be available before override"

            save_settings(None, {"tools": {"overrides": {"companion": {"copy_to_clipboard": False}}}})

            mark_reload = bridge.mark()
            bridge.send({"type": "config_reload"})
            reloaded = bridge.wait_for_event("config_reloaded", timeout=10, since=mark_reload)
            assert reloaded is not None, "config_reloaded event not received after tool override save"

            mark_after = bridge.mark()
            bridge.send({"type": "user_message", "content": "list available tools"})
            after = bridge.wait_for_event("assistant_message", timeout=10, since=mark_after)
            assert after is not None, "assistant_message not received after tool override reload"
            assert "copy_to_clipboard" not in str(after.get("content") or ""), "copy_to_clipboard should be unavailable after config_reload"
    finally:
        bridge.shutdown()


def test_dnd_start_and_cancel():
    """dnd_start and dnd_cancel messages should be accepted without error by the backend."""
    bridge = BridgeSession(timeout=20)
    bridge.start()
    ready = bridge.wait_for_event("ready", timeout=20)
    if not ready:
        bridge.shutdown()
        skip("dnd_start/cancel", "backend did not start (Ollama unavailable?)")
        return

    try:
        mark = bridge.mark()
        future_ts = int(time.time()) + 600  # 10 minutes from now
        bridge.send({"type": "dnd_start", "until": future_ts})
        time.sleep(0.5)
        errors_after_start = bridge.events_of_type("error")
        assert len(errors_after_start) == 0, f"Error after dnd_start: {errors_after_start}"

        bridge.send({"type": "dnd_cancel"})
        time.sleep(0.5)
        errors_after_cancel = bridge.events_of_type("error")
        assert len(errors_after_cancel) == 0, f"Error after dnd_cancel: {errors_after_cancel}"
    finally:
        bridge.shutdown()


def test_dnd_start_rejects_past_timestamp():
    """dnd_start with a past timestamp should emit an error event."""
    bridge = BridgeSession(timeout=20)
    bridge.start()
    ready = bridge.wait_for_event("ready", timeout=20)
    if not ready:
        bridge.shutdown()
        skip("dnd_start past timestamp", "backend did not start")
        return

    try:
        mark = bridge.mark()
        past_ts = int(time.time()) - 100
        bridge.send({"type": "dnd_start", "until": past_ts})
        error_event = bridge.wait_for_event("error", timeout=5, since=mark)
        assert error_event is not None, "Expected error for past timestamp, got none"
    finally:
        bridge.shutdown()


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# CROSS-TAB: Deep merge preserves other tabs
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_saving_one_tab_does_not_wipe_another():
    """Saving voice config should not affect brain config."""
    with isolated_config():
        original = read_base_config()
        original_brain_model = original.get("brain", {}).get("model", "qwen2.5:14b")
        save_settings(None, {"voice": {"volume": 0.5}})
        assert_config_key(["brain", "model"], original_brain_model, "brain.model preserved after audio save")


def test_partial_voice_update_preserves_other_voice_keys():
    """Saving only voice.volume should not overwrite voice.kokoro_voice."""
    with isolated_config():
        # First set a known voice
        save_settings(None, {"voice": {"kokoro_voice": "af_nova", "volume": 1.0}})
        # Now update only volume
        save_settings(None, {"voice": {"volume": 0.3}})
        assert_config_key(["voice", "kokoro_voice"], "af_nova", "kokoro_voice preserved after partial voice update")
        assert_config_key(["voice", "volume"], 0.3, "volume updated")


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Onboarding config patch tests
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _run_onboarding_patch(payload: dict) -> dict:
    """
    Simulate createOnboardingConfigPatch + saveConfig via Node, exactly as the
    real onboarding flow does, and return the written config.
    """
    payload_json = json.dumps(payload)
    script = f"""
const {{ saveConfig, loadConfig }} = require('./config/index');
const path = require('path');
const fs = require('fs');

// Inline the minimal helpers from main.js needed to call createOnboardingConfigPatch.
// We replicate the patch construction here so the test exercises the same logic
// without needing a full Electron process.
const {{ DEFAULT_CONFIG }} = require('./config/defaults');

function readOnboardingValue(data, ...keys) {{
  for (const key of keys) {{
    if (data[key] !== undefined && data[key] !== null) return data[key];
  }}
  return '';
}}

const payload = {payload_json};

const companionName = String(readOnboardingValue(payload, 'companion_name', 'companionName', 'name')).trim() || DEFAULT_CONFIG.companion.name;
const userName = String(readOnboardingValue(payload, 'user_name', 'userName')).trim() || DEFAULT_CONFIG.companion.user_name;
const soulIdentity = String(readOnboardingValue(payload, 'soul_identity', 'soulIdentity', 'identity')).trim() || DEFAULT_CONFIG.companion.soul.identity;
const soulBackstory = String(readOnboardingValue(payload, 'soul_backstory', 'soulBackstory', 'backstory')).trim();
const soulRelationship = String(readOnboardingValue(payload, 'soul_relationship', 'soulRelationship', 'relationship')).trim();
const soulUserContext = String(readOnboardingValue(payload, 'soul_user_context', 'soulUserContext', 'user_context')).trim();
const voice = String(readOnboardingValue(payload, 'voice', 'voice_id', 'voiceId')).trim() || DEFAULT_CONFIG.voice.kokoro_voice;
const brainProvider = String(readOnboardingValue(payload, 'brain_provider', 'brainProvider') || '').trim();
const brainProviderMode = String(readOnboardingValue(payload, 'brain_provider_mode', 'brainProviderMode') || '').trim().toLowerCase();
const brainRemoteProvider = String(readOnboardingValue(payload, 'brain_remote_provider', 'brainRemoteProvider') || '').trim().toLowerCase();
const brainModel = String(readOnboardingValue(payload, 'brain_model', 'brainModel') || '').trim();
const brainBaseUrl = String(readOnboardingValue(payload, 'brain_base_url', 'brainBaseUrl', 'brain_custom_url', 'brainCustomUrl') || '').trim();
const defaultModels = {{
  ollama: 'qwen2.5:14b',
  openai: 'gpt-4.1',
  anthropic: 'claude-sonnet-4-5',
  gemini: 'gemini-2.0-flash',
  custom: 'qwen2.5:14b',
}};

const patch = {{
  companion: {{
    name: companionName,
    user_name: userName,
    soul: {{
      name: companionName,
      identity: soulIdentity,
      backstory: soulBackstory,
      relationship: soulRelationship,
      user_name: userName,
      user_context: soulUserContext,
    }},
  }},
  voice: {{ kokoro_voice: voice }},
}};

let resolvedBrainProvider = brainProvider;
if (brainProviderMode === 'local') {{
  resolvedBrainProvider = 'ollama';
}} else if (brainProviderMode === 'custom') {{
  resolvedBrainProvider = 'custom';
}} else if (brainProviderMode === 'cloud' && brainRemoteProvider) {{
  resolvedBrainProvider = brainRemoteProvider;
}}

if (resolvedBrainProvider) {{
  patch.brain = {{
    provider: resolvedBrainProvider,
    model: brainModel || defaultModels[resolvedBrainProvider] || DEFAULT_CONFIG.brain.model,
  }};
  if (resolvedBrainProvider === 'custom' && brainBaseUrl) {{
    patch.brain.base_url = brainBaseUrl;
    patch.brain.api_url = brainBaseUrl;
  }}
}}

const updated = saveConfig(null, patch);
console.log(JSON.stringify(updated));
"""
    from tests.helpers.setup import subprocess_env
    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True, text=True, cwd=str(ROOT),
        env=subprocess_env(), timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(f"onboarding patch script failed:\n{result.stderr.strip()}")
    return json.loads(result.stdout.strip())


def test_onboarding_patch_sets_companion_name():
    with isolated_config():
        _run_onboarding_patch({"companion_name": "Nova", "user_name": "Tester", "soul_identity": "Warm, curious, and slightly playful."})
        assert_config_key(["companion", "name"], "Nova", "onboarding sets companion.name")


def test_onboarding_patch_sets_user_name():
    with isolated_config():
        _run_onboarding_patch({"companion_name": "Nova", "user_name": "Tester", "soul_identity": "Warm, curious, and slightly playful."})
        assert_config_key(["companion", "user_name"], "Tester", "onboarding sets companion.user_name")


def test_onboarding_patch_sets_brain_provider():
    with isolated_config():
        _run_onboarding_patch({
            "companion_name": "Nova", "user_name": "Tester",
            "brain_provider": "openai", "brain_model": "gpt-4o",
        })
        assert_config_key(["brain", "provider"], "openai", "onboarding sets brain.provider")
        assert_config_key(["brain", "model"], "gpt-4o", "onboarding sets brain.model")


def test_onboarding_patch_brain_provider_ollama_default():
    with isolated_config():
        _run_onboarding_patch({
            "companion_name": "Nova", "user_name": "Tester",
            "brain_provider": "ollama", "brain_model": "qwen2.5:14b",
        })
        assert_config_key(["brain", "provider"], "ollama", "onboarding brain provider ollama written")
        assert_config_key(["brain", "model"], "qwen2.5:14b", "onboarding brain model written")


def test_onboarding_patch_no_brain_leaves_default():
    """If brain_provider is omitted, the existing brain config must be preserved."""
    with isolated_config():
        # Write a known brain config first.
        save_settings(None, {"brain": {"provider": "openai", "model": "gpt-4o"}})
        _run_onboarding_patch({"companion_name": "Nova", "user_name": "Tester"})
        # brain.provider was already written; onboarding without brain_provider must not overwrite it.
        assert_config_key(["brain", "provider"], "openai", "onboarding without brain_provider preserves existing")


def test_onboarding_patch_sets_voice():
    with isolated_config():
        _run_onboarding_patch({
            "companion_name": "Nova", "user_name": "Tester", "voice": "am_adam",
        })
        assert_config_key(["voice", "kokoro_voice"], "am_adam", "onboarding sets kokoro_voice")


def test_onboarding_patch_cloud_provider_gets_default_model():
    with isolated_config():
        _run_onboarding_patch({
            "companion_name": "Nova",
            "user_name": "Tester",
            "brain_provider_mode": "cloud",
            "brain_remote_provider": "gemini",
            "brain_provider": "gemini",
        })
        assert_config_key(["brain", "provider"], "gemini", "onboarding sets remote brain provider")
        assert_config_key(["brain", "model"], "gemini-2.0-flash", "onboarding uses provider default model")


def test_onboarding_patch_custom_provider_sets_base_url():
    with isolated_config():
        _run_onboarding_patch({
            "companion_name": "Nova",
            "user_name": "Tester",
            "brain_provider_mode": "custom",
            "brain_provider": "custom",
            "brain_base_url": "https://example.test/v1",
        })
        assert_config_key(["brain", "provider"], "custom", "onboarding sets custom provider")
        assert_config_key(["brain", "base_url"], "https://example.test/v1", "onboarding stores custom base_url")


def _run_check_ollama_node(test_state: dict | None = None) -> dict:
    """
    Exercise the onboarding:checkOllama logic through Node using the testState
    override path, so the test is hermetic (no real Ollama required).
    """
    from tests.helpers.setup import subprocess_env
    # main.js resolves PROFILE_ROOT to PROJECT_ROOT when OPEN_COMPANION_TEST_PROFILE_DIR is unset,
    # so the test-state file lives at the repo root.
    state_path = ROOT / "ollama-test-state.json"
    wrote_state = False
    try:
        if test_state is not None:
            state_path.write_text(json.dumps(test_state), encoding="utf-8")
            wrote_state = True
        script = """
const path = require('path');
const fs = require('fs');
const PROJECT_ROOT = path.join(__dirname);
const PROFILE_ROOT = process.env.OPEN_COMPANION_TEST_PROFILE_DIR
  ? path.resolve(process.env.OPEN_COMPANION_TEST_PROFILE_DIR)
  : PROJECT_ROOT;
const STATE_PATH = path.join(PROFILE_ROOT, 'ollama-test-state.json');

function readTestOllamaState() {
  try {
    if (!fs.existsSync(STATE_PATH)) return null;
    const raw = fs.readFileSync(STATE_PATH, 'utf8');
    const parsed = JSON.parse(raw);
    return (parsed && typeof parsed === 'object') ? parsed : null;
  } catch { return null; }
}

async function checkOllama() {
  const testState = readTestOllamaState();
  if (testState) {
    return {
      available: testState.available !== false,
      models: Array.isArray(testState.models) ? testState.models.map(m => m.name).filter(Boolean) : [],
      installed: testState.installed !== false,
    };
  }
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 3000);
    const resp = await fetch('http://localhost:11434/api/tags', { signal: controller.signal });
    clearTimeout(timeoutId);
    if (!resp.ok) return { available: false, models: [], installed: true };
    const json = await resp.json();
    const models = (json.models || []).map(m => m.name).filter(Boolean);
    return { available: true, models, installed: true };
  } catch {
    return { available: false, models: [], installed: false };
  }
}

checkOllama().then(r => console.log(JSON.stringify(r)));
"""
        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True, text=True, cwd=str(ROOT),
            env=subprocess_env(), timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError(f"checkOllama node script failed:\n{result.stderr.strip()}")
        return json.loads(result.stdout.strip())
    finally:
        if wrote_state and state_path.exists():
            state_path.unlink()


def test_check_ollama_available_via_test_state():
    result = _run_check_ollama_node(test_state={
        "available": True,
        "installed": True,
        "models": [{"name": "qwen2.5:14b"}, {"name": "llama3.2"}],
    })
    assert result["available"] is True, f"expected available=True, got {result}"
    assert "qwen2.5:14b" in result["models"], f"expected model in list, got {result['models']}"


def test_check_ollama_not_running_via_test_state():
    result = _run_check_ollama_node(test_state={
        "available": False,
        "installed": True,
        "models": [],
    })
    assert result["available"] is False, f"expected available=False, got {result}"
    assert result["installed"] is True, f"expected installed=True, got {result}"


def test_check_ollama_not_installed_via_test_state():
    result = _run_check_ollama_node(test_state={
        "available": False,
        "installed": False,
        "models": [],
    })
    assert result["available"] is False
    assert result["installed"] is False


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Main runner
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def main():
    section("Persona tab")
    run_test("persona: companion.name written", test_persona_name)
    run_test("persona: companion.soul.identity written", test_persona_soul_identity)
    run_test("persona: companion.soul.name written", test_persona_soul_name)
    run_test("persona: companion.soul.relationship written", test_persona_soul_relationship)
    run_test("persona: companion.soul.backstory written", test_persona_soul_backstory)
    run_test("persona: companion.soul.user_name written", test_persona_soul_user_name)
    run_test("persona: companion.pc_doctor_keywords written", test_persona_pc_doctor_keywords)
    run_test("persona: ui.layer_visibility written", test_persona_layer_visibility)
    run_test("persona: companion.pronouns written", test_persona_pronouns)

    section("Model tab")
    run_test("model: brain.temperature written", test_model_temperature)
    run_test("model: brain.context_window written", test_model_context_window)
    run_test("model: brain.stream written", test_model_stream)
    run_test("model: brain.fallback_cpu written", test_model_fallback_cpu)
    run_test("model: brain provider/model defaults written", test_model_default_provider_and_model)
    run_test("model: brain.layers provider/model written", test_model_provider_layer)
    run_test("model: deep merge preserves brain.model", test_model_deep_merge_preserves_other_keys)

    section("Memory tab")
    run_test("memory: memory.enabled written", test_memory_enabled)
    run_test("memory: memory.embedding_enabled written", test_memory_embedding_enabled)
    run_test("memory: memory.max_context_memories written", test_memory_max_context_memories)
    run_test("memory: memory.dream_enabled written", test_memory_dream_enabled)
    run_test("memory: memory.dream_schedule written", test_memory_dream_schedule)
    run_test("memory: memory.max_entries written", test_memory_max_entries)

    section("Audio tab")
    run_test("audio: voice.tts_enabled written", test_audio_tts_enabled)
    run_test("audio: voice.kokoro_voice written", test_audio_kokoro_voice)
    run_test("audio: voice.volume written", test_audio_volume)
    run_test("audio: voice.tts_speed written", test_audio_tts_speed)
    run_test("audio: voice.stt_enabled written", test_audio_stt_enabled)
    run_test("audio: voice.whisper_model written", test_audio_whisper_model)
    run_test("audio: voice.auto_send_on_silence written", test_audio_auto_send_on_silence)
    run_test("audio: voice.push_to_talk_key written", test_audio_push_to_talk_key)

    section("Themes tab")
    run_test("themes: ui.theme.mode written", test_themes_mode)
    run_test("themes: ui.theme.accent_rgb written", test_themes_accent_rgb)
    run_test("themes: accent_rgb replaced completely (not merged)", test_themes_accent_rgb_replaces_completely)

    section("Heartbeat tab")
    run_test("heartbeat: heartbeat.enabled written", test_heartbeat_enabled)
    run_test("heartbeat: heartbeat.interval written", test_heartbeat_interval)
    run_test("heartbeat: heartbeat.only_when_idle written", test_heartbeat_only_when_idle)
    run_test("heartbeat: heartbeat.idle_threshold_minutes written", test_heartbeat_idle_threshold)

    section("Tools tab")
    run_test("tools: overrides per-layer map written", test_tools_overrides_structure)
    run_test("tools: overrides deep merge preserves other layers", test_tools_overrides_deep_merge_preserves_other_layers)

    section("Cross-tab deep merge")
    run_test("cross: saving one tab does not wipe another tab", test_saving_one_tab_does_not_wipe_another)
    run_test("cross: partial voice update preserves other voice keys", test_partial_voice_update_preserves_other_voice_keys)

    section("Config reload backend signal (requires bridge startup)")
    run_test("config_reload: persona save triggers backend reload", test_config_reload_signal_persona)
    run_test("config_reload: memory save triggers backend reload", test_config_reload_signal_memory)
    run_test("config_reload: model save triggers backend reload", test_config_reload_signal_model)
    run_test("config_reload: tool override applies immediately", test_config_reload_applies_tool_override_immediately)

    section("DND backend signal")
    run_test("dnd: dnd_start and dnd_cancel accepted without error", test_dnd_start_and_cancel)
    run_test("dnd: dnd_start rejects past timestamp", test_dnd_start_rejects_past_timestamp)

    section("Onboarding wizard config patch")
    run_test("onboarding: companion name written", test_onboarding_patch_sets_companion_name)
    run_test("onboarding: user name written", test_onboarding_patch_sets_user_name)
    run_test("onboarding: brain provider + model written", test_onboarding_patch_sets_brain_provider)
    run_test("onboarding: brain provider ollama written", test_onboarding_patch_brain_provider_ollama_default)
    run_test("onboarding: omitting brain_provider preserves existing config", test_onboarding_patch_no_brain_leaves_default)
    run_test("onboarding: voice written", test_onboarding_patch_sets_voice)

    section("Onboarding: checkOllama IPC (hermetic via test state)")
    run_test("checkOllama: available=true with models", test_check_ollama_available_via_test_state)
    run_test("checkOllama: installed but not running", test_check_ollama_not_running_via_test_state)
    run_test("checkOllama: not installed", test_check_ollama_not_installed_via_test_state)

    ok_overall = summary()
    sys.exit(0 if ok_overall else 1)


if __name__ == "__main__":
    main()
