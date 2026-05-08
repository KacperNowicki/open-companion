"""
Tests for memory pruning and brain context window resolution.

Run: python app/tests/test_memory_pruning.py
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

# Bootstrap: put the backend on sys.path so imports work directly.
_BACKEND_DIR = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# Stub runtime_paths before importing memory or brain.
if "runtime_paths" not in sys.modules:
    stub = types.ModuleType("runtime_paths")
    _root = Path(_BACKEND_DIR).parent.parent
    stub.PROJECT_ROOT = _root
    stub.PROFILE_ROOT = _root
    stub.MEMORY_DIR = _root / "memory"
    stub.VAULT_DIR = _root / "vault"
    stub.KEYCHAIN_SERVICE = "open-companion"
    stub.CONFIG_PATH = _root / "config.json"
    stub.LOCAL_CONFIG_PATH = _root / "config.local.json"
    stub.SOUL_DIR = _root / "soul"
    stub.SOUL_ACTIVE_DIR = _root / "soul" / "active"
    stub.SOUL_DEFAULTS_DIR = _root / "soul" / "defaults"
    stub.ensure_runtime_dirs = lambda: None
    sys.modules["runtime_paths"] = stub


class TestPruneMemoriesToLimit(unittest.TestCase):
    """Unit tests for memory.prune_memories_to_limit."""

    def setUp(self):
        if "memory" in sys.modules:
            del sys.modules["memory"]
        import memory as mem
        self.mem = mem

    def test_short_text_unchanged(self):
        text = "=== Long-term Memories ===\n\n- I like cats\n- User is a developer\n"
        result = self.mem.prune_memories_to_limit(text, max_tokens=1500)
        self.assertEqual(result, text)

    def test_over_limit_truncated(self):
        long_text = "- entry\n" * 1000
        result = self.mem.prune_memories_to_limit(long_text, max_tokens=1500)
        self.assertLess(len(result), len(long_text))
        self.assertIn("[Memory truncated to fit context window]", result)

    def test_truncation_at_newline(self):
        line = "- " + "x" * 50 + "\n"
        text = line * 200
        result = self.mem.prune_memories_to_limit(text, max_tokens=1500)
        self.assertTrue(result.endswith("[Memory truncated to fit context window]"))
        before_notice = result[: result.rfind("\n\n[Memory truncated")]
        self.assertNotIn("- " + "x" * 51, before_notice)

    def test_exact_limit_unchanged(self):
        text = "x" * (1500 * 4)
        result = self.mem.prune_memories_to_limit(text, max_tokens=1500)
        self.assertEqual(result, text)

    def test_custom_max_tokens(self):
        text = "- entry\n" * 100
        result = self.mem.prune_memories_to_limit(text, max_tokens=10)
        self.assertIn("[Memory truncated to fit context window]", result)
        self.assertLess(len(result), len(text) + 50)

    def test_empty_string_unchanged(self):
        self.assertEqual(self.mem.prune_memories_to_limit(""), "")


class TestGetModelMaxContext(unittest.TestCase):
    """Unit tests for brain.get_model_max_context."""

    def setUp(self):
        for mod in list(sys.modules.keys()):
            if mod.startswith("providers") or mod == "brain":
                del sys.modules[mod]
        providers_stub = types.ModuleType("providers")
        providers_stub.create_client = mock.MagicMock()
        providers_stub.normalize_provider_name = lambda x: str(x or "gemma").strip().lower()
        sys.modules["providers"] = providers_stub
        providers_base = types.ModuleType("providers.base")
        providers_base.BrainClientShim = object
        providers_base.BrainMessage = object
        providers_base.BrainProvider = object
        providers_base.is_ollama_backed_provider = lambda provider: str(provider or "").strip().lower() in {"gemma", "qwen", "ollama"}
        providers_base.split_system_messages = mock.MagicMock(return_value=("", []))
        providers_base.read_keyring_secret = mock.MagicMock(return_value="")
        sys.modules["providers.base"] = providers_base
        import brain as b
        self.brain = b
        b._model_max_context_cache.clear()

    def test_returns_context_length_from_ollama(self):
        response_data = {
            "model_info": {"llama.context_length": 8192},
            "details": {},
        }
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = mock.MagicMock()
            mock_resp.read.return_value = __import__("json").dumps(response_data).encode()
            mock_urlopen.return_value.__enter__ = lambda s: mock_resp
            mock_urlopen.return_value.__exit__ = mock.MagicMock(return_value=False)
            result = self.brain.get_model_max_context("qwen2.5:14b")
        self.assertEqual(result, 8192)

    def test_falls_back_to_default_context_on_error(self):
        with mock.patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
            result = self.brain.get_model_max_context("unknown-model")
        self.assertEqual(result, self.brain._DEFAULT_CONTEXT_WINDOW)

    def test_caches_result(self):
        response_data = {"model_info": {"llama.context_length": 16384}, "details": {}}
        call_count = 0

        def fake_urlopen(req, timeout=5):
            nonlocal call_count
            call_count += 1
            mock_resp = mock.MagicMock()
            mock_resp.read.return_value = __import__("json").dumps(response_data).encode()
            ctx = mock.MagicMock()
            ctx.__enter__ = lambda s: mock_resp
            ctx.__exit__ = mock.MagicMock(return_value=False)
            return ctx

        with mock.patch("requests.post", side_effect=Exception("use stdlib fallback")), mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            r1 = self.brain.get_model_max_context("mymodel")
            r2 = self.brain.get_model_max_context("mymodel")
        self.assertEqual(r1, 16384)
        self.assertEqual(r2, 16384)
        self.assertEqual(call_count, 1)

    def test_resolve_num_ctx_auto(self):
        self.brain._model_max_context_cache["mymodel"] = 32768
        result = self.brain.resolve_num_ctx({"context_window": "auto", "model": "mymodel", "provider": "gemma"})
        self.assertEqual(result, 32768)

    def test_resolve_num_ctx_numeric(self):
        result = self.brain.resolve_num_ctx({"context_window": 4096, "model": "mymodel", "provider": "gemma"})
        self.assertEqual(result, 4096)

    def test_resolve_num_ctx_zero_returns_none(self):
        result = self.brain.resolve_num_ctx({"context_window": 0, "model": "mymodel"})
        self.assertIsNone(result)

    def test_resolve_num_ctx_missing_returns_none_or_queries(self):
        result = self.brain.resolve_num_ctx({"provider": "gemma"})
        self.assertIsNone(result)


class TestLayerMemoryBudgets(unittest.TestCase):
    def setUp(self):
        if "memory" in sys.modules:
            del sys.modules["memory"]
        import memory as mem
        self.mem = mem

    def test_resolve_layer_memory_budget_uses_layer_percent_and_clamps(self):
        config = {
            "memory": {
                "layer_budgets": {
                    "assistant": {"percent": 0.25, "min_tokens": 2000, "max_tokens": 6000}
                }
            }
        }
        result = self.mem.resolve_layer_memory_budget(config, "assistant", remaining_prompt_budget_tokens=40000)
        self.assertEqual(result, 6000)

    def test_resolve_layer_memory_budget_returns_max_without_remaining_budget(self):
        config = {
            "memory": {
                "layer_budgets": {
                    "assistant": {"percent": 0.10, "min_tokens": 1024, "max_tokens": 4096}
                }
            }
        }
        result = self.mem.resolve_layer_memory_budget(config, "assistant", remaining_prompt_budget_tokens=None)
        self.assertEqual(result, 4096)

    def test_build_system_prompt_uses_topic_bundle_for_retrieval_ready_turns(self):
        config = {"memory": {"enabled": True}}
        with mock.patch.object(self.mem, "_retrieval_preload_completed", True), \
             mock.patch.object(self.mem, "retrieve_topic_bundle_memories", return_value="=== Relevant Memories ===\n- project context") as mock_bundle:
            prompt = self.mem.build_system_prompt(
                "soul",
                config,
                user_message="build a project note",
                memory_budget_tokens=3333,
                layer_name="assistant",
            )
        self.assertIn("soul", prompt)
        self.assertIn("project context", prompt)
        mock_bundle.assert_called_once_with("build a project note", config, 3333)

    def test_build_system_prompt_falls_back_to_full_memory_when_retrieval_not_ready(self):
        config = {"memory": {"enabled": True}}
        with mock.patch.object(self.mem, "_retrieval_preload_completed", False), \
             mock.patch.object(self.mem, "load_all_memories", return_value="- remembered thing"), \
             mock.patch.object(self.mem, "prune_memories_to_budget", return_value="- remembered thing") as mock_prune:
            prompt = self.mem.build_system_prompt(
                "soul",
                config,
                user_message="build a project note",
                memory_budget_tokens=2048,
                layer_name="companion",
            )
        self.assertIn("remembered thing", prompt)
        mock_prune.assert_called()


if __name__ == "__main__":
    unittest.main()
