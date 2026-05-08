from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from anthropic_models import get_anthropic_model_card
from gemini_models import get_gemini_model_card
from openai_models import get_openai_model_card
from qwen_models import get_qwen_model_card


GEMMA_CHANNEL_MARKER_RE = re.compile(r"<\|?channel\|?>?|<channel\|?>?|\|channel>", re.IGNORECASE)
GEMMA_THOUGHT_BLOCK_RE = re.compile(
    r"(?:<\|?channel\|?>?\s*)?thought\s*\n.*?(?:<\|?channel\|?>?|<channel\|?>?|\|channel>)",
    re.DOTALL | re.IGNORECASE,
)
LEGACY_THINK_BLOCK_RE = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL | re.IGNORECASE)
QWEN_REASONING_BLOCK_RE = re.compile(r"<reasoning_content>.*?</reasoning_content>", re.DOTALL | re.IGNORECASE)
QWEN_TOOL_CALL_BLOCK_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL | re.IGNORECASE)
GEMMA_TOOL_CALL_BLOCK_RE = re.compile(r"<\|tool_call>(.*?)<tool_call\|>", re.DOTALL | re.IGNORECASE)
GEMMA_TOOL_CALL_MARKER_RE = re.compile(r"<\|tool_call>|<tool_call\|>", re.IGNORECASE)
GEMMA_TOOL_RESPONSE_MARKER_RE = re.compile(r"</?\|?tool_response\|?>", re.IGNORECASE)
GEMMA_FULL_THINKING_BLOCK_RE = GEMMA_THOUGHT_BLOCK_RE
GEMMA_BARE_THINKING_BLOCK_RE = re.compile(
    r"^thought\s*\n.*?(?:<\|?channel\|?>?|<channel\|?>?|\|channel>)",
    re.DOTALL | re.IGNORECASE,
)


def strip_gemma_thought_blocks(text: str) -> str:
    return GEMMA_THOUGHT_BLOCK_RE.sub("", str(text or ""))


def strip_legacy_think_blocks(text: str) -> str:
    return LEGACY_THINK_BLOCK_RE.sub("", str(text or ""))


def strip_qwen_reasoning(text: str) -> str:
    cleaned = QWEN_REASONING_BLOCK_RE.sub("", str(text or ""))
    cleaned = strip_legacy_think_blocks(cleaned)
    return cleaned.strip()


def _extract_qwen_tool_calls(content: str) -> tuple[str, list[dict[str, Any]]]:
    raw_content = str(content or "")
    tool_calls: list[dict[str, Any]] = []
    for index, match in enumerate(QWEN_TOOL_CALL_BLOCK_RE.finditer(raw_content)):
        block = match.group(1).strip()
        try:
            payload = json.loads(block)
        except Exception:
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            name = str(candidate.get("name") or candidate.get("tool_name") or "").strip()
            arguments = candidate.get("arguments")
            if not name:
                continue
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments if isinstance(arguments, dict) else {}, ensure_ascii=False)
            tool_calls.append({
                "id": f"qwen_tool_call_{len(tool_calls) or index}",
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            })
    cleaned = QWEN_TOOL_CALL_BLOCK_RE.sub("", raw_content).strip()
    return cleaned, tool_calls


def strip_gemma_thinking(text: str) -> str:
    """
    Strip Gemma 4 thinking/channel tokens from completed reply text.

    This should only run on finalized replies or stored completed assistant turns,
    not on in-progress tool-loop messages.
    """
    if not text:
        return text

    cleaned = str(text)
    cleaned = GEMMA_FULL_THINKING_BLOCK_RE.sub("", cleaned)
    cleaned = GEMMA_BARE_THINKING_BLOCK_RE.sub("", cleaned)
    cleaned = re.sub(r"(?is)(?:<\|?channel\|?>?\s*)?thought\s*\n.*", "", cleaned)
    cleaned = GEMMA_CHANNEL_MARKER_RE.sub("", cleaned)

    if cleaned.startswith("thought\n"):
        parts = cleaned.split("\n\n", 1)
        cleaned = parts[1] if len(parts) == 2 else ""

    return cleaned.strip()


KNOWN_FAMILIES = frozenset({
    "google_gemma",
    "openai_reasoning",
    "openai_chat",
    "qwen",
    "gemini",
    "openrouter",
    "anthropic_claude_reasoning",
    "anthropic_claude_chat",
})


def resolve_model_family(settings: dict | None) -> str:
    config = settings or {}
    explicit_family = str(config.get("family") or "").strip().lower()
    if explicit_family in KNOWN_FAMILIES:
        return explicit_family

    provider = str(config.get("provider") or "").strip().lower()
    if provider == "openai":
        return get_openai_model_card(config.get("model")).family
    if provider == "anthropic":
        return get_anthropic_model_card(config.get("model")).family
    if provider == "gemini":
        return get_gemini_model_card(config.get("model")).family
    if provider == "openrouter":
        return "openrouter"
    if provider in {"qwen", "qwen_cloud"}:
        return get_qwen_model_card(config.get("model")).family

    model = str(config.get("model") or "").strip().lower()
    if "gemma" in model:
        return "google_gemma"
    if "qwen" in model:
        return "qwen"

    return "generic"


class _GemmaArgumentParser:
    def __init__(self, source: str):
        self.source = source
        self.index = 0

    def parse(self) -> Any:
        value = self._parse_value()
        self._skip_ws()
        if self.index != len(self.source):
            raise ValueError("Unexpected trailing content in Gemma tool payload.")
        return value

    def _skip_ws(self) -> None:
        while self.index < len(self.source) and self.source[self.index].isspace():
            self.index += 1

    def _consume(self, token: str) -> None:
        self._skip_ws()
        if not self.source.startswith(token, self.index):
            raise ValueError(f"Expected {token!r}")
        self.index += len(token)

    def _peek(self) -> str:
        self._skip_ws()
        return self.source[self.index:self.index + 1]

    def _parse_value(self) -> Any:
        self._skip_ws()
        token = self._peek()
        if token == "{":
            return self._parse_object()
        if token == "[":
            return self._parse_array()
        if self.source.startswith('<|"|>', self.index):
            return self._parse_string()
        return self._parse_atom()

    def _parse_object(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        self._consume("{")
        self._skip_ws()
        if self._peek() == "}":
            self.index += 1
            return result

        while True:
            key = self._parse_key()
            self._consume(":")
            result[key] = self._parse_value()
            self._skip_ws()
            token = self._peek()
            if token == "}":
                self.index += 1
                return result
            self._consume(",")

    def _parse_array(self) -> list[Any]:
        result: list[Any] = []
        self._consume("[")
        self._skip_ws()
        if self._peek() == "]":
            self.index += 1
            return result

        while True:
            result.append(self._parse_value())
            self._skip_ws()
            token = self._peek()
            if token == "]":
                self.index += 1
                return result
            self._consume(",")

    def _parse_key(self) -> str:
        self._skip_ws()
        if self.source.startswith('<|"|>', self.index):
            return self._parse_string()
        match = re.match(r"[A-Za-z0-9_.-]+", self.source[self.index:])
        if not match:
            raise ValueError("Invalid Gemma object key.")
        key = match.group(0)
        self.index += len(key)
        return key

    def _parse_string(self) -> str:
        delimiter = '<|"|>'
        self._consume(delimiter)
        end = self.source.find(delimiter, self.index)
        if end == -1:
            raise ValueError("Unterminated Gemma string delimiter.")
        value = self.source[self.index:end]
        self.index = end + len(delimiter)
        return value

    def _parse_atom(self) -> Any:
        match = re.match(r"[^,\]\}\s]+", self.source[self.index:])
        if not match:
            raise ValueError("Invalid Gemma value.")
        token = match.group(0)
        self.index += len(token)
        lowered = token.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        if lowered == "null":
            return None
        if re.fullmatch(r"-?\d+", token):
            return int(token)
        if re.fullmatch(r"-?(?:\d+\.\d*|\d*\.\d+)", token):
            return float(token)
        return token


def _parse_gemma_arguments(source: str) -> dict[str, Any]:
    source = str(source or "").strip()
    if not source:
        return {}
    try:
        parsed = _GemmaArgumentParser(source).parse()
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_json_like_tool_arguments(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        return payload
    if payload is None:
        return {}
    if not isinstance(payload, str):
        return None
    raw = payload.strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = _parse_gemma_arguments(raw)
    return parsed if isinstance(parsed, dict) else None


def _normalize_raw_tool_call_candidate(payload: Any, index: int) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    name = ""
    arguments: Any = None

    def _first_argument_alias(mapping: dict, *keys: str) -> Any:
        for key in keys:
            value = mapping.get(key)
            if value is not None:
                return value
        return None

    tool_call_name = payload.get("tool_call")
    if isinstance(tool_call_name, str):
        name = tool_call_name.strip()
        arguments = _first_argument_alias(payload, "arguments", "args", "params", "parameters")
    elif isinstance(payload.get("function"), str):
        name = str(payload.get("function") or payload.get("tool_name") or payload.get("name") or "").strip()
        arguments = _first_argument_alias(payload, "arguments", "args", "params", "parameters")
    elif isinstance(payload.get("function"), dict):
        function = payload.get("function") or {}
        name = str(function.get("name") or payload.get("tool_name") or payload.get("name") or "").strip()
        arguments = _first_argument_alias(function, "arguments", "args", "params", "parameters")
        if arguments is None:
            arguments = _first_argument_alias(payload, "arguments", "args", "params", "parameters")
    else:
        name = str(payload.get("tool_name") or payload.get("name") or "").strip()
        arguments = _first_argument_alias(payload, "arguments", "args", "params", "parameters")

    if not name:
        return None
    parsed_arguments = _parse_json_like_tool_arguments(arguments)
    if parsed_arguments is None:
        return None
    return {
        "id": f"gemma_tool_call_{index}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(parsed_arguments, ensure_ascii=False),
        },
    }


def _parse_raw_gemma_tool_calls(source: str) -> list[dict[str, Any]]:
    raw = str(source or "").strip()
    if not raw:
        return []
    raw = GEMMA_TOOL_CALL_MARKER_RE.sub("", raw).strip()
    raw = GEMMA_TOOL_RESPONSE_MARKER_RE.sub("", raw).strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
        raw = raw.strip()
    try:
        payload = json.loads(raw)
    except Exception:
        # Gemma often appends a bare `thought\n...` reasoning trace after the
        # tool-call JSON. Use raw_decode so trailing garbage does not destroy
        # an otherwise valid leading JSON document.
        try:
            payload, _end = json.JSONDecoder().raw_decode(raw)
        except Exception:
            return []

    tool_calls: list[dict[str, Any]] = []
    if isinstance(payload, dict) and isinstance(payload.get("tool_calls"), list):
        candidates = payload.get("tool_calls") or []
    else:
        candidates = payload if isinstance(payload, list) else [payload]
    for index, candidate in enumerate(candidates):
        normalized = _normalize_raw_tool_call_candidate(candidate, index)
        if normalized is not None:
            tool_calls.append(normalized)
    return tool_calls


def _extract_raw_tool_calls_from_text(source: str) -> tuple[str, list[dict[str, Any]]]:
    normalized = strip_gemma_thinking(strip_legacy_think_blocks(source)).strip()
    if not normalized:
        return "", []

    whole_payload_calls = _parse_raw_gemma_tool_calls(normalized)
    if whole_payload_calls:
        return "", whole_payload_calls

    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\{\[]", normalized):
        candidate = normalized[match.start():].strip()
        try:
            payload, end = decoder.raw_decode(candidate)
        except Exception:
            continue
        parsed_calls: list[dict[str, Any]] = []
        candidates = payload if isinstance(payload, list) else [payload]
        for index, item in enumerate(candidates):
            normalized_call = _normalize_raw_tool_call_candidate(item, index)
            if normalized_call is not None:
                parsed_calls.append(normalized_call)
        if parsed_calls:
            visible = normalized[:match.start()].strip()
            trailing = candidate[end:].strip()
            if trailing:
                visible = f"{visible}\n{strip_gemma_thinking(strip_legacy_think_blocks(trailing)).strip()}".strip()
            return visible, parsed_calls

    lines = normalized.splitlines()
    for start in range(1, len(lines)):
        candidate = "\n".join(lines[start:]).strip()
        if not candidate or candidate[:1] not in "{[`":
            continue
        candidate_calls = _parse_raw_gemma_tool_calls(candidate)
        if candidate_calls:
            visible = "\n".join(lines[:start]).strip()
            return visible, candidate_calls
    return normalized, []


def _extract_gemma_tool_calls(content: str) -> tuple[str, list[dict[str, Any]]]:
    raw_content = str(content or "")
    tool_calls: list[dict[str, Any]] = []

    for index, match in enumerate(GEMMA_TOOL_CALL_BLOCK_RE.finditer(raw_content)):
        block = match.group(1).strip()
        call_match = re.search(r"call:([A-Za-z0-9_.-]+)\s*(\{.*\})", block, flags=re.DOTALL)
        if not call_match:
            continue
        name = call_match.group(1).strip()
        arguments = _parse_gemma_arguments(call_match.group(2))
        tool_calls.append({
            "id": f"gemma_tool_call_{index}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(arguments, ensure_ascii=False),
            },
        })

    cleaned = GEMMA_TOOL_CALL_BLOCK_RE.sub("", raw_content)
    cleaned = GEMMA_TOOL_RESPONSE_MARKER_RE.sub("", cleaned)
    cleaned = cleaned.strip()
    if not tool_calls:
        visible_text, raw_tool_calls = _extract_raw_tool_calls_from_text(cleaned)
        if raw_tool_calls:
            return visible_text, raw_tool_calls
    return cleaned, tool_calls


@dataclass(slots=True)
class ModelFamilyAdapter:
    family: str = "generic"

    def parse_tool_calls(
        self,
        content: str,
        tool_calls: list[dict[str, Any]] | None,
        *,
        recover_raw: bool = False,
        allowed_tool_names: set[str] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        return str(content or ""), list(tool_calls or [])

    def append_assistant_tool_call_message(
        self,
        conversation: list[dict],
        *,
        content: str,
        tool_calls: list[dict[str, Any]],
    ) -> int:
        conversation.append({
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls,
        })
        return len(conversation) - 1

    def append_tool_result(
        self,
        conversation: list[dict],
        *,
        assistant_index: int | None,
        tool_call: dict[str, Any],
        result: Any,
    ) -> None:
        conversation.append({
            "role": "tool",
            "tool_call_id": tool_call.get("id"),
            "content": str(result),
        })

    def finalize_assistant_turn(
        self,
        conversation: list[dict],
        *,
        assistant_index: int | None,
        content: str,
    ) -> int:
        conversation.append({"role": "assistant", "content": content})
        return len(conversation) - 1

    def sanitize_completed_turn(self, conversation: list[dict], assistant_indexes: list[int]) -> None:
        return None

    def strip_final_reply(self, text: str) -> str:
        return str(text or "")


class GemmaFamilyAdapter(ModelFamilyAdapter):
    def __init__(self) -> None:
        super().__init__(family="google_gemma")

    def parse_tool_calls(
        self,
        content: str,
        tool_calls: list[dict[str, Any]] | None,
        *,
        recover_raw: bool = False,
        allowed_tool_names: set[str] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        existing = list(tool_calls or [])
        if existing or not recover_raw:
            return str(content or ""), existing
        cleaned_content, parsed_calls = _extract_gemma_tool_calls(content)
        if allowed_tool_names is not None:
            parsed_calls = [
                call for call in parsed_calls
                if (((call.get("function") or {}).get("name") or "") in allowed_tool_names)
            ]
        return cleaned_content, existing or parsed_calls

    def sanitize_completed_turn(self, conversation: list[dict], assistant_indexes: list[int]) -> None:
        for index in assistant_indexes:
            if index < 0 or index >= len(conversation):
                continue
            message = conversation[index]
            if message.get("role") != "assistant":
                continue
            message["content"] = self.strip_final_reply(message.get("content", ""))

    def strip_final_reply(self, text: str) -> str:
        return strip_gemma_thinking(text)


class QwenFamilyAdapter(ModelFamilyAdapter):
    def __init__(self) -> None:
        super().__init__(family="qwen")

    def parse_tool_calls(
        self,
        content: str,
        tool_calls: list[dict[str, Any]] | None,
        *,
        recover_raw: bool = False,
        allowed_tool_names: set[str] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        existing = list(tool_calls or [])
        if existing or not recover_raw:
            return str(content or ""), existing
        cleaned_content, parsed_calls = _extract_qwen_tool_calls(content)
        if allowed_tool_names is not None:
            parsed_calls = [
                call for call in parsed_calls
                if (((call.get("function") or {}).get("name") or "") in allowed_tool_names)
            ]
        return cleaned_content, existing or parsed_calls

    def sanitize_completed_turn(self, conversation: list[dict], assistant_indexes: list[int]) -> None:
        for index in assistant_indexes:
            if index < 0 or index >= len(conversation):
                continue
            message = conversation[index]
            if message.get("role") != "assistant":
                continue
            message["content"] = self.strip_final_reply(message.get("content", ""))

    def strip_final_reply(self, text: str) -> str:
        return strip_qwen_reasoning(text)


_GENERIC_ADAPTER = ModelFamilyAdapter()
_GEMMA_ADAPTER = GemmaFamilyAdapter()
_QWEN_ADAPTER = QwenFamilyAdapter()


def get_family_adapter(settings: dict | None) -> ModelFamilyAdapter:
    family = resolve_model_family(settings)
    if family == "google_gemma":
        return _GEMMA_ADAPTER
    if family == "qwen":
        return _QWEN_ADAPTER
    return _GENERIC_ADAPTER
