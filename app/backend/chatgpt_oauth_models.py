from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


_CATALOG_PATH = Path(__file__).resolve().parents[1] / "shared" / "chatgpt_oauth_models.json"


@dataclass(frozen=True, slots=True)
class ChatGPTOAuthModelCard:
    id: str
    aliases: tuple[str, ...]
    label: str
    description: str
    supports_tools: bool = True
    context_window: int | None = None
    max_output_tokens: int | None = None

    def matches(self, model_name: str | None) -> bool:
        normalized = str(model_name or "").strip().lower()
        if not normalized:
            return False
        for alias in self.aliases:
            candidate = str(alias or "").strip().lower()
            if not candidate:
                continue
            if normalized == candidate or normalized.startswith(f"{candidate}-"):
                return True
        return normalized == self.id.strip().lower()


def _coerce_optional_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


@lru_cache(maxsize=1)
def get_chatgpt_oauth_model_cards() -> tuple[ChatGPTOAuthModelCard, ...]:
    try:
        payload = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        payload = []
    cards: list[ChatGPTOAuthModelCard] = []
    for entry in payload if isinstance(payload, list) else []:
        if not isinstance(entry, dict):
            continue
        model_id = str(entry.get("id") or "").strip()
        if not model_id:
            continue
        aliases = tuple(
            alias
            for alias in (str(value or "").strip() for value in entry.get("aliases") or [model_id])
            if alias
        ) or (model_id,)
        cards.append(
            ChatGPTOAuthModelCard(
                id=model_id,
                aliases=aliases,
                label=str(entry.get("label") or model_id).strip() or model_id,
                description=str(entry.get("description") or "").strip(),
                supports_tools=bool(entry.get("supports_tools", True)),
                context_window=_coerce_optional_int(entry.get("context_window")),
                max_output_tokens=_coerce_optional_int(entry.get("max_output_tokens")),
            )
        )
    return tuple(cards)


def get_chatgpt_oauth_model_ids() -> list[str]:
    return [card.id for card in get_chatgpt_oauth_model_cards()]


def get_default_chatgpt_oauth_model_id() -> str:
    cards = get_chatgpt_oauth_model_cards()
    return cards[0].id if cards else "gpt-5.4"


def normalize_chatgpt_oauth_model_name(model_name: str | None) -> str:
    card = get_chatgpt_oauth_model_card(model_name)
    return card.id if card is not None else get_default_chatgpt_oauth_model_id()


def get_chatgpt_oauth_model_card(model_name: str | None) -> ChatGPTOAuthModelCard | None:
    for card in get_chatgpt_oauth_model_cards():
        if card.matches(model_name):
            return card
    return get_chatgpt_oauth_model_cards()[0] if get_chatgpt_oauth_model_cards() else None


def get_chatgpt_oauth_model_capabilities(model_name: str | None) -> dict:
    card = get_chatgpt_oauth_model_card(model_name)
    if card is None:
        return {
            "model": str(model_name or "").strip(),
            "registry_match": False,
        }
    return {
        "id": card.id,
        "model": str(model_name or "").strip() or card.id,
        "resolved_model": card.id,
        "label": card.label,
        "description": card.description,
        "supports_tools": card.supports_tools,
        "context_window": card.context_window,
        "max_output_tokens": card.max_output_tokens,
        "registry_match": any(existing.matches(model_name) for existing in get_chatgpt_oauth_model_cards()),
    }
