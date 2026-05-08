from __future__ import annotations

import json
import logging
from typing import Any
from urllib import error, request

from anthropic_models import AnthropicModelCard, get_anthropic_model_card
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

ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
def _error_message_from_body(body: str | bytes | None) -> str:
    if not body:
        return ""
    try:
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")
        payload = json.loads(body)
        if isinstance(payload, dict):
            error_payload = payload.get("error")
            if isinstance(error_payload, dict):
                message = str(error_payload.get("message") or "").strip()
                if message:
                    return message
            message = str(payload.get("message") or "").strip()
            if message:
                return message
    except Exception:
        pass
    return str(body).strip()


def _translate_http_error(provider_name: str, exc: error.HTTPError) -> Exception:
    status = int(getattr(exc, "code", 0) or 0)
    message = _error_message_from_body(exc.read())
    if status in {401, 403}:
        return BrainProviderAuthError(f"{provider_name} authentication failed: {message or exc.reason}")
    if status == 429:
        return BrainProviderRateLimitError(f"{provider_name} rate limit exceeded: {message or exc.reason}")
    if 400 <= status < 500:
        return BrainProviderBadRequestError(f"{provider_name} request rejected: {message or exc.reason}")
    return BrainProviderConnectionError(f"{provider_name} request failed: {message or exc.reason}")


def _split_content_blocks(content: Any) -> tuple[str, list[dict]]:
    text_parts: list[str] = []
    tool_calls: list[dict] = []
    if not isinstance(content, list):
        return normalize_message_content(content), tool_calls

    for index, item in enumerate(content):
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip().lower()
        if item_type == "text":
            text = str(item.get("text") or "").strip()
            if text:
                text_parts.append(text)
        elif item_type == "tool_use":
            tool_calls.append({
                "id": str(item.get("id") or f"tool_use_{index}"),
                "type": "function",
                "function": {
                    "name": str(item.get("name") or "").strip(),
                    "arguments": json.dumps(item.get("input") or {}, ensure_ascii=False),
                },
            })
    return "\n".join(text_parts).strip(), tool_calls


def _to_anthropic_tool(tool: dict) -> dict | None:
    if not isinstance(tool, dict):
        return None
    if str(tool.get("type") or "").strip().lower() != "function":
        return None
    function = tool.get("function") or {}
    name = str(function.get("name") or "").strip()
    if not name:
        return None
    return {
        "name": name,
        "description": str(function.get("description") or "").strip(),
        "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
    }


def _convert_image_url(url_value: str) -> dict | None:
    url_value = str(url_value or "").strip()
    if not url_value.startswith("data:image/") or ";base64," not in url_value:
        return None
    prefix, data = url_value.split(";base64,", 1)
    media_type = prefix.removeprefix("data:")
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": data,
        },
    }


def _convert_user_content(content: Any, extra_images: Any = None) -> list[dict]:
    blocks: list[dict] = []

    if isinstance(content, str):
        text = content.strip()
        if text:
            blocks.append({"type": "text", "text": text})
    elif isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip().lower()
            if item_type in {"text", "output_text"}:
                text = str(item.get("text") or item.get("content") or "").strip()
                if text:
                    blocks.append({"type": "text", "text": text})
                continue
            if item_type == "image_url":
                image_url = item.get("image_url") or {}
                if isinstance(image_url, dict):
                    image_block = _convert_image_url(str(image_url.get("url") or ""))
                    if image_block:
                        blocks.append(image_block)
    else:
        text = normalize_message_content(content)
        if text:
            blocks.append({"type": "text", "text": text})

    if isinstance(extra_images, list):
        for raw in extra_images:
            value = str(raw or "").strip()
            if not value:
                continue
            if value.startswith("data:image/"):
                image_block = _convert_image_url(value)
            else:
                image_block = {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": value,
                    },
                }
            if image_block:
                blocks.append(image_block)

    return blocks


def _convert_tool_result_message(message: dict) -> list[dict]:
    tool_call_id = str(message.get("tool_call_id") or "").strip()
    content = normalize_message_content(message.get("content"))
    return [{
        "type": "tool_result",
        "tool_use_id": tool_call_id or "tool_use_0",
        "content": content,
    }]


def _convert_assistant_message(message: dict) -> list[dict]:
    blocks: list[dict] = []
    text_content = normalize_message_content(message.get("content"))
    if text_content:
        blocks.append({"type": "text", "text": text_content})
    for index, tool_call in enumerate(message.get("tool_calls") or []):
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function") or {}
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        arguments = function.get("arguments") or "{}"
        try:
            input_payload = json.loads(arguments) if isinstance(arguments, str) else dict(arguments)
        except Exception:
            input_payload = {}
        blocks.append({
            "type": "tool_use",
            "id": str(tool_call.get("id") or f"tool_use_{index}"),
            "name": name,
            "input": input_payload,
        })
    return blocks


def _seed_tool_arguments(input_payload: Any) -> str:
    if input_payload in (None, {}, []):
        return ""
    try:
        return json.dumps(input_payload, ensure_ascii=False)
    except Exception:
        return ""


def _iter_sse_events(response):
    event_name = None
    data_lines: list[str] = []
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, (bytes, bytearray)) else str(raw_line)
        line = line.rstrip("\r\n")
        if not line:
            if event_name is not None or data_lines:
                yield event_name, "\n".join(data_lines)
            event_name = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip())
    if event_name is not None or data_lines:
        yield event_name, "\n".join(data_lines)


class AnthropicProvider(BrainProvider):
    def __init__(self, settings: dict, layer_name: str = "companion") -> None:
        super().__init__(settings, layer_name)
        self.provider_name = "anthropic"
        self.base_url = str(settings.get("base_url") or ANTHROPIC_BASE_URL).rstrip("/")
        self.api_key = str(
            settings.get("api_key")
            or settings.get("anthropic_api_key")
            or ""
        ).strip()
        self._missing_api_key = not self.api_key
        self._active_model_card: AnthropicModelCard | None = get_anthropic_model_card(self.model)

    def _ensure_ready(self) -> None:
        if self._missing_api_key:
            raise BrainProviderAuthError(
                "Anthropic API key is missing from keyring for this profile."
            )
        if not self.base_url:
            raise BrainProviderError("Anthropic base URL is not configured.")

    def _resolve_thinking(self, card: AnthropicModelCard, max_tokens: int) -> dict[str, Any] | None:
        if not card.reasoning_efforts or not card.thinking_budget_map:
            return None
        configured_effort = str(
            self.settings.get("reasoning_effort") or ""
        ).strip().lower()
        if not configured_effort or configured_effort not in card.thinking_budget_map:
            return None
        budget = card.thinking_budget_map[configured_effort]
        budget = min(budget, max(1, max_tokens - 1))
        return {"type": "enabled", "budget_tokens": budget}

    def _headers(self, stream: bool = False) -> dict[str, str]:
        headers = {
            "content-type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }
        if stream:
            headers["accept"] = "text/event-stream"
        return headers

    def _build_messages(self, messages: list[dict]) -> list[dict]:
        converted: list[dict] = []
        for message in messages or []:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "").strip().lower()
            if role == "system":
                continue
            if role == "user":
                converted.append({"role": "user", "content": _convert_user_content(message.get("content"), message.get("images"))})
                continue
            if role == "assistant":
                converted.append({"role": "assistant", "content": _convert_assistant_message(message)})
                continue
            if role == "tool":
                converted.append({"role": "user", "content": _convert_tool_result_message(message)})
                continue
            converted.append({"role": "user", "content": _convert_user_content(message.get("content"), message.get("images"))})
        return converted

    def _build_tools(self, tools: list[dict] | None) -> list[dict] | None:
        converted: list[dict] = []
        for tool in tools or []:
            anthropic_tool = _to_anthropic_tool(tool)
            if anthropic_tool:
                converted.append(anthropic_tool)
        return converted or None

    def _request(self, payload: dict, stream: bool = False):
        url = f"{self.base_url}/messages"
        debug_tool_schema("PROVIDER_REQUEST_BODY", {"provider": self.provider_name, "api": "anthropic_messages", "body": payload})
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(url, data=body, headers=self._headers(stream=stream), method="POST")
        try:
            return request.urlopen(req, timeout=60)
        except error.HTTPError as exc:
            raise _translate_http_error(self.provider_name, exc) from exc
        except error.URLError as exc:
            raise BrainProviderConnectionError(f"Anthropic connection failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise BrainProviderConnectionError("Anthropic request timed out.") from exc

    def _parse_response(self, response_json: dict, model: str) -> BrainMessage:
        content = response_json.get("content") or []
        text, tool_calls = _split_content_blocks(content)
        return BrainMessage(
            content=text,
            tool_calls=normalize_tool_calls(tool_calls),
            role="assistant",
            model=model,
            raw=response_json,
        )

    def _parse_stream(self, response, model: str, stream_handler=None) -> BrainMessage:
        text_parts: list[str] = []
        tool_call_parts: dict[int, dict[str, Any]] = {}

        for event_name, data in _iter_sse_events(response):
            if not data:
                continue
            try:
                payload = json.loads(data)
            except Exception:
                continue

            if event_name == "content_block_start":
                index = int(payload.get("index") or 0)
                block = payload.get("content_block") or {}
                block_type = str(block.get("type") or "").strip().lower()
                if block_type == "tool_use":
                    tool_call_parts[index] = {
                        "id": str(block.get("id") or f"tool_use_{index}"),
                        "type": "function",
                        "function": {
                            "name": str(block.get("name") or "").strip(),
                            "arguments": _seed_tool_arguments(block.get("input")),
                        },
                    }
                continue

            if event_name == "content_block_delta":
                index = int(payload.get("index") or 0)
                delta = payload.get("delta") or {}
                delta_type = str(delta.get("type") or "").strip().lower()
                if delta_type == "text_delta":
                    text = str(delta.get("text") or "")
                    if text:
                        text_parts.append(text)
                        if stream_handler is not None:
                            try:
                                stream_handler(text)
                            except Exception:
                                pass
                elif delta_type == "input_json_delta":
                    partial = str(delta.get("partial_json") or "")
                    if not partial:
                        continue
                    aggregated = tool_call_parts.setdefault(
                        index,
                        {
                            "id": f"tool_use_{index}",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        },
                    )
                    aggregated["function"]["arguments"] += partial
                continue

            if event_name == "message_start":
                message = payload.get("message") or {}
                content = message.get("content") or []
                if isinstance(content, list):
                    for index, block in enumerate(content):
                        if not isinstance(block, dict):
                            continue
                        if str(block.get("type") or "").strip().lower() == "tool_use":
                            tool_call_parts[index] = {
                                "id": str(block.get("id") or f"tool_use_{index}"),
                                "type": "function",
                                "function": {
                                    "name": str(block.get("name") or "").strip(),
                                    "arguments": _seed_tool_arguments(block.get("input")),
                                },
                            }

        tool_calls = normalize_tool_calls(
            [
                {
                    "id": item["id"] or f"tool_use_{index}",
                    "type": item["type"],
                    "function": item["function"],
                }
                for index, item in sorted(tool_call_parts.items())
            ]
        )
        return BrainMessage(content="".join(text_parts), tool_calls=tool_calls, role="assistant", model=model)

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
            raise BrainProviderBadRequestError("anthropic model is not configured.")
        card = get_anthropic_model_card(resolved_model)
        self._active_model_card = card
        resolved_max_tokens = self.max_tokens if max_tokens is None else max_tokens
        payload: dict[str, Any] = {
            "model": resolved_model,
            "messages": self._build_messages(messages),
            "system": system,
            "max_tokens": resolved_max_tokens,
        }

        thinking = self._resolve_thinking(card, resolved_max_tokens)
        if thinking:
            payload["thinking"] = thinking
            payload["temperature"] = 1
        elif temperature is not None:
            payload["temperature"] = temperature
        elif self.temperature is not None:
            payload["temperature"] = self.temperature

        converted_tools = self._build_tools(tools)
        if converted_tools:
            payload["tools"] = converted_tools
            payload["tool_choice"] = {"type": "auto"}

        if stream:
            payload["stream"] = True
            response = self._request(payload, stream=True)
            try:
                return self._parse_stream(response, resolved_model, stream_handler=stream_handler)
            finally:
                try:
                    response.close()
                except Exception:
                    pass

        response = self._request(payload, stream=False)
        try:
            response_json = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            raise _translate_http_error(self.provider_name, exc) from exc
        finally:
            try:
                response.close()
            except Exception:
                pass
        return self._parse_response(response_json, resolved_model)

    def list_models(self) -> list[str]:
        self._ensure_ready()
        request_obj = request.Request(
            f"{ANTHROPIC_BASE_URL}/models",
            headers={
                "anthropic-version": ANTHROPIC_VERSION,
                "x-api-key": self.api_key,
                "accept": "application/json",
            },
            method="GET",
        )
        try:
            with request.urlopen(request_obj, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
            models = []
            for item in payload.get("data", []) if isinstance(payload, dict) else []:
                model_id = str((item or {}).get("id") or "").strip()
                if model_id:
                    models.append(model_id)
            if models:
                return models
        except error.HTTPError:
            pass
        except Exception:
            pass

        return [self.model] if self.model else []

    def health_check(self) -> bool:
        self.chat(
            messages=[{"role": "user", "content": "OK"}],
            system="Health check",
            stream=False,
            tools=None,
            model=self.model or None,
            temperature=0.0,
            max_tokens=1,
        )
        return True
