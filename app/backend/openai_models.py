from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class OpenAIModelCard:
    id: str
    aliases: tuple[str, ...]
    label: str
    family: str
    preferred_api: str
    reasoning_efforts: tuple[str, ...] = ()
    default_reasoning_effort: str | None = None
    default_reasoning_summary: str | None = None
    supports_tools: bool = True
    supports_structured_outputs: bool = False
    supports_image_input: bool = False
    context_window: int | None = None
    max_output_tokens: int | None = None
    knowledge_cutoff: str | None = None

    def matches(self, model_name: str) -> bool:
        normalized = str(model_name or "").strip().lower()
        if not normalized:
            return False
        for alias in self.aliases:
            candidate = alias.strip().lower()
            if not candidate:
                continue
            if normalized == candidate or normalized.startswith(f"{candidate}-"):
                return True
        return False

    def to_metadata(self) -> dict:
        return asdict(self)


_OPENAI_MODEL_CARDS: tuple[OpenAIModelCard, ...] = (
    OpenAIModelCard(
        id="gpt-5.4",
        aliases=("gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.4-pro"),
        label="GPT-5.4",
        family="openai_reasoning",
        preferred_api="responses",
        reasoning_efforts=("none", "minimal", "low", "medium", "high", "xhigh"),
        default_reasoning_effort="none",
        default_reasoning_summary="auto",
        supports_tools=True,
        supports_structured_outputs=True,
        supports_image_input=True,
        context_window=1_050_000,
        max_output_tokens=128_000,
        knowledge_cutoff="2025-08-31",
    ),
    OpenAIModelCard(
        id="gpt-5",
        aliases=("gpt-5", "gpt-5-mini", "gpt-5-nano"),
        label="GPT-5",
        family="openai_reasoning",
        preferred_api="responses",
        reasoning_efforts=("none", "minimal", "low", "medium", "high", "xhigh"),
        default_reasoning_effort="none",
        default_reasoning_summary="auto",
        supports_tools=True,
        supports_structured_outputs=True,
        supports_image_input=True,
        context_window=400_000,
        max_output_tokens=128_000,
        knowledge_cutoff="2024-06-01",
    ),
    OpenAIModelCard(
        id="o4-mini",
        aliases=("o4-mini",),
        label="o4-mini",
        family="openai_reasoning",
        preferred_api="responses",
        reasoning_efforts=("low", "medium", "high"),
        default_reasoning_effort="medium",
        default_reasoning_summary="auto",
        supports_tools=True,
        supports_structured_outputs=True,
        supports_image_input=True,
    ),
    OpenAIModelCard(
        id="o3",
        aliases=("o3", "o3-mini"),
        label="o3",
        family="openai_reasoning",
        preferred_api="responses",
        reasoning_efforts=("low", "medium", "high"),
        default_reasoning_effort="medium",
        default_reasoning_summary="auto",
        supports_tools=True,
        supports_structured_outputs=True,
        supports_image_input=True,
    ),
    OpenAIModelCard(
        id="gpt-4.1",
        aliases=("gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano"),
        label="GPT-4.1",
        family="openai_chat",
        preferred_api="chat_completions",
        supports_tools=True,
        supports_structured_outputs=True,
        supports_image_input=True,
        context_window=1_000_000,
        max_output_tokens=32_000,
    ),
    OpenAIModelCard(
        id="gpt-4o",
        aliases=("gpt-4o", "gpt-4o-mini"),
        label="GPT-4o",
        family="openai_chat",
        preferred_api="chat_completions",
        supports_tools=True,
        supports_structured_outputs=True,
        supports_image_input=True,
        context_window=128_000,
        max_output_tokens=16_384,
    ),
)

_OPENAI_LEGACY_FALLBACK = OpenAIModelCard(
    id="openai-legacy",
    aliases=(),
    label="OpenAI Legacy Fallback",
    family="openai_chat",
    preferred_api="chat_completions",
    supports_tools=True,
    supports_structured_outputs=False,
    supports_image_input=False,
)


def get_openai_model_card(model_name: str | None) -> OpenAIModelCard:
    normalized = str(model_name or "").strip()
    if not normalized:
        return _OPENAI_LEGACY_FALLBACK
    for card in _OPENAI_MODEL_CARDS:
        if card.matches(normalized):
            return card
    return _OPENAI_LEGACY_FALLBACK


def get_openai_model_capabilities(model_name: str | None) -> dict:
    card = get_openai_model_card(model_name)
    metadata = card.to_metadata()
    metadata["model"] = str(model_name or "").strip()
    metadata["registry_match"] = card.id != _OPENAI_LEGACY_FALLBACK.id
    return metadata
