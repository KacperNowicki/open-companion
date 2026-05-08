"""
Shared helpers for settings IPC tests.

Usage pattern:
  from tests.helpers.settings import save_settings, reload_config, read_config

All helpers call the Node settings:save IPC via a subprocess so tests
don't need a running Electron app. They write through the same
config/index.js code path the real app uses, and read back config.json
to assert the written value.
"""

from __future__ import annotations

import json
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from .setup import ROOT, subprocess_env, read_json, write_json, deep_merge

CONFIG_PATH = ROOT / "config.json"


def _node_script(script: str) -> str:
    """Run an inline Node script from the project root, return stdout."""
    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=subprocess_env(),
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Node script failed (exit {result.returncode}):\n"
            f"  stderr: {result.stderr.strip()}\n"
            f"  script: {script[:200]}"
        )
    return result.stdout.strip()


def save_settings(section: str, data: dict) -> dict:
    """
    Call settings:save(section, data) through the config/index.js saveConfig
    function exactly as the Electron main process does it.

    Returns the updated config as written to config.json.
    """
    data_json = json.dumps(data)
    script = f"""
const {{ saveConfig }} = require('./config/index');
const data = {data_json};
const updated = saveConfig(null, data);
console.log(JSON.stringify(updated));
"""
    raw = _node_script(script)
    return json.loads(raw)


def reload_config() -> dict:
    """Load the merged runtime config (config.json deep-merged over DEFAULT_CONFIG)."""
    script = "const {loadConfig} = require('./config/index'); console.log(JSON.stringify(loadConfig()));"
    return json.loads(_node_script(script))


def read_base_config() -> dict:
    """Read config.json directly (no defaults merge)."""
    return read_json(CONFIG_PATH)


@contextmanager
def isolated_config():
    """
    Context manager that saves and restores config.json around a test.
    Use this for every settings test so writes don't persist between tests.
    """
    original = CONFIG_PATH.read_text(encoding="utf-8")
    try:
        yield
    finally:
        CONFIG_PATH.write_text(original, encoding="utf-8")


def assert_config_key(expected_path: list[str], expected_value, label: str = "") -> None:
    """
    Read config.json and assert that the nested key at expected_path equals expected_value.
    Raises AssertionError with a descriptive message on mismatch.
    """
    config = read_base_config()
    node = config
    for key in expected_path:
        if not isinstance(node, dict) or key not in node:
            raise AssertionError(
                f"{label}: key path {expected_path!r} not found in config. "
                f"Node at this point: {node!r}"
            )
        node = node[key]

    if node != expected_value:
        raise AssertionError(
            f"{label}: expected config{expected_path} == {expected_value!r}, got {node!r}"
        )


def send_backend_message(bridge, msg_type: str, **kwargs) -> bool:
    """
    Send a typed message to a running BridgeSession and return True if it
    was accepted without an error event. Used for config_reload tests.
    """
    bridge.send({"type": msg_type, **kwargs})
    # Give the backend a moment to process
    import time
    time.sleep(0.5)
    errors = bridge.events_of_type("error")
    return not errors
