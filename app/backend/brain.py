"""
Brain Provider Abstraction
==========================
Wraps all LLM providers (Ollama, OpenAI, Anthropic, Gemini, custom) behind one interface.
OpenAI-compatible providers share one path; Anthropic uses its native Messages API.

wrapper.py calls brain.py and never talks to provider APIs directly.

This module also owns layer-aware config resolution:
- shared brain defaults live in config.json -> brain
- per-layer overrides live in config.json -> layers.<name>.brain
- each layer can load its own soul prompt file

Debug log map when OPENCOMPANION_DEBUG=1:
- PROMPT_SENT: written at the Ollama HTTP edge with the full request body after
  system prompt assembly, history trimming, tool result injection, and option resolution.
- OLLAMA_RAW: written from the Ollama provider with the raw response body/chunks
  before they are turned into BrainMessage objects.
- TOOL_OUTPUT: written when wrapper.py injects a tool result into layer context.
- ERROR: written when brain.py sees an exception escaping the provider call,
  including the full traceback.
"""

from __future__ import annotations

import json
import logging
import site
import sys
import urllib.request
import urllib.error
from copy import deepcopy
from pathlib import Path

_brain_logger = logging.getLogger(__name__)

user_site = site.getusersitepackages()
if user_site and user_site not in sys.path:
    sys.path.append(user_site)

from runtime_paths import (
    CONFIG_PATH,
    KEYCHAIN_SERVICE,
    LOCAL_CONFIG_PATH,
    PROJECT_ROOT,
    SOUL_ACTIVE_DIR,
    SOUL_DEFAULTS_DIR,
    SOUL_DIR,
    ensure_runtime_dirs,
)
import ctx_advisor
from model_family import resolve_model_family
from anthropic_models import get_anthropic_model_capabilities
from chatgpt_oauth_models import (
    get_chatgpt_oauth_model_capabilities,
    normalize_chatgpt_oauth_model_name,
)
from openai_models import get_openai_model_capabilities
from gemini_models import get_gemini_model_capabilities
from openrouter_models import get_openrouter_model_capabilities
from qwen_models import get_qwen_model_capabilities
from debug_log import debug_log_error
from providers import create_client as create_provider_client, normalize_provider_name
from providers.base import BrainClientShim, BrainMessage, BrainProvider, is_ollama_backed_provider, split_system_messages
from tool_schema import validate_openai_tool_schemas


def get_api_key(account: str) -> str:
    """Backward-compatible keychain lookup helper."""
    from providers.base import read_keyring_secret

    return read_keyring_secret([account], service_names=("open-companion", KEYCHAIN_SERVICE, "OpenCompanion"))

DEFAULT_LAYER_CONFIG = {
    "companion": {
        "enabled": True,
        "persistent": True,
        "permission_profile": "companion",
        "display_name": "",
        "identity_path": "companion/soul/active/soul_companion.md",
        "max_tool_iterations": 20,
        "brain": {},
    },
    "assistant": {
        "enabled": True,
        "persistent": False,
        "permission_profile": "assistant",
        "display_name": "Assistant",
        "identity_path": "companion/soul/active/soul_assistant.md",
        "max_tool_iterations": 100,
        "brain": {},
    },
}

CANONICAL_LAYERS = ("companion", "assistant")
LAYER_ALIASES = {
    "companion": ("companion",),
    "assistant": ("assistant", "assistant_low", "assistant_high", "pc_doctor", "pcdoctor"),
}

DEFAULT_LAYER_PROMPTS = {
    "companion": (
        "You are {{NAME}}, the user's local AI companion. Stay conversational, grounded, "
        "and emotionally coherent. Handle direct conversation yourself, use only the "
        "tools you are given, and route actionable work to the assistant layer quickly."
    ),
    "assistant": (
        "You are the companion's on-demand assistant layer. Complete concrete tasks "
        "directly using tools. Do the work first, then summarize. Return concise results."
    ),
}

DEFAULT_RUNTIME_CONFIG = {
    "version": "1.4.0",
    "vault": {
        "path": "./companion/vault",
        "enabled": True,
    },
    "companion": {
        "name": "Nova",
        "user_name": "User",
        "system_prompt_override": "",
        "layer_visibility": {
            "show_assistant_label": True,
        },
        "soul": {
            "name": "Nova",
            "pronouns": "she/her",
            "identity": "Warm, curious, and slightly playful.",
            "backstory": "",
            "relationship": "Trusted companion.",
            "user_name": "User",
            "user_context": "",
        },
        "pronouns": "she/her",
    },
    "brain": {
        "provider": "gemma",
        "base_url": "http://localhost:11434/v1",
        "api_url": "http://localhost:11434/v1",
        "model": "",
        "api_key": "",
        "temperature": 0.8,
        "context_window": "auto",
        "max_tokens": 1024,
        "stream": True,
        "fallback_cpu": False,
        "layers": {
            "companion": {"provider": "", "model": "", "temperature": "", "max_tokens": "", "reasoning_effort": ""},
            "assistant": {"provider": "", "model": "", "temperature": "", "max_tokens": "", "reasoning_effort": ""},
        },
    },
    "providers": {
        "custom": {
            "base_url": "",
            "model": "",
        },
    },
    "memory": {
        "enabled": True,
        "write_back_enabled": True,
        "extraction_source": "local",
        "extraction_provider": "",
        "extraction_base_url": "",
        "extraction_model": "gemma4:e4b",
        "extraction_mode": "local",
        "embedding_enabled": True,
        "embedding_model": "nomic-embed-text",
        "max_context_memories": 5,
        "layer_budgets": {
            "companion": {"percent": 0.15, "min_tokens": 2048, "max_tokens": 12000},
            "assistant": {"percent": 0.20, "min_tokens": 2048, "max_tokens": 16000},
        },
        "top_k": 5,
        "dream_enabled": True,
        "dream_schedule": "session_start",
        "max_entries": 200,
    },
    "context": {
        "session_summaries": {
            "enabled": True,
            "max_recent": 6,
            "max_injected": 2,
            "compact_after_messages": 40,
            "compact_after_tokens": 12000,
            "budget_tokens": 1500,
            "retain_recent_messages": 6,
        },
        "skills": {
            "index_enabled": True,
            "max_full_skills": 3,
            "index_budget_tokens": 700,
            "full_budget_tokens": 2200,
        },
    },
    "heartbeat": {
        "enabled": False,
        "interval": 1800,
        "only_when_idle": False,
        "idle_threshold_minutes": 5,
    },
    "voice": {
        "tts_enabled": True,
        "tts_provider": "kokoro",
        "kokoro_voice": "af_nova",
        "kokoro_model_path": "",
        "kokoro_voices_path": "",
        "tts_speed": 1.0,
        "volume": 1.0,
        "stt_enabled": True,
        "stt_provider": "whisper",
        "stt_model": "base.en",
        "whisper_model": "base.en",
        "language": "en-US",
        "push_to_talk_key": "",
        "auto_send_on_silence": True,
    },
    "ui": {
        "always_on_top": True,
        "transparent": True,
        "overlay_scale": 50,
        "window_width": 360,
        "window_height": 516,
        "selected_target_layer": "companion",
        "avatar_image_path": "",
        "avatar_model_path": "",
        "layer_visibility": True,
        "idle_timeout_seconds": 5,
        "show_animation_label": True,
        "layer_label_visibility": {
            "show_assistant_label": True,
        },
        "theme": {
            "mode": "dark",
            "accent_rgb": [139, 92, 246],
        },
    },
    "onboarding_complete": False,
    "onboarding_reset_memory_on_next_launch": False,
    "onboarding": {
        "completed": False,
        "modelsDownloaded": [],
    },
    "updater": {
        "check_on_startup": True,
        "last_check_ts": 0,
        "dismissed_version": "",
    },
    "layers": deepcopy(DEFAULT_LAYER_CONFIG),
    "tools": {
        "overrides": {
            "companion": {},
            "assistant": {},
        },
        "custom": [],
    },
}

PLACEHOLDER_KEYS = (
    ("{{NAME}}", "name", "Nova"),
    ("{{companion_name}}", "name", "Nova"),
    ("{{SOUL_NAME}}", "name", "Nova"),
    ("{{AGE}}", "age", ""),
    ("{{BACKSTORY}}", "backstory", ""),
    ("{{SOUL_BACKSTORY}}", "backstory", ""),
    ("{{PERSONALITY}}", "identity", ""),
    ("{{SOUL_IDENTITY}}", "identity", ""),
    ("{{COMMUNICATION_STYLE}}", "communication_style", ""),
    ("{{RELATIONSHIP}}", "relationship", ""),
    ("{{SOUL_RELATIONSHIP}}", "relationship", ""),
    ("{{USER_NAME}}", "user_name", "User"),
    ("{{SOUL_USER_NAME}}", "user_name", "User"),
    ("{{USER_DESCRIPTION}}", "user_context", ""),
    ("{{SOUL_USER_CONTEXT}}", "user_context", ""),
)

# Pronoun sets for _render_placeholders
_PRONOUN_SETS: dict[str, tuple[str, str, str]] = {
    "she/her":   ("she",  "her",  "her"),
    "he/him":    ("he",   "him",  "his"),
    "they/them": ("they", "them", "their"),
    "it/its":    ("it",   "it",   "its"),
}


def _resolve_pronouns(config: dict) -> tuple[str, str, str]:
    companion = config.get("companion", {}) if isinstance(config, dict) else {}
    raw = str(companion.get("pronouns", "she/her") or "she/her").strip().lower()
    if raw in _PRONOUN_SETS:
        return _PRONOUN_SETS[raw]
    parts = [p.strip() for p in raw.split("/")]
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], parts[1], parts[1] + "s"
    return "they", "them", "their"


def _deep_merge(base: dict, override: dict) -> dict:
    merged = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _migrate_provider_id(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    return normalized


def _get_nested(config: dict, path: tuple[str, ...], default: str = "") -> str:
    current = config
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return current if current is not None else default


def _get_companion_value(config: dict, key: str, default: str = "") -> str:
    companion = config.get("companion", {})
    soul = companion.get("soul", {}) if isinstance(companion, dict) else {}

    if isinstance(soul, dict):
        if key == "identity":
            identity = str(soul.get("identity", "") or "").strip()
            if identity:
                return identity
        if key == "user_context":
            user_context = str(soul.get("user_context", "") or soul.get("user_description", "") or "").strip()
            if user_context:
                return user_context
        soul_value = soul.get(key)
        if soul_value not in (None, ""):
            return soul_value

    if isinstance(companion, dict):
        companion_value = companion.get(key)
        if companion_value is not None:
            return companion_value

    return default


def _render_placeholders(text: str, config: dict, layer_name: str) -> str:
    rendered = text
    for placeholder, key, default in PLACEHOLDER_KEYS:
        rendered = rendered.replace(placeholder, str(_get_companion_value(config, key, default)))

    pronoun_subject, pronoun_object, pronoun_possessive = _resolve_pronouns(config)
    rendered = rendered.replace("{{pronoun_subject}}", pronoun_subject)
    rendered = rendered.replace("{{pronoun_object}}", pronoun_object)
    rendered = rendered.replace("{{pronoun_possessive}}", pronoun_possessive)

    layer_config = get_layer_config(config, layer_name)
    rendered = rendered.replace("{{LAYER_NAME}}", layer_name)
    rendered = rendered.replace("{{LAYER_DISPLAY_NAME}}", get_layer_display_name(config, layer_name))
    rendered = rendered.replace(
        "{{PERMISSION_PROFILE}}",
        str(layer_config.get("permission_profile", layer_name)),
    )
    return rendered


def _resolve_project_path(path_str: str | None) -> Path | None:
    """Resolve a project-relative path.

    For companion/soul/active/ paths, fall back to companion/soul/defaults/
    if the active file does not exist yet. Legacy soul/active/ paths are still
    accepted for backward compatibility.
    """
    if not path_str:
        return None
    candidate = Path(path_str)
    if candidate.is_absolute():
        return candidate

    # companion/soul/active lives under the writable profile, not the readonly
    # project root. Legacy soul/active paths are handled the same way.
    for prefix in (("companion", "soul", "active"), ("soul", "active")):
        if tuple(candidate.parts[: len(prefix)]) == prefix:
            rel = Path(*candidate.parts[len(prefix) :])
            resolved = SOUL_ACTIVE_DIR / rel
            if resolved.exists():
                return resolved
            fallback = SOUL_DEFAULTS_DIR / rel
            if fallback.exists():
                return fallback
            return resolved

    resolved = PROJECT_ROOT / candidate
    return resolved


def load_config(config_path: str | None = None) -> dict:
    """Load config.json from project root and merge config.local.json overrides."""
    if config_path is None:
        config_path = CONFIG_PATH
    ensure_runtime_dirs()
    config_path = Path(config_path)
    config = deepcopy(DEFAULT_RUNTIME_CONFIG)
    with open(config_path, "r", encoding="utf-8") as f:
        config = _deep_merge(config, json.load(f))

    local_config_path = LOCAL_CONFIG_PATH if config_path == CONFIG_PATH else config_path.with_name("config.local.json")
    if local_config_path.exists():
        with open(local_config_path, "r", encoding="utf-8") as f:
            local_config = json.load(f)
        config = _deep_merge(config, local_config)

    config.setdefault("brain", {})
    config["brain"]["provider"] = _migrate_provider_id(config["brain"].get("provider")) or DEFAULT_RUNTIME_CONFIG["brain"]["provider"]
    config["brain"].setdefault("context_window", DEFAULT_RUNTIME_CONFIG["brain"]["context_window"])
    layers = config["brain"].get("layers", {}) if isinstance(config["brain"], dict) else {}
    if not isinstance(layers, dict):
        layers = {}
    assistant_brain = layers.get("assistant") or layers.get("assistant_low") or layers.get("assistant_high") or layers.get("pc_doctor") or {}
    config["brain"]["layers"] = {
        "companion": _deep_merge(DEFAULT_RUNTIME_CONFIG["brain"]["layers"]["companion"], layers.get("companion", {}) if isinstance(layers.get("companion", {}), dict) else {}),
        "assistant": _deep_merge(DEFAULT_RUNTIME_CONFIG["brain"]["layers"]["assistant"], assistant_brain if isinstance(assistant_brain, dict) else {}),
    }
    for layer_brain in config["brain"]["layers"].values():
        if isinstance(layer_brain, dict):
            layer_brain["provider"] = _migrate_provider_id(layer_brain.get("provider")) if layer_brain.get("provider") else ""

    memory_cfg = config.get("memory", {})
    if not isinstance(memory_cfg, dict):
        memory_cfg = {}
        config["memory"] = memory_cfg
    budgets = memory_cfg.get("layer_budgets", {}) if isinstance(memory_cfg.get("layer_budgets", {}), dict) else {}
    assistant_budget = budgets.get("assistant") or budgets.get("assistant_low") or budgets.get("assistant_high") or budgets.get("pc_doctor") or {}
    memory_cfg["layer_budgets"] = {
        "companion": _deep_merge(DEFAULT_RUNTIME_CONFIG["memory"]["layer_budgets"]["companion"], budgets.get("companion", {}) if isinstance(budgets.get("companion", {}), dict) else {}),
        "assistant": _deep_merge(DEFAULT_RUNTIME_CONFIG["memory"]["layer_budgets"]["assistant"], assistant_budget if isinstance(assistant_budget, dict) else {}),
    }

    context_cfg = config.get("context", {})
    if not isinstance(context_cfg, dict):
        context_cfg = {}
        config["context"] = context_cfg
    session_summary_cfg = context_cfg.get("session_summaries", {})
    if not isinstance(session_summary_cfg, dict):
        session_summary_cfg = {}
    skill_cfg = context_cfg.get("skills", {})
    if not isinstance(skill_cfg, dict):
        skill_cfg = {}
    config["context"] = {
        "session_summaries": _deep_merge(DEFAULT_RUNTIME_CONFIG["context"]["session_summaries"], session_summary_cfg),
        "skills": _deep_merge(DEFAULT_RUNTIME_CONFIG["context"]["skills"], skill_cfg),
    }

    ui_cfg = config.get("ui", {})
    if not isinstance(ui_cfg, dict):
        ui_cfg = {}
        config["ui"] = ui_cfg
    selected = str(ui_cfg.get("selected_target_layer") or DEFAULT_RUNTIME_CONFIG["ui"]["selected_target_layer"]).strip()
    ui_cfg["selected_target_layer"] = "companion" if selected == "companion" else "assistant"

    layers_cfg = config.get("layers", {})
    if not isinstance(layers_cfg, dict):
        layers_cfg = {}
    assistant_layer = layers_cfg.get("assistant") or layers_cfg.get("assistant_low") or layers_cfg.get("assistant_high") or layers_cfg.get("pc_doctor") or {}
    config["layers"] = {
        "companion": _deep_merge(DEFAULT_RUNTIME_CONFIG["layers"]["companion"], layers_cfg.get("companion", {}) if isinstance(layers_cfg.get("companion", {}), dict) else {}),
        "assistant": _deep_merge(DEFAULT_RUNTIME_CONFIG["layers"]["assistant"], assistant_layer if isinstance(assistant_layer, dict) else {}),
    }

    tools_cfg = config.get("tools", {})
    if not isinstance(tools_cfg, dict):
        tools_cfg = {}
        config["tools"] = tools_cfg
    overrides = tools_cfg.get("overrides", {}) if isinstance(tools_cfg.get("overrides", {}), dict) else {}
    assistant_override = overrides.get("assistant") or overrides.get("assistant_low") or overrides.get("assistant_high") or overrides.get("pc_doctor") or {}
    tools_cfg["overrides"] = {
        "companion": dict(overrides.get("companion", {}) if isinstance(overrides.get("companion", {}), dict) else {}),
        "assistant": dict(assistant_override if isinstance(assistant_override, dict) else {}),
    }
    if not isinstance(tools_cfg.get("custom"), list):
        tools_cfg["custom"] = []

    providers_cfg = config.get("providers", {})
    if not isinstance(providers_cfg, dict):
        providers_cfg = {}
    custom_provider_cfg = providers_cfg.get("custom", {}) if isinstance(providers_cfg.get("custom", {}), dict) else {}
    config["providers"] = {
        "custom": _deep_merge(DEFAULT_RUNTIME_CONFIG["providers"]["custom"], custom_provider_cfg),
    }

    return config


def normalize_layer_name(layer_name: str) -> str:
    """Normalize legacy worker aliases to the two-layer runtime."""
    raw = str(layer_name or "").strip()
    if raw in {"assistant", "assistant_low", "assistant_high", "pc_doctor", "pcdoctor"}:
        return "assistant"
    return raw or "companion"


def _layer_lookup_keys(layer_name: str) -> tuple[str, ...]:
    canonical = normalize_layer_name(layer_name)
    return LAYER_ALIASES.get(canonical, (canonical,))


def get_layer_config(config: dict, layer_name: str) -> dict:
    """Return normalized config for a runtime layer."""
    canonical = normalize_layer_name(layer_name)
    defaults = DEFAULT_LAYER_CONFIG.get(canonical, {
        "enabled": True,
        "persistent": False,
        "permission_profile": canonical,
        "display_name": canonical.replace("_", " ").title(),
        "identity_path": "",
        "brain": {},
    })
    layers_config = config.get("layers", {}) if isinstance(config, dict) else {}
    layer_cfg = {}
    if isinstance(layers_config, dict):
        for key in _layer_lookup_keys(canonical):
            value = layers_config.get(key, {})
            if isinstance(value, dict) and value:
                layer_cfg = value
                break
    return _deep_merge(defaults, layer_cfg)


_LAYER_DISPLAY_FALLBACKS = {
    "assistant": "Assistant",
}


def get_layer_display_name(config: dict, layer_name: str) -> str:
    canonical = normalize_layer_name(layer_name)
    layer_config = get_layer_config(config, canonical)
    configured_name = str(layer_config.get("display_name") or "").strip()
    if configured_name:
        return configured_name
    if canonical == "companion":
        return str(_get_companion_value(config, "name", "Companion"))
    if canonical in _LAYER_DISPLAY_FALLBACKS:
        return _LAYER_DISPLAY_FALLBACKS[canonical]
    return canonical.replace("_", " ").title()


def get_public_layer_config(config: dict) -> dict:
    """Return non-secret layer metadata that the UI can safely inspect."""
    public = {}
    for layer_name in CANONICAL_LAYERS:
        layer_config = get_layer_config(config, layer_name)
        public[layer_name] = {
            "enabled": bool(layer_config.get("enabled", True)),
            "display_name": get_layer_display_name(config, layer_name),
            "persistent": bool(layer_config.get("persistent", False)),
            "permission_profile": layer_config.get("permission_profile", layer_name),
            "explicit_summon_only": bool(layer_config.get("explicit_summon_only", False)),
        }
    return public


def resolve_brain_config(config: dict, layer_name: str = "companion") -> dict:
    """Merge shared brain defaults with per-layer overrides."""
    shared = config.get("brain", {})
    layer_overrides = _get_brain_layer_overrides(config, layer_name)
    return _deep_merge(shared, layer_overrides)


def _get_brain_layer_overrides(config: dict, layer_name: str) -> dict:
    canonical = normalize_layer_name(layer_name)
    brain = config.get("brain", {}) if isinstance(config, dict) else {}
    layer_overrides: dict = {}

    if isinstance(brain, dict):
        layers = brain.get("layers", {})
        if isinstance(layers, dict):
            raw = {}
            for key in _layer_lookup_keys(canonical):
                value = layers.get(key, {}) or {}
                if isinstance(value, dict) and value:
                    raw = value
                    break
            # Strip empty-string values â€” "" means "inherit from global", not "override with empty"
            non_empty = {k: v for k, v in raw.items() if v != ""}
            layer_overrides = _deep_merge(layer_overrides, non_empty)

    legacy_layer_brain = get_layer_config(config, canonical).get("brain", {})
    if isinstance(legacy_layer_brain, dict):
        layer_overrides = _deep_merge(layer_overrides, legacy_layer_brain)

    return layer_overrides


def _default_model_for_provider(provider: str) -> str:
    return ""


def _first_available_provider_model(provider: str, base_url: str, api_key: str, layer_name: str) -> str:
    cache_key = (str(provider or "").strip(), str(base_url or "").strip(), str(api_key or "").strip())
    if cache_key in _resolved_model_cache:
        return _resolved_model_cache[cache_key]

    try:
        client = create_provider_client(
            {
                "provider": provider,
                "base_url": base_url,
                "api_url": base_url,
                "api_key": api_key,
            },
            layer_name=layer_name,
        )
        models = [str(name or "").strip() for name in client.list_models() if str(name or "").strip()]
    except Exception:
        models = []

    if is_ollama_backed_provider(provider) or provider == "custom":
        models = [name for name in models if not _is_embedding_model(name)]

    resolved = models[0] if models else ""
    _resolved_model_cache[cache_key] = resolved
    return resolved


def _resolve_provider_credentials(provider: str, brain: dict) -> tuple[str, str]:
    provider = normalize_provider_name(provider)
    if is_ollama_backed_provider(provider):
        return "http://localhost:11434/v1", "ollama"
    if provider == "qwen_cloud":
        from providers.base import read_keyring_secret

        api_key = str(
            brain.get("api_key")
            or brain.get("qwen_api_key")
            or read_keyring_secret(["qwen_api_key", "qwen", "dashscope_api_key"], service_names=("OpenCompanion",))
            or ""
        ).strip()
        return "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", api_key
    if provider == "openai":
        from providers.base import read_keyring_secret

        api_key = str(
            brain.get("api_key")
            or brain.get("openai_api_key")
            or read_keyring_secret(["openai_api_key", "openai"])
            or ""
        ).strip()
        return "https://api.openai.com/v1", api_key
    if provider == "anthropic":
        from providers.base import read_keyring_secret

        api_key = str(
            brain.get("api_key")
            or brain.get("anthropic_api_key")
            or read_keyring_secret(["anthropic_api_key", "anthropic"])
            or ""
        ).strip()
        return "https://api.anthropic.com/v1", api_key
    if provider == "gemini":
        from providers.base import read_keyring_secret

        api_key = str(
            brain.get("api_key")
            or brain.get("gemini_api_key")
            or read_keyring_secret(["gemini_api_key", "gemini"])
            or ""
        ).strip()
        return "https://generativelanguage.googleapis.com/v1beta", api_key
    if provider == "openrouter":
        from providers.base import read_keyring_secret

        api_key = str(
            brain.get("api_key")
            or brain.get("openrouter_api_key")
            or read_keyring_secret(["openrouter_api_key", "openrouter"])
            or ""
        ).strip()
        return "https://openrouter.ai/api/v1", api_key
    if provider == "custom":
        from providers.base import read_keyring_secret

        base_url = str(
            brain.get("base_url")
            or brain.get("api_url")
            or ""
        ).strip()
        api_key = str(
            brain.get("custom_api_key")
            or brain.get("api_key")
            or read_keyring_secret(["custom_api_key", "custom"])
            or ""
        ).strip()
        return base_url, api_key
    if provider == "chatgpt_oauth":
        # Tokens live in keyring under fixed keys — no base_url / api_key needed here.
        return "https://chatgpt.com/backend-api/codex/responses", ""
    base_url = str(brain.get("base_url") or brain.get("api_url") or "http://localhost:11434/v1").strip()
    return base_url, "ollama"


_OLLAMA_SHOW_URL = "http://localhost:11434/api/show"
_OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"
_OLLAMA_PS_URL = "http://localhost:11434/api/ps"
_EMBEDDING_HINTS = ("embed", "nomic", "mxbai", "snowflake", "arctic", "bge-", "granite-embedding", "all-minilm")
_model_max_context_cache: dict[str, int] = {}
_resolved_model_cache: dict[tuple[str, str, str], str] = {}


def _is_embedding_model(name: str) -> bool:
    low = name.lower()
    return any(hint in low for hint in _EMBEDDING_HINTS)


def _first_available_ollama_model(base_url: str | None = None) -> str:
    """Query Ollama /api/tags and return the first non-embedding model name, or ''."""
    tags_url = base_url.rstrip("/").rsplit("/v1", 1)[0] + "/api/tags" if base_url else _OLLAMA_TAGS_URL
    try:
        try:
            import requests as _requests
            data = _requests.get(tags_url, timeout=3).json()
        except ImportError:
            import urllib.request
            with urllib.request.urlopen(tags_url, timeout=3) as r:
                data = json.loads(r.read())
        for entry in data.get("models", []):
            name = str(entry.get("name") or "").strip()
            if name and not _is_embedding_model(name):
                return name
    except Exception:
        pass
    return ""
_DEFAULT_CONTEXT_WINDOW = 8192
_GEMMA_DEFAULT_TEMPERATURE = 1.0
_GEMMA_DEFAULT_TOP_P = 0.95
_GEMMA_DEFAULT_TOP_K = 64
CONTEXT_LENGTH_KEYS = [
    "gemma4.context_length",
    "gemma2.context_length",
    "gemma.context_length",
    "llama.context_length",
    "qwen2.context_length",
    "context_length",
]


def get_model_context_length(info: dict) -> int:
    model_info = info.get("model_info", {}) or {}
    parameters = info.get("parameters", {}) or {}
    details = info.get("details", {}) or {}
    candidates = []
    if isinstance(model_info, dict):
        for key in CONTEXT_LENGTH_KEYS:
            candidates.append(model_info.get(key))
    if isinstance(parameters, dict):
        candidates.append(parameters.get("num_ctx"))
    if isinstance(details, dict):
        candidates.append(details.get("context_length"))
    for candidate in candidates:
        try:
            value = int(candidate)
            if value > 0:
                return value
        except (TypeError, ValueError):
            continue
    return _DEFAULT_CONTEXT_WINDOW


def get_model_max_context(model_name: str) -> int:
    """
    Query Ollama for model's maximum context window. Cached per model name.
    Falls back to _DEFAULT_CONTEXT_WINDOW on any error.
    """
    if model_name in _model_max_context_cache:
        return _model_max_context_cache[model_name]
    result = _DEFAULT_CONTEXT_WINDOW
    try:
        import requests as _requests
        resp = _requests.post(
            _OLLAMA_SHOW_URL,
            json={"name": model_name},
            timeout=5,
        )
        info = resp.json()
        ctx = get_model_context_length(info)
        result = int(ctx)
    except Exception as exc:
        try:
            # requests may not be installed; fall back to stdlib
            payload = json.dumps({"name": model_name}).encode("utf-8")
            req = urllib.request.Request(
                _OLLAMA_SHOW_URL,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                info = json.loads(resp.read().decode("utf-8"))
            ctx = get_model_context_length(info)
            result = int(ctx)
        except Exception as exc2:
            _brain_logger.debug("get_model_max_context failed for %s: %s / %s", model_name, exc, exc2)
    _model_max_context_cache[model_name] = result
    return result


def get_loaded_model_vram_bytes(model_name: str) -> int:
    normalized = str(model_name or "").strip().lower()
    if not normalized:
        return 0
    try:
        import requests as _requests

        data = _requests.get(_OLLAMA_PS_URL, timeout=5).json()
    except Exception:
        try:
            with urllib.request.urlopen(_OLLAMA_PS_URL, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return 0

    models = data.get("models", []) if isinstance(data, dict) else []
    for entry in models:
        if not isinstance(entry, dict):
            continue
        names = [
            str(entry.get("model") or "").strip().lower(),
            str(entry.get("name") or "").strip().lower(),
        ]
        if normalized in names:
            try:
                return int(entry.get("size_vram") or 0)
            except (TypeError, ValueError):
                return 0
    return 0


def get_context_window(config: dict, layer_name: str = "companion") -> int:
    """
    Returns the context window size to use for a request.
    - "auto" â†’ use provider-native model metadata when available
    - "auto" on Ollama â†’ query Ollama for the model's maximum context length
    - integer â†’ use that value directly

    Uses layer-resolved brain config so per-layer model overrides are respected.
    """
    brain_cfg = get_layer_brain_config(config, layer_name)
    configured = brain_cfg.get("context_window", "auto")

    if configured != "auto":
        try:
            val = int(configured)
            if val > 0:
                return val
        except (TypeError, ValueError):
            pass

    provider = normalize_provider_name(brain_cfg.get("provider") or "gemma")

    if provider == "openai":
        capabilities = get_openai_model_capabilities(brain_cfg.get("model"))
        try:
            context_window = int(capabilities.get("context_window") or 0)
            if context_window > 0:
                return context_window
        except (AttributeError, TypeError, ValueError):
            pass

    if provider == "anthropic":
        capabilities = get_anthropic_model_capabilities(brain_cfg.get("model"))
        try:
            context_window = int(capabilities.get("context_window") or 0)
            if context_window > 0:
                return context_window
        except (AttributeError, TypeError, ValueError):
            pass

    if provider == "chatgpt_oauth":
        capabilities = get_chatgpt_oauth_model_capabilities(brain_cfg.get("model"))
        try:
            context_window = int(capabilities.get("context_window") or 0)
            if context_window > 0:
                return context_window
        except (AttributeError, TypeError, ValueError):
            pass
        return _DEFAULT_CONTEXT_WINDOW

    if provider in {"qwen", "qwen_cloud"}:
        capabilities = get_qwen_model_capabilities(brain_cfg.get("model"))
        try:
            context_window = int(capabilities.get("context_window") or 0)
            if context_window > 0:
                return context_window
        except (AttributeError, TypeError, ValueError):
            pass
        if provider == "qwen_cloud":
            return _DEFAULT_CONTEXT_WINDOW

    if provider == "gemini":
        capabilities = get_gemini_model_capabilities(brain_cfg.get("model"))
        try:
            context_window = int(capabilities.get("context_window") or 0)
            if context_window > 0:
                return context_window
        except (AttributeError, TypeError, ValueError):
            pass
        return _DEFAULT_CONTEXT_WINDOW

    if provider == "openrouter":
        capabilities = get_openrouter_model_capabilities(brain_cfg.get("model"))
        try:
            context_window = int(capabilities.get("context_window") or 0)
            if context_window > 0:
                return context_window
        except (AttributeError, TypeError, ValueError):
            pass
        return _DEFAULT_CONTEXT_WINDOW

    if not is_ollama_backed_provider(provider):
        return _DEFAULT_CONTEXT_WINDOW

    # "auto" path for Ollama-backed providers: query model max from Ollama
    model = str(brain_cfg.get("model") or "").strip()
    if model:
        return get_model_max_context(model)
    return _DEFAULT_CONTEXT_WINDOW


def resolve_num_ctx(brain_config: dict, layer_name: str = "assistant") -> int | None:
    """
    Return the num_ctx value to pass to Ollama, or None if not applicable.
    - context_window == "auto" â†’ query Ollama for the model's maximum
    - context_window is a positive int â†’ use that value
    - anything else â†’ None (let Ollama use its default)
    """
    ctx = brain_config.get("context_window", "auto")
    if ctx == "auto":
        model = str(brain_config.get("model") or "").strip()
        if not model:
            return None
        model_max = get_model_max_context(model)
        model_vram_bytes = get_loaded_model_vram_bytes(model)
        advised = ctx_advisor.calculate_optimal_ctx(model_vram_bytes, normalize_layer_name(layer_name))
        return min(model_max, advised)
    try:
        val = int(ctx)
        return val if val > 0 else None
    except (TypeError, ValueError):
        return None


def resolve_chat_temperature(brain_config: dict) -> float:
    """
    Resolve the effective chat temperature for the active model family.

    Gemma-family models default to temperature=1.0 per the current Gemma docs.
    To preserve intentional user overrides, any non-empty, non-default value is
    respected; a legacy generic default of 0.8 is upgraded to 1.0 for Gemma.
    """
    configured = brain_config.get("temperature")
    family = resolve_model_family(brain_config)

    try:
        parsed = None if configured in (None, "") else float(configured)
    except (TypeError, ValueError):
        parsed = None

    if family == "google_gemma":
        if parsed is None or parsed == 0.8:
            return _GEMMA_DEFAULT_TEMPERATURE
        return parsed

    if parsed is None:
        return 0.8
    return parsed


def resolve_ollama_options(brain_config: dict, layer_name: str = "assistant") -> dict[str, int | float]:
    """
    Resolve Ollama request options for the active model.

    Gemma-family models use the documented sampling defaults unless explicitly
    overridden in config. Non-Gemma models keep existing behavior.
    """
    options: dict[str, int | float] = {}
    num_ctx = resolve_num_ctx(brain_config, layer_name)
    if num_ctx is not None:
        options["num_ctx"] = num_ctx

    if resolve_model_family(brain_config) == "google_gemma":
        options["temperature"] = resolve_chat_temperature(brain_config)
        try:
            options["top_p"] = float(brain_config.get("top_p", _GEMMA_DEFAULT_TOP_P))
        except (TypeError, ValueError):
            options["top_p"] = _GEMMA_DEFAULT_TOP_P
        try:
            options["top_k"] = int(brain_config.get("top_k", _GEMMA_DEFAULT_TOP_K))
        except (TypeError, ValueError):
            options["top_k"] = _GEMMA_DEFAULT_TOP_K

    return options


def resolve_openai_model_capabilities(brain_config: dict) -> dict | None:
    provider = normalize_provider_name(brain_config.get("provider") or "gemma")
    if provider != "openai":
        return None
    return get_openai_model_capabilities(brain_config.get("model"))


def resolve_anthropic_model_capabilities(brain_config: dict) -> dict | None:
    provider = normalize_provider_name(brain_config.get("provider") or "gemma")
    if provider != "anthropic":
        return None
    return get_anthropic_model_capabilities(brain_config.get("model"))


def resolve_qwen_model_capabilities(brain_config: dict) -> dict | None:
    provider = normalize_provider_name(brain_config.get("provider") or "gemma")
    if provider not in {"qwen", "qwen_cloud"}:
        return None
    return get_qwen_model_capabilities(brain_config.get("model"))


def resolve_gemini_model_capabilities(brain_config: dict) -> dict | None:
    provider = normalize_provider_name(brain_config.get("provider") or "gemma")
    if provider != "gemini":
        return None
    return get_gemini_model_capabilities(brain_config.get("model"))


def resolve_openrouter_model_capabilities(brain_config: dict) -> dict | None:
    provider = normalize_provider_name(brain_config.get("provider") or "gemma")
    if provider != "openrouter":
        return None
    return get_openrouter_model_capabilities(brain_config.get("model"))


def _model_name_suggests_image_input(model_name: str | None) -> bool:
    normalized = str(model_name or "").strip().lower()
    return any(marker in normalized for marker in ("vl", "vision", "llava", "moondream", "bakllava"))


def layer_supports_image_input(config: dict, layer: str) -> bool:
    """Return whether the selected model for a layer can receive image input."""
    brain_config = get_layer_brain_config(config, layer)
    provider = normalize_provider_name(brain_config.get("provider") or "gemma")
    model = brain_config.get("model")

    capability_resolvers = {
        "openai": get_openai_model_capabilities,
        "anthropic": get_anthropic_model_capabilities,
        "chatgpt_oauth": get_chatgpt_oauth_model_capabilities,
        "qwen": get_qwen_model_capabilities,
        "qwen_cloud": get_qwen_model_capabilities,
        "gemini": get_gemini_model_capabilities,
        "openrouter": get_openrouter_model_capabilities,
    }
    resolver = capability_resolvers.get(provider)
    capabilities = resolver(model) if resolver is not None else {}
    if bool(capabilities.get("supports_image_input")):
        return True

    if provider == "gemini":
        return True
    if provider == "openrouter":
        return any(marker in str(model or "").lower() for marker in ("gemini", "claude", "gpt-4o", "vision"))
    if is_ollama_backed_provider(provider) or provider in {"custom", "qwen", "qwen_cloud"}:
        return _model_name_suggests_image_input(model)
    return False


def get_layer_brain_config(config: dict, layer: str) -> dict:
    """
    Return resolved {base_url, api_key, model} for a given layer name.

    Provider/model are read from config.brain.layers.<layer> with fallback
    to config.brain.model / config.brain.base_url for backwards compatibility.
    API keys are read from the OS keychain â€” never from config.json.
    """
    brain = resolve_brain_config(config, layer)
    provider = normalize_provider_name(brain.get("provider") or "gemma")
    if provider == "custom":
        providers_cfg = config.get("providers", {}) if isinstance(config, dict) else {}
        custom_cfg = providers_cfg.get("custom", {}) if isinstance(providers_cfg, dict) else {}
        if isinstance(custom_cfg, dict):
            if not str(brain.get("base_url") or brain.get("api_url") or "").strip():
                custom_base_url = str(custom_cfg.get("base_url") or "").strip()
                if custom_base_url:
                    brain["base_url"] = custom_base_url
                    brain["api_url"] = custom_base_url
            if not str(brain.get("model") or "").strip() and str(custom_cfg.get("model") or "").strip():
                brain["model"] = str(custom_cfg.get("model") or "").strip()
    base_url, api_key = _resolve_provider_credentials(provider, brain)
    model = str(brain.get("model") or _default_model_for_provider(provider)).strip()
    if not model:
        canonical = normalize_layer_name(layer)
        if is_ollama_backed_provider(provider) or provider == "custom":
            model = _first_available_provider_model(provider, base_url, api_key, canonical)
            if not model and is_ollama_backed_provider(provider):
                model = _first_available_ollama_model(base_url)
        else:
            model = _first_available_provider_model(provider, base_url, api_key, canonical)
    if provider == "chatgpt_oauth":
        model = normalize_chatgpt_oauth_model_name(model)
    return {
        **brain,
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
    }


def _soul_file_path(filename: str) -> Path:
    """Return the active soul file path, falling back to defaults if missing."""
    active = SOUL_ACTIVE_DIR / filename
    if active.exists():
        return active
    return SOUL_DEFAULTS_DIR / filename


def _load_companion_fallback_prompt(config: dict) -> str:
    # Check for generated soul.md first for backward compatibility.
    legacy_soul_path = PROJECT_ROOT / "soul.md"
    if legacy_soul_path.exists():
        return _render_placeholders(legacy_soul_path.read_text(encoding="utf-8"), config, "companion")

    # Try active then defaults for soul_companion.md
    candidate = _soul_file_path("soul_companion.md")
    if candidate.exists():
        return _render_placeholders(candidate.read_text(encoding="utf-8"), config, "companion")

    return _render_placeholders(DEFAULT_LAYER_PROMPTS["companion"], config, "companion")


_TTS_RULE = (
    "Your replies are read aloud by a text-to-speech engine. "
    "Never use markdown, bullet points, headers, asterisks, or special characters. "
    "Write only plain prose sentences."
)


def _inject_tts_rule(content: str) -> str:
    """Append the TTS plain-prose rule if not already present."""
    if "text-to-speech" in content:
        return content
    return content + "\n\n" + _TTS_RULE


def load_identity(config: dict, layer_name: str = "companion") -> str:
    """
    Load the system prompt content for a given layer.

    For the companion layer, this preserves backward compatibility with soul.md.
    Each runtime layer now loads a single soul prompt file.
    The TTS plain-prose rule is appended programmatically so soul files do not
    need to carry it.
    """
    canonical = normalize_layer_name(layer_name)
    layer_config = get_layer_config(config, canonical)
    shared_path = _soul_file_path("shared_runtime_contract.md")
    shared_content = ""
    if shared_path.exists():
        shared_content = _render_placeholders(shared_path.read_text(encoding="utf-8"), config, canonical).strip()

    identity_path = _resolve_project_path(layer_config.get("identity_path"))
    if identity_path and identity_path.exists():
        content = _render_placeholders(identity_path.read_text(encoding="utf-8"), config, canonical).strip()
        merged = "\n\n".join(part for part in (shared_content, content) if part)
        return _inject_tts_rule(merged)
    elif canonical == "companion":
        content = _load_companion_fallback_prompt(config).strip()
        merged = "\n\n".join(part for part in (shared_content, content) if part)
        return _inject_tts_rule(merged)

    fallback_key = canonical if canonical in DEFAULT_LAYER_PROMPTS else "assistant"
    content = _render_placeholders(DEFAULT_LAYER_PROMPTS[fallback_key], config, canonical).strip()
    merged = "\n\n".join(part for part in (shared_content, content) if part)
    return _inject_tts_rule(merged)


def create_client(config: dict, layer_name: str = "companion") -> BrainClientShim:
    """Create a provider-backed compatibility shim."""
    cfg = get_layer_brain_config(config, layer_name)
    return create_provider_client(cfg, layer_name=layer_name)


def chat(
    client,
    config: dict,
    messages: list[dict],
    tools: list[dict] | None = None,
    layer_name: str = "companion",
    stream_handler=None,
    ollama_options_override: dict | None = None,
) -> BrainMessage:
    """Send a chat completion request through the provider abstraction."""
    provider = getattr(client, "provider", None)
    if isinstance(provider, BrainProvider):
        active_provider = provider
    elif isinstance(client, BrainProvider):
        active_provider = client
    else:
        active_provider = create_provider_client(get_layer_brain_config(config, layer_name), layer_name=layer_name).provider

    brain_config = get_layer_brain_config(config, layer_name)
    system, normalized_messages = split_system_messages(messages)
    effective_temperature = resolve_chat_temperature(brain_config)

    # Pass Ollama options so the context window and family-specific sampling are respected.
    extra_body: dict | None = None
    resolved_provider = normalize_provider_name(brain_config.get("provider") or "gemma")
    if tools:
        tools = validate_openai_tool_schemas(tools, resolved_provider)
    if is_ollama_backed_provider(resolved_provider):
        options = resolve_ollama_options(brain_config, layer_name)
        if ollama_options_override:
            options.update(dict(ollama_options_override))
        if options:
            extra_body = {"options": options}

    try:
        return active_provider.chat(
            messages=normalized_messages,
            system=system,
            stream=bool(brain_config.get("stream")),
            tools=tools,
            stream_handler=stream_handler,
            model=brain_config.get("model"),
            temperature=effective_temperature,
            max_tokens=brain_config.get("max_tokens", 1024),
            extra_body=extra_body,
        )
    except Exception as exc:
        debug_log_error(
            exc,
            {
                "layer": layer_name,
                "provider": resolved_provider,
                "model": brain_config.get("model"),
            },
        )
        raise
