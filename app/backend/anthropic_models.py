from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class AnthropicModelCard:
    id: str
    aliases: tuple[str, ...]
    label: str
    family: str
    preferred_api: str
    description: str = ""
    reasoning_efforts: tuple[str, ...] = ()
    default_reasoning_effort: str | None = None
    thinking_budget_map: dict[str, int] | None = None
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


_ANTHROPIC_MODEL_CARDS: tuple[AnthropicModelCard, ...] = (
    # Aliases are canonical short names only.  Dated variants (e.g.
    # claude-sonnet-4-5-20250514) are discovered at runtime via the
    # /v1/models API and matched to cards through prefix matching in
    # matches().
    AnthropicModelCard(
        id="claude-opus-4",
        aliases=("claude-opus-4",),
        label="Claude Opus 4",
        family="anthropic_claude_reasoning",
        preferred_api="messages",
        description="Most capable Claude model with extended thinking support.",
        reasoning_efforts=("low", "medium", "high"),
        default_reasoning_effort="medium",
        thinking_budget_map={"low": 1024, "medium": 8192, "high": 32768},
        supports_tools=True,
        supports_structured_outputs=False,
        supports_image_input=True,
        context_window=200_000,
        max_output_tokens=32_000,
        knowledge_cutoff="2025-03-01",
    ),
    AnthropicModelCard(
        id="claude-sonnet-4",
        aliases=("claude-sonnet-4",),
        label="Claude Sonnet 4",
        family="anthropic_claude_reasoning",
        preferred_api="messages",
        description="High-capability Claude model with extended thinking support.",
        reasoning_efforts=("low", "medium", "high"),
        default_reasoning_effort="medium",
        thinking_budget_map={"low": 1024, "medium": 8192, "high": 32768},
        supports_tools=True,
        supports_structured_outputs=False,
        supports_image_input=True,
        context_window=200_000,
        max_output_tokens=16_000,
        knowledge_cutoff="2025-03-01",
    ),
    AnthropicModelCard(
        id="claude-haiku-4",
        aliases=("claude-haiku-4",),
        label="Claude Haiku 4",
        family="anthropic_claude_chat",
        preferred_api="messages",
        description="Fast and compact Claude model for everyday tasks.",
        supports_tools=True,
        supports_structured_outputs=False,
        supports_image_input=True,
        context_window=200_000,
        max_output_tokens=8_192,
        knowledge_cutoff="2025-03-01",
    ),
    AnthropicModelCard(
        id="claude-3-5-sonnet",
        aliases=("claude-3-5-sonnet",),
        label="Claude 3.5 Sonnet",
        family="anthropic_claude_chat",
        preferred_api="messages",
        description="Previous-generation Claude chat model.",
        supports_tools=True,
        supports_structured_outputs=False,
        supports_image_input=True,
        context_window=200_000,
        max_output_tokens=8_192,
        knowledge_cutoff="2024-04-01",
    ),
    AnthropicModelCard(
        id="claude-3-5-haiku",
        aliases=("claude-3-5-haiku",),
        label="Claude 3.5 Haiku",
        family="anthropic_claude_chat",
        preferred_api="messages",
        description="Fast previous-generation Claude model.",
        supports_tools=True,
        supports_structured_outputs=False,
        supports_image_input=True,
        context_window=200_000,
        max_output_tokens=8_192,
        knowledge_cutoff="2024-04-01",
    ),
)

_ANTHROPIC_FALLBACK = AnthropicModelCard(
    id="anthropic-unknown",
    aliases=(),
    label="Claude (unknown)",
    family="anthropic_claude_chat",
    preferred_api="messages",
    description="Conservative fallback profile for unrecognized Anthropic model ids.",
    supports_tools=True,
    supports_structured_outputs=False,
    supports_image_input=False,
)


def get_anthropic_model_card(model_name: str | None) -> AnthropicModelCard:
    normalized = str(model_name or "").strip()
    if not normalized:
        return _ANTHROPIC_FALLBACK
    for card in _ANTHROPIC_MODEL_CARDS:
        if card.matches(normalized):
            return card
    return _ANTHROPIC_FALLBACK


def get_anthropic_model_capabilities(model_name: str | None) -> dict:
    card = get_anthropic_model_card(model_name)
    metadata = card.to_metadata()
    metadata["model"] = str(model_name or "").strip()
    metadata["registry_match"] = card.id != _ANTHROPIC_FALLBACK.id
    return metadata
