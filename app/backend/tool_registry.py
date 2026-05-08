from __future__ import annotations

import importlib
import json
import logging
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

from runtime_paths import PROFILE_ROOT, PROJECT_ROOT, TEST_MODE

logger = logging.getLogger(__name__)

TOOLS_DIR = Path(__file__).resolve().parent / "tools"
PROJECT_TOOLS_DIR = PROJECT_ROOT / "app" / "backend" / "tools"
DEFAULT_REGISTRY_PATH = (
    PROJECT_TOOLS_DIR / "registry.json"
    if (PROJECT_TOOLS_DIR / "registry.json").exists()
    else TOOLS_DIR / "registry.json"
)
PROFILE_REGISTRY_PATH = PROFILE_ROOT / "app" / "backend" / "tools" / "registry.json"
REGISTRY_PATH = TOOLS_DIR / "registry.json"
CUSTOM_DIR = PROJECT_TOOLS_DIR / "custom" if (PROJECT_TOOLS_DIR / "custom").exists() else TOOLS_DIR / "custom"
CUSTOM_REGISTRY_PATH = CUSTOM_DIR / "registry.json"

VALID_LAYERS = {"companion", "assistant", "assistant_low", "assistant_high", "pc_doctor", "pcdoctor"}
_LAYER_PERMISSION_MAP = {
    "assistant_low": "assistant",
    "assistant_high": "assistant",
    "pc_doctor": "assistant",
    "pcdoctor": "assistant",
}
CUSTOM_TOOL_TYPES = {"custom_py", "custom_js"}
CONFIG_CUSTOM_ROOTS = {("app", "backend"), ("app", "scripts")}
CONFIG_CUSTOM_EXTENSIONS = {".py", ".js"}
TEST_MODE_HIDDEN_TOOLS = {"snooze_reminder"}
TEST_SCRIPT_HINTS = {
    "app/tests/test_suite.py",
    "app/tests/integration/run_python.py",
    "app/tests/integration/test_tools.py",
    "app/tests/integration/test_layers.py",
}

_registry: list[dict] = []
_loaded = False


def _builtin_registry_path() -> Path:
    if not TEST_MODE:
        return DEFAULT_REGISTRY_PATH

    if not PROFILE_REGISTRY_PATH.exists():
        PROFILE_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        PROFILE_REGISTRY_PATH.write_text(
            DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    return PROFILE_REGISTRY_PATH


def _hide_optional_tools_for_current_process() -> bool:
    if TEST_MODE:
        return True
    argv0 = Path(str(sys.argv[0] or "")).as_posix().lower()
    return any(argv0.endswith(hint) for hint in TEST_SCRIPT_HINTS)


def _clone(value):
    return deepcopy(value)


def _read_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to read %s: %s", path, exc)
    return _clone(default)


def _normalize_config_custom_command(command) -> str | None:
    raw = str(command or "").strip().replace("\\", "/")
    if not raw:
        return None
    if Path(raw).is_absolute() or raw.startswith("//"):
        return None
    if raw.startswith("./"):
        raw = raw[2:]
    if raw == ".." or raw.startswith("../") or "/../" in raw or raw.endswith("/.."):
        return None

    parts = [part for part in raw.split("/") if part]
    if len(parts) >= 2 and tuple(parts[:2]) in CONFIG_CUSTOM_ROOTS:
        pass
    elif parts and parts[0] == "backend":
        parts = ["app", "backend", *parts[1:]]
    elif parts and parts[0] == "scripts":
        parts = ["app", "scripts", *parts[1:]]
    else:
        return None
    suffix = Path(parts[-1]).suffix.lower()
    if suffix not in CONFIG_CUSTOM_EXTENSIONS:
        return None

    resolved = (PROJECT_ROOT / Path(*parts)).resolve()
    project_root = PROJECT_ROOT.resolve()
    project_root_prefix = f"{project_root}{os.sep}"
    if resolved != project_root and not str(resolved).startswith(project_root_prefix):
        return None
    return "/".join(parts)


def _normalize_config_custom_tool(tool) -> dict | None:
    if not isinstance(tool, dict):
        return None

    name = str(tool.get("name") or "").strip()
    if not name or not name.replace("_", "").isalnum():
        return None

    command = _normalize_config_custom_command(tool.get("command"))
    if not command:
        return None

    layers = [
        str(layer).strip()
        for layer in tool.get("layers", [])
        if str(layer).strip() in VALID_LAYERS
    ]
    if not layers:
        return None

    return {
        "name": name,
        "description": str(tool.get("description") or "").strip() or f"Custom tool: {name}",
        "layer": layers,
        "confirmation_required": True,
        "type": "config_custom",
        "command": command,
        "schema": {
            "type": "function",
            "function": {
                "name": name,
                "description": str(tool.get("description") or "").strip() or f"Custom tool: {name}",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        },
    }


def _config_custom_tools(config=None) -> list[dict]:
    if not isinstance(config, dict):
        return []
    tools_cfg = config.get("tools", {})
    if not isinstance(tools_cfg, dict):
        return []
    normalized = []
    seen = set()
    for raw in tools_cfg.get("custom", []):
        tool = _normalize_config_custom_tool(raw)
        if not tool or tool["name"] in seen:
            continue
        seen.add(tool["name"])
        normalized.append(tool)
    return normalized


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _normalize_schema(tool_def: dict) -> dict:
    schema = _clone(tool_def.get("schema") or {})
    if "type" not in schema:
        schema = {
            "type": "function",
            "function": schema,
        }
    return schema


def _normalize_registry_tool(tool_def: dict) -> dict | None:
    if not isinstance(tool_def, dict):
        return None

    name = str(tool_def.get("name") or "").strip()
    if not name:
        return None

    layers = [
        str(layer).strip()
        for layer in tool_def.get("layer", [])
        if str(layer).strip() in VALID_LAYERS
    ]
    if not layers:
        return None

    return {
        **tool_def,
        "name": name,
        "description": str(tool_def.get("description") or "").strip(),
        "layer": layers,
        "confirmation_required": bool(tool_def.get("confirmation_required", False)),
        "type": str(tool_def.get("type") or "builtin"),
        "module": str(tool_def.get("module") or "").strip(),
        "function": str(tool_def.get("function") or "").strip(),
        "schema": _normalize_schema(tool_def),
    }


def _validate_module_path(tool_type: str, module_path: str) -> bool:
    module_name = str(module_path or "").strip()
    if not module_name:
        return False
    if ".." in module_name or module_name.startswith(("/", "\\")):
        return False

    if tool_type == "builtin":
        return module_name.startswith("builtin.")

    if tool_type == "custom_py":
        return module_name.startswith("custom.")

    if tool_type == "custom_js":
        script_path = (CUSTOM_DIR / module_name).resolve()
        try:
            script_path.relative_to(CUSTOM_DIR.resolve())
        except ValueError:
            return False
        return script_path.suffix.lower() == ".js"

    return False


def load_registry(force: bool = False) -> list[dict]:
    global _loaded, _registry
    if _loaded and not force:
        return _registry

    base = _read_json(_builtin_registry_path(), {"tools": []})
    tools = []
    for raw in base.get("tools", []):
        normalized = _normalize_registry_tool(raw)
        if normalized:
            tools.append(normalized)

    custom = _read_json(CUSTOM_REGISTRY_PATH, {"tools": []})
    for raw in custom.get("tools", []):
        normalized = _normalize_registry_tool(raw)
        if normalized and normalized["type"] in CUSTOM_TOOL_TYPES and _validate_module_path(normalized["type"], normalized["module"]):
            tools.append(normalized)

    if _hide_optional_tools_for_current_process():
        tools = [tool for tool in tools if tool.get("name") not in TEST_MODE_HIDDEN_TOOLS]

    _registry = tools
    _loaded = True
    return _registry


def invalidate_registry() -> None:
    global _loaded
    _loaded = False


def _normalized_overrides(config) -> dict[str, dict[str, bool]]:
    normalized = {layer: {} for layer in VALID_LAYERS}
    if not isinstance(config, dict):
        return normalized
    tools_cfg = config.get("tools", {})
    if not isinstance(tools_cfg, dict):
        return normalized
    overrides = tools_cfg.get("overrides", {})
    if not isinstance(overrides, dict):
        return normalized

    all_keys = set(VALID_LAYERS) | set(overrides.keys())
    for layer in all_keys:
        if layer not in normalized:
            normalized[layer] = {}
        layer_overrides = overrides.get(layer, {})
        if not isinstance(layer_overrides, dict):
            continue
        for tool_name, enabled in layer_overrides.items():
            normalized[layer][str(tool_name).strip()] = bool(enabled)

    # Legacy worker aliases inherit from the assistant key.
    for new_layer, legacy_layer in _LAYER_PERMISSION_MAP.items():
        if not normalized.get(new_layer) and normalized.get(legacy_layer):
            normalized[new_layer] = dict(normalized[legacy_layer])

    return normalized


def _tool_enabled_for_layer(tool_def: dict, layer: str, config=None) -> bool:
    registry_layer = _LAYER_PERMISSION_MAP.get(layer, layer)
    if registry_layer not in tool_def.get("layer", []):
        return False
    # Use original layer name for config overrides, falling back to registry layer
    overrides = _normalized_overrides(config)
    layer_overrides = overrides.get(layer) or overrides.get(registry_layer, {})
    name = tool_def["name"]
    if name in layer_overrides:
        return layer_overrides[name]
    if tool_def.get("default_exposed") is False:
        return False
    return True


def get_tools_by_layer(layer: str, config=None) -> list[dict]:
    load_registry()
    base_tools = [
        _clone(tool_def)
        for tool_def in _registry
        if _tool_enabled_for_layer(tool_def, layer, config=config)
    ]
    for tool_def in _config_custom_tools(config):
        if _tool_enabled_for_layer(tool_def, layer, config=config):
            base_tools.append(_clone(tool_def))
    return base_tools


def get_tool_definition(name: str, config=None) -> dict | None:
    for tool_def in _config_custom_tools(config):
        if tool_def["name"] == name:
            return _clone(tool_def)
    load_registry()
    for tool_def in _registry:
        if tool_def["name"] == name:
            return _clone(tool_def)
    return None


def get_tool_schemas_for_layer(layer: str, config=None) -> list[dict]:
    return [_clone(tool_def["schema"]) for tool_def in get_tools_by_layer(layer, config=config)]


def get_tool_schemas_by_name(names: set[str] | list[str] | tuple[str, ...], layer: str, config=None) -> dict[str, dict]:
    requested = {str(name or "").strip() for name in names}
    return {
        tool_def["name"]: _clone(tool_def["schema"])
        for tool_def in get_tools_by_layer(layer, config=config)
        if tool_def["name"] in requested
    }


def is_tool_allowed(name: str, layer: str, config=None) -> bool:
    tool_def = get_tool_definition(name, config=config)
    return bool(tool_def and _tool_enabled_for_layer(tool_def, layer, config=config))


def needs_confirmation(name: str, layer: str, config=None) -> bool:
    tool_def = get_tool_definition(name, config=config)
    if not tool_def or not _tool_enabled_for_layer(tool_def, layer, config=config):
        return False
    return bool(tool_def.get("confirmation_required", False))


def _import_module(tool_def: dict):
    module_path = tool_def.get("module", "")
    if not _validate_module_path(tool_def.get("type", "builtin"), module_path):
        raise ValueError("Invalid tool module path.")
    return importlib.import_module(f"app.backend.tools.{module_path}")


def execute_tool(name: str, args: dict, layer: str, config=None) -> str:
    tool_def = get_tool_definition(name, config=config)
    if not tool_def:
        return f"Unknown tool: {name}"
    if not _tool_enabled_for_layer(tool_def, layer, config=config):
        return f"Tool {name} is not available in {layer} layer."

    tool_type = tool_def.get("type", "builtin")
    if tool_type == "config_custom":
        command = tool_def.get("command", "")
        resolved = (PROJECT_ROOT / Path(*command.split("/"))).resolve()
        suffix = resolved.suffix.lower()
        try:
            if suffix == ".py":
                result = subprocess.run(
                    [sys.executable, str(resolved)],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            elif suffix == ".js":
                result = subprocess.run(
                    ["node", str(resolved)],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            else:
                return f"Unsupported custom tool extension: {suffix}"
            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()
            return stdout or stderr or f"Custom tool exited with code {result.returncode}"
        except subprocess.TimeoutExpired:
            return "Custom tool timed out after 30 seconds."
        except Exception as exc:
            logger.exception("Config custom tool execution error: %s", name)
            return f"Error executing custom tool {name}: {exc}"

    if tool_type in {"builtin", "custom_py"}:
        module = _import_module(tool_def)
        fn = getattr(module, tool_def["function"])
        call_args = dict(args or {})
        if name == "list_available_tools":
            call_args.setdefault("layer", layer)
            call_args.setdefault("config", config or {})
        elif hasattr(fn, "__code__") and "config" in fn.__code__.co_varnames:
            call_args.setdefault("config", config or {})
        try:
            return str(fn(**call_args))
        except TypeError as exc:
            return f"Invalid arguments for {name}: {exc}"
        except Exception as exc:
            logger.exception("Tool execution error: %s", name)
            return f"Tool execution error: {exc}"

    if tool_type == "custom_js":
        if not _validate_module_path(tool_type, tool_def.get("module", "")):
            return "Invalid tool module path."
        script_path = (CUSTOM_DIR / tool_def["module"]).resolve()
        if not script_path.exists():
            return f"JS tool script not found: {tool_def['module']}"
        try:
            result = subprocess.run(
                ["node", str(script_path), json.dumps(args or {})],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=15,
            )
            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()
            return stdout or stderr or "No output"
        except subprocess.TimeoutExpired:
            return "Tool timed out after 15 seconds."
        except Exception as exc:
            return f"JS tool error: {exc}"

    return f"Unknown tool type: {tool_type}"


def register_custom_tool(tool_def: dict) -> bool:
    required = {"name", "description", "layer", "confirmation_required", "type", "module", "function"}
    if not required.issubset(tool_def):
        return False

    normalized = _normalize_registry_tool(tool_def)
    if not normalized or normalized["type"] not in CUSTOM_TOOL_TYPES:
        return False

    module_name = normalized["module"]
    if not _validate_module_path(normalized["type"], module_name):
        return False

    if normalized["type"] == "custom_py":
        relative_module = module_name.removeprefix("custom.").replace(".", "/")
        script_path = (CUSTOM_DIR / f"{relative_module}.py").resolve()
    else:
        script_path = (CUSTOM_DIR / module_name).resolve()
    try:
        script_path.relative_to(CUSTOM_DIR.resolve())
    except ValueError:
        return False

    existing = _read_json(CUSTOM_REGISTRY_PATH, {"tools": []})
    tools = [tool for tool in existing.get("tools", []) if tool.get("name") != normalized["name"]]
    tools.append(normalized)
    _write_json(CUSTOM_REGISTRY_PATH, {"tools": tools})
    invalidate_registry()
    load_registry()
    return True
