"""
Context Manager
===============
Token counting and budget utilities for dynamic context window management.

All counts are approximate (1 token ≈ 4 chars). This avoids a tokenizer
dependency while providing sufficient accuracy for budget decisions.
"""

from __future__ import annotations


def count_tokens(text: str) -> int:
    """Approximate token count. 1 token ≈ 4 chars."""
    return max(1, len(str(text or "")) // 4)


def count_messages_tokens(messages: list[dict]) -> int:
    """Count approximate tokens across a list of messages."""
    total = 0
    for msg in messages:
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = " ".join(
                p.get("text", "") for p in content if isinstance(p, dict)
            )
        total += count_tokens(str(content)) + 4  # +4 for role/overhead
    return total
