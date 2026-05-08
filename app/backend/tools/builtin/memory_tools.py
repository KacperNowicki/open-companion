from __future__ import annotations

import math

import memory as memory_store

DEFAULT_SEARCH_BUDGET = 1200


def write_memory(entry: str, topic: str = "", config=None) -> str:
    clean_entry = str(entry or "").strip()
    if not clean_entry:
        return "Memory entry is required."

    memory_store.init_memory()
    memory_store.append_memory(clean_entry)
    return f"Saved memory: {clean_entry}"


def search_memories(query: str, max_results: int = 5, config=None) -> str:
    clean_query = str(query or "").strip()
    if not clean_query:
        return "Memory search query is required."

    memory_store.init_memory()
    active_config = config or {}
    try:
        requested = int(max_results or 5)
    except (TypeError, ValueError):
        requested = 5
    requested = max(1, min(10, requested))
    budget = max(DEFAULT_SEARCH_BUDGET, requested * 300)
    result = memory_store.retrieve_topic_bundle_memories(clean_query, active_config, budget)
    clean_result = str(result or "").strip()
    if not clean_result or clean_result.startswith("[Memories pruned"):
        fallback = memory_store.load_all_memories().strip()
        if not fallback:
            return "No memories stored yet."
        lines = [line for line in fallback.splitlines() if clean_query.lower() in line.lower()]
        if lines:
            return "\n".join(lines[:requested])
        return fallback[: min(len(fallback), 3000)]
    return clean_result
