from __future__ import annotations

from .base import BrainProviderError, read_keyring_secret
from .openai import OpenAICompatibleBrainProvider, _translate_openai_error


class CustomProvider(OpenAICompatibleBrainProvider):
    provider_id = "custom"

    def __init__(self, settings: dict, layer_name: str = "companion") -> None:
        providers_cfg = settings.get("providers") if isinstance(settings.get("providers"), dict) else {}
        custom_cfg = providers_cfg.get("custom") if isinstance(providers_cfg.get("custom"), dict) else {}
        base_url = str(
            custom_cfg.get("base_url")
            or settings.get("base_url")
            or settings.get("api_url")
            or ""
        ).strip()
        api_key = str(
            settings.get("custom_api_key")
            or settings.get("api_key")
            or read_keyring_secret(["custom_api_key", "custom"])
            or ""
        ).strip()
        merged_settings = dict(settings or {})
        if custom_cfg.get("model") and not merged_settings.get("model"):
            merged_settings["model"] = custom_cfg.get("model")
        super().__init__(
            merged_settings,
            layer_name,
            provider_name=self.provider_id,
            base_url=base_url,
            api_key=api_key,
            requires_api_key=False,
        )

    def health_check(self) -> bool:
        if not self.base_url:
            raise BrainProviderError("custom endpoint base URL is not configured.")
        try:
            return super().health_check()
        except Exception:
            return False

    def list_models(self) -> list[str]:
        if not self.base_url:
            return []
        try:
            return super().list_models()
        except Exception:
            return []
