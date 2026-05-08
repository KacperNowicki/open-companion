from __future__ import annotations

import json
from urllib import request as urllib_request

from openrouter_models import get_openrouter_fallback_model_ids

from .base import read_keyring_secret
from .openai import OpenAICompatibleBrainProvider


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_SESSION_MODEL_CACHE: list[str] | None = None


class OpenRouterProvider(OpenAICompatibleBrainProvider):
    provider_id = "openrouter"

    def __init__(self, settings: dict, layer_name: str = "companion") -> None:
        api_key = str(
            settings.get("api_key")
            or settings.get("openrouter_api_key")
            or read_keyring_secret(["openrouter_api_key", "openrouter"])
            or ""
        ).strip()
        super().__init__(
            settings,
            layer_name,
            provider_name=self.provider_id,
            base_url=OPENROUTER_BASE_URL,
            api_key=api_key,
            requires_api_key=True,
        )

    def list_models(self) -> list[str]:
        global _SESSION_MODEL_CACHE
        if _SESSION_MODEL_CACHE is not None:
            return list(_SESSION_MODEL_CACHE)
        try:
            headers = {"Accept": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            req = urllib_request.Request(f"{OPENROUTER_BASE_URL}/models", headers=headers, method="GET")
            with urllib_request.urlopen(req, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
            models = [
                str(item.get("id") or "").strip()
                for item in payload.get("data", []) or []
                if isinstance(item, dict) and str(item.get("id") or "").strip()
            ]
            _SESSION_MODEL_CACHE = models or get_openrouter_fallback_model_ids()
        except Exception:
            _SESSION_MODEL_CACHE = get_openrouter_fallback_model_ids()
        return list(_SESSION_MODEL_CACHE)
