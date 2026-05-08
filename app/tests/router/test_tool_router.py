from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = ROOT / "app"
BACKEND_DIR = APP_ROOT / "backend"
for candidate in (str(APP_ROOT), str(BACKEND_DIR), str(ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from tool_router import classify_tool_route, routed_tools_for_turn


def names_for(text: str, layer: str = "companion") -> set[str]:
    route = classify_tool_route(text, layer)
    return {tool["name"] for tool in routed_tools_for_turn(layer, route, config={})}


def test_simple_math_uses_layer_default_tools() -> None:
    assert classify_tool_route("what is 2 + 2?", "companion").name == "companion_default"
    names = names_for("what is 2 + 2?")
    assert "read_clipboard" not in names
    assert "help_skill" not in names
    assert "list_skills" not in names
    assert "write_skill" not in names
    assert "delete_skill" not in names


def test_memory_prompt_uses_layer_membership_not_semantic_tool_pruning() -> None:
    names = names_for("Remember that my favorite color is electric blue.")
    assert {"write_memory", "search_memories"}.issubset(names)
    assert "run_terminal" in names


def test_clipboard_is_explicit_only() -> None:
    assert "read_clipboard" not in names_for("what is on my mind?")
    assert "read_clipboard" not in names_for("read my clipboard")


def test_skill_write_delete_are_explicit_only() -> None:
    assert "write_skill" not in names_for("what skills do you have?")
    assert "delete_skill" not in names_for("what skills do you have?")
    explicit = names_for("create a skill called planner")
    assert "write_skill" not in explicit
    assert "delete_skill" not in explicit


def test_windows_host_tools_keep_approval_metadata() -> None:
    names = names_for("run this in powershell: Get-Date")
    assert "run_windows_terminal" in names
    assert "run_terminal" in names
