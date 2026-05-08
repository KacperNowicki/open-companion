from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class QwenModelCard:
    model_id: str
    display_name: str
    context_window: int | None
    supports_thinking: bool
    supports_tools: bool
    path: str
    family: str = "qwen"

    def matches(self, model_name: str | None) -> bool:
        normalized = str(model_name or "").strip().lower()
        return bool(normalized and normalized == self.model_id.lower())

    def to_metadata(self) -> dict:
        return asdict(self)


QWEN_LOCAL_MODEL_CARDS: tuple[QwenModelCard, ...] = (
    QwenModelCard("qwen3:8b", "Qwen3 8B", 32_768, True, True, "local"),
    QwenModelCard("qwen3:14b", "Qwen3 14B", 32_768, True, True, "local"),
    QwenModelCard("qwen3:32b", "Qwen3 32B", 32_768, True, True, "local"),
    QwenModelCard("qwen3:30b-a3b", "Qwen3 30B A3B", 32_768, True, True, "local"),
    QwenModelCard("qwen3.6:27b", "Qwen3.6 27B", 262_144, True, True, "local"),
    QwenModelCard("qwen3.6:35b-a3b", "Qwen3.6 35B A3B", 262_144, True, True, "local"),
)

QWEN_CLOUD_MODEL_CARDS: tuple[QwenModelCard, ...] = (
    QwenModelCard("qwen3-max", "Qwen3 Max", 1_000_000, True, True, "cloud"),
    QwenModelCard("qwen3-max-preview", "Qwen3 Max Preview", 1_000_000, True, True, "cloud"),
    QwenModelCard("qwen3.5-plus", "Qwen3.5 Plus", 1_000_000, True, True, "cloud"),
    QwenModelCard("qwen3-235b-a22b", "Qwen3 235B A22B", 262_144, True, True, "cloud"),
    QwenModelCard("qwen3-coder-plus", "Qwen3 Coder Plus", 262_144, True, True, "cloud"),
    QwenModelCard("qwen3.5-flash", "Qwen3.5 Flash", 131_072, True, True, "cloud"),
)

QWEN_MODEL_CARDS: tuple[QwenModelCard, ...] = QWEN_LOCAL_MODEL_CARDS + QWEN_CLOUD_MODEL_CARDS

QWEN_FALLBACK_CARD = QwenModelCard(
    model_id="qwen-unknown",
    display_name="Qwen",
    context_window=None,
    supports_thinking=True,
    supports_tools=True,
    path="local",
)


def get_qwen_model_card(model_name: str | None) -> QwenModelCard:
    normalized = str(model_name or "").strip()
    if not normalized:
        return QWEN_FALLBACK_CARD
    for card in QWEN_MODEL_CARDS:
        if card.matches(normalized):
            return card
    if "qwen" in normalized.lower():
        return QWEN_FALLBACK_CARD
    return QWEN_FALLBACK_CARD


def get_qwen_model_capabilities(model_name: str | None) -> dict:
    card = get_qwen_model_card(model_name)
    metadata = card.to_metadata()
    metadata["model"] = str(model_name or "").strip()
    metadata["registry_match"] = card.model_id != QWEN_FALLBACK_CARD.model_id
    return metadata


def get_qwen_model_ids(path: str | None = None) -> list[str]:
    clean_path = str(path or "").strip().lower()
    cards = QWEN_MODEL_CARDS
    if clean_path in {"local", "cloud"}:
        cards = tuple(card for card in QWEN_MODEL_CARDS if card.path == clean_path)
    return [card.model_id for card in cards]
