#!/usr/bin/env python3
from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = ROOT / "app"
BACKEND_DIR = APP_ROOT / "backend"
SCRATCH_ROOT = APP_ROOT / "tests" / "integration" / ".tmp-memory"

for candidate in (str(APP_ROOT), str(BACKEND_DIR), str(ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from tests.helpers.setup import fail, ok, summary

MODULE_BASENAMES = (
    "runtime_paths",
    "brain",
    "memory",
    "dream",
    "tool_registry",
    "soul",
    "heartbeat",
    "tts",
    "stt",
    "wrapper",
)


def _deep_merge(base, override):
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = _deep_merge(merged.get(key), value)
        return merged
    if isinstance(override, list):
        return list(override)
    return override


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _clear_backend_modules() -> None:
    for base in MODULE_BASENAMES:
        sys.modules.pop(base, None)
        sys.modules.pop(f"backend.{base}", None)


def _load_modules() -> dict[str, object]:
    _clear_backend_modules()
    importlib.invalidate_caches()
    return {"memory": importlib.import_module("memory"), "dream": importlib.import_module("dream")}


def _base_config() -> dict:
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    config.setdefault("brain", {})["provider"] = "gemma"
    config["brain"]["model"] = "qwen2.5:14b"
    config.setdefault("memory", {})["enabled"] = True
    config["memory"]["embedding_enabled"] = True
    config["memory"]["embedding_model"] = "nomic-embed-text"
    config.setdefault("heartbeat", {})["enabled"] = False
    config.setdefault("voice", {})["tts_enabled"] = False
    config["voice"]["stt_enabled"] = False
    return config


def _scratch_dir(prefix: str) -> Path:
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    path = SCRATCH_ROOT / f"{prefix}-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


@contextmanager
def isolated_profile(config_patch: dict | None = None):
    original_test_mode = os.environ.get("OPEN_COMPANION_TEST_MODE")
    original_profile_dir = os.environ.get("OPEN_COMPANION_TEST_PROFILE_DIR")
    temp_root = _scratch_dir("profile")
    config = _base_config()
    if config_patch:
        config = _deep_merge(config, config_patch)
    _write_json(temp_root / "config.json", config)
    os.environ["OPEN_COMPANION_TEST_MODE"] = "1"
    os.environ["OPEN_COMPANION_TEST_PROFILE_DIR"] = str(temp_root)
    modules = _load_modules()
    modules["memory"].init_memory()
    try:
        yield temp_root, config, modules
    finally:
        if original_test_mode is None:
            os.environ.pop("OPEN_COMPANION_TEST_MODE", None)
        else:
            os.environ["OPEN_COMPANION_TEST_MODE"] = original_test_mode
        if original_profile_dir is None:
            os.environ.pop("OPEN_COMPANION_TEST_PROFILE_DIR", None)
        else:
            os.environ["OPEN_COMPANION_TEST_PROFILE_DIR"] = original_profile_dir
        _clear_backend_modules()
        shutil.rmtree(temp_root, ignore_errors=True)


def _embedding_vector(text: str) -> list[float]:
    lowered = str(text or "").lower()
    return [1.0 if "coffee" in lowered else 0.0, 1.0 if "morning" in lowered else 0.0, 1.0 if "green" in lowered else 0.0, 1.0 if "microsoft" in lowered else 0.0, 1.0 if "google" in lowered else 0.0]


def _extract_main_js_snippet() -> str:
    source = (ROOT / "app" / "frontend" / "main.js").read_text(encoding="utf-8")

    def extract_const(name: str) -> str:
        marker = f"const {name} = "
        start = source.index(marker)
        end = source.index(";\n", start) + 2
        return source[start:end]

    def extract_function(name: str) -> str:
        marker = f"function {name}("
        start = source.index(marker)
        brace_start = source.index("{", start)
        depth = 0
        index = brace_start
        while index < len(source):
            char = source[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return source[start:index + 1]
            index += 1
        raise ValueError(f"Could not extract function {name}")

    parts = [
        "const fs = require('fs');",
        "const path = require('path');",
        extract_const("DEFAULT_MEMORY_TOPICS"),
        extract_const("MEMORY_RESET_FILE_NAMES"),
        extract_function("isValidMemoryFileName"),
        extract_function("normalizeMemoryFileContent"),
        extract_function("normalizeMarkdownFileContent"),
        extract_function("resetLongTermMemory"),
        extract_function("memoryTimestamp"),
        extract_function("normalizeImportedMemorySection"),
        extract_function("writeOnboardingImportFiles"),
    ]
    return "\n\n".join(parts)


def _run_node_memory_script(memory_dir: Path, soul_dir: Path, body: str) -> None:
    script = "const MEMORY_DIR = process.env.MEMORY_DIR;\nconst SOUL_ACTIVE_DIR = process.env.SOUL_ACTIVE_DIR;\n" + _extract_main_js_snippet() + "\n\n" + body + "\n"
    env = os.environ.copy()
    env["MEMORY_DIR"] = str(memory_dir)
    env["SOUL_ACTIVE_DIR"] = str(soul_dir)
    result = subprocess.run(["node", "-e", script], cwd=str(ROOT), env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(result.stderr.strip() or result.stdout.strip() or "Node memory script failed")


def test_memory_write_persists_to_memory_file() -> None:
    name = "memory: append_memory writes to memory.md"
    with isolated_profile() as (_profile, _config, modules):
        memory = modules["memory"]
        memory.append_memory("The user loves coffee.")
        contents = (memory.MEMORY_DIR / "memory.md").read_text(encoding="utf-8")
        assert "coffee" in contents.lower()
        assert "Long-term Memories" in memory.load_all_memories()
    ok(name)


def test_memory_retrieval_prefers_relevant_entries() -> None:
    name = "memory: retrieval returns the most relevant entries"
    with isolated_profile() as (_profile, config, modules):
        memory = modules["memory"]
        memory.append_memory("The user loves coffee.")
        memory.append_memory("The user hates early mornings.")
        memory.append_memory("The favorite color is green.")
        with mock.patch.object(memory, "_get_embedding", side_effect=lambda text, model: _embedding_vector(text)):
            result = memory.retrieve_relevant_memories("coffee", config, top_k=1)
        assert "coffee" in result.lower(), result
    ok(name)


def test_memory_deduplicates_semantic_variants() -> None:
    name = "memory: append_memory deduplicates semantic favorite-color variants"
    with isolated_profile() as (_profile, _config, modules):
        memory = modules["memory"]
        memory.append_memory("User's favorite color is purple")
        memory.append_memory("The user's favorite color is purple.")
        memory.append_memory("Favorite color is purple")

        lines = [line for line in (memory.MEMORY_DIR / "memory.md").read_text(encoding="utf-8").splitlines() if line.startswith("- ")]
        assert len(lines) == 1, lines
    ok(name)


def test_dream_consolidation_and_lockfile() -> None:
    name = "memory: dream consolidation writes cleaned output and honors the lockfile"
    with isolated_profile() as (_profile, config, modules):
        memory = modules["memory"]
        dream = modules["dream"]
        target = memory.MEMORY_DIR / "memory.md"
        target.write_text("# User\n\n- [2026-04-01T10:00:00+00:00] I work at Google.\n", encoding="utf-8")
        fake_response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="# User\n\n- [2026-04-02T10:00:00+00:00] I work at Microsoft now.\n"))])
        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: fake_response)))
        dream.run_dream(fake_client, config)
        updated = target.read_text(encoding="utf-8")
        assert "Microsoft" in updated, updated
        assert not dream.DREAM_LOCK_PATH.exists(), "dream lockfile was not released"
        target.write_text("# User\n\n- [2026-04-03T10:00:00+00:00] I work at Microsoft now.\n", encoding="utf-8")
        dream.DREAM_LOCK_PATH.touch(exist_ok=False)
        try:
            dream.run_dream(fake_client, config)
        finally:
            dream.DREAM_LOCK_PATH.unlink(missing_ok=True)
        assert "Microsoft" in target.read_text(encoding="utf-8")
    ok(name)


def test_reset_and_import_survival() -> None:
    name = "memory: reset clears memory.md and imported facts can be restored"
    memory_dir = _scratch_dir("memory-reset")
    soul_dir = _scratch_dir("soul-reset")
    try:
        (memory_dir / "memory.md").write_text("# Memory\n\n- old user fact\n- old recent fact\n", encoding="utf-8")
        (memory_dir / "notes.txt").write_text("keep me", encoding="utf-8")
        (soul_dir / "soul_companion.md").write_text("Soul stays intact.", encoding="utf-8")
        body = """
const parsed = writeOnboardingImportFiles({
  memory: 'My name is Alex\\nI love coffee\\nWe met through OpenCompanion',
  soul_companion: 'You are a warm companion.',
});
resetLongTermMemory();
writeOnboardingImportFiles(parsed);
const memoryText = fs.readFileSync(path.join(MEMORY_DIR, 'memory.md'), 'utf8');
if (!memoryText.includes('Alex')) throw new Error('memory import was not restored after reset');
if (!memoryText.includes('OpenCompanion')) throw new Error('relationship details were not merged into memory import');
if (memoryText.includes('old recent fact')) throw new Error('resetLongTermMemory left stale memory content behind');
if (!fs.readFileSync(path.join(MEMORY_DIR, 'notes.txt'), 'utf8').includes('keep me')) throw new Error('resetLongTermMemory removed non-memory files');
if (!fs.readFileSync(path.join(SOUL_ACTIVE_DIR, 'soul_companion.md'), 'utf8').includes('warm companion')) throw new Error('writeOnboardingImportFiles did not preserve the companion soul import');
"""
        _run_node_memory_script(memory_dir, soul_dir, body)
        ok(name)
    finally:
        shutil.rmtree(memory_dir, ignore_errors=True)
        shutil.rmtree(soul_dir, ignore_errors=True)


def run_all() -> bool:
    tests = [
        test_memory_write_persists_to_memory_file,
        test_memory_retrieval_prefers_relevant_entries,
        test_memory_deduplicates_semantic_variants,
        test_dream_consolidation_and_lockfile,
        test_reset_and_import_survival,
    ]
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
