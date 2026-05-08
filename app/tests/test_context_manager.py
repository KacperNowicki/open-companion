"""
Tests for context_manager, memory pruning budget, sliding window, and get_context_window.

Run: python app/tests/test_context_manager.py
"""
from __future__ import annotations

import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "app"
_BACKEND_DIR = str(APP_ROOT / "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# Stub runtime_paths before any backend imports
if "runtime_paths" not in sys.modules:
    stub = types.ModuleType("runtime_paths")
    _root = REPO_ROOT
    stub.PROJECT_ROOT = _root
    stub.PROFILE_ROOT = _root
    stub.MEMORY_DIR = _root / "companion" / "memory"
    stub.VAULT_DIR = _root / "companion" / "vault"
    stub.KEYCHAIN_SERVICE = "open-companion"
    stub.CONFIG_PATH = _root / "config.json"
    stub.LOCAL_CONFIG_PATH = _root / "config.local.json"
    stub.SOUL_DIR = _root / "companion" / "soul"
    stub.SOUL_ACTIVE_DIR = _root / "companion" / "soul" / "active"
    stub.SOUL_DEFAULTS_DIR = _root / "companion" / "soul" / "defaults"
    stub.ensure_runtime_dirs = lambda: None
    sys.modules["runtime_paths"] = stub


class TestCountTokens(unittest.TestCase):
    def setUp(self):
        import context_manager as cm
        self.cm = cm

    def test_empty_string(self):
        # Empty string → max(1, 0 // 4) = 1
        self.assertEqual(self.cm.count_tokens(""), 1)

    def test_short_text(self):
        # "hello" = 5 chars → 5 // 4 = 1
        self.assertEqual(self.cm.count_tokens("hello"), 1)

    def test_longer_text(self):
        text = "a" * 400  # 400 chars → 100 tokens
        self.assertEqual(self.cm.count_tokens(text), 100)

    def test_approximate(self):
        # 1 token ≈ 4 chars; result must be positive
        result = self.cm.count_tokens("The quick brown fox jumps over the lazy dog")
        self.assertGreater(result, 0)


class TestCountMessagesTokens(unittest.TestCase):
    def setUp(self):
        import context_manager as cm
        self.cm = cm

    def test_empty_list(self):
        self.assertEqual(self.cm.count_messages_tokens([]), 0)

    def test_single_message(self):
        msgs = [{"role": "user", "content": "a" * 100}]
        # 100 chars → 25 tokens + 4 overhead = 29
        self.assertEqual(self.cm.count_messages_tokens(msgs), 29)

    def test_list_content(self):
        msgs = [{"role": "user", "content": [{"text": "hello world"}]}]
        result = self.cm.count_messages_tokens(msgs)
        self.assertGreater(result, 0)

    def test_multiple_messages(self):
        msgs = [
            {"role": "user", "content": "a" * 40},
            {"role": "assistant", "content": "b" * 40},
        ]
        # Each: 40 chars → 10 tokens + 4 = 14; total = 28
        self.assertEqual(self.cm.count_messages_tokens(msgs), 28)


class TestPruneMemoriesToBudget(unittest.TestCase):
    def setUp(self):
        for mod in list(sys.modules.keys()):
            if mod == "memory":
                del sys.modules[mod]
        import memory as mem
        self.mem = mem

    def test_under_budget_unchanged(self):
        text = "- I like cats\n- User is a developer\n"
        result = self.mem.prune_memories_to_budget(text, budget_tokens=5000)
        self.assertEqual(result, text)

    def test_over_budget_pruned(self):
        line = "- " + "entry\n"
        text = line * 500  # well over any reasonable budget
        result = self.mem.prune_memories_to_budget(text, budget_tokens=50)
        self.assertLess(len(result), len(text))

    def test_never_returns_empty(self):
        result = self.mem.prune_memories_to_budget("- short entry\n", budget_tokens=1)
        self.assertGreater(len(result), 0)

    def test_returns_fallback_message_when_nothing_fits(self):
        # Extremely small budget forces everything out
        result = self.mem.prune_memories_to_budget("- x\n" * 200, budget_tokens=0)
        self.assertIn("pruned", result.lower())


class TestApplySlidingWindow(unittest.TestCase):
    """Test sliding window logic directly using the standalone context_manager module."""

    def setUp(self):
        import context_manager as cm
        self.cm = cm
        # These mirror the constants in wrapper.py
        self.MIN_GUARANTEED_MESSAGES = 4
        self.OUTPUT_RESERVE_TOKENS = 1024

    def _apply_sliding_window(self, conversation, system_prompt, user_input, context_window):
        """Local re-implementation of the sliding window logic for isolated testing."""
        output_reserve = self.OUTPUT_RESERVE_TOKENS
        system_tokens = self.cm.count_tokens(system_prompt)
        input_tokens = self.cm.count_tokens(user_input)
        non_system = [m for m in conversation if m.get("role") != "system"]
        conversation_budget = context_window - output_reserve - system_tokens - input_tokens

        if conversation_budget <= 0:
            return non_system[-self.MIN_GUARANTEED_MESSAGES:]

        trimmed = list(non_system)
        while (
            len(trimmed) > self.MIN_GUARANTEED_MESSAGES
            and self.cm.count_messages_tokens(trimmed) > conversation_budget
        ):
            trimmed.pop(0)
        return trimmed

    def _make_conversation(self, n_pairs):
        """Build n_pairs of user/assistant messages."""
        msgs = []
        for i in range(n_pairs):
            msgs.append({"role": "user", "content": f"message {i}"})
            msgs.append({"role": "assistant", "content": f"reply {i}"})
        return msgs

    def test_returns_full_when_within_budget(self):
        msgs = self._make_conversation(2)  # 4 messages
        result = self._apply_sliding_window(msgs, "system", "hi", context_window=100_000)
        self.assertEqual(result, msgs)

    def test_never_trims_below_minimum(self):
        # Very large conversation, tiny context window → must keep MIN_GUARANTEED_MESSAGES
        msgs = self._make_conversation(50)  # 100 messages
        result = self._apply_sliding_window(msgs, "system", "hi", context_window=512)
        self.assertGreaterEqual(len(result), self.MIN_GUARANTEED_MESSAGES)

    def test_trims_old_messages_first(self):
        msgs = self._make_conversation(10)  # 20 messages
        result = self._apply_sliding_window(msgs, "system", "hi", context_window=2000)
        # The result should be a suffix of the original (oldest removed first)
        self.assertEqual(result, msgs[len(msgs) - len(result):])

    def test_emergency_path_zero_budget(self):
        msgs = self._make_conversation(10)
        # context_window so small that budget goes negative
        result = self._apply_sliding_window(msgs, "x" * 10000, "y" * 10000, context_window=100)
        self.assertLessEqual(len(result), self.MIN_GUARANTEED_MESSAGES)


class TestGetContextWindow(unittest.TestCase):
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
        providers_base.split_system_messages = mock.MagicMock(return_value=("", []))
        providers_base.read_keyring_secret = mock.MagicMock(return_value="")
        sys.modules["providers.base"] = providers_base
        import brain as b
        self.brain = b
        b._model_max_context_cache.clear()

    def test_returns_integer_for_auto_when_ollama_responds(self):
        self.brain._model_max_context_cache["qwen2.5:14b"] = 32768
        config = {"brain": {"model": "qwen2.5:14b", "provider": "gemma", "context_window": "auto"}}
        result = self.brain.get_context_window(config, "companion")
        self.assertIsInstance(result, int)
        self.assertGreater(result, 0)

    def test_returns_configured_value_when_integer_set(self):
        config = {"brain": {"model": "qwen2.5:14b", "provider": "gemma", "context_window": 4096}}
        result = self.brain.get_context_window(config, "companion")
        self.assertEqual(result, 4096)

    def test_falls_back_to_default_on_missing_model(self):
        config = {"brain": {"model": "", "provider": "gemma", "context_window": "auto"}}
        result = self.brain.get_context_window(config, "companion")
        self.assertEqual(result, self.brain._DEFAULT_CONTEXT_WINDOW)

    def test_falls_back_to_default_on_ollama_error(self):
        # No cached entry, Ollama unreachable
        config = {"brain": {"model": "unknown-model", "provider": "gemma", "context_window": "auto"}}
        with mock.patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
            with mock.patch.dict("sys.modules", {"requests": None}):
                result = self.brain.get_context_window(config, "companion")
        self.assertEqual(result, self.brain._DEFAULT_CONTEXT_WINDOW)


class TestConfigMigrationContextWindow(unittest.TestCase):
    """Verify that migrate.js backfills brain.context_window correctly."""

    def test_migration_adds_context_window(self):
        import subprocess
        result = subprocess.run(
            ["node", "-e", """
const { runMigrations } = require('./config/migrate');
const { DEFAULT_CONFIG } = require('./config/defaults');
const m = runMigrations({ brain: { model: 'x' } });
const passed =
  m.brain.context_window === DEFAULT_CONFIG.brain.context_window &&
  DEFAULT_CONFIG.brain.context_window === 'auto';
process.stdout.write(JSON.stringify({ passed, value: m.brain.context_window }));
"""],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(data["passed"], f"context_window={data['value']!r} expected 'auto'")

    def test_migration_preserves_existing_context_window(self):
        import subprocess
        result = subprocess.run(
            ["node", "-e", """
const { runMigrations } = require('./config/migrate');
const m = runMigrations({ brain: { model: 'x', context_window: 8192 } });
process.stdout.write(JSON.stringify({ value: m.brain.context_window }));
"""],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["value"], 8192)


if __name__ == "__main__":
    unittest.main()
