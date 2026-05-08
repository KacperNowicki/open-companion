from __future__ import annotations

import json
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from .base import (
    BrainProviderAuthError,
    BrainProviderConnectionError,
    BrainProviderError,
    OLLAMA_COMPATIBLE_BASE_URL,
    OllamaCompatibleMixin,
)
from .openai import OpenAICompatibleBrainProvider, _translate_openai_error


OLLAMA_CLOUD_MODEL_HINTS: tuple[str, ...] = (
    "kimi-k2.6:cloud",
    "deepseek-v3.1:671-cloud",
    "qwen3-coder:480b-cloud",
    "gpt-oss:120b-cloud",
    "gpt-oss:20b-cloud",
    "glm-4.6:cloud",
)


def is_ollama_cloud_model(model: str | None) -> bool:
    value = str(model or "").strip().lower()
    return value.endswith(":cloud") or value.endswith("-cloud")


def _translate_ollama_generic_error(exc: Exception, model: str | None = None) -> Exception:
    translated = _translate_openai_error("ollama", exc)
    lowered = str(translated).lower()
    if is_ollama_cloud_model(model) and (
        isinstance(translated, BrainProviderAuthError)
        or "unauthorized" in lowered
        or "forbidden" in lowered
        or "login" in lowered
        or "ollama account" in lowered
    ):
        return BrainProviderAuthError(
            f"Ollama cloud model '{model}' requires an Ollama account. Run `ollama login` in a terminal, then try again."
        )
    return translated


class OllamaGenericProvider(OllamaCompatibleMixin, OpenAICompatibleBrainProvider):
    provider_id = "ollama"

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

    def chat(self, *args, **kwargs):
        model = kwargs.get("model") or self.model
        try:
            return super().chat(*args, **kwargs)
        except Exception as exc:
            raise _translate_ollama_generic_error(exc, model) from exc

    def list_models(self) -> list[str]:
        models: list[str] = []
        try:
            with urllib_request.urlopen(f"{self.native_base_url}/api/tags", timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
            for item in payload.get("models", []) or []:
                model_id = str(item.get("name") or "").strip()
                if model_id:
                    models.append(model_id)
        except urllib_error.URLError as exc:
            raise BrainProviderConnectionError(f"ollama connection failed: {exc.reason}") from exc
        except Exception as exc:
            raise BrainProviderError(f"ollama error: {exc}") from exc

        seen = {model.lower() for model in models}
        for model_id in OLLAMA_CLOUD_MODEL_HINTS:
            if model_id.lower() not in seen:
                models.append(model_id)
        return models

    def health_check(self) -> bool:
        try:
            with urllib_request.urlopen(f"{self.native_base_url}/api/tags", timeout=30) as response:
                response.read()
            return True
        except urllib_error.URLError as exc:
            raise BrainProviderConnectionError(f"ollama connection failed: {exc.reason}") from exc
        except Exception as exc:
            raise BrainProviderError(f"ollama error: {exc}") from exc
