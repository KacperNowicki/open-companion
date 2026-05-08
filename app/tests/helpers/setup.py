"""
Shared test utilities for OpenCompanion tests.
Import from any test file: from tests.helpers.setup import ok, fail, skip, ROOT, ollama_available
"""

from __future__ import annotations

import json
import os
import site
import subprocess
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = ROOT / "app"
PYTHON = sys.executable
USER_SITE = site.getusersitepackages()
LOCAL_DEPS = ROOT / ".pytest-deps"

if USER_SITE and USER_SITE not in sys.path:
    sys.path.append(USER_SITE)


def _install_openai_test_stub() -> None:
    if "openai" in sys.modules:
        return
    try:
        import openai  # noqa: F401
        return
    except Exception:
        fake_openai = types.ModuleType("openai")

        class _FakeOpenAI:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

        fake_openai.OpenAI = _FakeOpenAI
        sys.modules["openai"] = fake_openai


_install_openai_test_stub()

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"
BOLD = "\033[1m"

passed: list[str] = []
failed: list[str] = []
skipped: list[str] = []


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
    print(
        f"{GREEN}{len(passed)} passed{RESET}  "
        f"{RED}{len(failed)} failed{RESET}  "
        f"{YELLOW}{len(skipped)} skipped{RESET}"
    )
    if failed:
        print(f"\n{RED}Failed:{RESET}")
        for name in failed:
            print(f"  - {name}")
    print()
    return len(failed) == 0


def subprocess_env() -> dict[str, str]:
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
    return env


def ollama_available() -> bool:
    """Check if Ollama is running and reachable."""
    try:
        with urlopen("http://localhost:11434/api/tags", timeout=3) as response:
            return response.status == 200
    except (URLError, OSError, ValueError):
        return False


def load_config() -> dict:
    """Load the merged runtime config (config.json + config.local.json over defaults)."""
    result = subprocess.run(
        [
            "node",
            "-e",
            "const {loadConfig} = require('./config/index'); console.log(JSON.stringify(loadConfig()));",
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=subprocess_env(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to load config: {result.stderr.strip()}")
    return json.loads(result.stdout.strip())


def load_fixture(name: str) -> dict:
    fixture_path = APP_ROOT / "tests" / "fixtures" / name
    with fixture_path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def read_json(path: Path) -> dict:
    with open(path, encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def deep_merge(base, override):
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = deep_merge(merged.get(key), value)
        return merged
    if isinstance(override, list):
        return list(override)
    return override


@contextmanager
def temporary_config(config_data: dict):
    config_path = ROOT / "config.json"
    original = config_path.read_text(encoding="utf-8")
    try:
        write_json(config_path, config_data)
        yield config_path
    finally:
        config_path.write_text(original, encoding="utf-8")


@contextmanager
def temporary_config_patch(patch: dict):
    config_path = ROOT / "config.json"
    original = config_path.read_text(encoding="utf-8")
    current = json.loads(original)
    updated = deep_merge(current, patch)
    try:
        write_json(config_path, updated)
        yield updated
    finally:
        config_path.write_text(original, encoding="utf-8")
