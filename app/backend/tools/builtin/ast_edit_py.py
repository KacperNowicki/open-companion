from __future__ import annotations

import ast


def ast_edit_py(source: str, target_name: str, target_type: str, new_text: str) -> str:
    """AST-aware replacement of a function, method, or top-level variable in Python source."""
    if target_type not in ("function", "method", "variable"):
        raise ValueError(
            f"Unknown target_type: {target_type}. Expected: function, method, variable"
        )

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError(f"Source is not valid Python: {exc}") from exc

    lines = source.splitlines(keepends=True)
    had_trailing_newline = source.endswith(("\n", "\r"))

    if target_type == "function":
        result = _replace_function(tree, lines, target_name, new_text)
    elif target_type == "method":
        if "." not in target_name:
            raise ValueError(
                f"method target_name must be in 'ClassName.method_name' format, got: {target_name}"
            )
        class_name, method_name = target_name.split(".", 1)
        result = _replace_method(tree, lines, class_name, method_name, new_text)
    else:
        result = _replace_variable(tree, lines, source, target_name, new_text)

    if had_trailing_newline and not result.endswith(("\n", "\r")):
        result += "\n"
    elif not had_trailing_newline and result.endswith("\n"):
        result = result.rstrip("\n")

    return result


def _replace_function(tree: ast.Module, lines: list[str], name: str, new_text: str) -> str:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            start_line = node.lineno
            if node.decorator_list:
                start_line = min(d.lineno for d in node.decorator_list)
            end_line = node.end_lineno

            replacement = new_text
            if not replacement.endswith("\n"):
                replacement += "\n"

            before = "".join(lines[: start_line - 1])
            after = "".join(lines[end_line:])
            return before + replacement + after

    raise ValueError(f"No function '{name}' found in file")


def _replace_method(
    tree: ast.Module, lines: list[str], class_name: str, method_name: str, new_text: str
) -> str:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if (
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name == method_name
                ):
                    return _rewrite_method_body(lines, item, new_text)
            raise ValueError(f"No method '{class_name}.{method_name}' found in file")

    raise ValueError(f"No method '{class_name}.{method_name}' found in file")


def _rewrite_method_body(
    lines: list[str],
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    new_text: str,
) -> str:
    if not func_node.body:
        raise ValueError(f"Method '{func_node.name}' has no body")

    first_body_stmt = func_node.body[0]
    body_start_line = first_body_stmt.lineno
    body_end_line = func_node.end_lineno
    body_indent = " " * first_body_stmt.col_offset

    reindented = _reindent_block(new_text, body_indent)
    if not reindented.endswith("\n"):
        reindented += "\n"

    before = "".join(lines[: body_start_line - 1])
    after = "".join(lines[body_end_line:])
    return before + reindented + after


def _reindent_block(text: str, target_indent: str) -> str:
    """Re-indent a block so its minimum indentation equals target_indent."""
    raw_lines = text.splitlines()
    if not raw_lines:
        return target_indent + "pass"

    non_empty = [ln for ln in raw_lines if ln.strip()]
    if not non_empty:
        return target_indent + "pass"

    existing_indent = min(len(ln) - len(ln.lstrip(" \t")) for ln in non_empty)

    out = []
    for ln in raw_lines:
        if not ln.strip():
            out.append("")
        else:
            out.append(target_indent + ln[existing_indent:])
    return "\n".join(out)


def _replace_variable(
    tree: ast.Module, lines: list[str], source: str, name: str, new_text: str
) -> str:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return _splice_value(lines, node.value, new_text)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                if node.value is None:
                    raise ValueError(f"Variable '{name}' has no value to replace")
                return _splice_value(lines, node.value, new_text)

    if _variable_exists_nested(tree, name):
        raise ValueError(f"Variable '{name}' is not at module level")
    raise ValueError(f"No variable '{name}' found in file")


def _variable_exists_nested(tree: ast.Module, name: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return True
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                return True
    return False


def _splice_value(lines: list[str], value_node: ast.AST, new_text: str) -> str:
    start_line = value_node.lineno
    start_col = value_node.col_offset
    end_line = value_node.end_lineno
    end_col = value_node.end_col_offset

    before = "".join(lines[: start_line - 1]) + lines[start_line - 1][:start_col]
    after = lines[end_line - 1][end_col:] + "".join(lines[end_line:])
    return before + new_text + after
