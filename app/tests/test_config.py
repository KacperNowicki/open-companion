#!/usr/bin/env python3
"""
Config system tests - Python side.
Tests loadConfig(), DEFAULT_CONFIG completeness, deepMerge, and migration
by calling the JS config module via subprocess.

Run: python app/tests/test_config.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = ROOT / "app"
TEST_RESULTS_ROOT = ROOT / "app" / "tests" / "test-results"
TEST_RESULTS_ROOT.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(APP_ROOT))
from tests.helpers.setup import load_fixture, ok, fail, summary, subprocess_env  # noqa: E402


def run_node(script: str):
    """Run a Node.js snippet and return parsed JSON output."""
    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=subprocess_env(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"Node error: {result.stderr.strip()}")
    return json.loads(result.stdout.strip())


def run_python(script: str, env: dict[str, str] | None = None):
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=env or subprocess_env(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"Python error: {result.stderr.strip()}")
    return json.loads(result.stdout.strip())


def test_config_loads():
    name = "Config loads with all required keys"
    try:
        config = run_node(
            "const {loadConfig} = require('./config/index');"
            "console.log(JSON.stringify(loadConfig()));"
        )
        required = ["version", "companion", "brain", "memory", "heartbeat", "voice", "ui", "vault"]
        for key in required:
            assert key in config, f"Missing key: {key}"
        assert "vision" not in config, "stale vision block should not be present"
        assert "tool_rag" not in config, "stale tool_rag block should not be present"
        assert "animations" not in config, "stale animations block should not be present"
        assert "age" not in config.get("companion", {}), "stale companion.age should not be present"
        ok(name)
    except Exception as exc:
        fail(name, str(exc))


def test_memory_extraction_defaults():
    name = "memory extraction defaults to local source and gemma4:e4b model"
    try:
        result = run_node(
            "const {DEFAULT_CONFIG} = require('./config/defaults');"
            "const {runMigrations} = require('./config/migrate');"
            "const migrated = runMigrations({});"
            "console.log(JSON.stringify({defaultModel: DEFAULT_CONFIG.memory.extraction_model, defaultSource: DEFAULT_CONFIG.memory.extraction_source, migratedModel: migrated.memory.extraction_model, migratedSource: migrated.memory.extraction_source}));"
        )
        assert result["defaultModel"] == "gemma4:e4b", "DEFAULT_CONFIG should set extraction_model to gemma4:e4b"
        assert result["defaultSource"] == "local", "DEFAULT_CONFIG should set extraction_source to local"
        assert result["migratedModel"] == "gemma4:e4b", "migration should backfill extraction_model to gemma4:e4b"
        assert result["migratedSource"] == "local", "migration should backfill extraction_source to local"
        ok(name)
    except Exception as exc:
        fail(name, str(exc))


def test_tool_budget_defaults():
    name = "tool loop limits default to companion 20 and assistant 100"
    try:
        result = run_python(
            "import json\n"
            "import os\n"
            "import sys\n"
            f"sys.path.insert(0, os.path.join(r'{ROOT}', 'app', 'backend'))\n"
            "from tool_budget import resolve_budget\n"
            "print(json.dumps({\n"
            "  'companion': resolve_budget('companion').max_calls,\n"
            "  'assistant': resolve_budget('assistant').max_calls,\n"
            "  'fallback': resolve_budget('unknown').max_calls,\n"
            "}))\n",
        )
        assert result["companion"] == 20
        assert result["assistant"] == 100
        assert result["fallback"] == 20

        defaults = run_node(
            "const {DEFAULT_CONFIG} = require('./config/defaults');"
            "console.log(JSON.stringify({companion: DEFAULT_CONFIG.layers.companion.max_tool_iterations, assistant: DEFAULT_CONFIG.layers.assistant.max_tool_iterations}));"
        )
        assert defaults["companion"] == 20
        assert defaults["assistant"] == 100
        ok(name)
    except Exception as exc:
        fail(name, str(exc))


def test_vault_in_defaults():
    name = "vault block present in DEFAULT_CONFIG"
    try:
        vault = run_node(
            "const {DEFAULT_CONFIG} = require('./config/defaults');"
            "console.log(JSON.stringify(DEFAULT_CONFIG.vault));"
        )
        assert vault is not None, "vault block missing"
        assert "path" in vault, "vault.path missing"
        assert "enabled" in vault, "vault.enabled missing"
        ok(name)
    except Exception as exc:
        fail(name, str(exc))


def test_deep_merge_fills_missing():
    name = "deepMerge fills missing keys from defaults"
    try:
        result = run_node(
            "const {deepMerge, DEFAULT_CONFIG} = require('./config/index');"
            "const partial = {brain: {model: 'gpt-4o'}};"
            "const merged = deepMerge(DEFAULT_CONFIG, partial);"
            "console.log(JSON.stringify({model: merged.brain.model, provider: merged.brain.provider}));"
        )
        assert result["model"] == "gpt-4o", "User value should win"
        assert result["provider"] == "gemma", "Missing key should fall back to default"
        ok(name)
    except Exception as exc:
        fail(name, str(exc))


def test_deep_merge_non_mutation():
    name = "deepMerge does not mutate original"
    try:
        result = run_node(
            "const {deepMerge} = require('./config/index');"
            "const a = {x: {b: 1}};"
            "const b = {x: {c: 2}};"
            "const merged = deepMerge(a, b);"
            "console.log(JSON.stringify({has_original_c: Object.prototype.hasOwnProperty.call(a.x, 'c'), merged_b: merged.x.b, merged_c: merged.x.c}));"
        )
        assert result["has_original_c"] is False, "Original should not be mutated"
        assert result["merged_b"] == 1, "Original keys preserved in merge"
        assert result["merged_c"] == 2, "New keys added in merge"
        ok(name)
    except Exception as exc:
        fail(name, str(exc))


def test_migration_adds_version():
    name = "Migration adds version key to unversioned config"
    try:
        fixture = load_fixture("config_legacy.json")
        fixture_json = json.dumps(fixture)
        result = run_node(
            f"const {{runMigrations}} = require('./config/migrate');"
            f"const old = {fixture_json};"
            f"const migrated = runMigrations(old);"
            f"console.log(JSON.stringify({{version: migrated.version}}));"
        )
        assert result["version"] is not None, "Migration should add version key"
        ok(name)
    except Exception as exc:
        fail(name, str(exc))


def test_minimal_config_gets_defaults():
    name = "Minimal config gets defaults filled in"
    try:
        fixture = load_fixture("config_minimal.json")
        fixture_json = json.dumps(fixture)
        result = run_node(
            f"const {{deepMerge, DEFAULT_CONFIG}} = require('./config/index');"
            f"const minimal = {fixture_json};"
            f"const merged = deepMerge(DEFAULT_CONFIG, minimal);"
            f"console.log(JSON.stringify({{provider: merged.brain.provider, top_k: merged.memory.top_k, has_vault: Object.prototype.hasOwnProperty.call(merged, 'vault')}}));"
        )
        assert result["provider"] == "gemma", "provider should default to gemma"
        assert result["top_k"] == 5, "top_k should default to 5"
        assert result["has_vault"] is True, "vault block should be present"
        ok(name)
    except Exception as exc:
        fail(name, str(exc))


def test_runtime_paths_use_profile_assets():
    name = "backend runtime paths should resolve into the profile asset dir"
    try:
        project_root = tempfile.mkdtemp(prefix="oc-project-", dir=str(TEST_RESULTS_ROOT))
        profile_root = tempfile.mkdtemp(prefix="oc-profile-", dir=str(TEST_RESULTS_ROOT))
        result = run_python(
            "import json\n"
            "import os\n"
            "import sys\n"
            f"sys.path.insert(0, os.path.join(r'{ROOT}', 'app'))\n"
            "import backend.runtime_paths as rp\n"
            "print(json.dumps({\"PROJECT_ROOT\": str(rp.PROJECT_ROOT), \"PROFILE_ROOT\": str(rp.PROFILE_ROOT), \"RUNTIME_ASSETS_DIR\": str(rp.RUNTIME_ASSETS_DIR), \"KOKORO_MODEL_PATH\": str(rp.KOKORO_MODEL_PATH), \"KOKORO_VOICES_PATH\": str(rp.KOKORO_VOICES_PATH)}))\n",
            env={
                **subprocess_env(),
                "OPEN_COMPANION_PROJECT_ROOT": project_root,
                "OPEN_COMPANION_PROFILE_DIR": profile_root,
            },
        )

        expected_assets = os.path.join(os.path.abspath(profile_root), "runtime-assets")
        expected_kokoro = os.path.join(expected_assets, "kokoro")
        assert os.path.abspath(result["PROJECT_ROOT"]) == os.path.abspath(project_root)
        assert os.path.abspath(result["PROFILE_ROOT"]) == os.path.abspath(profile_root)
        assert os.path.abspath(result["RUNTIME_ASSETS_DIR"]) == expected_assets
        assert os.path.abspath(result["KOKORO_MODEL_PATH"]) == os.path.join(expected_kokoro, "kokoro-v1.0.onnx")
        assert os.path.abspath(result["KOKORO_VOICES_PATH"]) == os.path.join(expected_kokoro, "voices-v1.0.bin")
        ok(name)
    except Exception as exc:
        fail(name, str(exc))


def test_runtime_paths_kokoro_env_override():
    name = "explicit Kokoro env overrides should win over defaults"
    try:
        project_root = tempfile.mkdtemp(prefix="oc-project-", dir=str(TEST_RESULTS_ROOT))
        profile_root = tempfile.mkdtemp(prefix="oc-profile-", dir=str(TEST_RESULTS_ROOT))
        kokoro_model = os.path.join(profile_root, "custom-kokoro-model.onnx")
        kokoro_voices = os.path.join(profile_root, "custom-kokoro-voices.bin")

        result = run_python(
            "import json\n"
            "import os\n"
            "import sys\n"
            f"sys.path.insert(0, os.path.join(r'{ROOT}', 'app'))\n"
            "import backend.runtime_paths as rp\n"
            "print(json.dumps({\"KOKORO_MODEL_PATH\": str(rp.KOKORO_MODEL_PATH), \"KOKORO_VOICES_PATH\": str(rp.KOKORO_VOICES_PATH)}))\n",
            env={
                **subprocess_env(),
                "OPEN_COMPANION_PROJECT_ROOT": project_root,
                "OPEN_COMPANION_PROFILE_DIR": profile_root,
                "OPEN_COMPANION_KOKORO_MODEL_PATH": kokoro_model,
                "OPEN_COMPANION_KOKORO_VOICES_PATH": kokoro_voices,
            },
        )

        assert os.path.abspath(result["KOKORO_MODEL_PATH"]) == os.path.abspath(kokoro_model)
        assert os.path.abspath(result["KOKORO_VOICES_PATH"]) == os.path.abspath(kokoro_voices)
        ok(name)
    except Exception as exc:
        fail(name, str(exc))


if __name__ == "__main__":
    print("Config system tests (Python)\n")
    test_config_loads()
    test_memory_extraction_defaults()
    test_tool_budget_defaults()
    test_vault_in_defaults()
    test_deep_merge_fills_missing()
    test_deep_merge_non_mutation()
    test_migration_adds_version()
    test_minimal_config_gets_defaults()
    test_runtime_paths_use_profile_assets()
    test_runtime_paths_kokoro_env_override()
    success = summary()
    sys.exit(0 if success else 1)
