from __future__ import annotations

import logging
from typing import Any

from openai import OpenAI

from openai_models import OpenAIModelCard, get_openai_model_card
from debug_log import debug_tool_schema
from .base import (
    BrainMessage,
    BrainProvider,
    BrainProviderAuthError,
    BrainProviderBadRequestError,
    BrainProviderConnectionError,
    BrainProviderError,
    BrainProviderRateLimitError,
    normalize_message_content,
    normalize_tool_calls,
)

logger = logging.getLogger(__name__)

OPENAI_BASE_URL = "https://api.openai.com/v1"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
OLLAMA_BASE_URL = "http://localhost:11434/v1"


def _get_attr(value: Any, key: str, default=None):
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _stringify_error(exc: Exception) -> str:
    return str(exc).strip() or exc.__class__.__name__


def _error_status(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "status", None)
    try:
        return int(status) if status is not None else None
    except Exception:
        return None


def _translate_openai_error(provider_name: str, exc: Exception) -> Exception:
    status = _error_status(exc)
    text = _stringify_error(exc)
    lowered = text.lower()
    if status in {401, 403} or "authentication" in lowered or "api key" in lowered:
        return BrainProviderAuthError(f"{provider_name} authentication failed: {text}")
    if status == 429 or "rate limit" in lowered or "too many requests" in lowered:
        return BrainProviderRateLimitError(f"{provider_name} rate limit exceeded: {text}")
    if status and 400 <= status < 500:
        return BrainProviderBadRequestError(f"{provider_name} request rejected: {text}")
    if "timeout" in lowered or "connection" in lowered or "network" in lowered:
        return BrainProviderConnectionError(f"{provider_name} connection failed: {text}")
    return BrainProviderError(f"{provider_name} error: {text}")


class OpenAICompatibleBrainProvider(BrainProvider):
    def __init__(
        self,
        settings: dict,
        layer_name: str = "companion",
        *,
        provider_name: str,
        base_url: str,
        api_key: str,
        requires_api_key: bool = False,
    ) -> None:
        super().__init__(settings, layer_name)
        self.provider_name = provider_name
        self.base_url = str(base_url or "").rstrip("/")
        self.api_key = str(api_key or "")
        self.requires_api_key = requires_api_key
        self._missing_api_key = bool(requires_api_key and not self.api_key)
        self._client = OpenAI(base_url=self.base_url, api_key=self.api_key or "ollama") if self.base_url else None
        self._active_model_card = get_openai_model_card(self.model) if self.provider_name == "openai" else None

    def _ensure_ready(self) -> None:
        if self._missing_api_key:
            raise BrainProviderAuthError(
                f"{self.provider_name} API key is missing from keyring for this profile."
            )
        if not self.base_url:
            raise BrainProviderError(f"{self.provider_name} base URL is not configured.")

    def _build_messages(self, messages: list[dict], system: str) -> list[dict]:
        normalized = list(messages or [])
        if system:
            return [{"role": "system", "content": system}, *normalized]
        return normalized

    def _build_responses_input(self, messages: list[dict]) -> list[dict]:
        items: list[dict] = []
        for message in messages or []:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "").strip().lower()
            content = normalize_message_content(message.get("content"))
            tool_calls = normalize_tool_calls(message.get("tool_calls"))

            if role in {"user", "assistant"} and content:
                items.append({"role": role, "content": content})

            if role == "assistant":
                for tool_call in tool_calls:
                    if not tool_call.function.name:
                        continue
                    items.append({
                        "type": "function_call",
                        "call_id": tool_call.id,
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments or "{}",
                    })
                continue

            if role == "tool":
                tool_call_id = str(message.get("tool_call_id") or "").strip()
                if not tool_call_id:
                    continue
                items.append({
                    "type": "function_call_output",
                    "call_id": tool_call_id,
                    "output": content,
                })

        return items

    def _parse_response_message_content(self, item: Any) -> str:
        parts: list[str] = []
        for content_item in _get_attr(item, "content", []) or []:
            content_type = str(_get_attr(content_item, "type", "") or "").strip().lower()
            if content_type not in {"output_text", "text"}:
                continue
            text = str(_get_attr(content_item, "text", "") or _get_attr(content_item, "content", "") or "").strip()
            if text:
                parts.append(text)
        return "\n".join(parts).strip()

    def _parse_responses_output(self, response: Any, model: str | None = None) -> BrainMessage:
        text_parts: list[str] = []
        raw_tool_calls: list[dict[str, Any]] = []

        for item in _get_attr(response, "output", []) or []:
            item_type = str(_get_attr(item, "type", "") or "").strip().lower()
            if item_type == "message":
                parsed_text = self._parse_response_message_content(item)
                if parsed_text:
                    text_parts.append(parsed_text)
                continue
            if item_type != "function_call":
                continue

            name = str(_get_attr(item, "name", "") or "").strip()
            if not name:
                continue
            raw_tool_calls.append({
                "id": str(_get_attr(item, "call_id", "") or _get_attr(item, "id", "") or f"tool_call_{len(raw_tool_calls)}"),
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": _get_attr(item, "arguments", ""),
                },
            })

        if not text_parts:
            output_text = normalize_message_content(_get_attr(response, "output_text", None))
            if output_text:
                text_parts.append(output_text)

        content = "\n".join(part for part in text_parts if part).strip()
        content, parsed_tool_calls = self.family_adapter.parse_tool_calls(content, raw_tool_calls)
        return BrainMessage(
            content=content,
            tool_calls=normalize_tool_calls(parsed_tool_calls),
            role="assistant",
            model=model,
            raw=response,
        )

    def _parse_message(self, message: Any, model: str | None = None) -> BrainMessage:
        content = normalize_message_content(getattr(message, "content", None))
        raw_tool_calls = getattr(message, "tool_calls", None)
        content, parsed_tool_calls = self.family_adapter.parse_tool_calls(content, raw_tool_calls)
        tool_calls = normalize_tool_calls(parsed_tool_calls)
        return BrainMessage(
            content=content,
            tool_calls=tool_calls,
            role=str(getattr(message, "role", "assistant") or "assistant"),
            model=model,
            raw=message,
        )

    def _parse_stream(self, stream_response, model: str | None = None, stream_handler=None) -> BrainMessage:
        text_parts: list[str] = []
        tool_call_parts: dict[int, dict[str, Any]] = {}

        for chunk in stream_response:
            if not getattr(chunk, "choices", None):
                continue
            choice = chunk.choices[0]
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue

            content = getattr(delta, "content", None)
            if isinstance(content, str) and content:
                text_parts.append(content)
                if stream_handler is not None:
                    try:
                        stream_handler(content)
                    except Exception:
                        pass

            for tool_call in getattr(delta, "tool_calls", None) or []:
                index = int(getattr(tool_call, "index", 0) or 0)
                aggregated = tool_call_parts.setdefault(
                    index,
                    {
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    },
                )
                if getattr(tool_call, "id", None):
                    aggregated["id"] = tool_call.id
                if getattr(tool_call, "type", None):
                    aggregated["type"] = tool_call.type

                function_delta = getattr(tool_call, "function", None)
                if function_delta is None:
                    continue
                if getattr(function_delta, "name", None):
                    aggregated["function"]["name"] += function_delta.name
                if getattr(function_delta, "arguments", None):
                    aggregated["function"]["arguments"] += function_delta.arguments

        parsed_content = "".join(text_parts)
        parsed_tool_calls = [
            {
                "id": item["id"] or f"tool_call_{index}",
                "type": item["type"],
                "function": item["function"],
            }
            for index, item in sorted(tool_call_parts.items())
        ]
        parsed_content, parsed_tool_calls = self.family_adapter.parse_tool_calls(parsed_content, parsed_tool_calls)
        tool_calls = normalize_tool_calls(parsed_tool_calls)
        return BrainMessage(content=parsed_content, tool_calls=tool_calls, role="assistant", model=model)

    def _resolve_openai_model_card(self, model: str) -> OpenAIModelCard:
        if self.provider_name != "openai":
            raise BrainProviderError(f"{self.provider_name} does not use OpenAI model cards.")
        card = get_openai_model_card(model)
        self._active_model_card = card
        return card

    def _build_responses_reasoning(self, card: OpenAIModelCard) -> dict[str, Any] | None:
        if not card.reasoning_efforts:
            return None

        configured_reasoning = self.settings.get("reasoning")
        configured_effort = None
        configured_summary = None
        if isinstance(configured_reasoning, dict):
            configured_effort = configured_reasoning.get("effort")
            configured_summary = configured_reasoning.get("summary")

        effort = str(
            self.settings.get("reasoning_effort")
            or configured_effort
            or card.default_reasoning_effort
            or ""
        ).strip().lower()
        summary = str(
            self.settings.get("reasoning_summary")
            or configured_summary
            or card.default_reasoning_summary
            or ""
        ).strip().lower()

        reasoning: dict[str, Any] = {}
        if effort and effort in card.reasoning_efforts:
            reasoning["effort"] = effort
        if summary:
            reasoning["summary"] = summary
        return reasoning or None


    def _build_responses_tools(self, tools: list[dict] | None) -> list[dict] | None:
        converted: list[dict] = []
        for tool in tools or []:
            if not isinstance(tool, dict):
                continue
            tool_type = str(tool.get("type") or "").strip().lower()
            if tool_type != "function":
                converted.append(tool)
                continue

            function = tool.get("function") or {}
            name = str(function.get("name") or tool.get("name") or "").strip()
            if not name:
                continue
            converted.append({
                "type": "function",
                "name": name,
                "description": str(function.get("description") or tool.get("description") or "").strip(),
                "parameters": function.get("parameters") or tool.get("parameters") or {"type": "object", "properties": {}},
                "strict": bool(function.get("strict") or tool.get("strict") or False),
            })
        return converted or None

    def _chat_responses(
        self,
        *,
        messages: list[dict],
        system: str,
        stream: bool,
        tools: list[dict] | None,
        stream_handler,
        model: str,
        temperature: float | None,
        max_tokens: int | None,
        extra_body: dict | None,
        card: OpenAIModelCard,
    ) -> BrainMessage:
        if self._client is None or not hasattr(self._client, "responses"):
            raise BrainProviderError("OpenAI Responses API is unavailable in the configured SDK client.")

        kwargs: dict[str, Any] = {
            "model": model,
            "input": self._build_responses_input(messages),
            "temperature": temperature,
            "max_output_tokens": max_tokens,
        }
        if system:
            kwargs["instructions"] = system
        response_tools = self._build_responses_tools(tools)
        if response_tools and card.supports_tools:
            kwargs["tools"] = response_tools
            kwargs["tool_choice"] = "auto"
        reasoning = self._build_responses_reasoning(card)
        if reasoning:
            kwargs["reasoning"] = reasoning
        if extra_body:
            kwargs["extra_body"] = extra_body

        try:
            debug_tool_schema("PROVIDER_REQUEST_BODY", {"provider": self.provider_name, "api": "responses", "body": kwargs})
            response = self._client.responses.create(**kwargs)
        except Exception as exc:
            if tools and "does not support tools" in _stringify_error(exc).lower():
                kwargs.pop("tools", None)
                kwargs.pop("tool_choice", None)
                try:
                    response = self._client.responses.create(**kwargs)
                except Exception as retry_exc:
                    raise _translate_openai_error(self.provider_name, retry_exc) from retry_exc
            else:
                raise _translate_openai_error(self.provider_name, exc) from exc

        parsed = self._parse_responses_output(response, model)
        if stream and stream_handler is not None and parsed.content:
            try:
                stream_handler(parsed.content)
            except Exception:
                pass
        return parsed

    def chat(
        self,
        messages: list[dict],
        system: str = "",
        stream: bool = False,
        tools: list[dict] | None = None,
        stream_handler=None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra_body: dict | None = None,
    ) -> BrainMessage:
        self._ensure_ready()
        resolved_model = str(model or self.model or "").strip()
        if not resolved_model:
            raise BrainProviderBadRequestError(f"{self.provider_name} model is not configured.")
        resolved_temperature = self.temperature if temperature is None else temperature
        resolved_max_tokens = self.max_tokens if max_tokens is None else max_tokens

        if self.provider_name == "openai":
            card = self._resolve_openai_model_card(resolved_model)
            if card.preferred_api == "responses":
                return self._chat_responses(
                    messages=messages,
                    system=system,
                    stream=stream,
                    tools=tools,
                    stream_handler=stream_handler,
                    model=resolved_model,
                    temperature=resolved_temperature,
                    max_tokens=resolved_max_tokens,
                    extra_body=extra_body,
                    card=card,
                )

        kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": self._build_messages(messages, system),
            "temperature": resolved_temperature,
            "max_tokens": resolved_max_tokens,
        }
        if stream:
            kwargs["stream"] = True
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if extra_body:
            kwargs["extra_body"] = extra_body

        if self._client is None:
            raise BrainProviderError(f"{self.provider_name} base URL is not configured.")

        try:
            debug_tool_schema("PROVIDER_REQUEST_BODY", {"provider": self.provider_name, "api": "chat.completions", "body": kwargs})
            response = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            if tools and "does not support tools" in _stringify_error(exc).lower():
                kwargs.pop("tools", None)
                kwargs.pop("tool_choice", None)
                try:
                    response = self._client.chat.completions.create(**kwargs)
                except Exception as retry_exc:
                    raise _translate_openai_error(self.provider_name, retry_exc) from retry_exc
            else:
                raise _translate_openai_error(self.provider_name, exc) from exc

        if not stream:
            message = getattr(response.choices[0], "message", None) if getattr(response, "choices", None) else None
            if message is None:
                return BrainMessage(content="", tool_calls=[], role="assistant", model=resolved_model, raw=response)
            return self._parse_message(message, resolved_model)

        return self._parse_stream(response, resolved_model, stream_handler=stream_handler)

    def list_models(self) -> list[str]:
        self._ensure_ready()
        if self._client is None:
            raise BrainProviderError(f"{self.provider_name} base URL is not configured.")
        try:
            response = self._client.models.list()
        except Exception as exc:
            raise _translate_openai_error(self.provider_name, exc) from exc

        models: list[str] = []
        for item in getattr(response, "data", None) or []:
            model_id = str(getattr(item, "id", "") or "").strip()
            if model_id:
                models.append(model_id)
        return models

    def health_check(self) -> bool:
        self._ensure_ready()
        if self._client is None:
            raise BrainProviderError(f"{self.provider_name} base URL is not configured.")
        try:
            self._client.models.list()
        except Exception as exc:
            raise _translate_openai_error(self.provider_name, exc) from exc
        return True


class OpenAIProvider(OpenAICompatibleBrainProvider):
    def __init__(self, settings: dict, layer_name: str = "companion") -> None:
        api_key = str(
            settings.get("api_key")
            or settings.get("openai_api_key")
            or ""
        ).strip()
        super().__init__(
            settings,
            layer_name,
            provider_name="openai",
            base_url=OPENAI_BASE_URL,
            api_key=api_key,
            requires_api_key=True,
        )


class OllamaProvider(OpenAICompatibleBrainProvider):
    def __init__(self, settings: dict, layer_name: str = "companion") -> None:
        super().__init__(
            settings,
            layer_name,
            provider_name="ollama",
            base_url=OLLAMA_BASE_URL,
            api_key="ollama",
            requires_api_key=False,
        )


class GeminiProvider(OpenAICompatibleBrainProvider):
    def __init__(self, settings: dict, layer_name: str = "companion") -> None:
        api_key = str(
            settings.get("api_key")
            or settings.get("gemini_api_key")
            or ""
        ).strip()
        super().__init__(
            settings,
            layer_name,
            provider_name="gemini",
            base_url=GEMINI_BASE_URL,
            api_key=api_key,
            requires_api_key=True,
        )

    def list_models(self) -> list[str]:
        self._ensure_ready()
        try:
            models = super().list_models()
            if models:
                return models
        except Exception:
            pass

        return [self.model] if self.model else []


class CustomProvider(OpenAICompatibleBrainProvider):
    def __init__(self, settings: dict, layer_name: str = "companion") -> None:
        base_url = str(
            settings.get("base_url")
            or settings.get("api_url")
            or ""
        ).strip()
        api_key = str(
            settings.get("custom_api_key")
            or settings.get("api_key")
            or ""
        ).strip()
        super().__init__(
            settings,
            layer_name,
            provider_name="custom",
            base_url=base_url,
            api_key=api_key,
            requires_api_key=False,
        )
