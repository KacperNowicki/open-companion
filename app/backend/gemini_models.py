from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class GeminiModelCard:
    model_id: str
    display_name: str
    context_window: int | None
    supports_thinking: bool
    supports_tools: bool
    family: str = "gemini"

    def matches(self, model_name: str | None) -> bool:
        normalized = str(model_name or "").strip().lower()
        return bool(normalized and normalized == self.model_id.lower())

    def to_metadata(self) -> dict:
        return asdict(self)


GEMINI_MODEL_CARDS: tuple[GeminiModelCard, ...] = (
    GeminiModelCard("gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview", 1_048_576, True, True),
    GeminiModelCard("gemini-3-pro-preview", "Gemini 3 Pro Preview", 1_048_576, True, True),
    GeminiModelCard("gemini-3-flash-preview", "Gemini 3 Flash Preview", 1_048_576, True, True),
    GeminiModelCard("gemini-2.5-pro", "Gemini 2.5 Pro", 1_048_576, True, True),
    GeminiModelCard("gemini-2.5-flash", "Gemini 2.5 Flash", 1_048_576, True, True),
    GeminiModelCard("gemini-2.0-flash", "Gemini 2.0 Flash", 1_048_576, False, True),
)

GEMINI_FALLBACK_CARD = GeminiModelCard("gemini", "Gemini", None, False, True)


def get_gemini_model_card(model_name: str | None) -> GeminiModelCard:
    normalized = str(model_name or "").strip()
    if not normalized:
        return GEMINI_FALLBACK_CARD
    for card in GEMINI_MODEL_CARDS:
        if card.matches(normalized):
            return card
    if "gemini" in normalized.lower():
        return GEMINI_FALLBACK_CARD
    return GEMINI_FALLBACK_CARD


def get_gemini_model_capabilities(model_name: str | None) -> dict:
    card = get_gemini_model_card(model_name)
    metadata = card.to_metadata()
    metadata["model"] = str(model_name or "").strip()
    metadata["registry_match"] = card.model_id != GEMINI_FALLBACK_CARD.model_id
    return metadata


def get_gemini_model_ids() -> list[str]:
    return [card.model_id for card in GEMINI_MODEL_CARDS]
