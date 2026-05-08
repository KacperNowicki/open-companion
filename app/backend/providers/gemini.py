from __future__ import annotations

import json
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from debug_log import debug_tool_schema
from gemini_models import get_gemini_model_ids

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
    read_keyring_secret,
)


GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


def _translate_gemini_error(exc: Exception) -> Exception:
    if isinstance(exc, urllib_error.HTTPError):
        status = int(getattr(exc, "code", 0) or 0)
        try:
            raw = exc.read().decode("utf-8", "replace")
            payload = json.loads(raw) if raw else {}
            error_payload = payload.get("error") if isinstance(payload, dict) else {}
            message = str(error_payload.get("message") if isinstance(error_payload, dict) else raw or exc)
        except Exception:
            message = str(exc)
        lowered = message.lower()
        if status in {401, 403} or "api key" in lowered or "permission" in lowered:
            return BrainProviderAuthError(f"gemini authentication failed: {message}")
        if status == 429 or "quota" in lowered or "rate" in lowered:
            return BrainProviderRateLimitError(f"gemini rate limit exceeded: {message}")
        if 400 <= status < 500:
            return BrainProviderBadRequestError(f"gemini request rejected: {message}")
        return BrainProviderError(f"gemini error: {message}")
    if isinstance(exc, urllib_error.URLError):
        return BrainProviderConnectionError(f"gemini connection failed: {exc.reason}")
    return BrainProviderError(f"gemini error: {exc}")


def _schema_to_gemini(schema: Any) -> Any:
    if isinstance(schema, dict):
        converted = {}
        for key, value in schema.items():
            if key == "type" and isinstance(value, str):
                converted[key] = value.upper()
            elif key == "additionalProperties":
                continue
            else:
                converted[key] = _schema_to_gemini(value)
        return converted
    if isinstance(schema, list):
        return [_schema_to_gemini(item) for item in schema]
    return schema


class GeminiProvider(BrainProvider):
    provider_id = "gemini"

    def __init__(self, settings: dict, layer_name: str = "companion") -> None:
        super().__init__(settings, layer_name)
        self.provider_name = self.provider_id
        self.base_url = str(settings.get("base_url") or GEMINI_BASE_URL).rstrip("/")
        self.api_key = str(
            settings.get("api_key")
            or settings.get("gemini_api_key")
            or read_keyring_secret(["gemini_api_key", "gemini"])
            or ""
        ).strip()

    def _ensure_ready(self) -> None:
        if not self.api_key:
            raise BrainProviderAuthError("gemini API key is missing from keyring for this profile.")

    def _thinking_config(self, model: str, extra_body: dict | None = None) -> dict[str, Any] | None:
        enabled = bool(
            self.settings.get("enable_thinking")
            or self.settings.get("thinking_enabled")
            or self.settings.get("thinking")
            or self.settings.get("reasoning_effort")
        )
        if isinstance(extra_body, dict) and "enable_thinking" in extra_body:
            enabled = bool(extra_body.get("enable_thinking"))
        if not enabled:
            return None

        lower_model = model.lower()
        effort = str(self.settings.get("reasoning_effort") or "").strip().lower()
        if lower_model.startswith("gemini-3"):
            if effort in {"minimal", "low", "medium", "high"}:
                return {"thinkingLevel": effort}
            return {"thinkingLevel": "low"}

        budget = self.settings.get("thinking_budget")
        if isinstance(extra_body, dict) and extra_body.get("thinking_budget") is not None:
            budget = extra_body.get("thinking_budget")
        try:
            parsed_budget = int(budget)
        except (TypeError, ValueError):
            parsed_budget = -1
        return {"thinkingBudget": parsed_budget}

    def _build_contents(self, messages: list[dict], system: str) -> list[dict]:
        contents: list[dict] = []
        if system:
            contents.append({"role": "user", "parts": [{"text": system}]})
            contents.append({"role": "model", "parts": [{"text": "Understood."}]})

        for message in messages or []:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "user").strip().lower()
            content = normalize_message_content(message.get("content"))
            if role == "tool":
                tool_name = str(message.get("name") or message.get("tool_call_id") or "tool_result").strip()
                contents.append({
                    "role": "function",
                    "parts": [{"functionResponse": {"name": tool_name, "response": {"content": content}}}],
                })
                continue
            if role == "assistant":
                role = "model"
            elif role not in {"user", "model"}:
                role = "user"
            parts: list[dict[str, Any]] = []
            if content:
                parts.append({"text": content})
            for tool_call in normalize_tool_calls(message.get("tool_calls")):
                if not tool_call.function.name:
                    continue
                try:
                    args = json.loads(tool_call.function.arguments or "{}")
                except Exception:
                    args = {}
                parts.append({"functionCall": {"name": tool_call.function.name, "args": args}})
            if parts:
                contents.append({"role": role, "parts": parts})
        return contents or [{"role": "user", "parts": [{"text": ""}]}]

    def _build_tools(self, tools: list[dict] | None) -> list[dict] | None:
        declarations: list[dict[str, Any]] = []
        for tool in tools or []:
            if not isinstance(tool, dict):
                continue
            function = tool.get("function") or {}
            name = str(function.get("name") or tool.get("name") or "").strip()
            if not name:
                continue
            declarations.append({
                "name": name,
                "description": str(function.get("description") or tool.get("description") or "").strip(),
                "parameters": _schema_to_gemini(function.get("parameters") or tool.get("parameters") or {"type": "object", "properties": {}}),
            })
        return [{"functionDeclarations": declarations}] if declarations else None

    def _request(self, endpoint: str, payload: dict | None = None, method: str = "POST"):
        self._ensure_ready()
        if method == "POST":
            debug_tool_schema("PROVIDER_REQUEST_BODY", {"provider": "gemini", "api": "generate_content", "endpoint": endpoint, "body": payload})
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib_request.Request(
            endpoint,
            data=data,
            headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
            method=method,
        )
        return urllib_request.urlopen(req, timeout=300)

    def _parse_payload(self, payload: dict, model: str) -> BrainMessage:
        text_parts: list[str] = []
        raw_tool_calls: list[dict[str, Any]] = []
        for candidate in payload.get("candidates", []) or []:
            content = candidate.get("content") or {}
            for part in content.get("parts", []) or []:
                if not isinstance(part, dict) or part.get("thought"):
                    continue
                if part.get("text"):
                    text_parts.append(str(part.get("text")))
                    continue
                function_call = part.get("functionCall") or part.get("function_call")
                if isinstance(function_call, dict):
                    name = str(function_call.get("name") or "").strip()
                    if not name:
                        continue
                    raw_tool_calls.append({
                        "id": f"gemini_tool_call_{len(raw_tool_calls)}",
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(function_call.get("args") or {}, ensure_ascii=False),
                        },
                    })
        content = "".join(text_parts).strip()
        content, parsed_tool_calls = self.family_adapter.parse_tool_calls(content, raw_tool_calls)
        return BrainMessage(content=content, tool_calls=normalize_tool_calls(parsed_tool_calls), role="assistant", model=model, raw=payload)

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
            raise BrainProviderBadRequestError("gemini model is not configured.")
        generation_config: dict[str, Any] = {
            "temperature": self.temperature if temperature is None else temperature,
            "maxOutputTokens": self.max_tokens if max_tokens is None else max_tokens,
        }
        thinking_config = self._thinking_config(resolved_model, extra_body)
        if thinking_config:
            generation_config["thinkingConfig"] = thinking_config
        payload: dict[str, Any] = {
            "contents": self._build_contents(messages, system),
            "generationConfig": generation_config,
        }
        gemini_tools = self._build_tools(tools)
        if gemini_tools:
            payload["tools"] = gemini_tools

        suffix = "streamGenerateContent?alt=sse" if stream else "generateContent"
        endpoint = f"{self.base_url}/models/{resolved_model}:{suffix}"
        try:
            response = self._request(endpoint, payload)
        except Exception as exc:
            raise _translate_gemini_error(exc) from exc

        if not stream:
            try:
                parsed = json.loads(response.read().decode("utf-8", "replace"))
            finally:
                try:
                    response.close()
                except Exception:
                    pass
            return self._parse_payload(parsed, resolved_model)

        merged = {"candidates": [{"content": {"parts": []}}]}
        try:
            for raw_line in response:
                line = raw_line.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                chunk = json.loads(data)
                for candidate in chunk.get("candidates", []) or []:
                    for part in ((candidate.get("content") or {}).get("parts") or []):
                        merged["candidates"][0]["content"]["parts"].append(part)
                        if stream_handler is not None and isinstance(part, dict) and part.get("text") and not part.get("thought"):
                            try:
                                stream_handler(str(part.get("text")))
                            except Exception:
                                pass
        finally:
            try:
                response.close()
            except Exception:
                pass
        return self._parse_payload(merged, resolved_model)

    def list_models(self) -> list[str]:
        try:
            response = self._request(f"{self.base_url}/models", None, method="GET")
            payload = json.loads(response.read().decode("utf-8", "replace"))
            models = [
                str(item.get("name") or "").rsplit("/", 1)[-1]
                for item in payload.get("models", []) or []
                if isinstance(item, dict) and "generateContent" in item.get("supportedGenerationMethods", [])
            ]
            return models or get_gemini_model_ids()
        except Exception:
            return get_gemini_model_ids()

    def health_check(self) -> bool:
        try:
            response = self._request(f"{self.base_url}/models", None, method="GET")
            response.read()
            return True
        except Exception as exc:
            raise _translate_gemini_error(exc) from exc
