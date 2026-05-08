from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = ROOT / "app"
BACKEND_DIR = APP_ROOT / "backend"
for candidate in (str(APP_ROOT), str(BACKEND_DIR), str(ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from tool_schema import ToolSchemaValidationError, validate_openai_tool_schema


def test_malformed_tool_schema_errors_are_clear() -> None:
    bad = {"type": "function", "function": {"parameters": {"type": "object", "properties": {}, "required": []}}}
    try:
        validate_openai_tool_schema(bad, "openai", 0)
    except ToolSchemaValidationError as exc:
        assert "provider=openai" in str(exc)
        assert "index=0" in str(exc)
        assert "missing function.name" in str(exc)
    else:
        raise AssertionError("expected schema validation error")


def test_raw_json_schema_top_level_is_rejected() -> None:
    try:
        validate_openai_tool_schema({"type": "object", "properties": {}}, "openai", 2)
    except ToolSchemaValidationError as exc:
        assert "type must equal 'function'" in str(exc)
    else:
        raise AssertionError("expected schema validation error")


@contextmanager
def isolated_profile():
    original_test_mode = os.environ.get("OPEN_COMPANION_TEST_MODE")
    original_profile_dir = os.environ.get("OPEN_COMPANION_TEST_PROFILE_DIR")
    temp_root = ROOT / "app" / "tests" / "tools" / f".tmp-{uuid.uuid4().hex}"
    temp_root.mkdir(parents=True)
    (temp_root / "config.json").write_text((ROOT / "config.json").read_text(encoding="utf-8"), encoding="utf-8")
    os.environ["OPEN_COMPANION_TEST_MODE"] = "1"
    os.environ["OPEN_COMPANION_TEST_PROFILE_DIR"] = str(temp_root)
    for name in list(sys.modules):
        if name in {"runtime_paths", "tool_registry", "app.backend.runtime_paths", "app.backend.tool_registry", "app.backend.tools.builtin.files"}:
            sys.modules.pop(name, None)
    tool_registry = importlib.import_module("tool_registry")
    runtime_paths = importlib.import_module("runtime_paths")
    Path(runtime_paths.VAULT_DIR).mkdir(parents=True, exist_ok=True)
    try:
        yield Path(runtime_paths.VAULT_DIR), tool_registry
    finally:
        if original_test_mode is None:
            os.environ.pop("OPEN_COMPANION_TEST_MODE", None)
        else:
            os.environ["OPEN_COMPANION_TEST_MODE"] = original_test_mode
        if original_profile_dir is None:
            os.environ.pop("OPEN_COMPANION_TEST_PROFILE_DIR", None)
        else:
            os.environ["OPEN_COMPANION_TEST_PROFILE_DIR"] = original_profile_dir
        shutil.rmtree(temp_root, ignore_errors=True)


def test_new_file_tools_append_list_search_and_split_edit() -> None:
    with isolated_profile() as (vault, tool_registry):
        append = tool_registry.execute_tool("append_file", {"path": "Daily Notes/2026-04-26.md", "content": "hello world\n"}, "assistant")
        assert append.startswith("Appended:")
        listing = tool_registry.execute_tool("list_files", {"path": "Daily Notes"}, "assistant")
        assert "Daily Notes/2026-04-26.md" in listing
        search = tool_registry.execute_tool("search_files", {"query": "hello", "path": "Daily Notes"}, "assistant")
        assert "hello world" in search
        replaced = tool_registry.execute_tool(
            "replace_text_in_file",
            {"path": "Daily Notes/2026-04-26.md", "old_text": "hello", "new_text": "goodbye"},
            "assistant",
        )
        assert "replaced 1 occurrence" in replaced
        assert (vault / "Daily Notes" / "2026-04-26.md").read_text(encoding="utf-8") == "goodbye world\n"
