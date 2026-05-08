#!/usr/bin/env python3
from __future__ import annotations

import importlib
import json
import os
import shutil
import site
import sys
import types
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = ROOT / "app"
BACKEND_DIR = APP_ROOT / "backend"
USER_SITE = site.getusersitepackages()
EXTRA_SITE = ROOT / ".pytest-deps"
for candidate in (str(APP_ROOT), str(BACKEND_DIR), str(EXTRA_SITE) if EXTRA_SITE.exists() else "", USER_SITE):
    if candidate and candidate not in sys.path:
        sys.path.insert(0, candidate)

if "openai" not in sys.modules:
    fake_openai = types.ModuleType("openai")

    class _FakeOpenAI:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    fake_openai.OpenAI = _FakeOpenAI
    sys.modules["openai"] = fake_openai

from tests.helpers.setup import BOLD, RESET, fail, ok, section, summary

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
    "layer_skills",
    "session_summaries",
    "wrapper",
)
SCRATCH_ROOT = APP_ROOT / "tests" / "integration" / ".tmp-context-lifecycle"


def _deep_merge(base, override):
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = _deep_merge(merged.get(key), value)
        return merged
    if isinstance(override, list):
        return list(override)
    return override


def _clear_backend_modules() -> None:
    for base in MODULE_BASENAMES:
        sys.modules.pop(base, None)
        sys.modules.pop(f"backend.{base}", None)


def _load_modules() -> dict[str, object]:
    _clear_backend_modules()
    importlib.invalidate_caches()
    return {
        "brain": importlib.import_module("brain"),
        "memory": importlib.import_module("memory"),
        "layer_skills": importlib.import_module("layer_skills"),
        "session_summaries": importlib.import_module("session_summaries"),
        "wrapper": importlib.import_module("wrapper"),
    }


def _base_config() -> dict:
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    config.setdefault("brain", {})["provider"] = "gemma"
    config["brain"]["model"] = "qwen2.5:14b"
    config.setdefault("brain", {}).setdefault("layers", {})
    config["brain"]["layers"].setdefault("companion", {"provider": "", "model": ""})
    config["brain"]["layers"].setdefault("assistant", {"provider": "", "model": ""})
    config.setdefault("memory", {})["enabled"] = True
    config["memory"]["embedding_enabled"] = False
    config["memory"]["write_back_enabled"] = False
    config.setdefault("heartbeat", {})["enabled"] = False
    config.setdefault("voice", {})["tts_enabled"] = False
    config["voice"]["stt_enabled"] = False
    config.setdefault("context", {})
    config["context"]["session_summaries"] = {
        "enabled": True,
        "max_recent": 6,
        "max_injected": 2,
        "compact_after_messages": 40,
        "compact_after_tokens": 12000,
        "budget_tokens": 1200,
        "retain_recent_messages": 4,
    }
    config["context"]["skills"] = {
        "index_enabled": True,
        "max_full_skills": 2,
        "index_budget_tokens": 400,
        "full_budget_tokens": 1200,
    }
    return config


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


@contextmanager
def isolated_profile(config_patch: dict | None = None):
    original_test_mode = os.environ.get("OPEN_COMPANION_TEST_MODE")
    original_profile_dir = os.environ.get("OPEN_COMPANION_TEST_PROFILE_DIR")
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    temp_root = SCRATCH_ROOT / f"profile-{uuid.uuid4().hex}"
    temp_root.mkdir(parents=True, exist_ok=False)
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


class FakeClient:
    def health_check(self):
        return True


@contextmanager
def patched_runtime(modules: dict[str, object]):
    wrapper = modules["wrapper"]
    stack = ExitStack()

    def _noop_refresh_voice(self):
        self.tts = None
        self.stt = None

    stack.enter_context(mock.patch.object(wrapper.LayeredRuntime, "_warm_companion_runtime_async", lambda self: None))
    stack.enter_context(mock.patch.object(wrapper.LayeredRuntime, "_refresh_voice_engines", _noop_refresh_voice))
    stack.enter_context(mock.patch.object(wrapper.brain, "create_client", lambda _config, layer_name="companion": FakeClient()))
    stack.enter_context(mock.patch.object(wrapper.brain, "load_identity", lambda _config, layer_name="companion": f"identity for {layer_name}"))
    stack.enter_context(mock.patch.object(wrapper.soul_module, "ensure_soul_active", lambda _config: None))
    stack.enter_context(mock.patch.object(wrapper.dream, "maybe_trigger_dream", lambda *_args, **_kwargs: None))
    try:
        yield
    finally:
        stack.close()


@contextmanager
def temporary_skill_tree(modules: dict[str, object], files: dict[str, str]):
    layer_skills = modules["layer_skills"]
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    temp_root = SCRATCH_ROOT / f"skills-{uuid.uuid4().hex}"
    temp_root.mkdir(parents=True, exist_ok=False)
    try:
        for rel_path, content in files.items():
            target = temp_root / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        with mock.patch.object(layer_skills, "SKILLS_ROOT", temp_root), mock.patch.object(layer_skills, "ensure_skill_library", lambda: None):
            yield temp_root
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_session_reset_clears_raw_conversation_but_preserves_memory() -> None:
    with isolated_profile() as (_profile, _config, modules), patched_runtime(modules):
        wrapper = modules["wrapper"]
        memory = modules["memory"]
        runtime = wrapper.LayeredRuntime()
        try:
            memory.append_memory("The user likes coffee.")
            runtime.companion_session.conversation.extend([
                {"role": "user", "content": "Please remember that I like coffee."},
                {"role": "assistant", "content": "I'll keep that in mind."},
            ])

            result = runtime.reset_session("companion", preserve_summary=False)

            assert result["layer"] == "companion"
            assert len(runtime.companion_session.conversation) == 1
            assert runtime.companion_session.conversation[0]["role"] == "system"
            assert "coffee" in memory.MEMORY_PATH.read_text(encoding="utf-8").lower()
        finally:
            runtime._refresh_voice_engines()


def test_session_reset_with_preserve_summary_writes_artifact() -> None:
    with isolated_profile() as (_profile, _config, modules), patched_runtime(modules):
        wrapper = modules["wrapper"]
        session_summaries = modules["session_summaries"]
        runtime = wrapper.LayeredRuntime()
        runtime.assistant_session.conversation.extend([
            {"role": "user", "content": "Update the diagnostics notes."},
            {"role": "assistant", "content": "I updated the diagnostics notes and saved the file."},
        ])

        result = runtime.reset_session("assistant", preserve_summary=True)

        assert result["layer"] == "assistant"
        assert result["summary_paths"], result
        saved_path = Path(result["summary_paths"][0])
        assert saved_path.exists(), saved_path
        body = saved_path.read_text(encoding="utf-8")
        assert "# Session Summary" in body
        assert "## Major User Intents Handled" in body
        assert "assistant" in body.lower()
        listed = session_summaries.list_recent_summaries("assistant", limit=5)
        assert listed and Path(listed[0]["path"]) == saved_path


def test_failed_summary_generation_does_not_delete_conversation() -> None:
    with isolated_profile() as (_profile, _config, modules), patched_runtime(modules):
        wrapper = modules["wrapper"]
        runtime = wrapper.LayeredRuntime()
        runtime.companion_session.conversation.extend([
            {"role": "user", "content": "Track this thread."},
            {"role": "assistant", "content": "Working on it."},
        ])
        original = list(runtime.companion_session.conversation)

        with mock.patch.object(modules["session_summaries"], "write_session_summary", side_effect=RuntimeError("boom")):
            try:
                runtime.reset_session("companion", preserve_summary=True)
            except RuntimeError:
                pass
            else:
                raise AssertionError("reset_session should surface summary failures")

        assert runtime.companion_session.conversation == original


def test_compaction_trigger_creates_summary_and_trims_history() -> None:
    config_patch = {
        "context": {
            "session_summaries": {
                "enabled": True,
                "compact_after_messages": 2,
                "compact_after_tokens": 999999,
                "retain_recent_messages": 2,
            }
        }
    }
    with isolated_profile(config_patch=config_patch) as (_profile, config, modules), patched_runtime(modules):
        wrapper = modules["wrapper"]
        session = wrapper.LayerSession(config, "assistant", include_memory=False)
        try:
            session.conversation.extend([
                {"role": "user", "content": "First request about logs."},
                {"role": "assistant", "content": "Checked the logs."},
                {"role": "user", "content": "Second request about services."},
                {"role": "assistant", "content": "Restarted the service."},
            ])

            result = session.maybe_compact_session()

            assert result is not None
            assert Path(result["path"]).exists()
            non_system = [message for message in session.conversation if message.get("role") != "system"]
            assert len(non_system) == 2, non_system
        finally:
            stack = getattr(session, "_test_stack", None)
            if stack is not None:
                stack.close()


def test_summary_retrieval_returns_recent_and_relevant() -> None:
    with isolated_profile() as (_profile, _config, modules):
        session_summaries = modules["session_summaries"]
        coffee_conversation = [
            {"role": "user", "content": "Plan coffee notes."},
            {"role": "assistant", "content": "Saved the coffee checklist."},
        ]
        debug_conversation = [
            {"role": "user", "content": "Debug the updater flow."},
            {"role": "assistant", "content": "Traced the updater branch."},
        ]
        session_summaries.write_session_summary("companion", coffee_conversation, trigger="reset")
        session_summaries.write_session_summary("assistant", debug_conversation, trigger="reset")
        session_summaries.write_session_summary("companion", [
            {"role": "user", "content": "Debug something else."},
            {"role": "assistant", "content": "Checked it."},
        ], trigger="threshold")

        recent = session_summaries.retrieve_recent_summaries("companion", limit=2, budget_tokens=800)
        relevant = session_summaries.retrieve_relevant_summaries("companion", "coffee checklist", limit=1, recent_limit=5, budget_tokens=800)

        assert "Session Summary" in recent
        assert "coffee" in relevant.lower(), relevant
        assert "updater" not in relevant.lower(), relevant


def test_skill_index_contains_all_discovered_skills_and_only_selected_subset_is_loaded() -> None:
    with isolated_profile() as (_profile, config, modules), temporary_skill_tree(
        modules,
        {
            "shared/weather_workflows.md": "---\npurpose: Handle weather lookups and forecasts.\ntools: run_terminal\nsyntax: curl wttr.in\nargs: city\n---\n# Weather\n\nHandle weather lookups and forecasts.",
            "assistant/logs.md": "---\npurpose: Inspect logs and services for debugging.\ntools: run_terminal\nsyntax: tail -n 50 logfile\nargs: path\n---\n# Logs\n\nInspect logs and services for debugging.",
            "assistant/notes/SKILL.md": "---\npurpose: Write notes and checklists.\ntools: write_file\nsyntax: write_file path content\nargs: path, content\n---\n# Notes\n\nWrite a concise note to disk.",
        },
    ):
        layer_skills = modules["layer_skills"]
        discovered = layer_skills.discover_layer_skills("assistant")
        names = {item["name"].lower() for item in discovered}
        index_section = layer_skills.build_skill_index_section(discovered, budget_tokens=500)
        selected = layer_skills.select_relevant_skills("assistant", "check the logs please", config=config, skills=discovered)
        full_section = layer_skills.build_selected_skill_section(selected, budget_tokens=800)

        assert names == {"weather_workflows", "logs", "notes"}
        assert "**weather_workflows** (shared): Handle weather lookups and forecasts." in index_section
        assert "**logs** (assistant): Inspect logs and services for debugging." in index_section
        assert "**notes** (assistant): Write notes and checklists." in index_section
        assert "### logs" in full_section
        assert "# Logs" in full_section
        assert "# Weather" not in full_section
        assert "# Notes" not in full_section


def test_turn_freeze_keeps_soul_only_prompt_stable_during_tool_loop() -> None:
    with isolated_profile() as (_profile, config, modules), patched_runtime(modules):
        wrapper = modules["wrapper"]
        session = wrapper.LayerSession(config, "assistant", include_memory=True)
        try:
            session.conversation.append({"role": "user", "content": "Check the weather logs."})
            session._reset_turn_state("Check the weather logs.")
            session.conversation.append({"role": "user", "content": "Check the weather logs."})

            with mock.patch.object(modules["memory"], "get_memory_prompt_block", return_value="=== Relevant Memories ===\n- keep memory"), mock.patch.object(
                modules["layer_skills"],
                "prepare_skill_context",
                side_effect=[
                    {
                        "all_skills": [],
                        "selected_skills": [{"name": "weather", "content": "# Weather\nOne"}],
                        "index_section": "## Runtime Skills Index\n- weather",
                        "selected_section": "## Selected Runtime Skills\n# Weather\nOne",
                    },
                    {
                        "all_skills": [],
                        "selected_skills": [{"name": "logs", "content": "# Logs\nTwo"}],
                        "index_section": "## Runtime Skills Index\n- logs",
                        "selected_section": "## Selected Runtime Skills\n# Logs\nTwo",
                    },
                ],
            ) as mock_skills, mock.patch.object(
                modules["session_summaries"],
                "retrieve_relevant_summaries",
                side_effect=[
                    "## Relevant Session Summaries\nAlpha",
                    "## Relevant Session Summaries\nBeta",
                ],
            ) as mock_summaries:
                first = session._freeze_turn_context(config, "Check the weather logs.", 32768)
                second = session._freeze_turn_context(config, "Check the weather logs.", 32768)

            assert first == second
            assert "Runtime Skills Index" not in first
            assert "# Weather" not in first
            assert "Relevant Session Summaries" not in first
            assert mock_skills.call_count == 0
            assert mock_summaries.call_count == 0
        finally:
            stack = getattr(session, "_test_stack", None)
            if stack is not None:
                stack.close()


def test_turn_freeze_does_not_inject_compact_skill_index() -> None:
    config_patch = {"context": {"skills": {"max_full_skills": 0, "full_budget_tokens": 0}}}
    with isolated_profile(config_patch=config_patch) as (_profile, config, modules), patched_runtime(modules), temporary_skill_tree(
        modules,
        {
            "shared/weather_workflows.md": "---\npurpose: Handle forecasts.\ntools: run_terminal\nsyntax: curl wttr.in\nargs: city\n---\n# Weather\n\nFull weather body.",
            "assistant/logs.md": "---\npurpose: Inspect logs safely.\ntools: run_terminal\nsyntax: tail -n 50 logfile\nargs: path\n---\n# Logs\n\nFull logs body.",
        },
    ):
        wrapper = modules["wrapper"]
        session = wrapper.LayerSession(config, "assistant", include_memory=False)
        try:
            frozen = session._freeze_turn_context(config, "Check the logs.", 32768)
            assert "## Skills" not in frozen
            assert "Inspect logs safely." not in frozen
            assert "Full logs body." not in frozen
        finally:
            stack = getattr(session, "_test_stack", None)
            if stack is not None:
                stack.close()


def run_test(name: str, fn) -> None:
    try:
        fn()
        ok(name)
    except AssertionError as exc:
        fail(name, str(exc))
    except Exception as exc:
        fail(name, f"{type(exc).__name__}: {exc}")


def main() -> None:
    print(f"{BOLD}OpenCompanion Context Lifecycle Suite{RESET}")
    print(f"Python: {sys.version}")
    print(f"Root: {ROOT}")
    print()

    section("Session lifecycle")
    run_test("session reset clears raw conversation but preserves long-term memory", test_session_reset_clears_raw_conversation_but_preserves_memory)
    run_test("session reset with preserve_summary writes a summary artifact", test_session_reset_with_preserve_summary_writes_artifact)
    run_test("failed summary generation does not delete conversation", test_failed_summary_generation_does_not_delete_conversation)
    run_test("threshold compaction creates a summary and trims raw history", test_compaction_trigger_creates_summary_and_trims_history)
    run_test("session summary retrieval returns recent and relevant summaries", test_summary_retrieval_returns_recent_and_relevant)

    section("Lazy skills and frozen turns")
    run_test("skill index contains all discovered skills and only loads a selected subset", test_skill_index_contains_all_discovered_skills_and_only_selected_subset_is_loaded)
    run_test("soul-only prompt stays frozen for one tool loop", test_turn_freeze_keeps_soul_only_prompt_stable_during_tool_loop)
    run_test("compact skill index is not injected into frozen system prompt", test_turn_freeze_does_not_inject_compact_skill_index)

    success = summary()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
