"""AST-aware editing for JavaScript and TypeScript files.

Uses tree-sitter-javascript. TypeScript is parsed by the JS grammar well
enough for v1 replacements of functions/methods/variables.

Method body replacement preserves the braces of the original statement_block
and replaces only the content between them. The caller supplies the new body
without the surrounding `{ }`.
"""
from __future__ import annotations

import site
import sys
from pathlib import Path

project_deps = Path(__file__).resolve().parents[4] / ".python-deps"
if project_deps.exists() and str(project_deps) not in sys.path:
    sys.path.append(str(project_deps))
user_site = site.getusersitepackages()
if user_site and user_site not in sys.path:
    sys.path.append(user_site)

try:
    import tree_sitter_javascript as _tsjs
    from tree_sitter import Language as _Language, Parser as _Parser
except ImportError as exc:  # pragma: no cover - exercised only when deps missing
    raise ImportError(
        "tree-sitter and tree-sitter-javascript are required for ast_edit_js. "
        "Install them with: pip install tree-sitter tree-sitter-javascript"
    ) from exc


_JS_LANGUAGE = _Language(_tsjs.language())
_PARSER = _Parser(_JS_LANGUAGE)


def _parse(source: str):
    tree = _PARSER.parse(source.encode("utf-8"))
    root = tree.root_node
    if root.has_error:
        raise ValueError("JavaScript parse failed")
    return tree, root


def _identifier_name(node, source_bytes: bytes) -> str | None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    return source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8")


def _splice(source_bytes: bytes, start: int, end: int, replacement: str) -> str:
    return (source_bytes[:start] + replacement.encode("utf-8") + source_bytes[end:]).decode("utf-8")


def _find_function(root, source_bytes: bytes, target_name: str):
    """Return (start_byte, end_byte) for the whole declaration of `target_name`.

    Matches:
      - function declarations
      - `const/let/var NAME = arrow_function`
      - `const/let/var NAME = function_expression`
    """
    for node in root.children:
        if node.type == "function_declaration":
            if _identifier_name(node, source_bytes) == target_name:
                return node.start_byte, node.end_byte

        if node.type in ("lexical_declaration", "variable_declaration"):
            # Only match when there's a single declarator whose value is a
            # function/arrow expression. That's the "function-like variable".
            declarators = [c for c in node.children if c.type == "variable_declarator"]
            if len(declarators) != 1:
                continue
            declarator = declarators[0]
            name_node = declarator.child_by_field_name("name")
            value_node = declarator.child_by_field_name("value")
            if name_node is None or value_node is None:
                continue
            name = source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8")
            if name != target_name:
                continue
            if value_node.type in ("arrow_function", "function_expression", "function"):
                return node.start_byte, node.end_byte
    return None


def _find_variable(root, source_bytes: bytes, target_name: str):
    """Return (value_start_byte, value_end_byte) for the RHS of `target_name`."""
    for node in root.children:
        if node.type not in ("lexical_declaration", "variable_declaration"):
            continue
        for declarator in node.children:
            if declarator.type != "variable_declarator":
                continue
            name_node = declarator.child_by_field_name("name")
            value_node = declarator.child_by_field_name("value")
            if name_node is None or value_node is None:
                continue
            name = source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8")
            if name == target_name:
                return value_node.start_byte, value_node.end_byte
    return None


def _iter_classes(root):
    for node in root.children:
        if node.type in ("class_declaration", "class"):
            yield node
        # Classes may live inside `export` statements.
        if node.type.startswith("export"):
            for child in node.children:
                if child.type in ("class_declaration", "class"):
                    yield child


def _find_method_body(root, source_bytes: bytes, class_name: str, method_name: str):
    """Return (body_open_brace_end, body_close_brace_start) for the method body.

    We return the byte range INSIDE the braces, so the caller writes the new
    body without needing to include `{ }`.
    """
    for class_node in _iter_classes(root):
        if _identifier_name(class_node, source_bytes) != class_name:
            continue
        body = class_node.child_by_field_name("body")
        if body is None:
            continue
        for member in body.children:
            if member.type != "method_definition":
                continue
            name_node = member.child_by_field_name("name")
            if name_node is None:
                continue
            name = source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8")
            if name != method_name:
                continue
            block = member.child_by_field_name("body")
            if block is None or block.type != "statement_block":
                continue
            # statement_block is `{ ... }` — splice only the inner region so
            # the method's braces and surrounding signature are preserved.
            inner_start = block.start_byte + 1  # after the `{`
            inner_end = block.end_byte - 1      # before the `}`
            return inner_start, inner_end
    return None


def ast_edit_js(source: str, target_name: str, target_type: str, new_text: str) -> str:
    """Replace a named JS/TS construct in `source` with `new_text`.

    target_type:
      - "function":  replace the entire top-level declaration.
      - "method":    target_name is "ClassName.methodName"; replace the body
                     content between the braces (braces themselves are kept).
      - "variable":  replace just the right-hand side value expression.
    """
    if target_type not in ("function", "method", "variable"):
        raise ValueError(f"Unknown target_type: {target_type}")

    # Validate method target_name format up-front so the caller gets a clearer
    # error than a generic parse failure if the source is also malformed.
    if target_type == "method" and "." not in target_name:
        raise ValueError(
            f"method target_name must be in 'ClassName.methodName' format, got: {target_name}"
        )

    source_bytes = source.encode("utf-8")
    _tree, root = _parse(source)

    if target_type == "function":
        span = _find_function(root, source_bytes, target_name)
        if span is None:
            raise ValueError(f"No function '{target_name}' found in file")
        return _splice(source_bytes, span[0], span[1], new_text)

    if target_type == "variable":
        span = _find_variable(root, source_bytes, target_name)
        if span is None:
            raise ValueError(f"No variable '{target_name}' found in file")
        return _splice(source_bytes, span[0], span[1], new_text)

    # method (format already validated above)
    class_name, method_name = target_name.split(".", 1)
    span = _find_method_body(root, source_bytes, class_name, method_name)
    if span is None:
        raise ValueError(f"No method '{target_name}' found in file")
    return _splice(source_bytes, span[0], span[1], new_text)
