from __future__ import annotations

import json
import logging
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from debug_log import debug_tool_schema
from qwen_models import get_qwen_model_ids

from .base import (
    BrainMessage,
    BrainProviderBadRequestError,
    BrainProviderConnectionError,
    BrainProviderError,
    BrainProviderRateLimitError,
    OLLAMA_COMPATIBLE_BASE_URL,
    OllamaCompatibleMixin,
    normalize_message_content,
    normalize_tool_calls,
    read_keyring_secret,
)
from .openai import OpenAICompatibleBrainProvider, _translate_openai_error

logger = logging.getLogger(__name__)

DASHSCOPE_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
QWEN_REASONING_OPEN = "<reasoning_content>"
QWEN_REASONING_CLOSE = "</reasoning_content>"


def _extract_ollama_content_parts(content: Any) -> tuple[str, list[str]]:
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return normalize_message_content(content), []

    text_parts: list[str] = []
    images: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip().lower()
        if item_type in {"text", "output_text"}:
            text = str(item.get("text") or item.get("content") or "").strip()
            if text:
                text_parts.append(text)
            continue
        if item_type in {"image_url", "input_image"}:
            image_value = item.get("image_url")
            if isinstance(image_value, dict):
                image_value = image_value.get("url")
            image_url = str(image_value or item.get("url") or "").strip()
            if image_url.startswith("data:image/") and "," in image_url:
                images.append(image_url.split(",", 1)[1])
    return "\n".join(text_parts).strip(), images


def _normalize_native_tool_calls_for_request(tool_calls: Any) -> Any:
    normalized: list[dict[str, Any]] = []
    for item in tool_calls or []:
        if not isinstance(item, dict):
            normalized.append(item)
            continue
        cloned = dict(item)
        function = dict(cloned.get("function") or {})
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                function["arguments"] = json.loads(arguments)
            except Exception:
                function["arguments"] = arguments
        cloned["function"] = function
        normalized.append(cloned)
    return normalized


def _translate_ollama_error(exc: Exception) -> Exception:
    if isinstance(exc, urllib_error.HTTPError):
        status = int(getattr(exc, "code", 0) or 0)
        try:
            raw = exc.read().decode("utf-8", "replace")
            payload = json.loads(raw) if raw else {}
            error_text = payload.get("error") or payload.get("message") or raw or str(exc)
        except Exception:
            error_text = str(exc)
        lowered = str(error_text).lower()
        if status == 429 or "too many requests" in lowered:
            return BrainProviderRateLimitError(f"ollama rate limit exceeded: {error_text}")
        if 400 <= status < 500:
            return BrainProviderBadRequestError(f"ollama request rejected: {error_text}")
        return BrainProviderError(f"ollama error: {error_text}")
    if isinstance(exc, urllib_error.URLError):
        return BrainProviderConnectionError(f"ollama connection failed: {exc.reason}")
    return BrainProviderError(f"ollama error: {exc}")


def _compose_qwen_content(content: Any, reasoning_content: Any) -> str:
    text = normalize_message_content(content)
    reasoning = str(reasoning_content or "").strip()
    if not reasoning:
        return text
    return f"{QWEN_REASONING_OPEN}{reasoning}{QWEN_REASONING_CLOSE}{text}"


def _get_attr(value: Any, key: str, default=None):
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


class QwenProvider(OllamaCompatibleMixin, OpenAICompatibleBrainProvider):
    def __init__(self, settings: dict, layer_name: str = "companion") -> None:
        provider_name = str((settings or {}).get("provider") or "qwen").strip().lower() or "qwen"
        self.path = "cloud" if provider_name == "qwen_cloud" else "local"
        api_key = ""
        base_url = OLLAMA_COMPATIBLE_BASE_URL
        requires_api_key = False
        if self.path == "cloud":
            api_key = str(
                (settings or {}).get("api_key")
                or (settings or {}).get("qwen_api_key")
                or read_keyring_secret(["qwen_api_key", "qwen", "dashscope_api_key"], service_names=("OpenCompanion",))
                or ""
            ).strip()
            base_url = DASHSCOPE_BASE_URL
            requires_api_key = True

        super().__init__(
            settings,
            layer_name,
            provider_name=provider_name,
            base_url=base_url,
            api_key=api_key if self.path == "cloud" else "ollama",
            requires_api_key=requires_api_key,
        )
        self._init_ollama_compatible_endpoint(self.base_url)

    def _thinking_enabled(self, extra_body: dict | None = None) -> bool:
        if isinstance(extra_body, dict) and "enable_thinking" in extra_body:
            return bool(extra_body.get("enable_thinking"))
        for key in ("enable_thinking", "thinking_enabled", "thinking"):
            if key in self.settings:
                return bool(self.settings.get(key))
        effort = str(self.settings.get("reasoning_effort") or "").strip().lower()
        return bool(effort and effort not in {"none", "off", "false", "0", "disabled"})

    def _append_thinking_suffix(self, messages: list[dict], enabled: bool) -> list[dict]:
        suffix = "/think" if enabled else "/no_think"
        built = list(messages or [])
        for index in range(len(built) - 1, -1, -1):
            message = built[index]
            if not isinstance(message, dict) or str(message.get("role") or "").strip().lower() != "user":
                continue
            cloned = dict(message)
            content, images = _extract_ollama_content_parts(cloned.get("content"))
            if not content.rstrip().endswith(suffix):
                content = f"{content.rstrip()}\n{suffix}".strip()
            cloned["content"] = content
            if images:
                cloned["images"] = [*images, *list(cloned.get("images") or [])]
            built[index] = cloned
            break
        return built

    def _build_native_messages(self, messages: list[dict], system: str, thinking_enabled: bool) -> list[dict]:
        normalized = list(messages or [])
        if system:
            normalized = [{"role": "system", "content": system}, *normalized]
        normalized = self._append_thinking_suffix(normalized, thinking_enabled)

        built: list[dict] = []
        tool_call_names: dict[str, str] = {}
        for message in normalized:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "user").strip() or "user"
            content, images = _extract_ollama_content_parts(message.get("content"))
            existing_images = message.get("images")
            if isinstance(existing_images, list):
                for image in existing_images:
                    cleaned = str(image or "").strip()
                    if cleaned.startswith("data:image/") and "," in cleaned:
                        cleaned = cleaned.split(",", 1)[1]
                    if cleaned and cleaned not in images:
                        images.append(cleaned)
            payload = {"role": role, "content": content}
            if images:
                payload["images"] = images
            if message.get("tool_calls") is not None:
                native_tool_calls = _normalize_native_tool_calls_for_request(message.get("tool_calls"))
                payload["tool_calls"] = native_tool_calls
                for call in native_tool_calls or []:
                    if not isinstance(call, dict):
                        continue
                    call_id = str(call.get("id") or "").strip()
                    function = call.get("function") if isinstance(call.get("function"), dict) else {}
                    tool_name = str(function.get("name") or "").strip()
                    if call_id and tool_name:
                        tool_call_names[call_id] = tool_name
            if role == "tool":
                tool_name = str(message.get("tool_name") or message.get("name") or "").strip()
                if not tool_name:
                    tool_name = tool_call_names.get(str(message.get("tool_call_id") or "").strip(), "")
                if tool_name:
                    payload["tool_name"] = tool_name
            elif message.get("tool_call_id") is not None:
                payload["tool_call_id"] = message.get("tool_call_id")
            built.append(payload)
        return built

    def _parse_native_message(self, payload: dict[str, Any], model: str) -> BrainMessage:
        message = payload.get("message") or {}
        content = _compose_qwen_content(message.get("content", ""), message.get("reasoning_content") or message.get("thinking"))
        content, parsed_tool_calls = self.family_adapter.parse_tool_calls(content, message.get("tool_calls", []))
        return BrainMessage(
            content=content,
            tool_calls=normalize_tool_calls(parsed_tool_calls),
            role=str(message.get("role") or "assistant"),
            model=model,
            raw=payload,
        )

    def _parse_message(self, message: Any, model: str | None = None) -> BrainMessage:
        content = _compose_qwen_content(_get_attr(message, "content", ""), _get_attr(message, "reasoning_content", ""))
        raw_tool_calls = _get_attr(message, "tool_calls", None)
        content, parsed_tool_calls = self.family_adapter.parse_tool_calls(content, raw_tool_calls)
        return BrainMessage(
            content=content,
            tool_calls=normalize_tool_calls(parsed_tool_calls),
            role=str(_get_attr(message, "role", "assistant") or "assistant"),
            model=model,
            raw=message,
        )

    def _parse_stream(self, stream_response, model: str | None = None, stream_handler=None) -> BrainMessage:
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_call_parts: dict[int, dict[str, Any]] = {}
        for chunk in stream_response:
            if not getattr(chunk, "choices", None):
                continue
            delta = getattr(chunk.choices[0], "delta", None)
            if delta is None:
                continue
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                reasoning_parts.append(str(reasoning))
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
                aggregated = tool_call_parts.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
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

        content = _compose_qwen_content("".join(text_parts), "".join(reasoning_parts))
        parsed_tool_calls = [
            {"id": item["id"] or f"tool_call_{index}", "type": item["type"], "function": item["function"]}
            for index, item in sorted(tool_call_parts.items())
        ]
        content, parsed_tool_calls = self.family_adapter.parse_tool_calls(content, parsed_tool_calls)
        return BrainMessage(content=content, tool_calls=normalize_tool_calls(parsed_tool_calls), role="assistant", model=model)

    def _native_chat(
        self,
        *,
        messages: list[dict],
        system: str,
        stream: bool,
        tools: list[dict] | None,
        stream_handler,
        model: str,
        temperature: float,
        max_tokens: int,
        extra_body: dict | None,
    ) -> BrainMessage:
        options = dict((extra_body or {}).get("options") or {})
        options.setdefault("temperature", temperature)
        options.setdefault("num_predict", max_tokens)
        payload: dict[str, Any] = {
            "model": model,
            "messages": self._build_native_messages(messages, system, self._thinking_enabled(extra_body)),
            "stream": bool(stream),
            "options": options,
        }
        if tools:
            payload["tools"] = tools
        debug_tool_schema("PROVIDER_REQUEST_BODY", {"provider": "qwen", "api": "ollama_native_chat", "body": payload})
        req = urllib_request.Request(
            f"{self.native_base_url}/api/chat",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            response = urllib_request.urlopen(req, timeout=300)
        except Exception as exc:
            raise _translate_ollama_error(exc) from exc

        if not stream:
            try:
                parsed = json.loads(response.read().decode("utf-8"))
            finally:
                try:
                    response.close()
                except Exception:
                    pass
            return self._parse_native_message(parsed, model)

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        latest_role = "assistant"
        latest_tool_calls: list[Any] = []
        latest_payload: dict[str, Any] | None = None
        try:
            for raw_line in response:
                line = raw_line.decode("utf-8", "replace").strip()
                if not line:
                    continue
                parsed = json.loads(line)
                latest_payload = parsed
                message = parsed.get("message") or {}
                latest_role = str(message.get("role") or latest_role)
                reasoning_delta = message.get("reasoning_content") or message.get("thinking")
                if reasoning_delta:
                    reasoning_parts.append(str(reasoning_delta))
                if message.get("content"):
                    chunk = str(message.get("content"))
                    content_parts.append(chunk)
                    if stream_handler is not None:
                        try:
                            stream_handler(chunk)
                        except Exception:
                            pass
                if message.get("tool_calls"):
                    latest_tool_calls = message.get("tool_calls") or []
        finally:
            try:
                response.close()
            except Exception:
                pass
        content = _compose_qwen_content("".join(content_parts), "".join(reasoning_parts))
        content, parsed_tool_calls = self.family_adapter.parse_tool_calls(content, latest_tool_calls)
        return BrainMessage(content=content, tool_calls=normalize_tool_calls(parsed_tool_calls), role=latest_role, model=model, raw=latest_payload)

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
        resolved_model = str(model or self.model or "").strip()
        if not resolved_model:
            raise BrainProviderBadRequestError("qwen model is not configured.")
        resolved_temperature = self.temperature if temperature is None else temperature
        resolved_max_tokens = self.max_tokens if max_tokens is None else max_tokens
        if self.path == "local":
            return self._native_chat(
                messages=messages,
                system=system,
                stream=stream,
                tools=tools,
                stream_handler=stream_handler,
                model=resolved_model,
                temperature=resolved_temperature,
                max_tokens=resolved_max_tokens,
                extra_body=extra_body,
            )

        cloud_extra_body = dict(extra_body or {})
        if self._thinking_enabled(extra_body):
            cloud_extra_body["enable_thinking"] = True
        return super().chat(
            messages=messages,
            system=system,
            stream=stream,
            tools=tools,
            stream_handler=stream_handler,
            model=resolved_model,
            temperature=resolved_temperature,
            max_tokens=resolved_max_tokens,
            extra_body=cloud_extra_body or None,
        )

    def list_models(self) -> list[str]:
        if self.path == "cloud":
            try:
                return super().list_models()
            except Exception:
                return get_qwen_model_ids("cloud")
        try:
            with urllib_request.urlopen(f"{self.native_base_url}/api/tags", timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise _translate_ollama_error(exc) from exc
        models: list[str] = []
        for item in payload.get("models", []) or []:
            model_id = str(item.get("name") or "").strip()
            if model_id:
                models.append(model_id)
        return models

    def health_check(self) -> bool:
        if self.path == "cloud":
            return super().health_check()
        try:
            with urllib_request.urlopen(f"{self.native_base_url}/api/tags", timeout=30) as response:
                response.read()
            return True
        except Exception as exc:
            raise _translate_ollama_error(exc) from exc
