from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import context_manager
from runtime_paths import SESSION_SUMMARIES_DIR, ensure_runtime_dirs

_FRONTMATTER_RE = re.compile(r"^---\n(?P<body>.*?)\n---\n?", re.DOTALL)
_TOKEN_RE = re.compile(r"[a-z0-9_]{3,}")
_PATH_RE = re.compile(r"(?:[A-Za-z]:\\|/?)(?:[\w .-]+[\\/])+[\w.-]+")
_DEFAULT_SUMMARY_CONFIG = {
    "enabled": True,
    "max_recent": 6,
    "max_injected": 2,
    "compact_after_messages": 40,
    "compact_after_tokens": 12000,
    "budget_tokens": 1500,
    "retain_recent_messages": 6,
}
_MEMORY_FACT_HINTS = ("remember", "i like", "i prefer", "my ", "i work", "i live", "favorite")


def get_summary_config(config: dict | None = None) -> dict:
    context_cfg = config.get("context", {}) if isinstance(config, dict) else {}
    summary_cfg = context_cfg.get("session_summaries", {}) if isinstance(context_cfg, dict) else {}
    merged = dict(_DEFAULT_SUMMARY_CONFIG)
    if isinstance(summary_cfg, dict):
        merged.update(summary_cfg)
    merged["enabled"] = bool(merged.get("enabled", True))
    for key in ("max_recent", "max_injected", "compact_after_messages", "compact_after_tokens", "budget_tokens", "retain_recent_messages"):
        try:
            merged[key] = max(0, int(merged.get(key, _DEFAULT_SUMMARY_CONFIG[key])))
        except (TypeError, ValueError):
            merged[key] = _DEFAULT_SUMMARY_CONFIG[key]
    return merged


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _slugify_timestamp(timestamp: str) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "", str(timestamp or ""))


def _summary_layer_dir(layer_name: str) -> Path:
    return SESSION_SUMMARIES_DIR / layer_name


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return " ".join(
            str(item.get("text") or item.get("content") or "").strip()
            for item in content
            if isinstance(item, dict)
        ).strip()
    if isinstance(content, dict):
        return str(content.get("content") or content.get("text") or "").strip()
    return str(content or "").strip()


def _shorten(text: str, limit: int = 160) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def _dedupe_keep_order(items: list[str], limit: int = 5) -> list[str]:
    seen: set[str] = set()
    chosen: list[str] = []
    for item in items:
        clean = _shorten(item)
        if not clean:
            continue
        lowered = clean.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        chosen.append(clean)
    return chosen[:limit]


def _collect_tool_results(conversation: list[dict]) -> list[str]:
    results: list[str] = []
    for message in conversation:
        if message.get("role") == "tool":
            results.append(_extract_text(message.get("content")))
        for tool_result in message.get("tool_responses", []) or []:
            if isinstance(tool_result, dict):
                results.append(_extract_text(tool_result.get("result") or tool_result.get("content")))
            else:
                results.append(_extract_text(tool_result))
    return _dedupe_keep_order(results, limit=4)


def _candidate_memory_facts(user_messages: list[str]) -> list[str]:
    facts = []
    for message in user_messages:
        lowered = message.lower()
        if any(hint in lowered for hint in _MEMORY_FACT_HINTS):
            facts.append(message)
    return _dedupe_keep_order(facts, limit=4)


def _temporary_context(user_messages: list[str], assistant_messages: list[str], tool_results: list[str]) -> list[str]:
    findings: list[str] = []
    for bucket in (user_messages, assistant_messages, tool_results):
        for item in bucket:
            for match in _PATH_RE.findall(item):
                findings.append(match.replace("\\", "/"))
    if not findings and tool_results:
        findings.extend(tool_results[:2])
    return _dedupe_keep_order(findings, limit=4)


def _unresolved_threads(user_messages: list[str], assistant_messages: list[str]) -> list[str]:
    if not user_messages:
        return []
    if not assistant_messages:
        return [user_messages[-1]]
    last_reply = assistant_messages[-1].lower()
    if any(marker in last_reply for marker in ("couldn't", "failed", "error", "not sure", "need more")):
        return [user_messages[-1]]
    return []


def _render_bullets(title: str, items: list[str]) -> list[str]:
    lines = [f"## {title}"]
    if items:
        lines.extend(f"- {item}" for item in items)
    else:
        lines.append("- None recorded.")
    return lines


def _frontmatter(metadata: dict[str, object]) -> str:
    lines = ["---"]
    for key, value in metadata.items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


def build_session_summary(
    layer_name: str,
    conversation: list[dict],
    *,
    session_id: str = "",
    checkpoint: int = 0,
    trigger: str = "threshold",
    session_started_at: str = "",
    session_ended_at: str = "",
) -> dict[str, object]:
    non_system = [message for message in conversation if message.get("role") != "system"]
    if not non_system:
        raise ValueError("No conversation history is available for summary compaction.")
    created_at = _now()
    user_messages = _dedupe_keep_order(
        [_extract_text(message.get("content")) for message in non_system if message.get("role") == "user"],
        limit=5,
    )
    assistant_messages = _dedupe_keep_order(
        [_extract_text(message.get("content")) for message in non_system if message.get("role") == "assistant"],
        limit=5,
    )
    tool_results = _collect_tool_results(non_system)
    metadata = {
        "layer": layer_name,
        "session_id": session_id or "session",
        "checkpoint": checkpoint,
        "trigger": trigger,
        "created_at": created_at,
        "session_started_at": session_started_at or created_at,
        "session_ended_at": session_ended_at or created_at,
    }
    lines = [
        _frontmatter(metadata),
        "# Session Summary",
        "",
        *_render_bullets("Major User Intents Handled", user_messages),
        "",
        *_render_bullets("Decisions Made", assistant_messages or tool_results),
        "",
        *_render_bullets("Unresolved Threads", _unresolved_threads(user_messages, assistant_messages)),
        "",
        *_render_bullets("Important Temporary Context", _temporary_context(user_messages, assistant_messages, tool_results)),
        "",
        *_render_bullets("Candidate Memory Facts", _candidate_memory_facts(user_messages)),
        "",
        *_render_bullets("Important Tool Results", tool_results),
        "",
    ]
    return {
        "metadata": metadata,
        "content": "\n".join(lines).strip() + "\n",
    }


def write_session_summary(
    layer_name: str,
    conversation: list[dict],
    *,
    session_id: str = "",
    checkpoint: int = 0,
    trigger: str = "threshold",
    session_started_at: str = "",
    session_ended_at: str = "",
) -> dict[str, object]:
    ensure_runtime_dirs()
    summary = build_session_summary(
        layer_name,
        conversation,
        session_id=session_id,
        checkpoint=checkpoint,
        trigger=trigger,
        session_started_at=session_started_at,
        session_ended_at=session_ended_at,
    )
    metadata = dict(summary["metadata"])
    layer_dir = _summary_layer_dir(layer_name)
    layer_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{_slugify_timestamp(metadata['created_at'])}-{metadata['session_id']}-cp{checkpoint:02d}-{trigger}.md"
    path = layer_dir / filename
    path.write_text(str(summary["content"]), encoding="utf-8")
    return {
        **metadata,
        "path": str(path),
        "content": str(summary["content"]),
    }


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    match = _FRONTMATTER_RE.match(content or "")
    if not match:
        return {}, content
    metadata: dict[str, str] = {}
    for line in match.group("body").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip()
    return metadata, content[match.end():].strip()


def list_recent_summaries(layer_name: str, limit: int = 5) -> list[dict[str, object]]:
    layer_dir = _summary_layer_dir(layer_name)
    if not layer_dir.exists():
        return []
    summaries = []
    for candidate in sorted(layer_dir.glob("*.md"), reverse=True):
        try:
            raw = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        metadata, body = _parse_frontmatter(raw)
        summaries.append(
            {
                **metadata,
                "path": str(candidate),
                "content": raw,
                "body": body,
            }
        )
    return summaries[: max(0, limit)]


def _query_tokens(query: str) -> set[str]:
    return set(_TOKEN_RE.findall(str(query or "").lower()))


def retrieve_recent_summaries(layer_name: str, limit: int = 2, budget_tokens: int | None = None) -> str:
    summaries = list_recent_summaries(layer_name, limit=limit)
    return _render_retrieved_summaries(summaries, budget_tokens=budget_tokens)


def _render_retrieved_summaries(items: list[dict[str, object]], budget_tokens: int | None = None) -> str:
    if not items:
        return ""
    header = "## Relevant Session Summaries"
    parts = [header]
    budget = budget_tokens if budget_tokens is not None else _DEFAULT_SUMMARY_CONFIG["budget_tokens"]
    for item in items:
        section = f"### {item.get('created_at', '')}\n{str(item.get('body') or '').strip()}"
        candidate = "\n\n".join(parts + [section])
        if context_manager.count_tokens(candidate) > budget:
            break
        parts.append(section)
    if len(parts) == 1:
        return ""
    return "\n\n".join(parts).strip()


def retrieve_relevant_summaries(
    layer_name: str,
    query: str,
    *,
    limit: int = 2,
    recent_limit: int = 6,
    budget_tokens: int | None = None,
) -> str:
    query_tokens = _query_tokens(query)
    if not query_tokens:
        return ""
    candidates = list_recent_summaries(layer_name, limit=recent_limit)
    scored = []
    for index, item in enumerate(candidates):
        haystack = str(item.get("body") or item.get("content") or "").lower()
        hits = sum(1 for token in query_tokens if token in haystack)
        if hits <= 0:
            continue
        scored.append((hits * 10 + max(0, recent_limit - index), item))
    scored.sort(key=lambda entry: entry[0], reverse=True)
    selected = [item for _, item in scored[: max(0, limit)]]
    return _render_retrieved_summaries(selected, budget_tokens=budget_tokens)
