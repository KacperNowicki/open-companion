from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class OpenRouterModelCard:
    model_id: str
    display_name: str
    context_window: int | None = None
    supports_tools: bool = True
    family: str = "openrouter"

    def to_metadata(self) -> dict:
        return asdict(self)


OPENROUTER_FALLBACK_MODELS: tuple[OpenRouterModelCard, ...] = (
    OpenRouterModelCard("deepseek/deepseek-v4-pro", "DeepSeek V4 Pro"),
    OpenRouterModelCard("deepseek/deepseek-v4-flash", "DeepSeek V4 Flash"),
    OpenRouterModelCard("moonshotai/kimi-k2.6", "Kimi K2.6"),
    OpenRouterModelCard("meta-llama/llama-3.3-70b-instruct", "Llama 3.3 70B Instruct"),
    OpenRouterModelCard("google/gemini-2.5-flash", "Gemini 2.5 Flash"),
    OpenRouterModelCard("anthropic/claude-sonnet-4", "Claude Sonnet 4"),
    OpenRouterModelCard("qwen/qwen3-235b-a22b", "Qwen3 235B A22B"),
)


def get_openrouter_fallback_model_ids() -> list[str]:
    return [card.model_id for card in OPENROUTER_FALLBACK_MODELS]


def get_openrouter_model_capabilities(model_name: str | None) -> dict:
    normalized = str(model_name or "").strip().lower()
    for card in OPENROUTER_FALLBACK_MODELS:
        if normalized == card.model_id.lower():
            metadata = card.to_metadata()
            metadata["model"] = str(model_name or "").strip()
            metadata["registry_match"] = True
            return metadata
    return {
        "model_id": str(model_name or "").strip(),
        "display_name": str(model_name or "OpenRouter").strip() or "OpenRouter",
        "context_window": None,
        "supports_tools": True,
        "family": "openrouter",
        "model": str(model_name or "").strip(),
        "registry_match": False,
    }
