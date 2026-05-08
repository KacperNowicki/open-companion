from __future__ import annotations

import site
import sys

user_site = site.getusersitepackages()
if user_site and user_site not in sys.path:
    sys.path.append(user_site)

from .base import (
    BrainClientShim,
    BrainMessage,
    BrainProvider,
    BrainProviderAuthError,
    BrainProviderBadRequestError,
    BrainProviderConnectionError,
    BrainProviderError,
    BrainProviderRateLimitError,
    BrainToolCall,
    BrainToolFunction,
    read_keyring_secret,
)
from .anthropic import AnthropicProvider
from .chatgpt_oauth import ChatGPTOAuthProvider
from .custom import CustomProvider
from .gemma import GemmaProvider
from .gemini import GeminiProvider
from .ollama_generic import OllamaGenericProvider
from .openai import OpenAIProvider
from .openrouter import OpenRouterProvider
from .qwen import QwenProvider

PROVIDER_CLASSES = {
    "gemma": GemmaProvider,
    "ollama": OllamaGenericProvider,
    "qwen": QwenProvider,
    "qwen_cloud": QwenProvider,
    "openai": OpenAIProvider,
    "openrouter": OpenRouterProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "custom": CustomProvider,
    "chatgpt_oauth": ChatGPTOAuthProvider,
}


def normalize_provider_name(provider: str | None) -> str:
    value = str(provider or "").strip().lower()
    return value or "gemma"


def create_provider(settings: dict, layer_name: str = "companion") -> BrainProvider:
    provider_name = normalize_provider_name(settings.get("provider"))
    provider_cls = PROVIDER_CLASSES.get(provider_name, GemmaProvider)
    return provider_cls(settings=settings, layer_name=layer_name)


def create_client(settings: dict, layer_name: str = "companion") -> BrainClientShim:
    return BrainClientShim(create_provider(settings, layer_name=layer_name))
