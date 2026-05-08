from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolBudget:
    layer_name: str
    max_rounds: int
    max_calls: int


LAYER_BUDGETS = {
    "companion": ToolBudget("companion", max_rounds=20, max_calls=20),
    "assistant": ToolBudget("assistant", max_rounds=100, max_calls=100),
}


def resolve_budget(layer_name: str | None) -> ToolBudget:
    normalized = str(layer_name or "").strip().lower()
    return LAYER_BUDGETS.get(normalized, LAYER_BUDGETS["companion"])


def tool_result_is_progress(tool_name: str, arguments: dict, result: str, previous_results: set[str]) -> bool:
    text = str(result or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if lowered.startswith("error:") or "not found" in lowered or "does not exist" in lowered:
        return False
    signature = f"{tool_name}:{text[:500]}"
    if signature in previous_results:
        return False
    if tool_name in {"write_file", "append_file", "replace_text_in_file", "edit_code_symbol", "write_skill", "delete_skill", "write_memory"}:
        return any(word in lowered for word in ("written", "appended", "edited", "deleted", "saved", "backup", "created"))
    if tool_name in {"read_file", "list_files", "search_files", "search_memories", "run_terminal"}:
        return True
    return bool(text)
