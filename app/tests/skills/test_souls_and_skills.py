from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = ROOT / "app"
BACKEND_DIR = APP_ROOT / "backend"
for candidate in (str(APP_ROOT), str(BACKEND_DIR), str(ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)


DEFAULT_DIR = ROOT / "companion" / "soul" / "defaults"
ACTIVE_DIR = ROOT / "companion" / "soul" / "active"


def headings(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("#")]


def test_default_souls_are_anonymized() -> None:
    fixture = ROOT / "app" / "tests" / "skills" / "soul_blocklist.txt"
    extra = []
    if fixture.exists():
        extra = [line.strip() for line in fixture.read_text(encoding="utf-8").splitlines() if line.strip()]
    blocked = ["PrivateUserName", "PrivateCompanionName", "PrivateProfileName", *extra]
    for path in DEFAULT_DIR.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        for value in blocked:
            assert value not in text, f"{path.name} contains blocked default-soul string: {value}"


def test_active_souls_follow_default_section_structure() -> None:
    for name in ("shared_runtime_contract.md", "soul_companion.md", "soul_assistant.md"):
        assert headings(ACTIVE_DIR / name) == headings(DEFAULT_DIR / name)


def test_scheduler_skill_uses_append_file_not_shell_echo() -> None:
    text = (ROOT / "companion" / "skills" / "companion" / "scheduler_workflows.md").read_text(encoding="utf-8")
    assert "append_file" in text
    assert "echo " not in text
