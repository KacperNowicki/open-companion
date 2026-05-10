#!/usr/bin/env python3
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
SCRATCH_ROOT = APP_ROOT / "tests" / "integration" / ".tmp-file-tools"

for candidate in (str(APP_ROOT), str(BACKEND_DIR), str(ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from tests.helpers.setup import BOLD, RESET, fail, ok, section, summary

MODULE_NAMES = (
    "runtime_paths",
    "tool_registry",
    "app.backend.runtime_paths",
    "app.backend.tool_registry",
    "app.backend.tools.builtin.files",
)

EXPECTED_TOOLS = {
    "write_memory", "search_memories", "read_file", "write_file", "append_file",
    "list_files", "search_files", "replace_text_in_file", "add_reminder", "add_recurring_reminder", "run_terminal",
}

LEGACY_EDIT_FILE_CONFIG = {"tools": {"overrides": {"assistant": {"edit_file": True}}}}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _base_config() -> dict:
    config_path = ROOT / "config.json"
    if config_path.exists():
        return json.loads(config_path.read_text(encoding="utf-8"))
    return {
        "version": "test",
        "vault": {"path": "./companion/vault", "enabled": True},
        "brain": {
            "provider": "gemma",
            "model": "qwen2.5:14b",
            "layers": {
                "companion": {},
                "assistant": {},
            },
        },
        "memory": {"enabled": True, "embedding_enabled": True},
        "heartbeat": {"enabled": False},
        "voice": {"tts_enabled": False, "stt_enabled": False},
        "layers": {
            "companion": {"enabled": True, "permission_profile": "companion"},
            "assistant": {"enabled": True, "permission_profile": "assistant"},
        },
        "tools": {
            "overrides": {
                "companion": {},
                "assistant": {},
            },
            "custom": [],
        },
    }


def _clear_modules() -> None:
    for name in MODULE_NAMES:
        sys.modules.pop(name, None)


def _load_tool_modules() -> tuple[object, object]:
    _clear_modules()
    importlib.invalidate_caches()
    return importlib.import_module("tool_registry"), importlib.import_module("runtime_paths")


@contextmanager
def isolated_profile():
    original_test_mode = os.environ.get("OPEN_COMPANION_TEST_MODE")
    original_profile_dir = os.environ.get("OPEN_COMPANION_TEST_PROFILE_DIR")
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    temp_root = SCRATCH_ROOT / f"profile-{uuid.uuid4().hex}"
    temp_root.mkdir(parents=True, exist_ok=False)
    _write_json(temp_root / "config.json", _base_config())
    os.environ["OPEN_COMPANION_TEST_MODE"] = "1"
    os.environ["OPEN_COMPANION_TEST_PROFILE_DIR"] = str(temp_root)
    tool_registry, runtime_paths = _load_tool_modules()
    tool_registry.load_registry(force=True)
    Path(runtime_paths.VAULT_DIR).mkdir(parents=True, exist_ok=True)
    try:
        yield temp_root, Path(runtime_paths.VAULT_DIR), tool_registry
    finally:
        if original_test_mode is None:
            os.environ.pop("OPEN_COMPANION_TEST_MODE", None)
        else:
            os.environ["OPEN_COMPANION_TEST_MODE"] = original_test_mode
        if original_profile_dir is None:
            os.environ.pop("OPEN_COMPANION_TEST_PROFILE_DIR", None)
        else:
            os.environ["OPEN_COMPANION_TEST_PROFILE_DIR"] = original_profile_dir
        _clear_modules()
        shutil.rmtree(temp_root, ignore_errors=True)


def test_registry_exposure() -> None:
    with isolated_profile() as (_profile, _vault_dir, tool_registry):
        for layer in ("companion", "assistant"):
            names = {tool["name"] for tool in tool_registry.get_tools_by_layer(layer, config={})}
            assert EXPECTED_TOOLS.issubset(names), (layer, names)


def test_read_file_returns_numbered_content_and_ranges() -> None:
    with isolated_profile() as (_profile, vault_dir, tool_registry):
        target = vault_dir / "Projects" / "notes.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

        full = tool_registry.execute_tool("read_file", {"path": "Projects/notes.md"}, "assistant")
        ranged = tool_registry.execute_tool("read_file", {"path": "Projects/notes.md", "start_line": 2, "end_line": 3}, "assistant")

        assert full == "1: alpha\n2: beta\n3: gamma", full
        assert ranged == "2: beta\n3: gamma", ranged


def test_write_file_new_file_writes_immediately() -> None:
    with isolated_profile() as (_profile, vault_dir, tool_registry):
        result = tool_registry.execute_tool(
            "write_file",
            {"path": "Daily Notes/2026-04-19.md", "content": "# Title\n\nbody\n"},
            "assistant",
        )

        written = vault_dir / "Daily Notes" / "2026-04-19.md"
        assert written.exists(), written
        assert written.read_text(encoding="utf-8") == "# Title\n\nbody\n"
        assert result == "Written: Daily Notes/2026-04-19.md (14 bytes, 3 lines)", result


def test_write_file_existing_file_requires_confirm_before_overwrite() -> None:
    with isolated_profile() as (_profile, vault_dir, tool_registry):
        target = vault_dir / "Projects" / "draft.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        original = "\n".join(f"line {index}" for index in range(1, 26)) + "\n"
        target.write_text(original, encoding="utf-8")

        preview = tool_registry.execute_tool(
            "write_file",
            {"path": "Projects/draft.md", "content": "replacement\n"},
            "assistant",
        )

        assert "[File exists - first 20 lines shown]" in preview, preview
        assert "1: line 1" in preview, preview
        assert "20: line 20" in preview, preview
        assert "21: line 21" not in preview, preview
        assert "[Call write_file again with confirm=true to overwrite]" in preview, preview
        assert target.read_text(encoding="utf-8") == original


def test_write_file_existing_file_overwrites_with_confirm() -> None:
    with isolated_profile() as (_profile, vault_dir, tool_registry):
        target = vault_dir / "Projects" / "draft.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("old value\n", encoding="utf-8")

        result = tool_registry.execute_tool(
            "write_file",
            {"path": "Projects/draft.md", "content": "new value\n", "confirm": True},
            "assistant",
        )

        assert target.read_text(encoding="utf-8") == "new value\n"
        assert result == "Written: Projects/draft.md (10 bytes, 1 lines)", result


def test_edit_file_single_and_replace_all_modify_disk() -> None:
    with isolated_profile() as (_profile, vault_dir, tool_registry):
        single_target = vault_dir / "Projects" / "single.txt"
        single_target.parent.mkdir(parents=True, exist_ok=True)
        single_target.write_text("const label = 'before';\n", encoding="utf-8")
        single = tool_registry.execute_tool(
            "edit_file",
            {"path": "Projects/single.txt", "old_text": "before", "new_text": "after"},
            "assistant",
            config=LEGACY_EDIT_FILE_CONFIG,
        )
        assert "after" in single_target.read_text(encoding="utf-8")
        assert "replaced 1 occurrence" in single, single

        all_target = vault_dir / "Projects" / "repeated.txt"
        all_target.parent.mkdir(parents=True, exist_ok=True)
        all_target.write_text("apple\napple\npear\napple\n", encoding="utf-8")
        replace_all = tool_registry.execute_tool(
            "edit_file",
            {"path": "Projects/repeated.txt", "old_text": "apple", "new_text": "orange", "replace_all": True},
            "assistant",
            config=LEGACY_EDIT_FILE_CONFIG,
        )
        assert all_target.read_text(encoding="utf-8") == "orange\norange\npear\norange\n"
        assert "replaced 3 occurrences" in replace_all, replace_all


def test_edit_file_ast_python_function() -> None:
    with isolated_profile() as (_profile, vault_dir, tool_registry):
        target = vault_dir / "mod.py"
        target.write_text(
            "def greet():\n    return 'hello'\n\n\ndef other():\n    return 1\n",
            encoding="utf-8",
        )
        result = tool_registry.execute_tool(
            "edit_file",
            {
                "path": "mod.py",
                "new_text": "def greet():\n    return 'hi there'\n",
                "target_name": "greet",
                "target_type": "function",
            },
            "assistant",
            config=LEGACY_EDIT_FILE_CONFIG,
        )
        contents = target.read_text(encoding="utf-8")
        assert result.startswith("Edited: mod.py"), result
        assert "return 'hi there'" in contents, contents
        assert "return 'hello'" not in contents, contents
        assert "def other():" in contents, contents


def test_edit_file_ast_python_method() -> None:
    with isolated_profile() as (_profile, vault_dir, tool_registry):
        target = vault_dir / "svc.py"
        target.write_text(
            "class Svc:\n"
            "    def run(self):\n"
            "        return 'old'\n"
            "\n"
            "    def keep(self):\n"
            "        return 'kept'\n",
            encoding="utf-8",
        )
        result = tool_registry.execute_tool(
            "edit_file",
            {
                "path": "svc.py",
                "new_text": "return 'new'",
                "target_name": "Svc.run",
                "target_type": "method",
            },
            "assistant",
            config=LEGACY_EDIT_FILE_CONFIG,
        )
        contents = target.read_text(encoding="utf-8")
        assert result.startswith("Edited: svc.py"), result
        assert "class Svc:" in contents, contents
        assert "return 'new'" in contents, contents
        assert "return 'old'" not in contents, contents
        assert "return 'kept'" in contents, contents


def test_edit_file_ast_python_variable() -> None:
    with isolated_profile() as (_profile, vault_dir, tool_registry):
        target = vault_dir / "cfg.py"
        target.write_text(
            "VERSION = 1\nNAME = 'companion'\nOTHER = 99\n",
            encoding="utf-8",
        )
        result = tool_registry.execute_tool(
            "edit_file",
            {
                "path": "cfg.py",
                "new_text": "42",
                "target_name": "VERSION",
                "target_type": "variable",
            },
            "assistant",
            config=LEGACY_EDIT_FILE_CONFIG,
        )
        contents = target.read_text(encoding="utf-8")
        assert result.startswith("Edited: cfg.py"), result
        assert "VERSION = 42" in contents, contents
        assert "VERSION = 1" not in contents, contents
        assert "NAME = 'companion'" in contents, contents
        assert "OTHER = 99" in contents, contents


def test_edit_file_ast_js_function_declaration() -> None:
    with isolated_profile() as (_profile, vault_dir, tool_registry):
        target = vault_dir / "script.js"
        target.write_text(
            "function greet() {\n  return 'hello';\n}\n\nfunction other() { return 1; }\n",
            encoding="utf-8",
        )
        result = tool_registry.execute_tool(
            "edit_file",
            {
                "path": "script.js",
                "new_text": "function greet() {\n  return 'hi there';\n}",
                "target_name": "greet",
                "target_type": "function",
            },
            "assistant",
            config=LEGACY_EDIT_FILE_CONFIG,
        )
        contents = target.read_text(encoding="utf-8")
        assert result.startswith("Edited: script.js"), result
        assert "return 'hi there'" in contents, contents
        assert "return 'hello'" not in contents, contents
        assert "function other()" in contents, contents


def test_edit_file_ast_js_arrow_function() -> None:
    with isolated_profile() as (_profile, vault_dir, tool_registry):
        target = vault_dir / "arrow.js"
        target.write_text(
            "const greet = () => 'hello';\nconst keep = () => 'kept';\n",
            encoding="utf-8",
        )
        result = tool_registry.execute_tool(
            "edit_file",
            {
                "path": "arrow.js",
                "new_text": "const greet = () => 'hi there';",
                "target_name": "greet",
                "target_type": "function",
            },
            "assistant",
            config=LEGACY_EDIT_FILE_CONFIG,
        )
        contents = target.read_text(encoding="utf-8")
        assert result.startswith("Edited: arrow.js"), result
        assert "const greet = () => 'hi there';" in contents, contents
        assert "'hello'" not in contents, contents
        assert "const keep = () => 'kept';" in contents, contents


def test_edit_file_ast_js_method() -> None:
    with isolated_profile() as (_profile, vault_dir, tool_registry):
        target = vault_dir / "cls.js"
        target.write_text(
            "class Svc {\n"
            "  run() {\n"
            "    return 'old';\n"
            "  }\n"
            "  keep() {\n"
            "    return 'kept';\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        result = tool_registry.execute_tool(
            "edit_file",
            {
                "path": "cls.js",
                "new_text": "\n    return 'new';\n  ",
                "target_name": "Svc.run",
                "target_type": "method",
            },
            "assistant",
            config=LEGACY_EDIT_FILE_CONFIG,
        )
        contents = target.read_text(encoding="utf-8")
        assert result.startswith("Edited: cls.js"), result
        assert "class Svc {" in contents, contents
        assert "return 'new';" in contents, contents
        assert "return 'old';" not in contents, contents
        assert "return 'kept';" in contents, contents


def test_edit_file_ast_target_not_found_returns_error_string() -> None:
    with isolated_profile() as (_profile, vault_dir, tool_registry):
        target = vault_dir / "mod.py"
        original = "def greet():\n    return 'hello'\n"
        target.write_text(original, encoding="utf-8")
        result = tool_registry.execute_tool(
            "edit_file",
            {
                "path": "mod.py",
                "new_text": "def nope():\n    return 'nope'\n",
                "target_name": "missing",
                "target_type": "function",
            },
            "assistant",
            config=LEGACY_EDIT_FILE_CONFIG,
        )
        assert result.startswith("Error:"), result
        assert "missing" in result, result
        assert target.read_text(encoding="utf-8") == original


def test_edit_file_ast_unsupported_extension_returns_error_string() -> None:
    with isolated_profile() as (_profile, vault_dir, tool_registry):
        target = vault_dir / "notes.md"
        original = "# Title\nbody\n"
        target.write_text(original, encoding="utf-8")
        result = tool_registry.execute_tool(
            "edit_file",
            {
                "path": "notes.md",
                "new_text": "whatever",
                "target_name": "anything",
                "target_type": "function",
            },
            "assistant",
            config=LEGACY_EDIT_FILE_CONFIG,
        )
        assert result == "Error: AST editing not supported for .md files", result
        assert target.read_text(encoding="utf-8") == original


def test_edit_file_zero_and_multiple_match_failures_leave_file_unchanged() -> None:
    with isolated_profile() as (_profile, vault_dir, tool_registry):
        zero_target = vault_dir / "Projects" / "zero.txt"
        zero_target.parent.mkdir(parents=True, exist_ok=True)
        zero_target.write_text("hello world\n", encoding="utf-8")
        zero_result = tool_registry.execute_tool(
            "edit_file",
            {"path": "Projects/zero.txt", "old_text": "missing", "new_text": "found"},
            "assistant",
            config=LEGACY_EDIT_FILE_CONFIG,
        )
        assert zero_result == "Error: old_text was not found in Projects/zero.txt.", zero_result
        assert zero_target.read_text(encoding="utf-8") == "hello world\n"

        multi_target = vault_dir / "Projects" / "multi.txt"
        multi_target.write_text("same same same\n", encoding="utf-8")
        multi_result = tool_registry.execute_tool(
            "edit_file",
            {"path": "Projects/multi.txt", "old_text": "same", "new_text": "diff"},
            "assistant",
            config=LEGACY_EDIT_FILE_CONFIG,
        )
        assert multi_result == "Error: old_text matched 3 times in Projects/multi.txt. Call edit_file again with replace_all=true.", multi_result
        assert multi_target.read_text(encoding="utf-8") == "same same same\n"


def test_path_security_rejects_traversal_absolute_and_outside_paths_before_io() -> None:
    with isolated_profile() as (_profile, vault_dir, tool_registry):
        escape_file = vault_dir.parent / "config.json"
        original_contents = escape_file.read_text(encoding="utf-8") if escape_file.exists() else None

        traversal = tool_registry.execute_tool("read_file", {"path": "../../config.json"}, "assistant")
        absolute = tool_registry.execute_tool("read_file", {"path": str((vault_dir / "Projects" / "notes.md").resolve())}, "assistant")
        leading_slash = tool_registry.execute_tool("write_file", {"path": "/outside.txt", "content": "nope"}, "assistant")

        assert traversal == "Error: Path escapes vault: ../../config.json", traversal
        assert absolute.startswith("Error: Absolute paths are not allowed:"), absolute
        assert leading_slash == "Error: Absolute paths are not allowed: /outside.txt", leading_slash
        if original_contents is not None:
            assert escape_file.read_text(encoding="utf-8") == original_contents
        assert not (vault_dir / "outside.txt").exists()


def test_read_file_rejects_binary_and_missing_files_without_crashing() -> None:
    with isolated_profile() as (_profile, vault_dir, tool_registry):
        binary_target = vault_dir / "Projects" / "blob.bin"
        binary_target.parent.mkdir(parents=True, exist_ok=True)
        binary_target.write_bytes(b"\x00\x01\x02")

        binary = tool_registry.execute_tool("read_file", {"path": "Projects/blob.bin"}, "assistant")
        missing = tool_registry.execute_tool("read_file", {"path": "Projects/missing.md"}, "assistant")

        assert binary == "Error: Binary files are not supported: blob.bin", binary
        assert missing == "Error: File does not exist: missing.md", missing


def test_confirmation_metadata_is_disabled_for_vault_file_tools() -> None:
    with isolated_profile() as (_profile, _vault_dir, tool_registry):
        for layer in ("companion", "assistant"):
            for name in ("read_file", "write_file", "edit_file"):
                assert tool_registry.needs_confirmation(name, layer, config={}) is False, (layer, name)
            for name in ("append_file", "list_files", "search_files", "replace_text_in_file", "edit_code_symbol"):
                assert tool_registry.needs_confirmation(name, layer, config={}) is False, (layer, name)


def run_test(name: str, fn) -> None:
    try:
        fn()
        ok(name)
    except AssertionError as exc:
        fail(name, str(exc))
    except Exception as exc:
        fail(name, f"{type(exc).__name__}: {exc}")


def main() -> None:
    print(f"{BOLD}OpenCompanion Integration File Tools Suite{RESET}")
    print(f"Python: {sys.version}")
    print(f"Root: {ROOT}")
    print()

    section("Registry and policy")
    run_test("companion and assistant expose vault file tools", test_registry_exposure)
    run_test("vault file tools never need confirmation", test_confirmation_metadata_is_disabled_for_vault_file_tools)

    section("Read and write behavior")
    run_test("read_file returns numbered content and requested ranges", test_read_file_returns_numbered_content_and_ranges)
    run_test("write_file creates new files immediately", test_write_file_new_file_writes_immediately)
    run_test("write_file previews existing files until confirm=true", test_write_file_existing_file_requires_confirm_before_overwrite)
    run_test("write_file overwrites existing files when confirm=true", test_write_file_existing_file_overwrites_with_confirm)

    section("Edit behavior")
    run_test("edit_file handles single replace and replace_all", test_edit_file_single_and_replace_all_modify_disk)
    run_test("edit_file leaves files unchanged on zero or multiple matches", test_edit_file_zero_and_multiple_match_failures_leave_file_unchanged)

    section("AST edit behavior")
    run_test("edit_file AST replaces a top-level Python function", test_edit_file_ast_python_function)
    run_test("edit_file AST replaces a Python class method body", test_edit_file_ast_python_method)
    run_test("edit_file AST replaces a top-level Python variable RHS", test_edit_file_ast_python_variable)
    run_test("edit_file AST replaces a JS function declaration", test_edit_file_ast_js_function_declaration)
    run_test("edit_file AST replaces a JS arrow function", test_edit_file_ast_js_arrow_function)
    run_test("edit_file AST replaces a JS class method body", test_edit_file_ast_js_method)
    run_test("edit_file AST returns an error string when target not found", test_edit_file_ast_target_not_found_returns_error_string)
    run_test("edit_file AST returns an error string for unsupported extensions", test_edit_file_ast_unsupported_extension_returns_error_string)

    section("Security")
    run_test("path security rejects traversal and absolute paths before I/O", test_path_security_rejects_traversal_absolute_and_outside_paths_before_io)
    run_test("read_file rejects binary and missing files cleanly", test_read_file_rejects_binary_and_missing_files_without_crashing)

    success = summary()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
