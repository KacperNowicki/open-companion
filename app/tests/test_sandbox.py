"""Integration tests for the sandbox tools — calls real WSL, no mocks."""
from __future__ import annotations

import shutil
import sys
import types
import unittest

# ---------------------------------------------------------------------------
# Bootstrap: put the backend on sys.path so imports work when run directly.
# ---------------------------------------------------------------------------
import os

_BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

if "runtime_paths" not in sys.modules:
    stub = types.ModuleType("runtime_paths")
    from pathlib import Path
    stub.PROJECT_ROOT = Path(_BACKEND_DIR).parent.parent
    stub.PROFILE_ROOT = Path(_BACKEND_DIR).parent.parent
    stub.VAULT_DIR = Path(_BACKEND_DIR).parent.parent / "companion" / "vault"
    sys.modules["runtime_paths"] = stub

_TOOLS_DIR = os.path.join(_BACKEND_DIR, "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

# ---------------------------------------------------------------------------
# Detect WSL availability once for the whole module.
# ---------------------------------------------------------------------------
WSL_AVAILABLE = shutil.which("wsl") is not None


# ---------------------------------------------------------------------------
# Integration tests — skipped on machines without WSL.
# ---------------------------------------------------------------------------
@unittest.skipUnless(WSL_AVAILABLE, "WSL not available on this machine")
class TestSandboxIntegration(unittest.TestCase):

    def test_check_sandbox_status_returns_correct_structure(self):
        from builtin.sandbox import check_sandbox_status
        result = check_sandbox_status()
        self.assertIn("available", result)
        self.assertIn("installed", result)
        self.assertTrue(result["available"])  # WSL is present (we checked above)

    def test_sandbox_distro_is_installed(self):
        from builtin.sandbox import check_sandbox_status
        result = check_sandbox_status()
        self.assertTrue(result["installed"], "OpenCompanion-Sandbox distro should be installed")

    def test_run_terminal_whoami_returns_companion(self):
        from builtin.sandbox import run_terminal
        result = run_terminal("whoami")
        self.assertIn("companion", result)
        self.assertNotIn("root", result)

    def test_run_terminal_no_windows_drive_access(self):
        # Automount is disabled: /mnt/c either doesn't exist or is an empty
        # mount-point with no Windows files in it.  Either outcome is correct.
        from builtin.sandbox import run_terminal
        result = run_terminal(
            "if [ -d /mnt/c ]; then ls /mnt/c | wc -l; else echo ABSENT; fi"
        )
        # Acceptable: mount-point absent OR present but empty (0 files)
        self.assertTrue(
            "ABSENT" in result or result.strip() == "0",
            f"Expected /mnt/c to be absent or empty, got: {result}",
        )

    def test_run_terminal_vault_is_mounted(self):
        from builtin.sandbox import run_terminal
        result = run_terminal("ls /home/companion/vault")
        self.assertNotIn("No such file", result)

    def test_run_terminal_python_venv_works(self):
        from builtin.sandbox import run_terminal
        result = run_terminal("/home/companion/env/bin/python3 --version")
        self.assertIn("Python 3", result)

    def test_run_terminal_timeout_cap(self):
        from builtin.sandbox import run_terminal, MAX_TIMEOUT  # noqa: F401
        # Passing timeout > MAX_TIMEOUT should still complete (capped internally)
        result = run_terminal("echo hi", timeout_seconds=9999)
        self.assertIn("hi", result)


# ---------------------------------------------------------------------------
# Degradation tests — run even without WSL.
# ---------------------------------------------------------------------------
class TestSandboxNoWSL(unittest.TestCase):
    """Graceful-degradation tests that run regardless of WSL availability."""

    def test_run_terminal_returns_string_not_exception(self):
        from builtin.sandbox import run_terminal
        result = run_terminal("echo hi")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)


if __name__ == "__main__":
    unittest.main()
