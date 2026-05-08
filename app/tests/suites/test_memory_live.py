#!/usr/bin/env python3
"""
Live memory / dream / RAG tests for OpenCompanion.

These tests run real backend code against an isolated temp profile and temp
memory directory. No mocks are used.

Run:
    python app/tests/suites/test_memory_live.py
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import site
import sys
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

# TODO: Dream gating does not currently follow the architecture-doc "cheapest
# first" flow of timestamp check -> session scan -> lock acquire. It uses
# .dream_state.json counters and only acquires the lock inside run_dream().
ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = ROOT / "app"
BACKEND_DIR = APP_ROOT / "backend"
USER_SITE = site.getusersitepackages()
TEMP_PROFILE_ROOT = APP_ROOT / "tests" / ".tmp-test-profiles"
PYTEST_DEPS = ROOT / ".pytest-deps"

for candidate in (str(APP_ROOT), str(BACKEND_DIR), USER_SITE, str(PYTEST_DEPS) if PYTEST_DEPS.exists() else ""):
    if candidate and candidate not in sys.path:
        sys.path.insert(0, candidate)

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"
BOLD = "\033[1m"

passed: list[str] = []
failed: list[str] = []
skipped: list[str] = []

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
    return not failed


def run_test(name: str, fn) -> None:
    try:
        fn()
        ok(name)
    except AssertionError as exc:
        fail(name, str(exc))
    except Exception as exc:
        fail(name, f"{type(exc).__name__}: {exc}")


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
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _clear_backend_modules() -> None:
    for base in MODULE_BASENAMES:
        sys.modules.pop(base, None)
        sys.modules.pop(f"backend.{base}", None)


def _load_backend_modules() -> dict[str, object]:
    _clear_backend_modules()
    importlib.invalidate_caches()
    loaded = {
        "runtime_paths": importlib.import_module("runtime_paths"),
        "brain": importlib.import_module("brain"),
        "memory": importlib.import_module("memory"),
        "dream": importlib.import_module("dream"),
        "wrapper": importlib.import_module("wrapper"),
    }
    return loaded


def _default_test_config() -> dict:
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

    brain_cfg = config.setdefault("brain", {})
    brain_cfg["provider"] = "ollama"
    brain_cfg["model"] = "gemma4:e4b"
    brain_cfg["temperature"] = 0.0
    brain_cfg["max_tokens"] = 128

    memory_cfg = config.setdefault("memory", {})
    memory_cfg["enabled"] = True
    memory_cfg["max_context_memories"] = memory_cfg.get("max_context_memories", 5)
    memory_cfg["embedding_model"] = memory_cfg.get("embedding_model", "nomic-embed-text")

    config.setdefault("heartbeat", {})["enabled"] = False

    voice_cfg = config.setdefault("voice", {})
    voice_cfg["tts_enabled"] = False
    voice_cfg["stt_enabled"] = False
    voice_cfg["tts_provider"] = ""
    voice_cfg["stt_provider"] = ""

    return config


@contextmanager
def isolated_profile(config_patch: dict | None = None):
    original_test_mode = os.environ.get("OPEN_COMPANION_TEST_MODE")
    original_profile_dir = os.environ.get("OPEN_COMPANION_TEST_PROFILE_DIR")

    TEMP_PROFILE_ROOT.mkdir(parents=True, exist_ok=True)
    temp_root = TEMP_PROFILE_ROOT / f"oc-memory-live-{uuid.uuid4().hex}"
    temp_root.mkdir(parents=True, exist_ok=False)

    config = _default_test_config()
    if config_patch:
        config = _deep_merge(config, config_patch)

    _write_json(temp_root / "config.json", config)

    os.environ["OPEN_COMPANION_TEST_MODE"] = "1"
    os.environ["OPEN_COMPANION_TEST_PROFILE_DIR"] = str(temp_root)

    modules = _load_backend_modules()
    modules["memory"].init_memory()
    _write_json(
        modules["dream"].DREAM_STATE_PATH,
        {
            "last_consolidation_ts": time.time(),
            "conversations_since": 0,
        },
    )

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


def _ollama_request(path: str, payload: dict | None = None, timeout: int = 30) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"http://localhost:11434{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def _model_present(models: set[str], name: str) -> bool:
    return name in models or any(model.split(":", 1)[0] == name for model in models)


def ensure_ollama_ready() -> None:
    try:
        tags = _ollama_request("/api/tags", timeout=10)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AssertionError(f"Ollama is not reachable at http://localhost:11434: {exc}") from exc

    models = {
        str(item.get("name") or "").strip()
        for item in tags.get("models", [])
        if str(item.get("name") or "").strip()
    }

    chat_model = str(_default_test_config().get("brain", {}).get("model") or "gemma4:e4b")
    if not _model_present(models, chat_model):
        raise AssertionError(f"Required chat model is not installed in Ollama: {chat_model}")

    if _model_present(models, "nomic-embed-text"):
        return

    try:
        _ollama_request(
            "/api/pull",
            {"name": "nomic-embed-text", "stream": False},
            timeout=1800,
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AssertionError(f"Failed to pull nomic-embed-text via Ollama: {exc}") from exc

    refreshed = _ollama_request("/api/tags", timeout=10)
    refreshed_models = {
        str(item.get("name") or "").strip()
        for item in refreshed.get("models", [])
        if str(item.get("name") or "").strip()
    }
    if not _model_present(refreshed_models, "nomic-embed-text"):
        raise AssertionError("nomic-embed-text was requested but is still not installed")


def _memory_dir(modules: dict[str, object]) -> Path:
    return modules["runtime_paths"].MEMORY_DIR


def _seed_memory_section(
    modules: dict[str, object],
    heading: str,
    entries: list[str],
    description: str = "",
) -> Path:
    lines = [f"# {heading}"]
    if description:
        lines.append(description)
    if entries:
        lines.append("")
        lines.extend(f"- {entry}" for entry in entries)
    path = _memory_dir(modules) / "memory.md"
    existing = path.read_text(encoding="utf-8").rstrip() if path.exists() else ""
    rendered = "\n".join(lines).rstrip()
    if existing:
        path.write_text(existing + "\n\n" + rendered + "\n", encoding="utf-8")
    else:
        path.write_text(rendered + "\n", encoding="utf-8")
    return path


def _create_client(modules: dict[str, object], config: dict | None = None):
    active_config = config or modules["brain"].load_config()
    return modules["brain"].create_client(active_config)


def _wait_until(predicate, timeout: int = 120, interval: float = 0.25) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _rag_embedding_vector(text: str) -> list[float]:
    lowered = str(text or "").lower()
    return [
        1.0 if any(token in lowered for token in ("play", "game", "gaming", "roguelike", "survival")) else 0.0,
        1.0 if any(token in lowered for token in ("invoice", "frontend", "work", "project")) else 0.0,
    ]


def _snapshot_memory_files(modules: dict[str, object]) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(_memory_dir(modules).glob("*")):
        if path.is_file():
            snapshot[path.name] = path.read_text(encoding="utf-8")
    return snapshot


def _seed_recent_dream_state(modules: dict[str, object], hours_ago: float, conversations_since: int) -> None:
    ts = time.time() - (hours_ago * 3600)
    _write_json(
        modules["dream"].DREAM_STATE_PATH,
        {
            "last_consolidation_ts": ts,
            "conversations_since": conversations_since,
        },
    )


def test_ollama_prerequisites():
    ensure_ollama_ready()


def test_dream_gate_does_not_run():
    with isolated_profile() as (_, config, modules):
        path = _seed_memory_section(
            modules,
            "Recent Activity",
            [
                "[2026-02-01T10:00:00+00:00] User is learning Python.",
                "[2026-02-01T10:00:00+00:00] User is learning Python.",
            ],
            "Transient notes for dream gating.",
        )
        _seed_recent_dream_state(modules, hours_ago=25, conversations_since=4)
        before_content = path.read_text(encoding="utf-8")
        before_state = modules["dream"].DREAM_STATE_PATH.read_text(encoding="utf-8")

        modules["dream"].maybe_trigger_dream(_create_client(modules, config), config)
        time.sleep(1.5)

        after_content = path.read_text(encoding="utf-8")
        after_state = modules["dream"].DREAM_STATE_PATH.read_text(encoding="utf-8")
        assert before_content == after_content, "dream changed memory content even though the gate should fail"
        assert before_state == after_state, "dream state changed even though the gate should fail"


def test_dream_gate_runs_when_both_conditions_pass():
    with isolated_profile() as (_, config, modules):
        path = _seed_memory_section(
            modules,
            "Recent Activity",
            [
                "[2026-02-01T10:00:00+00:00] User is learning Python.",
                "[2026-02-01T10:00:00+00:00] User is learning Python.",
                "[2026-02-02T12:00:00+00:00] User is learning Python.",
            ],
            "Transient notes for dream gating.",
        )
        _seed_recent_dream_state(modules, hours_ago=25, conversations_since=5)
        before_content = path.read_text(encoding="utf-8")
        before_ts = _read_json(modules["dream"].DREAM_STATE_PATH)["last_consolidation_ts"]

        modules["dream"].maybe_trigger_dream(_create_client(modules, config), config)

        finished = _wait_until(
            lambda: (
                _read_json(modules["dream"].DREAM_STATE_PATH).get("conversations_since") == 0
                and _read_json(modules["dream"].DREAM_STATE_PATH).get("last_consolidation_ts", 0.0) > before_ts
                and not modules["dream"].DREAM_LOCK_PATH.exists()
            ),
            timeout=180,
        )
        assert finished, "dream did not finish after both gates passed"

        after_content = path.read_text(encoding="utf-8")
        assert after_content != before_content, "dream gate passed but the topic file did not change"


def test_dream_lockfile_exits_cleanly():
    with isolated_profile() as (_, config, modules):
        _seed_memory_section(
            modules,
            "Recent Activity",
            ["[2026-02-01T10:00:00+00:00] User is learning Python."],
        )
        modules["dream"].DREAM_LOCK_PATH.write_text("locked\n", encoding="utf-8")
        snapshot = _snapshot_memory_files(modules)

        modules["dream"].run_dream(_create_client(modules, config), config)

        assert snapshot == _snapshot_memory_files(modules), "dream modified memory while the lockfile was present"


def test_dream_resolves_contradiction_across_sections():
    with isolated_profile() as (_, config, modules):
        older = "[2026-01-05T09:00:00+00:00] User's favorite game genre is strategy games."
        newer = "[2026-02-20T09:00:00+00:00] User's favorite game genre is cozy farming games."
        _seed_memory_section(modules, "User Profile", [older], "Profile facts.")
        _seed_memory_section(modules, "Facts", [newer], "Misc facts.")

        modules["dream"].run_dream(_create_client(modules, config), config)

        combined = (_memory_dir(modules) / "memory.md").read_text(encoding="utf-8")
        assert "cozy farming games" in combined, "the newer contradictory fact should remain after dream consolidation"
        assert "strategy games" not in combined, "the older contradictory fact should be removed after dream consolidation"

def test_memory_semantic_retrieval_prefers_relevant_section():
    with isolated_profile() as (_, config, modules):
        _seed_memory_section(
            modules,
            "Gaming Preferences",
            ["User enjoys roguelike deckbuilders and co-op survival games."],
            "Long-term gaming preferences.",
        )
        _seed_memory_section(
            modules,
            "Work Projects",
            ["User is tracking invoices and frontend bug fixes for work projects."],
            "Work context.",
        )

        result = modules["memory"].retrieve_relevant_memories(
            "Which games fit someone who likes roguelike deckbuilders?",
            config,
            top_k=1,
        )
        assert "[Gaming Preferences]" in result, f"expected gaming topic to rank first, got: {result}"
        assert "[Work Projects]" not in result, f"irrelevant work topic should not be top-1, got: {result}"


def test_memory_retrieval_degrades_gracefully_when_embedding_unavailable():
    with isolated_profile(
        {
            "memory": {
                "embedding_model": "missing-embed-model-for-live-test",
            }
        }
    ) as (_, config, modules):
        _seed_memory_section(
            modules,
            "Gaming Preferences",
            ["User enjoys roguelike deckbuilders and co-op survival games."],
        )
        _seed_memory_section(
            modules,
            "Work Projects",
            ["User is tracking invoices and frontend bug fixes for work projects."],
        )

        result = modules["memory"].retrieve_relevant_memories(
            "Which games fit someone who likes roguelike deckbuilders?",
            config,
            top_k=2,
        )
        assert isinstance(result, str), "retrieval should return a string fallback instead of crashing"
        assert "=== Long-term Memories ===" in result, f"expected fallback full-memory load, got: {result}"


def test_memory_append_rejects_tool_capability_summary():
    with isolated_profile() as (_, _, modules):
        facts_path = _memory_dir(modules) / "memory.md"
        before = facts_path.read_text(encoding="utf-8")

        modules["memory"].append_memory("list_available_tools shows all available commands in the current layer.")

        after = facts_path.read_text(encoding="utf-8")
        assert after == before, "tool/capability summaries should not be persisted as long-term memory"


def test_memory_append_keeps_normal_durable_fact():
    with isolated_profile() as (_, _, modules):
        facts_path = _memory_dir(modules) / "memory.md"
        modules["memory"].append_memory("User prefers matcha lattes over coffee.")

        after = facts_path.read_text(encoding="utf-8")
        assert "User prefers matcha lattes over coffee." in after, "normal durable facts should still be persisted"


def test_memory_append_deduplicates_exact_and_subject_matches():
    with isolated_profile() as (_, _, modules):
        facts_path = _memory_dir(modules) / "memory.md"
        modules["memory"].append_memory("User prefers matcha lattes over coffee.")
        modules["memory"].append_memory("User prefers matcha lattes over coffee.")
        modules["memory"].append_memory("User prefers tea over coffee.")

        lines = [line for line in facts_path.read_text(encoding="utf-8").splitlines() if line.startswith("- ")]
        assert len(lines) == 1, f"expected dedupe to keep one entry, got {lines}"


def test_prompt_seed_includes_long_term_memory():
    with isolated_profile() as (_, _, modules):
        _seed_memory_section(
            modules,
            "Gaming Preferences",
            ["User enjoys roguelike deckbuilders and co-op survival games."],
            "Long-term gaming preferences.",
        )
        _seed_memory_section(
            modules,
            "Work Projects",
            ["User is tracking invoices and frontend bug fixes for work projects."],
            "Work context.",
        )

        prompt = modules["memory"].load_all_memories()

        assert "=== Long-term Memories ===" in prompt, "session-start prompt should include long-term memory"
        assert "Gaming Preferences" in prompt, "session-start prompt should include seeded gaming memory"
        assert "Work Projects" in prompt, "session-start prompt should include seeded work memory"


def test_prompt_refresh_prefers_most_relevant_memory():
    with isolated_profile({"memory": {"max_context_memories": 1}}) as (_, _, modules):
        _seed_memory_section(
            modules,
            "Gaming Preferences",
            ["User enjoys roguelike deckbuilders and co-op survival games."],
            "Long-term gaming preferences.",
        )
        _seed_memory_section(
            modules,
            "Work Projects",
            ["User is tracking invoices and frontend bug fixes for work projects."],
            "Work context.",
        )

        budget = modules["memory"].resolve_layer_memory_budget({"memory": {"max_context_memories": 1}}, "companion", None)
        with mock.patch.object(modules["memory"], "_get_embedding", side_effect=lambda text, model: _rag_embedding_vector(text)):
            refreshed_prompt = modules["memory"].retrieve_topic_bundle_memories(
                "What should I play tonight?",
                {"memory": {"embedding_model": "nomic-embed-text", "max_context_memories": 1}},
                budget,
            )

        assert "=== Relevant Memories ===" in refreshed_prompt, "prompt refresh should use relevant-memory retrieval"
        assert "[Gaming Preferences]" in refreshed_prompt, "refreshed prompt should include the most relevant gaming topic"


def test_rag_wrapper_omits_irrelevant_memory_after_relevant_refresh():
    with isolated_profile({"memory": {"max_context_memories": 1}}) as (_, _, modules):
        _seed_memory_section(
            modules,
            "Gaming Preferences",
            ["User enjoys roguelike deckbuilders and co-op survival games."],
            "Long-term gaming preferences.",
        )
        _seed_memory_section(
            modules,
            "Work Projects",
            ["User is tracking invoices and frontend bug fixes for work projects."],
            "Work context.",
        )

        runtime = modules["wrapper"].LayeredRuntime()
        runtime.submit_user_message("What should I play tonight?")
        refreshed_prompt = runtime.get_companion_system_prompt()

        assert "[Work Projects]" not in refreshed_prompt, (
            "irrelevant work memory should not be injected when max_context_memories=1"
        )


def main() -> None:
    print(f"{BOLD}OpenCompanion Live Memory / Dream / RAG Suite{RESET}")
    print(f"Python: {sys.version}")
    print(f"Root: {ROOT}")
    print()

    section("Prerequisites")
    run_test("ollama: live models available", test_ollama_prerequisites)

    section("Dream consolidation")
    run_test("dream: gate does not run with fewer than 5 conversations", test_dream_gate_does_not_run)
    run_test("dream: gate runs when both conditions pass", test_dream_gate_runs_when_both_conditions_pass)
    run_test("dream: lockfile exits cleanly", test_dream_lockfile_exits_cleanly)
    run_test("dream: contradiction resolves across memory sections", test_dream_resolves_contradiction_across_sections)

    section("Memory retrieval")
    run_test("memory: semantic retrieval prefers the relevant section", test_memory_semantic_retrieval_prefers_relevant_section)
    run_test(
        "memory: retrieval degrades gracefully when embedding model unavailable",
        test_memory_retrieval_degrades_gracefully_when_embedding_unavailable,
    )
    run_test(
        "memory: append_memory rejects tool capability summaries",
        test_memory_append_rejects_tool_capability_summary,
    )
    run_test(
        "memory: append_memory keeps normal durable facts",
        test_memory_append_keeps_normal_durable_fact,
    )
    run_test(
        "memory: append_memory deduplicates exact and subject matches",
        test_memory_append_deduplicates_exact_and_subject_matches,
    )

    section("Prompt injection")
    run_test("prompt: initial seed includes long-term memory", test_prompt_seed_includes_long_term_memory)
    run_test(
        "prompt: refresh prefers the most relevant memory",
        test_prompt_refresh_prefers_most_relevant_memory,
    )
    run_test(
        "prompt: irrelevant memory is not injected after relevant refresh",
        test_rag_wrapper_omits_irrelevant_memory_after_relevant_refresh,
    )

    success = summary()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
