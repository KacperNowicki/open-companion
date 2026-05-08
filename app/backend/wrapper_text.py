"""Text, display, and terminal-log helpers for wrapper.py."""

from __future__ import annotations

import json
import re
import sys

from model_family import strip_gemma_thinking, strip_gemma_thought_blocks, strip_legacy_think_blocks
from runtime_paths import SOUL_ACTIVE_DIR, SOUL_DEFAULTS_DIR


def sanitize_text_for_utf8(value: str) -> str:
    clean = str(value or "").encode("utf-8", errors="replace").decode("utf-8")
    clean = clean.replace("\ufffd", " ")
    clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", clean)
    return re.sub(r"[ \t]{2,}", " ", clean)


def strip_gemma_token_bleed(s: str) -> str:
    """Strip Gemma 4 special token bleed variants from visible text."""
    s = re.sub(r"<\|tool_call>.*?<tool_call\|>", "", s, flags=re.DOTALL)
    s = re.sub(r"call:\w+\{[^}]*\}<tool_call\|>", "", s, flags=re.DOTALL)
    s = re.sub(r"<\|tool_response>.*?<tool_response\|>", "", s, flags=re.DOTALL)
    s = re.sub(r"<\|tool>.*?<tool\|>", "", s, flags=re.DOTALL)
    s = re.sub(r'<\|tool_call>|<tool_call\|>|<\|tool_response>|<tool_response\|>|<\|tool>|<tool\|>|<\|"\|>', "", s)
    s = re.sub(r"<\|?channel\|?>?|<channel\|?>?|\|channel>", "", s, flags=re.IGNORECASE)
    return s


_GEMMA_BLEED_RE = re.compile(
    r"(?:<\|tool_call>)?call:(\w+)\{([^}]*)\}<tool_call\|>",
    re.DOTALL,
)
_GEMMA_BLEED_ARG_RE = re.compile(
    r'(\w+)\s*:\s*<\|"\|>(.*?)<\|"\|>',
    re.DOTALL,
)


def make_brain_tool_call(name: str, arguments: dict, call_id: str):
    from providers import BrainToolCall, BrainToolFunction

    return BrainToolCall(
        id=call_id,
        function=BrainToolFunction(name=name, arguments=json.dumps(arguments, ensure_ascii=False)),
    )


def extract_explicit_vault_file_path(text: str) -> str:
    source = str(text or "")
    pattern = re.compile(
        r"(?<![\w:])([A-Za-z0-9][A-Za-z0-9 _.-]*(?:[/\\][A-Za-z0-9][A-Za-z0-9 _.-]*)+\.(?:md|txt|json|js|ts|py))",
        re.IGNORECASE,
    )
    match = pattern.search(source)
    if not match:
        return ""
    path = match.group(1).replace("\\", "/").strip(" .")
    lowered = path.lower()
    for marker in ("read the file ", " file called ", " file named ", " called ", " named ", " path "):
        marker_index = lowered.rfind(marker)
        if marker_index >= 0:
            path = path[marker_index + len(marker):].strip(" .")
            lowered = path.lower()
    return path


def recover_gemma_text_tool_calls(content: str):
    """Parse Gemma text-bleed tool calls and synthesize BrainToolCall objects."""
    calls = []
    for i, match in enumerate(_GEMMA_BLEED_RE.finditer(content)):
        tool_name = match.group(1)
        args_blob = match.group(2)
        args = {}
        for arg_match in _GEMMA_BLEED_ARG_RE.finditer(args_blob):
            args[arg_match.group(1)] = arg_match.group(2)
        calls.append(make_brain_tool_call(tool_name, args, f"gemma_bleed_{i}"))
    cleaned = _GEMMA_BLEED_RE.sub("", content).strip()
    return calls, cleaned


def strip_display_artifacts(text: str) -> str:
    """Remove internal model artifacts that should never appear in the visible UI."""
    s = str(text or "")
    s = strip_gemma_thinking(s)
    s = strip_gemma_token_bleed(s)
    s = strip_gemma_thought_blocks(s)
    s = strip_legacy_think_blocks(s)
    s = re.sub(r"\[calls\s+[^\]]*?\]", "", s)
    s = re.sub(r"(?<!\*)\*\*([^*]+?)\*\*(?!\*)", r"\1", s)
    s = re.sub(r"(?<!_)__([^_]+?)__(?!_)", r"\1", s)
    s = re.sub(r"(?<!\*)\*[^*]+?\*(?!\*)", "", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def tool_result_looks_like_failure(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    failure_markers = (
        "error:",
        "failed",
        "not found",
        "unavailable",
        "timed out",
        "access is denied",
        "denied",
        "no data",
        "could not",
        "cannot ",
        "can't ",
    )
    return any(marker in normalized for marker in failure_markers)


def sanitize_json_payload(value):
    if isinstance(value, str):
        return sanitize_text_for_utf8(value)
    if isinstance(value, list):
        return [sanitize_json_payload(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_json_payload(item) for item in value]
    if isinstance(value, dict):
        return {
            sanitize_text_for_utf8(key) if isinstance(key, str) else key: sanitize_json_payload(item)
            for key, item in value.items()
        }
    return value


def soul_path(filename: str) -> str:
    """Return active soul path when present, otherwise the shipped default path."""
    active = SOUL_ACTIVE_DIR / filename
    if active.exists():
        return str(active)
    return str(SOUL_DEFAULTS_DIR / filename)


def extract_user_text(message_content) -> str:
    if isinstance(message_content, str):
        return message_content
    if isinstance(message_content, list):
        parts = []
        for item in message_content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                text = str(item.get("text") or "").strip()
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()
    return ""


def truncate_terminal_text(text: str, limit: int = 300) -> str:
    clean = sanitize_text_for_utf8(text).replace("\n", "\\n")
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..."


def quote_terminal_text(text: str) -> str:
    return json.dumps(sanitize_text_for_utf8(text), ensure_ascii=False)


def summarize_terminal_content(content) -> str:
    if isinstance(content, str):
        return truncate_terminal_text(content)
    if isinstance(content, list):
        parts = []
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip().lower()
            if item_type in {"text", "output_text"}:
                text = str(item.get("text") or item.get("content") or "").strip()
                if text:
                    parts.append(text)
            elif item_type in {"image_url", "input_image"}:
                parts.append("[image]")
            elif item_type:
                parts.append(f"[{item_type}]")
        return truncate_terminal_text(" | ".join(parts))
    if isinstance(content, dict):
        redacted = {k: ("[image]" if k in {"image_url", "images"} else v) for k, v in content.items()}
        return truncate_terminal_text(json.dumps(redacted, ensure_ascii=False))
    return truncate_terminal_text(str(content or ""))


def log_terminal_message(direction: str, layer_name: str, message: dict) -> None:
    if not isinstance(message, dict):
        return
    role = str(message.get("role") or "unknown")
    content = summarize_terminal_content(message.get("content"))
    print(
        f"[{direction}] layer={layer_name} role={role} content={quote_terminal_text(content)}",
        file=sys.stderr,
        flush=True,
    )
    for tool_call in message.get("tool_calls") or []:
        function = tool_call.get("function") or {}
        print(
            f"[{direction} TOOL_CALL] layer={layer_name} name={quote_terminal_text(str(function.get('name', '')))} arguments={quote_terminal_text(truncate_terminal_text(str(function.get('arguments', ''))))}",
            file=sys.stderr,
            flush=True,
        )
    for tool_response in message.get("tool_responses") or []:
        response_value = tool_response.get("response")
        response_text = response_value if isinstance(response_value, str) else json.dumps(response_value, ensure_ascii=False)
        print(
            f"[{direction} TOOL_RESPONSE] layer={layer_name} name={quote_terminal_text(str(tool_response.get('name', '')))} response={quote_terminal_text(truncate_terminal_text(str(response_text)))}",
            file=sys.stderr,
            flush=True,
        )


def looks_like_capability_query(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(text or "").strip().lower())
    if not normalized:
        return False
    capability_phrases = (
        "what can you do",
        "what do you do",
        "what are your capabilities",
        "what can u do",
        "show me your tools",
        "list your tools",
        "list available tools",
        "what tools do you have",
        "what commands do you have",
    )
    return any(phrase in normalized for phrase in capability_phrases)


def extract_spotify_query(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    lowered = normalized.lower().strip(" ?!.,")
    music_markers = ("play ", "put on ", "start ", "spotify ")
    if not any(lowered.startswith(marker) for marker in music_markers):
        return ""

    query = lowered
    for prefix in ("play ", "put on ", "start ", "spotify "):
        if query.startswith(prefix):
            query = query[len(prefix):]
            break

    query = re.sub(r"\bon spotify\b", "", query).strip(" ?!.,")
    if query.startswith("some "):
        query = query[5:].strip()
    if query.startswith("a bit of "):
        query = query[9:].strip()
    if query.startswith("some music"):
        query = query.replace("some music", "music", 1).strip()

    return query or ""


def strip_legacy_json_directives(reply_text: str) -> str:
    text = str(reply_text or "")
    lines = text.splitlines()
    remaining_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            remaining_lines.append(line)
            continue

        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            remaining_lines.append(line)
            continue

        if not isinstance(payload, dict) or payload.get("type") != "animation":
            remaining_lines.append(line)
            continue

    return "\n".join(remaining_lines).strip() or "[No response]"


def one_sentence_description(description: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(description or "")).strip()
    if not cleaned:
        return ""
    match = re.search(r"^(.+?[.!?])(?:\s|$)", cleaned)
    return match.group(1).strip() if match else cleaned
