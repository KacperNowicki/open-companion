from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


class ToolSchemaValidationError(ValueError):
    def __init__(self, provider: str, index: int, tool_name: str, reason: str) -> None:
        self.provider = provider
        self.index = index
        self.tool_name = tool_name
        self.reason = reason
        label = tool_name or "<unknown>"
        super().__init__(f"Malformed tool schema for provider={provider} index={index} tool={label}: {reason}")


@dataclass(frozen=True)
class ToolArgumentValidationResult:
    ok: bool
    reason: str = ""


def canonical_openai_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Return the single canonical OpenAI-style function wrapper.

    This is the only schema repair path. It preserves already-wrapped function
    schemas and wraps registry entries whose `schema` value is the function body.
    Raw JSON schemas are intentionally not accepted here.
    """
    if not isinstance(tool, dict):
        raise ValueError("tool must be an object")
    if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
        return deepcopy(tool)
    if "name" in tool and "parameters" in tool:
        return {
            "type": "function",
            "function": {
                "name": str(tool.get("name") or ""),
                "description": str(tool.get("description") or ""),
                "parameters": deepcopy(tool.get("parameters")),
            },
        }
    return deepcopy(tool)


def validate_openai_tool_schema(tool: Any, provider: str, index: int) -> dict[str, Any]:
    tool_name = ""
    if isinstance(tool, dict):
        function = tool.get("function") if isinstance(tool.get("function"), dict) else {}
        tool_name = str(function.get("name") or tool.get("name") or "").strip()
    if not isinstance(tool, dict):
        raise ToolSchemaValidationError(provider, index, tool_name, "tool must be an object")
    if "type" not in tool:
        if tool.get("properties") is not None or tool.get("required") is not None:
            raise ToolSchemaValidationError(provider, index, tool_name, "raw JSON schema was sent as a top-level tool")
        raise ToolSchemaValidationError(provider, index, tool_name, "missing type")
    if tool.get("type") != "function":
        raise ToolSchemaValidationError(provider, index, tool_name, "type must equal 'function'")
    function = tool.get("function")
    if not isinstance(function, dict):
        raise ToolSchemaValidationError(provider, index, tool_name, "missing function")
    tool_name = str(function.get("name") or "").strip()
    if not tool_name:
        raise ToolSchemaValidationError(provider, index, tool_name, "missing function.name")
    parameters = function.get("parameters")
    if not isinstance(parameters, dict):
        raise ToolSchemaValidationError(provider, index, tool_name, "missing function.parameters")
    if parameters.get("type") != "object":
        raise ToolSchemaValidationError(provider, index, tool_name, "function.parameters must be an object schema")
    if not isinstance(parameters.get("properties", {}), dict):
        raise ToolSchemaValidationError(provider, index, tool_name, "function.parameters.properties must be an object")
    if not isinstance(parameters.get("required", []), list):
        raise ToolSchemaValidationError(provider, index, tool_name, "function.parameters.required must be a list")
    return deepcopy(tool)


def validate_openai_tool_schemas(tools: list[dict] | None, provider: str) -> list[dict]:
    validated: list[dict] = []
    for index, tool in enumerate(tools or []):
        validated.append(validate_openai_tool_schema(tool, provider, index))
    return validated


def _coerce_args(arguments: Any) -> tuple[dict[str, Any] | None, str]:
    if isinstance(arguments, dict):
        return arguments, ""
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments or "{}")
        except json.JSONDecodeError as exc:
            return None, f"arguments are not valid JSON: {exc.msg}"
        if isinstance(parsed, dict):
            return parsed, ""
    return None, "arguments must be an object"


def validate_tool_arguments(schema: dict[str, Any] | None, arguments: Any) -> ToolArgumentValidationResult:
    args, error = _coerce_args(arguments)
    if args is None:
        return ToolArgumentValidationResult(False, error)
    parameters = ((schema or {}).get("function") or {}).get("parameters") or {}
    required = parameters.get("required") or []
    for key in required:
        if key not in args:
            return ToolArgumentValidationResult(False, f"missing required argument: {key}")
    properties = parameters.get("properties") or {}
    for key, value in args.items():
        prop = properties.get(key)
        if not isinstance(prop, dict):
            continue
        expected = prop.get("type")
        if expected == "string" and not isinstance(value, str):
            return ToolArgumentValidationResult(False, f"argument {key} must be a string")
        if expected == "integer" and not isinstance(value, int):
            return ToolArgumentValidationResult(False, f"argument {key} must be an integer")
        if expected == "boolean" and not isinstance(value, bool):
            return ToolArgumentValidationResult(False, f"argument {key} must be a boolean")
        if expected == "object" and not isinstance(value, dict):
            return ToolArgumentValidationResult(False, f"argument {key} must be an object")
        if expected == "array" and not isinstance(value, list):
            return ToolArgumentValidationResult(False, f"argument {key} must be an array")
        enum = prop.get("enum")
        if isinstance(enum, list) and value not in enum:
            return ToolArgumentValidationResult(False, f"argument {key} must be one of: {', '.join(map(str, enum))}")
    return ToolArgumentValidationResult(True)
