"""
Memory System
=============
Single-file long-term memory with embedding-based retrieval.

Storage:
  companion/memory/memory.md  # All durable memory entries

Short-term: Full conversation history in context window (automatic).
Long-term: Facts extracted during conversation and appended to memory.md.
Retrieval: nomic-embed-text embeddings via Ollama, top-k cosine similarity.
  Falls back to full load only when embeddings are unavailable.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path

from runtime_paths import MEMORY_DIR, PROJECT_ROOT, ensure_runtime_dirs

logger = logging.getLogger(__name__)

MEMORY_FILENAME = "memory.md"
MEMORY_PATH = MEMORY_DIR / MEMORY_FILENAME

# Default file created when companion/memory/ does not exist yet.
DEFAULT_TOPICS = {
    MEMORY_FILENAME: "# Memory\n",
}

TIMESTAMP_PREFIX_RE = re.compile(r"^\[(?P<timestamp>[^\]]+)\]\s*")
_LEADING_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)
_LEADING_USER_POSSESSIVE_RE = re.compile(r"^(?:the\s+)?user's\s+", re.IGNORECASE)
_SUBJECT_PATTERNS = (
    re.compile(r"^(?P<subject>.+?)\s+is\s+.+$", re.IGNORECASE),
    re.compile(r"^(?P<subject>.+?)\s+are\s+.+$", re.IGNORECASE),
    re.compile(r"^(?P<subject>.+?)\s+was\s+.+$", re.IGNORECASE),
    re.compile(r"^(?P<subject>.+?)\s+were\s+.+$", re.IGNORECASE),
    re.compile(r"^(?P<subject>.+?)\s+likes\s+.+$", re.IGNORECASE),
    re.compile(r"^(?P<subject>.+?)\s+dislikes\s+.+$", re.IGNORECASE),
    re.compile(r"^(?P<subject>.+?)\s+prefers\s+.+$", re.IGNORECASE),
    re.compile(r"^(?P<subject>.+?)\s+loves\s+.+$", re.IGNORECASE),
    re.compile(r"^(?P<subject>.+?)\s+hates\s+.+$", re.IGNORECASE),
)

_TOOL_REGISTRY_PATH = PROJECT_ROOT / "app" / "backend" / "tools" / "registry.json"
_TOOL_MEMORY_PHRASES = (
    "available commands",
    "available command",
    "available tools",
    "available tool",
    "list available tools",
    "what i can do",
    "what you can do",
    "companion mode includes commands",
    "current layer",
    "pc doctor is used for",
)
_TOOL_MEMORY_HINTS = (
    "command",
    "commands",
    "tool",
    "tools",
    "capability",
    "capabilities",
    "diagnostic",
    "diagnostics",
    "system repair",
    "system repairs",
    "repair",
    "repairs",
    "available",
)


def _load_known_tool_names() -> set[str]:
    try:
        payload = json.loads(_TOOL_REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return set()

    raw_tools = payload.get("tools", payload if isinstance(payload, list) else [])
    names: set[str] = set()
    if not isinstance(raw_tools, list):
        return names

    for item in raw_tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip().lower()
        if name:
            names.add(name)
    return names


KNOWN_TOOL_NAMES = _load_known_tool_names()


def strip_llm_preamble(text: str) -> str:
    """Strip Gemma thought blocks and remaining control tokens."""
    text = re.sub(r"<\|channel>.*?<channel\|>", "", text, flags=re.DOTALL)
    text = re.sub(r"<\|[^>]+\|?>", "", text)
    return text.strip()


def _sanitize_text_for_utf8(value: str) -> str:
    clean = str(value or "").encode("utf-8", errors="replace").decode("utf-8")
    clean = clean.replace("\ufffd", " ")
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", clean)


def _read_memory_file(filepath: Path) -> tuple[list[str], list[str]]:
    header_lines: list[str] = []
    entries: list[str] = []
    if not filepath.exists():
        return ["# Memory"], []

    for line in filepath.read_text(encoding="utf-8").splitlines():
        if line.startswith("- "):
            entry = line[2:].strip()
            if entry:
                entries.append(entry)
        else:
            header_lines.append(line)

    if not header_lines:
        header_lines = ["# Memory"]
    return header_lines, entries


def _render_memory_file(header_lines: list[str], entries: list[str]) -> str:
    lines = list(header_lines) if header_lines else ["# Memory"]
    cleaned_entries = [str(entry).strip() for entry in entries if str(entry).strip()]
    if cleaned_entries and lines and lines[-1].strip():
        lines.append("")
    lines.extend(f"- {entry}" for entry in cleaned_entries)
    return "\n".join(lines).rstrip() + "\n"


def _migrate_topic_files_to_single() -> None:
    """Merge legacy topic files into memory.md and remove the old files."""
    ensure_runtime_dirs()

    legacy_files = [
        filepath
        for filepath in sorted(MEMORY_DIR.glob("memory_*.md"))
        if filepath.name != MEMORY_FILENAME
    ]
    target = MEMORY_PATH

    if not legacy_files:
        return

    merged_entries: list[str] = []
    header_lines: list[str] = ["# Memory"]

    if target.exists():
        header_lines, existing_entries = _read_memory_file(target)
        merged_entries.extend(existing_entries)

    for filepath in legacy_files:
        _, entries = _read_memory_file(filepath)
        merged_entries.extend(entries)

    # Deduplicate during migration so repeated facts do not survive the merge.
    deduped_entries: list[str] = []
    for entry in merged_entries:
        if not _is_duplicate(entry, deduped_entries):
            deduped_entries.append(entry)

    if deduped_entries:
        target.write_text(_render_memory_file(header_lines, deduped_entries), encoding="utf-8")
    elif not target.exists():
        target.write_text("# Memory\n", encoding="utf-8")

    for filepath in legacy_files:
        try:
            filepath.unlink()
        except Exception:
            logger.exception("[memory] Failed to remove legacy memory file %s", filepath.name)


def init_memory() -> None:
    """
    Initialize the memory directory and ensure memory.md exists.
    Called once at startup.
    """
    ensure_runtime_dirs()
    _migrate_topic_files_to_single()
    if not MEMORY_PATH.exists():
        MEMORY_PATH.write_text("# Memory\n", encoding="utf-8")


def _memory_file_has_entries(content: str) -> bool:
    return any(line.startswith("- ") for line in content.splitlines())


def load_all_memories() -> str:
    """
    Load memory.md and return a formatted string suitable for prompt injection.
    """
    if not MEMORY_PATH.exists():
        return ""

    content = MEMORY_PATH.read_text(encoding="utf-8").strip()
    if not content or not _memory_file_has_entries(content):
        return ""

    return "=== Long-term Memories ===\n\n" + content


def _memory_timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def format_memory_entry(entry: str, timestamp: str | None = None) -> str:
    clean_entry = _sanitize_text_for_utf8(entry).strip()
    if not clean_entry:
        return ""

    if TIMESTAMP_PREFIX_RE.match(clean_entry):
        return clean_entry

    return f"[{timestamp or _memory_timestamp()}] {clean_entry}"


def strip_memory_timestamp(entry: str) -> str:
    return TIMESTAMP_PREFIX_RE.sub("", str(entry or "").strip(), count=1).strip()


def _normalize_memory_phrase(text: str) -> str:
    normalized = " ".join(str(text or "").strip().lower().split())
    normalized = _LEADING_ARTICLE_RE.sub("", normalized)
    normalized = _LEADING_USER_POSSESSIVE_RE.sub("", normalized)
    return normalized


def _normalize_memory_body_for_compare(entry: str) -> str:
    body = strip_memory_timestamp(entry).rstrip(".")
    return _normalize_memory_phrase(body)


def _entry_subject(entry: str) -> str | None:
    body = strip_memory_timestamp(entry).rstrip(".")
    for pattern in _SUBJECT_PATTERNS:
        match = pattern.match(body)
        if match:
            subject = _normalize_memory_phrase(match.group("subject"))
            if subject:
                return subject
    return None


def _looks_like_tool_memory(entry: str) -> bool:
    body = strip_memory_timestamp(entry).lower()
    if not body:
        return False

    if any(phrase in body for phrase in _TOOL_MEMORY_PHRASES):
        return True

    if any(tool_name in body for tool_name in KNOWN_TOOL_NAMES):
        return True

    if "pc doctor" in body and any(hint in body for hint in _TOOL_MEMORY_HINTS):
        return True

    if ("companion mode" in body or "assistant layer" in body) and any(hint in body for hint in _TOOL_MEMORY_HINTS):
        return True

    return False


def _is_duplicate(new_entry: str, existing_entries: list[str]) -> bool:
    new_body = _normalize_memory_body_for_compare(new_entry)
    new_subject = _entry_subject(new_entry)
    for existing in existing_entries:
        existing_body = _normalize_memory_body_for_compare(existing)
        if new_body == existing_body:
            return True
        if new_subject and new_subject == _entry_subject(existing):
            return True
    return False


def append_memory(entry: str) -> None:
    """Append a memory entry to memory.md."""
    ensure_runtime_dirs()
    if not MEMORY_PATH.exists():
        MEMORY_PATH.write_text("# Memory\n", encoding="utf-8")

    if _looks_like_tool_memory(entry):
        logger.info("Skipping tool/capability memory: %s", str(entry)[:80])
        return

    formatted_entry = format_memory_entry(entry)
    if not formatted_entry:
        return

    header_lines, existing_entries = _read_memory_file(MEMORY_PATH)
    if _is_duplicate(formatted_entry, existing_entries):
        logger.info("Skipping duplicate memory entry: %s", formatted_entry[:80])
        return

    existing_entries.append(formatted_entry)
    MEMORY_PATH.write_text(_render_memory_file(header_lines, existing_entries), encoding="utf-8")
    logger.info("Memory appended: %s", formatted_entry[:80])


_EXTRACTION_SYSTEM = (
    "You are a memory extraction assistant. Given a single user message and the companion's reply, "
    "extract facts worth storing long-term. Only extract concrete, reusable facts - "
    "user preferences, corrections, stated personal details, important events, or things "
    "explicitly asked to be remembered. Skip small talk and transient details.\n\n"
    "Never store tool inventories, command lists, capability summaries, layer descriptions, "
    "permission descriptions, or runtime/system/tool-registry details. Tool availability must "
    "be looked up live, not remembered.\n\n"
    "Return a JSON array of strings. Each string is a single concise fact (under 120 chars). "
    "Return [] if nothing is worth storing. Return only the JSON array, no other text."
)

CLOUD_BRAIN_PROVIDERS = {"openai", "anthropic", "gemini", "qwen_cloud", "openrouter"}
DEFAULT_CLOUD_EXTRACTION_MODEL = "qwen2.5:3b"
_CLOUD_MODEL_PREFIXES = ("claude-", "gpt-", "gemini-", "qwen", "o1-", "o3-", "o4-")
_OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"

_available_ollama_models_cache: set[str] | None = None
_retrieval_preload_lock = threading.Lock()
_retrieval_preload_started = False
_retrieval_preload_completed = False
_embedding_cache: dict[str, list[float]] = {}
_embedding_cache_loaded = False
_embedding_cache_dirty = False
_embedding_cache_lock = threading.RLock()
_EMBEDDING_CACHE_PATH = MEMORY_DIR / "embedding_cache.json"
_EMBEDDING_CACHE_VERSION = 1
_EMBEDDING_CACHE_MAX_ENTRIES = 2000


def _looks_like_cloud_model(model_name: str) -> bool:
    name = str(model_name or "").lower().strip()
    return any(name.startswith(prefix) for prefix in _CLOUD_MODEL_PREFIXES)


def _normalize_ollama_model_name(model_name: str) -> str:
    clean_name = str(model_name or "").strip().lower()
    if not clean_name:
        return ""
    return clean_name[:-7] if clean_name.endswith(":latest") else clean_name


def _get_available_ollama_models() -> set[str]:
    global _available_ollama_models_cache
    if _available_ollama_models_cache is not None:
        return _available_ollama_models_cache

    try:
        with urllib.request.urlopen(_OLLAMA_TAGS_URL, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.debug("Could not fetch Ollama model tags: %s", exc)
        _available_ollama_models_cache = set()
        return _available_ollama_models_cache

    names: set[str] = set()
    for item in data.get("models", []):
        if not isinstance(item, dict):
            continue
        raw_name = str(item.get("name") or "").strip()
        if raw_name:
            names.add(_normalize_ollama_model_name(raw_name))

    _available_ollama_models_cache = names
    return names


def _is_ollama_model_available(model_name: str) -> bool:
    normalized = _normalize_ollama_model_name(model_name)
    if not normalized:
        return False
    return normalized in _get_available_ollama_models()


def _embedding_cache_key(text: str, model: str) -> str:
    digest = sha256()
    digest.update(str(model or "").strip().lower().encode("utf-8", errors="replace"))
    digest.update(b"\0")
    digest.update(str(text or "").encode("utf-8", errors="replace"))
    return digest.hexdigest()


def _coerce_embedding_vector(value) -> list[float] | None:
    if not isinstance(value, list):
        return None
    vector: list[float] = []
    for item in value:
        try:
            vector.append(float(item))
        except (TypeError, ValueError):
            return None
    return vector or None


def _load_embedding_cache() -> None:
    global _embedding_cache_loaded

    with _embedding_cache_lock:
        if _embedding_cache_loaded:
            return

    if not _EMBEDDING_CACHE_PATH.exists():
        with _embedding_cache_lock:
            _embedding_cache_loaded = True
        return

    try:
        payload = json.loads(_EMBEDDING_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("Could not read embedding cache: %s", exc)
        with _embedding_cache_lock:
            _embedding_cache_loaded = True
        return

    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, dict):
        with _embedding_cache_lock:
            _embedding_cache_loaded = True
        return

    loaded: dict[str, list[float]] = {}
    for key, raw_vector in entries.items():
        if not isinstance(key, str):
            continue
        vector = _coerce_embedding_vector(raw_vector)
        if vector is not None:
            loaded[key] = vector

    with _embedding_cache_lock:
        _embedding_cache.update(loaded)
        while len(_embedding_cache) > _EMBEDDING_CACHE_MAX_ENTRIES:
            _embedding_cache.pop(next(iter(_embedding_cache)), None)
        _embedding_cache_loaded = True


def _save_embedding_cache_if_dirty() -> None:
    global _embedding_cache_dirty

    with _embedding_cache_lock:
        if not _embedding_cache_dirty:
            return
        entries = dict(_embedding_cache)

    ensure_runtime_dirs()
    _EMBEDDING_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = _EMBEDDING_CACHE_PATH.with_name(f".{_EMBEDDING_CACHE_PATH.name}.tmp")
    payload = {
        "version": _EMBEDDING_CACHE_VERSION,
        "entries": entries,
    }
    try:
        temp_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        temp_path.replace(_EMBEDDING_CACHE_PATH)
    except Exception as exc:
        logger.debug("Could not write embedding cache: %s", exc)
        return

    with _embedding_cache_lock:
        _embedding_cache_dirty = False


def _normalize_provider_name(provider: str | None) -> str:
    return str(provider or "").strip().lower() or "gemma"


def _is_ollama_backed_provider(provider: str | None) -> bool:
    return _normalize_provider_name(provider) in {"gemma", "qwen", "ollama"}


def _normalize_extraction_source(memory_cfg: dict) -> str:
    raw = str(memory_cfg.get("extraction_source") or "").strip().lower()
    if raw in {"brain", "local", "api"}:
        return raw
    legacy_mode = str(memory_cfg.get("extraction_mode") or "local").strip().lower() or "local"
    return "brain" if legacy_mode == "provider" else "local"


def _resolve_companion_brain_config(config: dict) -> dict:
    try:
        import brain

        return brain.get_layer_brain_config(config, "companion")
    except Exception:
        return dict(config.get("brain", {}) if isinstance(config, dict) else {})


def _build_local_ollama_extraction_client(model: str):
    from providers import create_client

    return create_client(
        {
            "provider": "gemma",
            "base_url": "http://localhost:11434/v1",
            "api_key": "ollama",
            "model": model,
            "temperature": 1.0,
            "max_tokens": 1024,
            "stream": False,
        },
        layer_name="memory_extraction",
    )


def _resolve_provider_credentials(provider: str, configured_base_url: str = "") -> tuple[str, str]:
    from providers.base import read_keyring_secret

    clean_provider = _normalize_provider_name(provider)
    if clean_provider == "openai":
        return "https://api.openai.com/v1", read_keyring_secret(["openai_api_key", "openai"])
    if clean_provider == "anthropic":
        return "https://api.anthropic.com/v1", read_keyring_secret(["anthropic_api_key", "anthropic"])
    if clean_provider == "gemini":
        return "https://generativelanguage.googleapis.com/v1beta", read_keyring_secret(["gemini_api_key", "gemini"])
    if clean_provider == "openrouter":
        return "https://openrouter.ai/api/v1", read_keyring_secret(["openrouter_api_key", "openrouter"])
    if clean_provider == "qwen_cloud":
        return "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", read_keyring_secret(["qwen_api_key", "qwen", "dashscope_api_key"], service_names=("OpenCompanion",))
    if clean_provider == "custom":
        return configured_base_url.strip(), read_keyring_secret(["custom_api_key", "custom"])
    return "http://localhost:11434/v1", "ollama"


def _build_provider_extraction_client(provider: str, model: str, base_url: str = ""):
    from providers import create_client

    resolved_base_url, api_key = _resolve_provider_credentials(provider, base_url)
    return create_client(
        {
            "provider": provider,
            "base_url": resolved_base_url,
            "api_url": resolved_base_url,
            "api_key": api_key,
            "model": model,
            "temperature": 1.0,
            "max_tokens": 1024,
            "stream": False,
        },
        layer_name="memory_extraction",
    )


def resolve_extraction_target(config: dict, client):
    memory_cfg = config.get("memory", {}) if isinstance(config, dict) else {}
    configured_model = str(memory_cfg.get("extraction_model") or "").strip()
    extraction_source = _normalize_extraction_source(memory_cfg)
    configured_provider = _normalize_provider_name(memory_cfg.get("extraction_provider"))
    configured_base_url = str(memory_cfg.get("extraction_base_url") or "").strip()
    companion_brain = _resolve_companion_brain_config(config)
    brain_provider = _normalize_provider_name(companion_brain.get("provider"))
    brain_model = str(companion_brain.get("model") or "").strip()

    if extraction_source == "api":
        provider = configured_provider if not _is_ollama_backed_provider(configured_provider) else brain_provider
        model = configured_model or brain_model
        if provider == "custom" and not configured_base_url:
            logger.warning("Memory extraction source=api provider=custom without base URL - falling back to brain provider.")
        elif not _is_ollama_backed_provider(provider) and model:
            return _build_provider_extraction_client(provider, model, configured_base_url), model, provider

    if extraction_source == "brain":
        if not _is_ollama_backed_provider(brain_provider):
            model = configured_model or brain_model
            if model:
                if not configured_model:
                    return client, model, brain_provider
                base_url = str(companion_brain.get("base_url") or companion_brain.get("api_url") or "")
                return _build_provider_extraction_client(brain_provider, model, base_url), model, brain_provider
        elif configured_model:
            if _is_ollama_model_available(configured_model):
                return _build_local_ollama_extraction_client(configured_model), configured_model, "gemma"
            logger.warning(
                "Memory extraction model '%s' is not available in Ollama - falling back to the companion brain model.",
                configured_model,
            )
        return client, brain_model, brain_provider

    if configured_model and _is_ollama_model_available(configured_model):
        return _build_local_ollama_extraction_client(configured_model), configured_model, "gemma"

    if configured_model:
        logger.warning(
            "Memory extraction model '%s' is not available in Ollama - falling back to a local default.",
            configured_model,
        )

    if brain_provider in CLOUD_BRAIN_PROVIDERS:
        if brain_model and not _looks_like_cloud_model(brain_model) and _is_ollama_model_available(brain_model):
            return _build_local_ollama_extraction_client(brain_model), brain_model, "gemma"
        if _is_ollama_model_available(DEFAULT_CLOUD_EXTRACTION_MODEL):
            return _build_local_ollama_extraction_client(DEFAULT_CLOUD_EXTRACTION_MODEL), DEFAULT_CLOUD_EXTRACTION_MODEL, "gemma"
        logger.warning(
            "Memory extraction: neither companion model '%s' nor fallback '%s' is available in Ollama.",
            brain_model,
            DEFAULT_CLOUD_EXTRACTION_MODEL,
        )
        return _build_local_ollama_extraction_client(DEFAULT_CLOUD_EXTRACTION_MODEL), DEFAULT_CLOUD_EXTRACTION_MODEL, "gemma"

    return client, brain_model, brain_provider


def extract_and_save_memories(user_msg: str, assistant_reply: str, client, config: dict) -> None:
    """
    Call the brain with a compact extraction prompt and append notable facts to memory.md.
    """
    if not config.get("memory", {}).get("enabled", True):
        return

    today = date.today().isoformat()
    user_content = (
        f"Date: {today}\n"
        f"User said: {user_msg}\n"
        f"Companion replied: {assistant_reply}"
    )

    try:
        extraction_client, model, provider = resolve_extraction_target(config, client)
        logger.debug("Memory extraction using provider=%s model=%s", provider, model)

        response = extraction_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _EXTRACTION_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            temperature=1.0,
            top_p=0.95,
            max_tokens=1024,
        )
        raw = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.warning("Memory extraction LLM call failed: %s", exc)
        return

    raw = strip_llm_preamble(raw).strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        raw = raw.strip()

    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Memory extraction returned invalid JSON: %s", raw[:200])
        return

    if not isinstance(entries, list):
        logger.warning("Memory extraction returned non-list: %s", type(entries))
        return

    for item in entries:
        if isinstance(item, str) and item.strip():
            append_memory(item.strip())


_OLLAMA_EMBED_URL = "http://localhost:11434/api/embeddings"


def _get_embedding(text: str, model: str) -> list[float] | None:
    """Call Ollama embeddings API. Returns None on any failure."""
    global _embedding_cache_dirty

    clean_text = _sanitize_text_for_utf8(text)
    _load_embedding_cache()
    cache_key = _embedding_cache_key(clean_text, model)
    with _embedding_cache_lock:
        cached = _embedding_cache.get(cache_key)
        if cached is not None:
            return list(cached)

    payload = json.dumps({"model": model, "prompt": clean_text}).encode("utf-8")
    req = urllib.request.Request(
        _OLLAMA_EMBED_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            vec = _coerce_embedding_vector(data.get("embedding"))
            if vec is None:
                return None
            with _embedding_cache_lock:
                _embedding_cache[cache_key] = vec
                while len(_embedding_cache) > _EMBEDDING_CACHE_MAX_ENTRIES:
                    _embedding_cache.pop(next(iter(_embedding_cache)), None)
                _embedding_cache_dirty = True
            return list(vec)
    except Exception as exc:
        logger.debug("Embedding request failed: %s", exc)
        return None


def preload_retrieval_runtime(config: dict) -> None:
    """Warm the embedding model and cache current memory-entry vectors in background."""
    global _retrieval_preload_started, _retrieval_preload_completed

    memory_cfg = config.get("memory", {}) if isinstance(config, dict) else {}
    if not memory_cfg.get("enabled", True):
        return
    if memory_cfg.get("embedding_enabled", True) is False:
        return

    with _retrieval_preload_lock:
        if _retrieval_preload_started:
            return
        _retrieval_preload_started = True
        _retrieval_preload_completed = False

    embedding_model = str(memory_cfg.get("embedding_model", "nomic-embed-text") or "nomic-embed-text").strip()
    try:
        _get_embedding("memory warmup", embedding_model)
        for _, entry_text in _collect_all_entries():
            _get_embedding(strip_memory_timestamp(entry_text), embedding_model)
        _retrieval_preload_completed = True
    except Exception:
        logger.exception("Memory retrieval preload failed")
    finally:
        _save_embedding_cache_if_dirty()


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _collect_all_entries() -> list[tuple[str, str]]:
    """
    Return a flat list of (topic_header, entry_text) for each bullet entry in memory.md.
    """
    if not MEMORY_PATH.exists():
        return []

    entries: list[tuple[str, str]] = []
    header = "Memory"
    for line in MEMORY_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            header = line.lstrip("# ").strip() or "Memory"
        elif line.startswith("- "):
            entry = line[2:].strip()
            if entry:
                entries.append((header, entry))
    return entries


def retrieve_relevant_memories(user_message: str, config: dict, top_k: int = 5) -> str:
    """
    Embed user_message and each memory entry via nomic-embed-text, return the
    top_k most relevant entries formatted for system prompt injection.

    Falls back to load_all_memories() if Ollama embeddings are unavailable.
    """
    if not MEMORY_PATH.exists():
        return ""

    embedding_model = config.get("memory", {}).get("embedding_model", "nomic-embed-text")

    entries = _collect_all_entries()
    if not entries:
        return ""

    query_vec = _get_embedding(user_message, embedding_model)
    if query_vec is None:
        logger.debug("Embeddings unavailable, falling back to full memory load.")
        return load_all_memories()

    scored: list[tuple[float, str, str]] = []
    for header, entry_text in entries:
        entry_vec = _get_embedding(strip_memory_timestamp(entry_text), embedding_model)
        if entry_vec is None:
            continue
        scored.append((_cosine_similarity(query_vec, entry_vec), header, entry_text))

    if not scored:
        _save_embedding_cache_if_dirty()
        return load_all_memories()

    _save_embedding_cache_if_dirty()
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]

    lines = ["=== Relevant Memories ===\n"]
    for _, header, entry_text in top:
        lines.append(f"[{header}] {entry_text}")

    return "\n".join(lines)


MAX_MEMORY_TOKENS = 1500
DEFAULT_LAYER_MEMORY_BUDGETS = {
    "companion": {"percent": 0.15, "min_tokens": 2048, "max_tokens": 12000},
    "assistant": {"percent": 0.20, "min_tokens": 2048, "max_tokens": 16000},
}


def resolve_layer_memory_budget(
    config: dict,
    layer_name: str,
    remaining_prompt_budget_tokens: int | None = None,
) -> int:
    memory_cfg = config.get("memory", {}) if isinstance(config, dict) else {}
    layer_budgets = memory_cfg.get("layer_budgets", {}) if isinstance(memory_cfg, dict) else {}
    canonical = "assistant" if layer_name in {"assistant", "assistant_low", "assistant_high", "pc_doctor", "pcdoctor"} else layer_name
    defaults = DEFAULT_LAYER_MEMORY_BUDGETS.get(layer_name) or DEFAULT_LAYER_MEMORY_BUDGETS.get(canonical) or {
        "percent": 0.15,
        "min_tokens": 1024,
        "max_tokens": 12000,
    }
    configured = layer_budgets.get(layer_name) or layer_budgets.get(canonical) or {}

    try:
        percent = float(configured.get("percent", defaults["percent"]))
    except (TypeError, ValueError):
        percent = defaults["percent"]
    try:
        min_tokens = int(configured.get("min_tokens", defaults["min_tokens"]))
    except (TypeError, ValueError):
        min_tokens = defaults["min_tokens"]
    try:
        max_tokens = int(configured.get("max_tokens", defaults["max_tokens"]))
    except (TypeError, ValueError):
        max_tokens = defaults["max_tokens"]

    if max_tokens < min_tokens:
        max_tokens = min_tokens

    if remaining_prompt_budget_tokens is None:
        return max_tokens

    computed = int(max(0, remaining_prompt_budget_tokens) * max(0.0, percent))
    if computed <= 0:
        return min_tokens
    return max(min_tokens, min(max_tokens, computed))


def prune_memories_to_limit(memory_text: str, max_tokens: int = MAX_MEMORY_TOKENS) -> str:
    """
    Hard-truncate memory block to fit within token budget (1 token ~= 4 chars).
    Kept for backward compatibility.
    """
    max_chars = max_tokens * 4
    if len(memory_text) <= max_chars:
        return memory_text
    truncated = memory_text[:max_chars]
    last_newline = truncated.rfind("\n")
    if last_newline > 0:
        truncated = truncated[:last_newline]
    return truncated + "\n\n[Memory truncated to fit context window]"


def prune_memories_to_budget(
    memory_text: str,
    budget_tokens: int,
) -> str:
    """
    Prune memory block to fit within token budget.
    Removes oldest entries first. Falls back to hard truncation if needed.
    """
    from context_manager import count_tokens

    if count_tokens(memory_text) <= budget_tokens:
        return memory_text

    lines = memory_text.split("\n")
    while count_tokens("\n".join(lines)) > budget_tokens and len(lines) > 1:
        lines.pop(0)

    result = "\n".join(lines).strip()
    if not result:
        return "[Memories pruned - conversation too long]"
    return result


def retrieve_topic_bundle_memories(
    user_message: str,
    config: dict,
    budget_tokens: int,
) -> str:
    """
    Retrieve a larger memory bundle by relevance until the token budget is filled.
    Falls back to full-memory pruning when embeddings are unavailable.
    """
    from context_manager import count_tokens

    if not MEMORY_PATH.exists():
        return ""

    entries = _collect_all_entries()
    if not entries:
        return ""

    embedding_model = config.get("memory", {}).get("embedding_model", "nomic-embed-text")
    query_vec = _get_embedding(user_message, embedding_model)
    if query_vec is None:
        return prune_memories_to_budget(load_all_memories(), budget_tokens)

    scored: list[tuple[float, str, str]] = []
    for header, entry_text in entries:
        entry_vec = _get_embedding(strip_memory_timestamp(entry_text), embedding_model)
        if entry_vec is None:
            continue
        scored.append((_cosine_similarity(query_vec, entry_vec), header, entry_text))

    if not scored:
        _save_embedding_cache_if_dirty()
        return prune_memories_to_budget(load_all_memories(), budget_tokens)

    _save_embedding_cache_if_dirty()
    scored.sort(key=lambda item: item[0], reverse=True)
    grouped: dict[str, list[str]] = defaultdict(list)
    for _score, header, entry_text in scored:
        grouped[header or "Memory"].append(entry_text)

    lines = ["=== Relevant Memories ===", ""]
    current_tokens = count_tokens("\n".join(lines))
    added_any = False
    for header, grouped_entries in sorted(
        grouped.items(),
        key=lambda item: max(
            (
                score
                for score, grouped_header, grouped_entry in scored
                if (grouped_header or "Memory") == item[0] and grouped_entry in item[1]
            ),
            default=0.0,
        ),
        reverse=True,
    ):
        section_lines = [f"[{header}]"]
        for entry in grouped_entries:
            candidate = section_lines + [f"- {entry}"]
            candidate_tokens = count_tokens("\n".join(lines + candidate))
            if candidate_tokens > budget_tokens and added_any:
                continue
            if candidate_tokens > budget_tokens:
                break
            section_lines.append(f"- {entry}")
            added_any = True
        if len(section_lines) > 1:
            section_text = "\n".join(section_lines)
            next_tokens = count_tokens("\n".join(lines + [section_text, ""]))
            if next_tokens > budget_tokens and added_any:
                continue
            lines.extend([section_text, ""])
            current_tokens = next_tokens
        if current_tokens >= budget_tokens and added_any:
            break

    if not added_any:
        return "[Memories pruned - conversation too long]"

    return "\n".join(lines).strip()


def build_system_prompt(
    soul: str,
    config: dict,
    user_message: str = "",
    memory_budget_tokens: int | None = None,
    layer_name: str = "companion",
) -> str:
    """
    Build the full system prompt by combining the soul and long-term memories.
    """
    parts = [soul]

    memory_block = get_memory_prompt_block(
        config,
        user_message=user_message,
        memory_budget_tokens=memory_budget_tokens,
        layer_name=layer_name,
    )
    if memory_block:
        parts.append(memory_block)

    return "\n\n".join(parts)


def get_memory_prompt_block(
    config: dict,
    user_message: str = "",
    memory_budget_tokens: int | None = None,
    layer_name: str = "companion",
) -> str:
    """Return only the memory tier block for prompt assembly."""
    if config.get("memory", {}).get("enabled", True):
        budget = (
            memory_budget_tokens
            if memory_budget_tokens is not None
            else resolve_layer_memory_budget(config, layer_name, None)
        )
        if user_message and _retrieval_preload_completed:
            memories = retrieve_topic_bundle_memories(user_message, config, budget)
        else:
            memories = prune_memories_to_budget(load_all_memories(), budget)
        if memories:
            return prune_memories_to_budget(memories, budget)

    return ""
