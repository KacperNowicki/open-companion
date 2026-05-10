from __future__ import annotations

import fnmatch
from collections.abc import Iterator
from pathlib import Path, PureWindowsPath

try:
    from app.backend.runtime_paths import VAULT_DIR
except ImportError:  # pragma: no cover - fallback for direct module execution
    from runtime_paths import VAULT_DIR

MAX_READ_LINES = 500
MAX_READ_BYTES = 50 * 1024
MAX_BINARY_PROBE_BYTES = 8 * 1024
WRITE_PREVIEW_LINES = 20
MAX_LIST_RESULTS = 200
MAX_SEARCH_RESULTS = 100


def resolve_vault_path(path_str: str) -> Path:
    raw_path = str(path_str or "").strip()
    if not raw_path:
        raise ValueError("Path is required.")
    if raw_path.startswith(("/", "\\")):
        raise ValueError(f"Absolute paths are not allowed: {path_str}")
    if PureWindowsPath(raw_path).drive:
        raise ValueError(f"Absolute paths are not allowed: {path_str}")
    candidate = Path(raw_path)
    if candidate.is_absolute():
        raise ValueError(f"Absolute paths are not allowed: {path_str}")

    vault_root = Path(VAULT_DIR).resolve()
    resolved = (vault_root / candidate).resolve()
    try:
        resolved.relative_to(vault_root)
    except ValueError as exc:
        raise ValueError(f"Path escapes vault: {path_str}") from exc
    return resolved


def _count_lines(text: str) -> int:
    if text == "":
        return 0
    return len(text.splitlines())


def _is_binary_file(path: Path) -> bool:
    with path.open("rb") as handle:
        probe = handle.read(MAX_BINARY_PROBE_BYTES)
    return b"\x00" in probe


def _read_text_file(path: Path) -> str:
    if not path.exists():
        raise ValueError(f"File does not exist: {path.name}")
    if path.is_dir():
        raise ValueError(f"Path is a directory: {path.name}")
    if _is_binary_file(path):
        raise ValueError(f"Binary files are not supported: {path.name}")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"File is not valid UTF-8 text: {path.name}") from exc


def _format_numbered_lines(lines: list[str], start_line_number: int, total_lines: int) -> str:
    if not lines:
        return f"[No lines to show. File has {total_lines} total lines.]"

    formatted: list[str] = []
    used_bytes = 0
    truncated = False
    shown_end_line = start_line_number - 1

    for index, line in enumerate(lines):
        current_line_number = start_line_number + index
        rendered = f"{current_line_number}: {line}"
        rendered_bytes = len(rendered.encode("utf-8"))
        separator_bytes = 1 if formatted else 0

        if len(formatted) >= MAX_READ_LINES:
            truncated = True
            break

        if formatted and used_bytes + separator_bytes + rendered_bytes > MAX_READ_BYTES:
            truncated = True
            break

        if not formatted and rendered_bytes > MAX_READ_BYTES:
            allowed_chars = max(1, MAX_READ_BYTES - len(f"{current_line_number}: "))
            rendered = f"{current_line_number}: {line[:allowed_chars]}"
            rendered_bytes = len(rendered.encode("utf-8"))
            truncated = True

        formatted.append(rendered)
        used_bytes += separator_bytes + rendered_bytes
        shown_end_line = current_line_number

        if len(formatted) >= MAX_READ_LINES:
            truncated = current_line_number < (start_line_number + len(lines) - 1)
            break

    output = "\n".join(formatted)
    if truncated:
        output += f"\n[Truncated: showing lines {start_line_number}-{shown_end_line} of {total_lines} total]"
    return output


def _atomic_write_text(path: Path, content: str) -> None:
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(path)


def _iter_visible_paths(root: Path, *, recursive: bool) -> Iterator[Path]:
    try:
        children = sorted(root.iterdir(), key=lambda p: p.as_posix().lower())
    except OSError:
        return
    for child in children:
        if child.name.startswith("."):
            continue
        yield child
        if recursive and child.is_dir():
            yield from _iter_visible_paths(child, recursive=True)


def _iter_search_files(root: Path) -> Iterator[Path]:
    if root.is_file():
        yield root
        return
    for item in _iter_visible_paths(root, recursive=True):
        if item.is_file():
            yield item


def read_file(path: str, start_line: int | None = None, end_line: int | None = None) -> str:
    try:
        resolved = resolve_vault_path(path)
        text = _read_text_file(resolved)
    except ValueError as exc:
        return f"Error: {exc}"

    try:
        start = 1 if start_line is None else int(start_line)
        end = None if end_line is None else int(end_line)
    except (TypeError, ValueError):
        return "Error: start_line and end_line must be integers."

    if start < 1:
        return "Error: start_line must be >= 1."
    if end is not None and end < 1:
        return "Error: end_line must be >= 1."
    if end is not None and end < start:
        return "Error: end_line must be >= start_line."

    all_lines = text.splitlines()
    total_lines = len(all_lines)
    if total_lines == 0:
        return "[Empty file]"

    start_index = start - 1
    if start_index >= total_lines:
        return f"[No lines to show. File has {total_lines} total lines.]"

    end_index = total_lines if end is None else min(end, total_lines)
    selected_lines = all_lines[start_index:end_index]
    return _format_numbered_lines(selected_lines, start, total_lines)


def write_file(path: str, content: str, create_parents: bool = True, confirm: bool = False) -> str:
    try:
        resolved = resolve_vault_path(path)
    except ValueError as exc:
        return f"Error: {exc}"

    if resolved.exists() and resolved.is_dir():
        return f"Error: Path is a directory: {path}"

    parent = resolved.parent
    if parent.exists() and not parent.is_dir():
        return f"Error: Parent path is not a directory: {path}"

    if resolved.exists() and not confirm:
        if _is_binary_file(resolved):
            return (
                "[File exists - binary preview unavailable]\n"
                "[Call write_file again with confirm=true to overwrite]"
            )
        preview = read_file(path, start_line=1, end_line=WRITE_PREVIEW_LINES)
        return (
            "[File exists - first 20 lines shown]\n"
            f"{preview}\n"
            "[Call write_file again with confirm=true to overwrite]"
        )

    if not parent.exists():
        if create_parents:
            parent.mkdir(parents=True, exist_ok=True)
        else:
            return f"Error: Parent directory does not exist: {parent}"

    try:
        _atomic_write_text(resolved, str(content))
    except Exception as exc:  # noqa: BLE001
        return f"Error: Failed to write file: {exc}"

    byte_count = len(str(content).encode("utf-8"))
    line_count = _count_lines(str(content))
    return f"Written: {path} ({byte_count} bytes, {line_count} lines)"


def append_file(path: str, content: str, create_parents: bool = True) -> str:
    try:
        resolved = resolve_vault_path(path)
    except ValueError as exc:
        return f"Error: {exc}"
    if resolved.exists() and resolved.is_dir():
        return f"Error: Path is a directory: {path}"
    parent = resolved.parent
    if parent.exists() and not parent.is_dir():
        return f"Error: Parent path is not a directory: {path}"
    if not parent.exists():
        if create_parents:
            parent.mkdir(parents=True, exist_ok=True)
        else:
            return f"Error: Parent directory does not exist: {parent}"
    try:
        with resolved.open("a", encoding="utf-8", newline="") as handle:
            handle.write(str(content))
    except Exception as exc:  # noqa: BLE001
        return f"Error: Failed to append file: {exc}"
    byte_count = len(str(content).encode("utf-8"))
    line_count = _count_lines(str(content))
    return f"Appended: {path} ({byte_count} bytes, {line_count} lines)"


def list_files(path: str = "", recursive: bool = False, max_results: int = MAX_LIST_RESULTS, include_dirs: bool = True) -> str:
    try:
        resolved = resolve_vault_path(path or ".")
    except ValueError as exc:
        return f"Error: {exc}"
    if not resolved.exists():
        return f"Error: Path does not exist: {path or '.'}"
    if not resolved.is_dir():
        rel = resolved.relative_to(Path(VAULT_DIR).resolve()).as_posix()
        return rel
    try:
        limit = max(1, min(int(max_results or MAX_LIST_RESULTS), 1000))
    except Exception:
        limit = MAX_LIST_RESULTS
    vault_root = Path(VAULT_DIR).resolve()
    entries: list[str] = []
    for item in _iter_visible_paths(resolved, recursive=bool(recursive)):
        if item.is_dir() and not include_dirs:
            continue
        rel = item.resolve().relative_to(vault_root).as_posix()
        entries.append(rel + ("/" if item.is_dir() else ""))
        if len(entries) >= limit:
            break
    if not entries:
        return "[No files found]"
    suffix = "\n[Truncated]" if len(entries) >= limit else ""
    return "\n".join(entries) + suffix


def search_files(query: str, path: str = "", max_results: int = MAX_SEARCH_RESULTS, file_glob: str = "*") -> str:
    needle = str(query or "")
    if not needle:
        return "Error: query is required."
    try:
        root = resolve_vault_path(path or ".")
    except ValueError as exc:
        return f"Error: {exc}"
    if not root.exists():
        return f"Error: Path does not exist: {path or '.'}"
    try:
        limit = max(1, min(int(max_results or MAX_SEARCH_RESULTS), 500))
    except Exception:
        limit = MAX_SEARCH_RESULTS
    vault_root = Path(VAULT_DIR).resolve()
    results: list[str] = []
    needle_lower = needle.lower()
    for file_path in _iter_search_files(root):
        rel = file_path.resolve().relative_to(vault_root).as_posix()
        if file_path.name.startswith(".") or not fnmatch.fnmatch(rel, str(file_glob or "*")):
            continue
        try:
            if _is_binary_file(file_path):
                continue
            with file_path.open("r", encoding="utf-8") as handle:
                for line_no, raw_line in enumerate(handle, start=1):
                    line = raw_line.rstrip("\r\n")
                    if needle_lower in line.lower():
                        results.append(f"{rel}:{line_no}: {line}")
                        if len(results) >= limit:
                            return "\n".join(results) + "\n[Truncated]"
        except Exception:
            continue
    return "\n".join(results) if results else "[No matches]"


def replace_text_in_file(
    path: str,
    old_text: str,
    new_text: str,
    replace_all: bool = False,
) -> str:
    return edit_file(path=path, old_text=old_text, new_text=new_text, replace_all=replace_all)


def edit_code_symbol(
    path: str,
    target_name: str,
    target_type: str,
    new_text: str,
) -> str:
    return edit_file(path=path, target_name=target_name, target_type=target_type, new_text=new_text)


def edit_file(
    path: str,
    new_text: str,
    old_text: str | None = None,
    target_name: str | None = None,
    target_type: str | None = None,
    replace_all: bool = False,
) -> str:
    new_text = str(new_text)

    target_name_str = str(target_name).strip() if target_name else ""
    target_type_str = str(target_type).strip() if target_type else ""
    use_ast = bool(target_name_str) and bool(target_type_str)

    try:
        resolved = resolve_vault_path(path)
        original = _read_text_file(resolved)
    except ValueError as exc:
        return f"Error: {exc}"

    if use_ast:
        ext = resolved.suffix.lower()
        if ext == ".py":
            try:
                from app.backend.tools.builtin.ast_edit_py import ast_edit_py
            except ImportError:
                from builtin.ast_edit_py import ast_edit_py
            try:
                updated = ast_edit_py(original, target_name_str, target_type_str, new_text)
            except ValueError as exc:
                return f"Error: {exc}"
        elif ext in (".js", ".ts"):
            try:
                from app.backend.tools.builtin.ast_edit_js import ast_edit_js
            except ImportError:
                from builtin.ast_edit_js import ast_edit_js
            try:
                updated = ast_edit_js(original, target_name_str, target_type_str, new_text)
            except ValueError as exc:
                return f"Error: {exc}"
        else:
            return f"Error: AST editing not supported for {ext} files"

        try:
            _atomic_write_text(resolved, updated)
        except Exception as exc:  # noqa: BLE001
            return f"Error: Failed to edit file: {exc}"

        after_bytes = len(updated.encode("utf-8"))
        return f"Edited: {path} ({after_bytes} bytes)"

    old_text = "" if old_text is None else str(old_text)
    if old_text == "":
        return "Error: old_text is required."

    match_count = original.count(old_text)
    if match_count == 0:
        return f"Error: old_text was not found in {path}."
    if not replace_all and match_count > 1:
        return f"Error: old_text matched {match_count} times in {path}. Call edit_file again with replace_all=true."

    if replace_all:
        updated = original.replace(old_text, new_text)
        replaced_count = match_count
    else:
        updated = original.replace(old_text, new_text, 1)
        replaced_count = 1

    try:
        _atomic_write_text(resolved, updated)
    except Exception as exc:  # noqa: BLE001
        return f"Error: Failed to edit file: {exc}"

    before_bytes = len(original.encode("utf-8"))
    after_bytes = len(updated.encode("utf-8"))
    occurrence_word = "occurrence" if replaced_count == 1 else "occurrences"
    return (
        f"Edited: {path} - replaced {replaced_count} {occurrence_word} "
        f"(was {before_bytes} bytes, now {after_bytes} bytes)"
    )
