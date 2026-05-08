from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from providers.base import BrainMessage, BrainToolCall, BrainToolFunction, normalize_message_content, normalize_tool_calls
from tool_schema import validate_tool_arguments


HIDDEN_VISIBLE_PATTERNS = [
    re.compile(r"(?is)<\|?channel\|?>?\s*thought\s*\n.*?(?:<\|?channel\|?>?|<channel\|?>?|\|channel>)"),
    re.compile(r"(?is)<think(?:ing)?>.*?</think(?:ing)?>"),
    re.compile(r"(?is)<tool_call>.*?</tool_call>"),
    re.compile(r"(?is)<\|tool_call>.*?<tool_call\|>"),
    re.compile(r"(?is)<\|tool_response>.*?<tool_response\|>"),
    re.compile(r"(?im)^\s*(tool_code|reasoning|thought)\s*[:\n].*$"),
]


@dataclass
class NormalizedToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    source: str = "official_structured"
    provider: str = ""


@dataclass
class NormalizedProviderResponse:
    contentForUser: str
    hiddenReasoning: str | None = None
    toolCalls: list[NormalizedToolCall] = field(default_factory=list)
    rawProviderResponse: Any = None


def filter_visible_output(text: str) -> str:
    cleaned = str(text or "")
    for pattern in HIDDEN_VISIBLE_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = re.sub(r"</?\|?(?:channel|tool_call|tool_response|tool|thought|reasoning)\|?>", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("<|\"|>", '"')
    return cleaned.strip()


def brain_tool_call_from_normalized(call: NormalizedToolCall) -> BrainToolCall:
    return BrainToolCall(
        id=call.id,
        type="function",
        function=BrainToolFunction(call.name, json.dumps(call.arguments, ensure_ascii=False)),
        source=call.source,
        provider=call.provider,
    )


def normalize_brain_message(message: BrainMessage, provider: str = "") -> NormalizedProviderResponse:
    calls: list[NormalizedToolCall] = []
    for index, call in enumerate(normalize_tool_calls(getattr(message, "tool_calls", None))):
        try:
            args = json.loads(call.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        calls.append(NormalizedToolCall(
            id=call.id or f"tool_call_{index}",
            name=call.function.name,
            arguments=args if isinstance(args, dict) else {},
            source=getattr(call, "source", "official_structured") or "official_structured",
            provider=getattr(call, "provider", provider) or provider,
        ))
    return NormalizedProviderResponse(
        contentForUser=filter_visible_output(getattr(message, "content", "") or ""),
        hiddenReasoning=getattr(message, "hidden_reasoning", None),
        toolCalls=calls,
        rawProviderResponse=getattr(message, "raw", None),
    )


def _parse_gemma_call_payload(payload: str) -> tuple[str, dict[str, Any]] | None:
    text = payload.strip()
    call_match = re.match(r"^call:([A-Za-z_][\w-]*)\s*(\{.*\})$", text, re.DOTALL)
    if call_match:
        name = call_match.group(1)
        args_text = call_match.group(2).replace('<|"|>', '"')
        args_text = re.sub(r"([,{]\s*)([A-Za-z_][\w-]*)(\s*:)", r'\1"\2"\3', args_text)
        try:
            args = json.loads(args_text)
        except Exception:
            return None
        return name, args if isinstance(args, dict) else {}
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    function = parsed.get("function") if isinstance(parsed.get("function"), dict) else {}
    name = str(function.get("name") or parsed.get("tool_name") or parsed.get("name") or parsed.get("tool_call") or "").strip()
    args = function.get("arguments") if function else None
    if args is None:
        args = parsed.get("arguments", parsed.get("args", parsed.get("params", parsed.get("parameters", {}))))
    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except Exception:
            return None
    if not name or not isinstance(args, dict):
        return None
    return name, args


def recover_gemma_ollama_tool_calls(
    content: str,
    *,
    provider: str,
    allowed_tool_schemas: dict[str, dict],
) -> tuple[str, list[NormalizedToolCall], list[str]]:
    """Conservative recovery for observed Gemma/Ollama raw tool leakage.

    Recovery is route-aware: candidates whose tool is not in the current exposed
    set are rejected before becoming runtime tool calls.
    """
    raw = str(content or "")
    diagnostics: list[str] = []
    calls: list[NormalizedToolCall] = []

    candidates: list[tuple[str, tuple[int, int]]] = []
    for match in re.finditer(r"<\|tool_call>(.*?)<tool_call\|>", raw, re.DOTALL | re.IGNORECASE):
        candidates.append((match.group(1).strip(), match.span()))
    if not candidates:
        stripped = raw.strip()
        if re.fullmatch(r"\{.*\}", stripped, flags=re.DOTALL):
            candidates.append((stripped, (raw.find(stripped), raw.find(stripped) + len(stripped))))
        else:
            match = re.search(r"call:[A-Za-z_][\w-]*\{.*?\}<tool_call\|>", raw, re.DOTALL)
            if match:
                payload = match.group(0).removesuffix("<tool_call|>")
                candidates.append((payload, match.span()))

    remove_spans: list[tuple[int, int]] = []
    for index, (payload, span) in enumerate(candidates):
        parsed = _parse_gemma_call_payload(payload)
        if not parsed:
            diagnostics.append("raw candidate did not match a strict supported syntax")
            continue
        name, args = parsed
        schema = allowed_tool_schemas.get(name)
        if not schema:
            diagnostics.append(f"recovered_provider_tool_call blocked: {name} is not exposed for this route")
            remove_spans.append(span)
            continue
        validation = validate_tool_arguments(schema, args)
        if not validation.ok:
            diagnostics.append(f"recovered_provider_tool_call blocked: invalid args for {name}: {validation.reason}")
            remove_spans.append(span)
            continue
        calls.append(NormalizedToolCall(
            id=f"recovered_provider_tool_call_{index}",
            name=name,
            arguments=args,
            source="recovered_provider_content",
            provider=provider,
        ))
        diagnostics.append(f"recovered_provider_tool_call accepted: {name}")
        remove_spans.append(span)

    cleaned = raw
    for start, end in sorted(remove_spans, reverse=True):
        cleaned = cleaned[:start] + cleaned[end:]
    return filter_visible_output(cleaned), calls, diagnostics
