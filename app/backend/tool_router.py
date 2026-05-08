from __future__ import annotations

import re
from dataclasses import dataclass

from tool_registry import get_tools_by_layer


@dataclass(frozen=True)
class ToolRoute:
    name: str
    mode: str
    explicit_skill_write: bool = False
    explicit_clipboard: bool = False


def classify_tool_route(user_text: str, layer_name: str = "companion", event_type: str = "") -> ToolRoute:
    """Minimal exposure gate for the two-layer runtime.

    This is intentionally not semantic routing. Layer membership comes from the
    registry. Only special privacy/safety tools are gated by explicit wording.
    """
    text = str(user_text or "").strip().lower()
    explicit_clipboard = "clipboard" in text
    explicit_skill_write = bool(
        re.search(r"\b(create|write|edit|update|overwrite|delete|remove)\b.*\bskill\b", text)
        or re.search(r"\bskill\b.*\b(create|write|edit|update|overwrite|delete|remove)\b", text)
    )
    mode = "simple"
    if any(word in text for word in ("remember", "memory", "remind", "reminder", "snooze")):
        mode = "memory"
    if any(word in text for word in ("file", "vault", ".md", ".txt", "daily notes", "todo", "skill")):
        mode = "vault"
    if any(word in text for word in ("terminal", "command", "powershell", "bash", "code", "project")):
        mode = "coding/dev"
    return ToolRoute(
        name=f"{layer_name}_default",
        mode=mode,
        explicit_skill_write=explicit_skill_write,
        explicit_clipboard=explicit_clipboard,
    )


def routed_tools_for_turn(layer: str, route: ToolRoute, config=None) -> list[dict]:
    tools = []
    for tool in get_tools_by_layer(layer, config=config):
        name = str(tool.get("name") or "")
        if tool.get("default_exposed") is False:
            continue
        if name == "read_clipboard" and not route.explicit_clipboard:
            continue
        if name in {"write_skill", "delete_skill"} and not route.explicit_skill_write:
            continue
        tools.append(tool)
    return tools
