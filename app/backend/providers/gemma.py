from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

try:
    import psutil  # type: ignore
except ImportError:  # pragma: no cover - optional in stripped subprocess envs
    psutil = None

import ctx_advisor
from debug_log import DEBUG_ENABLED, debug_log, debug_log_error, debug_tool_schema
from .base import (
    BrainMessage,
    BrainProvider,
    BrainProviderBadRequestError,
    BrainProviderConnectionError,
    BrainProviderError,
    BrainProviderRateLimitError,
    OLLAMA_COMPATIBLE_BASE_URL,
    OllamaCompatibleMixin,
    normalize_message_content,
    normalize_tool_calls,
)
from .openai import OpenAICompatibleBrainProvider, _translate_openai_error

logger = logging.getLogger(__name__)
DEFAULT_ACTIVE_KEEPALIVE = "10m"
VERBOSE_RUNTIME_LOGS = os.environ.get("OPEN_COMPANION_VERBOSE_RUNTIME_LOGS") == "1"


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


def _compose_gemma_content(content: str, thinking: Any) -> str:
    text = str(content or "")
    thought = str(thinking or "").strip()
    if not thought:
        return text
    return f"<|channel>thought\n{thought}\n<channel|>{text}"


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


def _translate_ollama_http_error(status: int, error_text: str) -> Exception:
    lowered = str(error_text).lower()
    if status == 429 or "too many requests" in lowered:
        return BrainProviderRateLimitError(f"ollama rate limit exceeded: {error_text}")
    if 400 <= status < 500:
        return BrainProviderBadRequestError(f"ollama request rejected: {error_text}")
    return BrainProviderError(f"ollama error: {error_text}")


def _extract_http_error_text(exc: urllib_error.HTTPError) -> str:
    try:
        raw = exc.read().decode("utf-8", "replace")
        payload = json.loads(raw) if raw else {}
        return str(payload.get("error") or payload.get("message") or raw or exc)
    except Exception:
        return str(exc)


def _is_ollama_memory_error(error_text: str) -> bool:
    lowered = str(error_text or "").lower()
    return "requires more system memory" in lowered or "out of memory" in lowered


def _fallback_num_ctx_values(num_ctx: Any) -> list[int]:
    try:
        current = int(num_ctx)
    except (TypeError, ValueError):
        return []
    if current <= 4096:
        return []
    candidates: list[int] = []
    while current > 4096:
        current = max(4096, current // 2)
        if not candidates or current != candidates[-1]:
            candidates.append(current)
        if current == 4096:
            break
    return candidates


def _normalize_model_name(name: str) -> str:
    return str(name or "").strip().lower()


def _get_running_model_vram_bytes(native_base_url: str, model: str) -> int:
    normalized = _normalize_model_name(model)
    if not normalized:
        return 0
    try:
        with urllib_request.urlopen(f"{native_base_url}/api/ps", timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except Exception:
        return 0

    for entry in payload.get("models", []) or []:
        entry_name = _normalize_model_name(entry.get("name") or entry.get("model") or "")
        if not entry_name:
            continue
        if entry_name == normalized or entry_name.split(":", 1)[0] == normalized.split(":", 1)[0]:
            try:
                return int(entry.get("size_vram") or 0)
            except (TypeError, ValueError):
                return 0
    return 0


def _resolve_num_ctx(
    native_base_url: str,
    model: str,
    layer_name: str,
    settings: dict[str, Any],
    requested_num_ctx: Any,
) -> tuple[int | None, int, int]:
    free_ram_bytes = 0
    try:
        free_ram_bytes = int(psutil.virtual_memory().available) if psutil is not None else 0
    except Exception:
        free_ram_bytes = 0

    configured_ctx = settings.get("context_window", "auto")
    if configured_ctx != "auto":
        try:
            explicit = int(requested_num_ctx if requested_num_ctx not in (None, "", "n/a") else configured_ctx)
            if explicit > 0:
                return explicit, 0, free_ram_bytes
        except (TypeError, ValueError):
            pass

    model_vram_bytes = _get_running_model_vram_bytes(native_base_url, model)
    total_vram, free_vram_raw = ctx_advisor.get_vram_total_bytes()
    free_vram_effective = max(0, free_vram_raw - max(0, int(model_vram_bytes or 0))) if total_vram > 0 else 0
    free_ram = free_ram_bytes
    advised = ctx_advisor.calculate_optimal_ctx(model_vram_bytes, layer_name)
    return advised, free_vram_effective, free_ram


class GemmaProvider(OllamaCompatibleMixin, OpenAICompatibleBrainProvider):
    provider_id = "gemma"

    def __init__(self, settings: dict, layer_name: str = "companion") -> None:
        super().__init__(
            settings,
            layer_name,
            provider_name=self.provider_id,
            base_url=OLLAMA_COMPATIBLE_BASE_URL,
            api_key="ollama",
            requires_api_key=False,
        )
        self._init_ollama_compatible_endpoint(self.base_url)

    def _build_native_messages(self, messages: list[dict], system: str) -> list[dict]:
        normalized = list(messages or [])
        # Gemma 4 thinking is enabled by prepending the <|think|> token to the system
        # prompt content. Disabled during active tool turns because reasoning mode
        # breaks Gemma 4 tool-call formatting; turn is "active" when the message
        # window already contains a tool result.
        if system and self.model_family == "google_gemma" and self.settings.get("gemma_thinking", True):
            has_tool_message = any(
                isinstance(m, dict) and str(m.get("role") or "").strip().lower() == "tool"
                for m in normalized
            )
            if not has_tool_message:
                system = "<|think|>" + system
        if system:
            normalized = [{"role": "system", "content": system}, *normalized]

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
                    if not cleaned:
                        continue
                    if cleaned.startswith("data:image/") and "," in cleaned:
                        cleaned = cleaned.split(",", 1)[1]
                    if cleaned not in images:
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
        content = _compose_gemma_content(message.get("content", ""), message.get("thinking"))
        raw_tool_calls = message.get("tool_calls", [])
        content, parsed_tool_calls = self.family_adapter.parse_tool_calls(content, raw_tool_calls)
        return BrainMessage(
            content=content,
            tool_calls=normalize_tool_calls(parsed_tool_calls),
            role=str(message.get("role") or "assistant"),
            model=model,
            raw=payload,
        )

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
        resolved_num_ctx, free_vram_bytes, free_ram_bytes = _resolve_num_ctx(
            self.native_base_url,
            model,
            self.layer_name,
            self.settings,
            options.get("num_ctx"),
        )
        if resolved_num_ctx is not None:
            options["num_ctx"] = resolved_num_ctx
        options.setdefault("temperature", temperature)
        options.setdefault("num_predict", max_tokens)
        if VERBOSE_RUNTIME_LOGS:
            print(
                "[CTX ADVISOR] "
                f"layer={self.layer_name} "
                f"vram_free={int(max(0, free_vram_bytes) / (1024 * 1024))}MB "
                f"ram_free={int(max(0, free_ram_bytes) / (1024 * 1024))}MB "
                f"num_ctx={options.get('num_ctx', 'n/a')}",
                file=sys.stderr,
                flush=True,
            )
        message_payloads = self._build_native_messages(messages, system)
        attempt_options = dict(options)
        attempted_num_ctx: set[int] = set()
        response = None
        while True:
            payload: dict[str, Any] = {
                "model": model,
                "messages": message_payloads,
                "stream": bool(stream),
                "keep_alive": DEFAULT_ACTIVE_KEEPALIVE,
                "options": attempt_options,
            }
            if tools:
                payload["tools"] = tools

            if VERBOSE_RUNTIME_LOGS:
                print(
                    "[OLLAMA REQUEST] "
                    f"layer={self.layer_name} "
                    f"family={self.model_family!r} "
                    f"model={model!r} "
                    f"endpoint={self.native_base_url + '/api/chat'!r} "
                    f"stream={bool(stream)} "
                    f"messages={len(payload['messages'])} "
                    f"tools={len(tools or [])} "
                    f"num_ctx={attempt_options.get('num_ctx', 'n/a')} "
                    f"temperature={attempt_options.get('temperature', 'n/a')} "
                    f"top_p={attempt_options.get('top_p', 'n/a')} "
                    f"top_k={attempt_options.get('top_k', 'n/a')}",
                    file=sys.stderr,
                    flush=True,
                )
            logger.info(
                "ollama native request layer=%s family=%s model=%s endpoint=%s stream=%s messages=%s tools=%s num_ctx=%s top_p=%s top_k=%s temperature=%s",
                self.layer_name,
                self.model_family,
                model,
                f"{self.native_base_url}/api/chat",
                bool(stream),
                len(payload["messages"]),
                len(tools or []),
                attempt_options.get("num_ctx"),
                attempt_options.get("top_p"),
                attempt_options.get("top_k"),
                attempt_options.get("temperature"),
            )

            body_text = json.dumps(payload, ensure_ascii=False)
            debug_tool_schema("PROVIDER_REQUEST_BODY", {"provider": self.provider_name, "api": "ollama_native_chat", "body": payload})
            if DEBUG_ENABLED:
                debug_log("PROMPT_SENT", body_text)
            body = body_text.encode("utf-8")
            req = urllib_request.Request(
                f"{self.native_base_url}/api/chat",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            try:
                response = urllib_request.urlopen(req, timeout=300)
                break
            except urllib_error.HTTPError as exc:
                debug_log_error(exc, {"layer": self.layer_name, "model": model, "endpoint": f"{self.native_base_url}/api/chat"})
                status = int(getattr(exc, "code", 0) or 0)
                error_text = _extract_http_error_text(exc)
                current_num_ctx = attempt_options.get("num_ctx")
                if _is_ollama_memory_error(error_text) and current_num_ctx not in (None, "n/a"):
                    attempted_num_ctx.add(int(current_num_ctx))
                    next_num_ctx = next(
                        (candidate for candidate in _fallback_num_ctx_values(current_num_ctx) if candidate not in attempted_num_ctx),
                        None,
                    )
                    if next_num_ctx is not None:
                        print(
                            "[OLLAMA RETRY] "
                            f"layer={self.layer_name} "
                            f"model={model!r} "
                            f"reason={error_text!r} "
                            f"num_ctx={current_num_ctx} -> {next_num_ctx}",
                            file=sys.stderr,
                            flush=True,
                        )
                        logger.warning(
                            "Retrying Ollama request for layer=%s model=%s after memory error: num_ctx %s -> %s (%s)",
                            self.layer_name,
                            model,
                            current_num_ctx,
                            next_num_ctx,
                            error_text,
                        )
                        attempt_options = dict(attempt_options)
                        attempt_options["num_ctx"] = next_num_ctx
                        continue
                raise _translate_ollama_http_error(status, error_text) from exc
            except Exception as exc:
                debug_log_error(exc, {"layer": self.layer_name, "model": model, "endpoint": f"{self.native_base_url}/api/chat"})
                raise _translate_ollama_error(exc) from exc

        if not stream:
            try:
                raw = response.read().decode("utf-8")
                if DEBUG_ENABLED:
                    debug_log("OLLAMA_RAW", raw)
                parsed = json.loads(raw)
            finally:
                try:
                    response.close()
                except Exception:
                    pass
            return self._parse_native_message(parsed, model)

        content_parts: list[str] = []
        thought_parts: list[str] = []
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
                if message.get("thinking"):
                    thought_parts.append(str(message.get("thinking")))
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

        content = _compose_gemma_content("".join(content_parts), "".join(thought_parts))
        content, parsed_tool_calls = self.family_adapter.parse_tool_calls(content, latest_tool_calls)
        if DEBUG_ENABLED:
            debug_log(
                "OLLAMA_RESPONSE",
                {
                    "model": model,
                    "message": {
                        "role": latest_role,
                        "content": content,
                        "tool_calls": latest_tool_calls,
                    },
                    "done": True,
                    "raw_final": latest_payload,
                },
            )
        return BrainMessage(
            content=content,
            tool_calls=normalize_tool_calls(parsed_tool_calls),
            role=latest_role,
            model=model,
            raw=latest_payload,
        )

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
            raise BrainProviderBadRequestError("gemma model is not configured.")
        resolved_temperature = self.temperature if temperature is None else temperature
        resolved_max_tokens = self.max_tokens if max_tokens is None else max_tokens

        if self.model_family != "google_gemma":
            compat_payload = {
                "model": resolved_model,
                "messages": self._build_messages(messages, system),
                "temperature": resolved_temperature,
                "max_tokens": resolved_max_tokens,
            }
            if stream:
                compat_payload["stream"] = True
            if tools:
                compat_payload["tools"] = tools
                compat_payload["tool_choice"] = "auto"
            if extra_body:
                compat_payload["extra_body"] = extra_body
            if DEBUG_ENABLED:
                debug_log("PROMPT_SENT", json.dumps(compat_payload, ensure_ascii=False))
            debug_tool_schema("PROVIDER_REQUEST_BODY", {"provider": self.provider_name, "api": "ollama_compat_chat", "body": compat_payload})
            if VERBOSE_RUNTIME_LOGS:
                print(
                    "[OLLAMA REQUEST] "
                    f"layer={self.layer_name} "
                    f"family={self.model_family!r} "
                    f"model={resolved_model!r} "
                    f"endpoint={self.base_url!r} "
                    "mode='compat'",
                    file=sys.stderr,
                    flush=True,
                )
            logger.info(
                "ollama compatibility request layer=%s family=%s model=%s endpoint=%s",
                self.layer_name,
                self.model_family,
                resolved_model,
                self.base_url,
            )
            return super().chat(
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

    def list_models(self) -> list[str]:
        try:
            with urllib_request.urlopen(f"{self.native_base_url}/api/tags", timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            if self.model_family == "google_gemma":
                raise _translate_ollama_error(exc) from exc
            return super().list_models()

        models: list[str] = []
        for item in payload.get("models", []) or []:
            model_id = str(item.get("name") or "").strip()
            if model_id:
                models.append(model_id)
        return models

    def health_check(self) -> bool:
        try:
            with urllib_request.urlopen(f"{self.native_base_url}/api/tags", timeout=30) as response:
                response.read()
            return True
        except Exception as exc:
            if self.model_family == "google_gemma":
                raise _translate_ollama_error(exc) from exc
            return super().health_check()
