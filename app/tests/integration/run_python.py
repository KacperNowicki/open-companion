#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
INTEGRATION_DIR = Path(__file__).resolve().parent

TEST_FILES = [
    INTEGRATION_DIR / "test_tools.py",
    INTEGRATION_DIR / "test_scheduler_realistic.py",
    INTEGRATION_DIR / "test_file_tools.py",
    INTEGRATION_DIR / "test_layers.py",
    INTEGRATION_DIR / "test_memory.py",
    INTEGRATION_DIR / "test_context_lifecycle.py",
    INTEGRATION_DIR / "test_heartbeat.py",
    INTEGRATION_DIR / "test_providers.py",
    INTEGRATION_DIR / "test_gemma.py",
    INTEGRATION_DIR / "test_debug_log.py",
]


def main() -> int:
    failed = False
    for path in TEST_FILES:
        print(f"\n== Python integration: {path.name} ==")
        result = subprocess.run(
            [sys.executable, str(path)],
            cwd=str(ROOT),
        )
        if result.returncode != 0:
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
