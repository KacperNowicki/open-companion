from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Iterable

try:
    import keyring
except Exception:  # pragma: no cover - optional runtime dependency during tests
    class _MissingKeyring:
        @staticmethod
        def get_password(*_args, **_kwargs):
            return None

    keyring = _MissingKeyring()

from model_family import get_family_adapter, resolve_model_family
from runtime_paths import KEYCHAIN_SERVICE

logger = logging.getLogger(__name__)
_WINDOWS_KEYRING_LOGGER = logging.getLogger("keyring.backends.Windows")

KEYRING_SERVICES = (
    "open-companion",
    KEYCHAIN_SERVICE,
    "OpenCompanion",
)

OLLAMA_COMPATIBLE_BASE_URL = "http://localhost:11434/v1"
OLLAMA_NATIVE_BASE_URL = "http://localhost:11434"


def derive_ollama_native_base_url(base_url: str | None = None) -> str:
    normalized = str(base_url or OLLAMA_COMPATIBLE_BASE_URL).rstrip("/")
    if normalized.endswith("/v1"):
        return normalized[:-3]
    return normalized


def is_ollama_backed_provider(provider: str | None) -> bool:
    return str(provider or "").strip().lower() in {"gemma", "qwen", "ollama"}


def is_cloud_provider(provider: str | None) -> bool:
    return str(provider or "").strip().lower() in {
        "openai",
        "anthropic",
        "gemini",
        "custom",
        "chatgpt_oauth",
        "qwen_cloud",
        "openrouter",
    }


class OllamaCompatibleMixin:
    """Shared endpoint setup for providers backed by the local Ollama runtime."""

    ollama_base_url = OLLAMA_COMPATIBLE_BASE_URL
    ollama_api_key = "ollama"

    def _init_ollama_compatible_endpoint(self, base_url: str | None = None) -> None:
        self.native_base_url = derive_ollama_native_base_url(base_url)


@dataclass(slots=True)
class BrainToolFunction:
    name: str
    arguments: str = ""


@dataclass(slots=True)
class BrainToolCall:
    id: str
    type: str = "function"
    function: BrainToolFunction = field(default_factory=lambda: BrainToolFunction(name=""))
    source: str = "official_structured"
    provider: str = ""


@dataclass(slots=True)
class BrainMessage:
    content: str = ""
    tool_calls: list[BrainToolCall] = field(default_factory=list)
    role: str = "assistant"
    model: str | None = None
    raw: Any = None


class BrainProviderError(RuntimeError):
    pass


class BrainProviderAuthError(BrainProviderError):
    pass


class BrainProviderRateLimitError(BrainProviderError):
    pass


class BrainProviderConnectionError(BrainProviderError):
    pass


class BrainProviderBadRequestError(BrainProviderError):
    pass


class BrainProvider(ABC):
    def __init__(self, settings: dict, layer_name: str = "companion") -> None:
        self.settings = dict(settings or {})
        self.layer_name = layer_name
        self.provider_name = str(self.settings.get("provider") or "gemma").strip().lower() or "gemma"
        self.model_family = resolve_model_family(self.settings)
        self.family_adapter = get_family_adapter(self.settings)
        self.model = str(self.settings.get("model") or "").strip()
        self.temperature = self._coerce_float(self.settings.get("temperature"), 0.8)
        self.max_tokens = self._coerce_int(self.settings.get("max_tokens"), 1024)
        self.stream = bool(self.settings.get("stream", False))

    @staticmethod
    def _coerce_float(value: Any, default: float) -> float:
        try:
            if value is None or value == "":
                return default
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _coerce_int(value: Any, default: int) -> int:
        try:
            if value is None or value == "":
                return default
            return int(value)
        except Exception:
            return default

    @abstractmethod
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
        raise NotImplementedError

    @abstractmethod
    def list_models(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> bool:
        raise NotImplementedError


def _recover_keytar_value(value: str | None) -> str:
    """Recover a credential written by Electron's keytar on Windows.

    keytar writes the password as UTF-8 bytes into the Windows CredentialBlob.
    Python's keyring reads CredentialBlob as UTF-16-LE, producing garbled text
    when the original was UTF-8.  Re-encode as UTF-16-LE then decode as UTF-8
    to recover the original value.
    """
    if not value:
        return ""
    try:
        return value.encode("utf-16-le").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return value


def _read_keyring_password(service: str, account: str) -> str | None:
    """Read a keyring secret while suppressing Windows keytar compatibility noise."""
    previous_level = _WINDOWS_KEYRING_LOGGER.level
    try:
        _WINDOWS_KEYRING_LOGGER.setLevel(logging.ERROR)
        return keyring.get_password(service, account)
    except Exception:
        return None
    finally:
        _WINDOWS_KEYRING_LOGGER.setLevel(previous_level)


def read_keyring_secret(account_names: Iterable[str], service_names: Iterable[str] | None = None) -> str:
    services = tuple(service_names or KEYRING_SERVICES)
    accounts = tuple(str(account).strip() for account in account_names if str(account).strip())
    for service in services:
        for account in accounts:
            value = _read_keyring_password(service, account)
            if value:
                return value
            # Electron's keytar stores credentials with TargetName =
            # "service/account" while Python's keyring uses just "service".
            # Fall back to the keytar-style key so secrets saved from the
            # Settings UI are visible to the Python backend on Windows.
            value = _read_keyring_password(f"{service}/{account}", account)
            if value:
                return _recover_keytar_value(value)
    return ""


def normalize_message_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip().lower()
            if item_type in {"text", "output_text"}:
                text = str(item.get("text") or item.get("content") or "").strip()
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()
    return str(content).strip()


def split_system_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    system_parts: list[str] = []
    normalized_messages: list[dict] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip().lower()
        if role == "system":
            text = normalize_message_content(message.get("content"))
            if text:
                system_parts.append(text)
            continue
        normalized_messages.append(message)
    return "\n\n".join(system_parts).strip(), normalized_messages


def _normalize_tool_call(tool_call: Any, index: int) -> BrainToolCall:
    def _normalize_arguments(arguments: Any) -> str:
        if isinstance(arguments, str):
            return arguments
        if arguments is None:
            return ""
        try:
            return json.dumps(arguments, ensure_ascii=False)
        except Exception:
            return str(arguments)

    if isinstance(tool_call, dict):
        function = tool_call.get("function") or {}
        name = str(function.get("name") or "").strip()
        arguments = _normalize_arguments(function.get("arguments"))
        return BrainToolCall(
            id=str(tool_call.get("id") or f"tool_call_{index}"),
            type=str(tool_call.get("type") or "function"),
            function=BrainToolFunction(name=name, arguments=arguments),
            source=str(tool_call.get("source") or "official_structured"),
            provider=str(tool_call.get("provider") or ""),
        )

    function = getattr(tool_call, "function", None)
    return BrainToolCall(
        id=str(getattr(tool_call, "id", None) or f"tool_call_{index}"),
        type=str(getattr(tool_call, "type", None) or "function"),
        function=BrainToolFunction(
            name=str(getattr(function, "name", None) or "").strip(),
            arguments=_normalize_arguments(getattr(function, "arguments", None)),
        ),
        source=str(getattr(tool_call, "source", None) or "official_structured"),
        provider=str(getattr(tool_call, "provider", None) or ""),
    )


def normalize_tool_calls(tool_calls: Any) -> list[BrainToolCall]:
    normalized: list[BrainToolCall] = []
    for index, tool_call in enumerate(tool_calls or []):
        normalized.append(_normalize_tool_call(tool_call, index))
    return normalized


def to_openai_like_response(message: BrainMessage):
    tool_calls = [
        SimpleNamespace(
            id=item.id,
            type=item.type,
            function=SimpleNamespace(name=item.function.name, arguments=item.function.arguments),
        )
        for item in message.tool_calls
    ]
    response_message = SimpleNamespace(
        role=message.role,
        content=message.content,
        tool_calls=tool_calls,
    )
    return SimpleNamespace(
        id=f"brain-{id(message)}",
        object="chat.completion",
        model=message.model,
        choices=[SimpleNamespace(index=0, finish_reason="tool_calls" if tool_calls else "stop", message=response_message)],
        raw=message.raw,
    )


def stream_like_response(message: BrainMessage):
    delta = SimpleNamespace(
        role="assistant",
        content=message.content,
        tool_calls=[
            SimpleNamespace(
                index=index,
                id=item.id,
                type=item.type,
                function=SimpleNamespace(name=item.function.name, arguments=item.function.arguments),
            )
            for index, item in enumerate(message.tool_calls)
        ],
    )
    chunk = SimpleNamespace(choices=[SimpleNamespace(index=0, delta=delta, finish_reason=None)])

    def _iterator():
        yield chunk

    return _iterator()


class _ShimCompletions:
    def __init__(self, provider: BrainProvider) -> None:
        self._provider = provider

    def create(self, **kwargs):
        messages = list(kwargs.pop("messages", []) or [])
        system, normalized_messages = split_system_messages(messages)
        stream = bool(kwargs.pop("stream", False))
        response = self._provider.chat(
            messages=normalized_messages,
            system=system,
            stream=stream,
            tools=kwargs.pop("tools", None),
            stream_handler=kwargs.pop("stream_handler", None),
            model=kwargs.pop("model", None),
            temperature=kwargs.pop("temperature", None),
            max_tokens=kwargs.pop("max_tokens", None),
            extra_body=kwargs.pop("extra_body", None),
        )
        if kwargs:
            logger.debug("Ignoring unsupported OpenAI client kwargs: %s", sorted(kwargs))
        if stream:
            return stream_like_response(response)
        return to_openai_like_response(response)


class _ShimChat:
    def __init__(self, provider: BrainProvider) -> None:
        self.completions = _ShimCompletions(provider)


class _ShimModels:
    def __init__(self, provider: BrainProvider) -> None:
        self._provider = provider

    def list(self):
        models = self._provider.list_models()
        return SimpleNamespace(
            data=[SimpleNamespace(id=model, object="model") for model in models],
            object="list",
        )


class BrainClientShim:
    def __init__(self, provider: BrainProvider) -> None:
        self.provider = provider
        self.chat = _ShimChat(provider)
        self.models = _ShimModels(provider)

    def health_check(self) -> bool:
        return self.provider.health_check()

    def list_models(self) -> list[str]:
        return self.provider.list_models()
