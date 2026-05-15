"""
Wrapper - Backend session and transport entrypoints
===================================================
This module is the backend hub for OpenCompanion. It:

1. Loads config, identity files, and memory
2. Creates layer-aware brain clients
3. Keeps the companion layer persistent
4. Invokes the selected layer directly
5. Handles tool execution, confirmation gating, and JSON bridge transport

The wrapper never calls provider APIs directly and always goes through brain.py.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime

# Ensure stdio is UTF-8 regardless of the Windows console codepage (cp1252 etc.).
# errors='replace' is a last-resort safety net: if reconfigure itself fails for any
# reason, non-encodable characters are replaced with '?' rather than crashing.
for stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        if hasattr(stream, "reconfigure") and getattr(stream, "encoding", "utf-8").lower() != "utf-8":
            stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from pathlib import Path
from runtime_paths import PROJECT_ROOT, TEST_MODE, VAULT_DIR

BACKEND_DIR = Path(__file__).resolve().parent
for candidate in (str(PROJECT_ROOT), str(BACKEND_DIR)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import brain
import context_manager
from debug_log import DEBUG_ENABLED, debug_log, debug_tool_schema
import dream
import heartbeat
import memory
import session_summaries
from model_family import get_family_adapter
import soul as soul_module
import stt as stt_module
import tts as tts_module
from providers.base import is_ollama_backed_provider
from provider_normalization import brain_tool_call_from_normalized, recover_gemma_ollama_tool_calls
from tool_budget import resolve_budget, tool_result_is_progress
from tool_registry import (
    execute_tool,
    is_tool_allowed,
    needs_confirmation,
)
from tool_router import ToolRoute, classify_tool_route, routed_tools_for_turn
from tool_schema import validate_openai_tool_schemas, validate_tool_arguments
from wrapper_ollama import (
    _normalize_ollama_model_key,
    _validate_ollama_model_name,
    list_ollama_models,
    ollama_present_flags as _ollama_present_flags,
    pull_ollama_model,
    set_model_keepalive,
)
from wrapper_scheduler import (
    ensure_schedule_file as _ensure_schedule_file,
    invoke_optional_scheduler_hook as _invoke_optional_scheduler_hook,
    load_scheduler_module as _load_scheduler_module,
)
from wrapper_text import (
    extract_explicit_vault_file_path as _extract_explicit_vault_file_path,
    extract_spotify_query as _extract_spotify_query,
    extract_user_text,
    log_terminal_message as _log_terminal_message,
    looks_like_capability_query as _looks_like_capability_query,
    one_sentence_description as _one_sentence_description,
    quote_terminal_text as _quote_terminal_text,
    sanitize_json_payload as _sanitize_json_payload,
    sanitize_text_for_utf8 as _sanitize_text_for_utf8,
    soul_path,
    strip_display_artifacts as _strip_display_artifacts,
    strip_gemma_token_bleed as _strip_gemma_token_bleed,
    strip_legacy_json_directives,
    tool_result_looks_like_failure as _tool_result_looks_like_failure,
    truncate_terminal_text as _truncate_terminal_text,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("wrapper")

MAX_TOOL_ITERATIONS = 10
SCHEDULER_ENABLED_IN_TEST_MODE = os.environ.get("OPEN_COMPANION_TEST_ENABLE_SCHEDULER") == "1"
VERBOSE_RUNTIME_LOGS = os.environ.get("OPEN_COMPANION_VERBOSE_RUNTIME_LOGS") == "1"
MIN_GUARANTEED_MESSAGES = 4   # always keep at least 2 full turns
OUTPUT_RESERVE_TOKENS = 1024  # reserved for model output
AUTO_CTX_HEADROOM_MULTIPLIER = 1.2
AUTO_CTX_EXTRA_TOKENS = 2048
AUTO_CTX_ROUNDING = 2048
AUTO_CTX_MIN_BY_LAYER = {
    "companion": 8192,
    "assistant": 16384,
}

KEEPALIVE_ACTIVE = "10m"
KEEPALIVE_IDLE = "2m"
KEEPALIVE_UNLOAD = 0

INTERNAL_LAYER_REQUEST_DEFINITIONS: list[dict] = []
INTERNAL_LAYER_REQUEST_NAMES: set[str] = set()


def _resolve_max_tool_iterations(config: dict, layer_name: str) -> int:
    try:
        layer_cfg = brain.get_layer_config(config, layer_name)
        limit = layer_cfg.get("max_tool_iterations")
        if limit is not None:
            return max(1, int(limit))
    except (AttributeError, TypeError, ValueError):
        pass
    return MAX_TOOL_ITERATIONS


class LayerSession:
    """Conversation state for one runtime layer."""

    def __init__(self, config: dict, layer_name: str, include_memory: bool = False):
        self.config = config
        self.layer_name = brain.normalize_layer_name(layer_name)
        self.layer_config = brain.get_layer_config(config, self.layer_name)
        self.tool_profile = self.layer_config.get("permission_profile", self.layer_name)
        self.client = brain.create_client(config, self.layer_name)
        self.display_name = brain.get_layer_display_name(config, self.layer_name)
        self.family_adapter = getattr(getattr(self.client, "provider", None), "family_adapter", get_family_adapter(brain.get_layer_brain_config(config, self.layer_name)))

        self._base_prompt = brain.load_identity(config, self.layer_name)
        self._include_memory = include_memory
        self.session_id = f"{self.layer_name}-{time.time_ns()}"
        self.session_started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self.session_checkpoint = 0
        self._frozen_turn_prompt = ""
        self.conversation = [{"role": "system", "content": self._build_initial_system_prompt()}]

        self.pending_tool_calls = []
        self.pending_tool_index: int | None = None
        self.current_turn_query = ""
        self.tool_iteration_count = 0
        self.last_user_text_for_memory = ""
        self.pending_side_events: list[dict] = []
        self._current_stream_handler = None
        self._current_reply_id = None
        self._active_tool_turn_index: int | None = None
        self._turn_assistant_message_indices: list[int] = []
        self._turn_tool_call_counts: dict[str, int] = {}
        self._turn_tool_results: dict[str, str] = {}
        self._turn_progress_results: set[str] = set()
        self._turn_total_tool_calls = 0
        self._turn_repeated_errors: dict[str, int] = {}
        self._current_tool_route: ToolRoute = classify_tool_route("")
        self._current_exposed_tool_names: set[str] = set()
        self._current_exposed_tool_schemas: dict[str, dict] = {}
        self._empty_followup_retry_used = False
        self._last_weather_tool_result = ""
        self._system_prompt_signature = self._base_prompt

    def reload_identity(self) -> str:
        prompt = brain.load_identity(self.config, self.layer_name)
        signature = prompt
        if signature == self._system_prompt_signature:
            return self.conversation[0]["content"]

        self._base_prompt = prompt
        self._system_prompt_signature = signature
        self._frozen_turn_prompt = ""
        refreshed = self._build_initial_system_prompt()
        self.conversation[0] = {"role": "system", "content": refreshed}
        return refreshed

    def _build_initial_system_prompt(self) -> str:
        return str(self._base_prompt or "").strip()

    def _freeze_turn_context(self, active_config: dict, user_input: str, ctx_window: int) -> str:
        refreshed = str(self._base_prompt or "").strip()
        self.conversation[0] = {"role": "system", "content": refreshed}
        self._frozen_turn_prompt = refreshed
        return refreshed

    def refresh_system_prompt(self, user_input: str = "") -> str:
        self._frozen_turn_prompt = ""
        refreshed = self._build_initial_system_prompt()
        self.conversation[0] = {"role": "system", "content": refreshed}
        return refreshed

    def _start_new_session(self) -> None:
        self.session_id = f"{self.layer_name}-{time.time_ns()}"
        self.session_started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self.session_checkpoint = 0
        self._frozen_turn_prompt = ""
        self.conversation = [{"role": "system", "content": self._build_initial_system_prompt()}]
        self._reset_turn_state("")

    def reset_session(self, preserve_summary: bool = True) -> dict:
        non_system = [message for message in self.conversation if message.get("role") != "system"]
        summary_paths: list[str] = []
        if preserve_summary and non_system:
            summary_info = session_summaries.write_session_summary(
                self.layer_name,
                non_system,
                session_id=self.session_id,
                checkpoint=self.session_checkpoint + 1,
                trigger="session_reset",
                session_started_at=self.session_started_at,
                session_ended_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            )
            self.session_checkpoint += 1
            summary_paths.append(str(summary_info["path"]))
        self._start_new_session()
        return {
            "layer": self.layer_name,
            "summary_paths": summary_paths,
        }

    def maybe_compact_session(self) -> dict | None:
        summary_cfg = session_summaries.get_summary_config(self.config)
        if not summary_cfg.get("enabled", True):
            return None
        non_system = [message for message in self.conversation if message.get("role") != "system"]
        if not non_system:
            return None
        message_count = len(non_system)
        token_count = context_manager.count_messages_tokens(non_system)
        if message_count < summary_cfg.get("compact_after_messages", 0) and token_count < summary_cfg.get("compact_after_tokens", 0):
            return None
        summary_info = session_summaries.write_session_summary(
            self.layer_name,
            non_system,
            session_id=self.session_id,
            checkpoint=self.session_checkpoint + 1,
            trigger="threshold",
            session_started_at=self.session_started_at,
            session_ended_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        self.session_checkpoint += 1
        retain_recent = max(1, summary_cfg.get("retain_recent_messages", MIN_GUARANTEED_MESSAGES))
        self.conversation = [self.conversation[0]] + non_system[-retain_recent:]
        return summary_info

    def _build_assistant_event(self, reply_text: str) -> dict:
        reply = _sanitize_text_for_utf8(reply_text).strip()
        if self.layer_name == "companion":
            reply = strip_legacy_json_directives(reply)
        reply = self.family_adapter.strip_final_reply(reply)
        reply = _strip_display_artifacts(reply) or "[No response]"
        reply = self._ground_final_reply_to_tool_results(reply)
        if self.layer_name == "companion":
            reply = strip_legacy_json_directives(reply)

        payload = {
            "type": "assistant_message",
            "content": reply,
            "state": "idle",
            "layer": self.layer_name,
            "message_id": self._current_reply_id or f"{self.layer_name}-{time.time_ns()}",
        }
        return payload

    def _ground_final_reply_to_tool_results(self, reply: str) -> str:
        query = str(self.current_turn_query or "").strip().lower()
        weather_result = _sanitize_text_for_utf8(self._last_weather_tool_result).strip()
        if "weather" in query and weather_result:
            return weather_result

        if self._turn_tool_results:
            latest_result = _sanitize_text_for_utf8(next(reversed(self._turn_tool_results.values()))).strip()
            if str(reply or "").strip() == "[No response]" and latest_result:
                return latest_result
            if latest_result and _tool_result_looks_like_failure(latest_result):
                lowered_reply = str(reply or "").strip().lower()
                acknowledged = any(
                    marker in lowered_reply
                    for marker in ("error", "failed", "unavailable", "not available", "not found", "timed out", "denied")
                )
                if not acknowledged:
                    return latest_result

        return reply

    def _build_tool_grounding_instruction(self) -> str:
        if not self._turn_tool_results:
            return ""
        lines = [
            "## Current Tool Results",
            "The tool results below are authoritative for the user's current request.",
            "Answer from these exact results. Do not invent different file contents or facts.",
            "Do not repeat a tool call with the same arguments; use the result already shown here.",
            "",
        ]
        for index, (signature, result) in enumerate(self._turn_tool_results.items(), start=1):
            lines.append(f"### Tool Result {index}")
            lines.append(f"Signature: {signature}")
            lines.append("Result:")
            lines.append(str(result))
            lines.append("")
        return "\n".join(lines).strip()

    def _infer_daily_note_path(self) -> str:
        daily_dir = Path(VAULT_DIR) / "Daily Notes"
        if not daily_dir.exists() or not daily_dir.is_dir():
            return ""
        today_name = f"{datetime.now().date().isoformat()}.md"
        today_path = daily_dir / today_name
        if today_path.exists():
            return f"Daily Notes/{today_name}"
        notes = sorted(
            [path for path in daily_dir.glob("*.md") if path.is_file()],
            key=lambda path: (path.stat().st_mtime, path.name),
            reverse=True,
        )
        if len(notes) == 1:
            return f"Daily Notes/{notes[0].name}"
        return ""

    def _maybe_convert_terminal_read_tool(self, tool_call: BrainToolCall, arguments: dict) -> BrainToolCall:
        return tool_call

    def _tool_already_executed_this_turn(self) -> bool:
        if self._turn_tool_call_counts:
            return True
        return any(
            isinstance(event, dict) and event.get("type") == "tool_executed"
            for event in self.pending_side_events
        )

    def has_pending_work(self) -> bool:
        return self.pending_tool_index is not None

    def _reset_turn_state(self, query_text: str = "") -> None:
        self.pending_tool_calls = []
        self.pending_tool_index = None
        self.current_turn_query = query_text
        self.tool_iteration_count = 0
        self._frozen_turn_prompt = ""
        self.pending_side_events = []
        self._current_stream_handler = None
        self._active_tool_turn_index = None
        self._turn_assistant_message_indices = []
        self._turn_tool_call_counts = {}
        self._turn_tool_results = {}
        self._turn_progress_results = set()
        self._turn_total_tool_calls = 0
        self._turn_repeated_errors = {}
        self._current_tool_route = classify_tool_route(query_text, self.layer_name)
        self._current_exposed_tool_names = set()
        self._current_exposed_tool_schemas = {}
        self._empty_followup_retry_used = False
        self._last_weather_tool_result = ""

    def _drain_side_events(self) -> list[dict]:
        events = list(self.pending_side_events)
        self.pending_side_events.clear()
        return events

    def _append_conversation_message(self, message: dict, direction: str = "HISTORY") -> int:
        self.conversation.append(message)
        _log_terminal_message(direction, self.layer_name, message)
        return len(self.conversation) - 1

    def _log_brain_response(self, response) -> None:
        message = {
            "role": getattr(response, "role", "assistant") or "assistant",
            "content": getattr(response, "content", "") or "",
        }
        if getattr(response, "tool_calls", None):
            message["tool_calls"] = self._serialize_tool_calls(response.tool_calls)
        _log_terminal_message("BRAIN IN", self.layer_name, message)

    def _record_tool_execution(
        self,
        tool_name: str,
        result: str,
        tool_args: dict | None = None,
        layer_override: str | None = None,
    ) -> None:
        self.pending_side_events.append({
            "type": "tool_executed",
            "tool_name": tool_name,
            "tool_args": dict(tool_args or {}),
            "result": str(result)[:200],
            "layer": layer_override or self.layer_name,
        })

    def _maybe_handle_runtime_shortcut(self, user_text: str, tool_config: dict | None = None) -> dict | None:
        active_config = tool_config or self.config

        if self.layer_name == "companion" and _looks_like_capability_query(user_text):
            result = execute_tool("list_available_tools", {}, "companion", config=active_config)
            self._record_tool_execution("list_available_tools", result, tool_args={}, layer_override="companion")
            event = self._build_assistant_event(result)
            self._append_conversation_message({"role": "assistant", "content": event["content"]})
            return event

        if self.layer_name == "companion":
            spotify_query = _extract_spotify_query(user_text)
            if spotify_query and is_tool_allowed("play_spotify", "assistant", config=active_config):
                result = execute_tool("play_spotify", {"query": spotify_query}, "assistant", config=active_config)
                self._record_tool_execution(
                    "play_spotify",
                    result,
                    tool_args={"query": spotify_query},
                    layer_override="assistant",
                )
                event = self._build_assistant_event(result)
                self._append_conversation_message({"role": "assistant", "content": event["content"]})
                return event

        return None

    def _serialize_tool_calls(self, tool_calls: list) -> list[dict]:
        return [
            {
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.function.name,
                    "arguments": tool_call.function.arguments,
                },
            }
            for tool_call in tool_calls
        ]

    def _parse_tool_arguments(self, tool_call) -> dict:
        try:
            return json.loads(tool_call.function.arguments or "{}")
        except json.JSONDecodeError:
            return {}

    def _append_tool_result(self, tool_call_id: str, content: str) -> None:
        tool_call = self._current_tool_call()
        if DEBUG_ENABLED:
            debug_log(
                "TOOL_OUTPUT",
                {
                    "layer": self.layer_name,
                    "tool_call_id": str(tool_call_id),
                    "tool_name": getattr(getattr(tool_call, "function", None), "name", "") if tool_call else "",
                    "content": content,
                },
            )
        tool_call_payload = {
            "id": tool_call_id,
            "type": "function",
            "function": {
                "name": getattr(getattr(tool_call, "function", None), "name", ""),
                "arguments": getattr(getattr(tool_call, "function", None), "arguments", ""),
            },
        }
        adapter = self.family_adapter
        if adapter is None:
            self._append_conversation_message({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": content,
            })
        else:
            adapter.append_tool_result(
                self.conversation,
                assistant_index=self._active_tool_turn_index,
                tool_call=tool_call_payload,
                result=content,
            )
        if VERBOSE_RUNTIME_LOGS:
            print(
                f"[TOOL RESULT] layer={self.layer_name} id={_quote_terminal_text(str(tool_call_id))} content={_quote_terminal_text(content)}",
                file=sys.stderr,
                flush=True,
            )

    def _finalize_turn_history(self) -> None:
        adapter = self.family_adapter
        if adapter is not None:
            adapter.sanitize_completed_turn(self.conversation, self._turn_assistant_message_indices)
        self._active_tool_turn_index = None
        self._turn_assistant_message_indices = []

    def _tool_definitions(self, tool_config: dict | None = None, query_text: str = "") -> list[dict]:
        active_config = tool_config or self.config
        route = self._current_tool_route
        if query_text:
            route = classify_tool_route(query_text, self.layer_name)
            self._current_tool_route = route
        routed_tools = routed_tools_for_turn(self.layer_name, route, config=active_config)
        self._current_exposed_tool_names = {tool["name"] for tool in routed_tools}
        self._current_exposed_tool_schemas = {tool["name"]: tool["schema"] for tool in routed_tools}
        for index, tool in enumerate(routed_tools):
            debug_tool_schema("TOOL_SCHEMA_REGISTRY_OBJECT", {"layer": self.layer_name, "route": route.name, "index": index, "tool": tool})
        schemas = [tool["schema"] for tool in routed_tools]
        for schema in schemas:
            func = schema.get("function")
            if isinstance(func, dict) and "description" in func:
                func["description"] = _one_sentence_description(func.get("description", ""))
            elif "description" in schema:
                schema["description"] = _one_sentence_description(schema.get("description", ""))
        provider_name = str(brain.get_layer_brain_config(active_config, self.layer_name).get("provider") or "unknown")
        validated = validate_openai_tool_schemas(schemas, provider_name)
        debug_tool_schema("TOOL_SCHEMA_FILTERED_FOR_TURN", {"layer": self.layer_name, "route": route.name, "tools": [s.get("function", {}).get("name") for s in validated]})
        debug_tool_schema("TOOL_SCHEMA_PROVIDER_NORMALIZED", {"provider": provider_name, "tools": validated})
        return validated

    def _tool_signature(self, tool_name: str, arguments: dict) -> str:
        return json.dumps(
            {
                "name": str(tool_name or "").strip(),
                "arguments": arguments or {},
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    def _should_block_repeated_tool_call(self, tool_name: str, arguments: dict) -> tuple[bool, str]:
        signature = self._tool_signature(tool_name, arguments)
        attempts = self._turn_tool_call_counts.get(signature, 0)
        if attempts < 1:
            return False, signature
        previous_result = self._turn_tool_results.get(signature)
        guidance = "You already called this tool with the same arguments in this turn."
        if previous_result:
            guidance += f" Use the previous result instead of repeating it:\n{previous_result}"
        else:
            guidance += " Do not repeat it. Continue with a different tool or answer the user."
        return True, guidance

    def _apply_sliding_window(
        self,
        conversation: list[dict],
        system_prompt: str,
        user_input: str,
        context_window: int,
    ) -> list[dict]:
        """
        Trim conversation history to fit within context budget.
        Always keeps the system message (index 0) and never removes below
        MIN_GUARANTEED_MESSAGES tail messages.
        Returns a trimmed copy; does not mutate self.conversation.
        """
        output_reserve = OUTPUT_RESERVE_TOKENS
        system_tokens = context_manager.count_tokens(system_prompt)
        input_tokens = context_manager.count_tokens(user_input)

        # Available budget for the stored conversation history (excluding system msg)
        non_system = [m for m in conversation if m.get("role") != "system"]
        conversation_budget = context_window - output_reserve - system_tokens - input_tokens

        if conversation_budget <= 0:
            # Emergency: keep only the minimum tail messages
            return non_system[-MIN_GUARANTEED_MESSAGES:]

        weighted_messages = [
            (message, context_manager.count_messages_tokens([message]))
            for message in non_system
        ]
        total_tokens = sum(tokens for _message, tokens in weighted_messages)
        trim_index = 0
        last_trim_index = max(0, len(weighted_messages) - MIN_GUARANTEED_MESSAGES)
        while trim_index < last_trim_index and total_tokens > conversation_budget:
            total_tokens -= weighted_messages[trim_index][1]
            trim_index += 1

        return [message for message, _tokens in weighted_messages[trim_index:]]

    def _resolve_working_context_window(self, max_ctx_window: int, required_tokens: int) -> int:
        active_brain_config = brain.get_layer_brain_config(self.config, self.layer_name)
        if active_brain_config.get("context_window", "auto") != "auto":
            return max_ctx_window

        layer_floor = AUTO_CTX_MIN_BY_LAYER.get(self.layer_name, 8192)
        target = int((required_tokens + AUTO_CTX_EXTRA_TOKENS) * AUTO_CTX_HEADROOM_MULTIPLIER)
        target = max(layer_floor, target)
        rounded = ((target + AUTO_CTX_ROUNDING - 1) // AUTO_CTX_ROUNDING) * AUTO_CTX_ROUNDING
        return max(4096, min(max_ctx_window, rounded))

    def _call_brain(
        self,
        tool_config: dict | None = None,
        query_text: str = "",
        allow_tools: bool = True,
    ):
        tool_definitions = self._tool_definitions(tool_config, query_text) if allow_tools else None

        # Ã¢â€â‚¬Ã¢â€â‚¬ Dynamic context management Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
        active_config = tool_config or self.config
        max_ctx_window = brain.get_context_window(active_config, self.layer_name)
        active_brain_config = brain.get_layer_brain_config(active_config, self.layer_name)
        resolved_provider = str(active_brain_config.get("provider") or "")
        resolved_model = str(active_brain_config.get("model") or "")
        resolved_temperature = brain.resolve_chat_temperature(active_brain_config)

        # System message is always index 0
        system_msg = self.conversation[0]["content"] if self.conversation else ""

        # Current user input (last human turn in conversation, if any)
        user_input = ""
        for msg in reversed(self.conversation):
            if msg.get("role") == "user":
                content = msg.get("content") or ""
                if isinstance(content, list):
                    content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
                user_input = str(content)
                break

        soul_only_tokens = context_manager.count_tokens(self._base_prompt)
        input_tokens = context_manager.count_tokens(user_input)
        min_conv_tokens = context_manager.count_messages_tokens(
            [m for m in self.conversation if m.get("role") != "system"][-MIN_GUARANTEED_MESSAGES:]
        )
        baseline_required_tokens = OUTPUT_RESERVE_TOKENS + soul_only_tokens + input_tokens + min_conv_tokens
        ctx_window = self._resolve_working_context_window(max_ctx_window, baseline_required_tokens)
        system_msg = self._freeze_turn_context(active_config, user_input, ctx_window)

        # Build trimmed conversation: system + sliding window of history
        trimmed_history = self._apply_sliding_window(
            self.conversation, system_msg, user_input, ctx_window
        )
        resolved_ollama_options = (
            brain.resolve_ollama_options(active_brain_config)
            if is_ollama_backed_provider(resolved_provider)
            else {}
        )
        if is_ollama_backed_provider(resolved_provider) and active_brain_config.get("context_window", "auto") == "auto":
            resolved_ollama_options["num_ctx"] = ctx_window
        resolved_num_ctx = (
            resolved_ollama_options.get("num_ctx")
            if is_ollama_backed_provider(resolved_provider)
            else None
        )
        system_tokens = context_manager.count_tokens(system_msg)
        raw_history = [m for m in self.conversation if m.get("role") != "system"]
        raw_history_tokens = context_manager.count_messages_tokens(raw_history)
        trimmed_history_tokens = context_manager.count_messages_tokens(trimmed_history)
        prompt_tokens_estimate = system_tokens + trimmed_history_tokens
        messages = [{"role": "system", "content": system_msg}] + trimmed_history
        if VERBOSE_RUNTIME_LOGS:
            for message in messages:
                _log_terminal_message("BRAIN OUT", self.layer_name, message)
            print(
                f"[BRAIN OUT META] layer={self.layer_name} allow_tools={allow_tools} tools={len(tool_definitions) if tool_definitions else 0}",
                file=sys.stderr,
                flush=True,
            )
            print(
                "[BRAIN BUDGET] "
                f"layer={self.layer_name} "
                f"provider={resolved_provider!r} "
                f"family={getattr(self.family_adapter, 'family', 'generic')!r} "
                f"model={resolved_model!r} "
                f"ctx_window={max_ctx_window} "
                f"num_ctx={resolved_num_ctx if resolved_num_ctx is not None else 'n/a'} "
                f"temperature={resolved_temperature} "
                f"top_p={resolved_ollama_options.get('top_p', 'n/a')} "
                f"top_k={resolved_ollama_options.get('top_k', 'n/a')} "
                f"max_tokens={active_brain_config.get('max_tokens', 1024)} "
                f"output_reserve={OUTPUT_RESERVE_TOKENS} "
                f"system_tokens={system_tokens} "
                f"input_tokens={input_tokens} "
                f"raw_history_tokens={raw_history_tokens} "
                f"trimmed_history_tokens={trimmed_history_tokens} "
                f"prompt_estimate={prompt_tokens_estimate} "
                f"raw_messages={len(raw_history)} "
                f"trimmed_messages={len(trimmed_history)}",
                file=sys.stderr,
                flush=True,
            )
        # Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬

        if os.environ.get("OC_DEBUG_PROMPTS"):
            import pathlib
            _dbg = pathlib.Path(__file__).parent.parent / "memory" / ".prompt_debug.jsonl"
            _dbg.parent.mkdir(parents=True, exist_ok=True)
            with open(_dbg, "a", encoding="utf-8") as _f:
                _f.write(json.dumps({
                    "ts": time.time(),
                    "layer": self.layer_name,
                    "allow_tools": allow_tools,
                    "tools_count": len(tool_definitions) if tool_definitions else 0,
                    "messages": messages,
                }, ensure_ascii=False) + "\n")

        return brain.chat(
            client=self.client,
            config=active_config,
            messages=messages,
            tools=tool_definitions,
            layer_name=self.layer_name,
            stream_handler=self._current_stream_handler,
            ollama_options_override=resolved_ollama_options if is_ollama_backed_provider(resolved_provider) else None,
        )

    def _current_tool_call(self):
        if self.pending_tool_index is None:
            return None
        if self.pending_tool_index >= len(self.pending_tool_calls):
            return None
        return self.pending_tool_calls[self.pending_tool_index]

    def _force_final_response(self, tool_config: dict | None = None) -> dict:
        self._append_conversation_message({
            "role": "system",
            "content": (
                "Maximum tool iterations reached. Summarize what was accomplished so far and "
                "respond to the user now without calling more tools."
            ),
        })
        try:
            final_response = self._call_brain(tool_config, allow_tools=False)
            reply = final_response.content or "[No response]"
        except Exception as exc:
            logger.error("Brain error on forced final response for %s: %s", self.layer_name, exc)
            reply = f"[Error getting response from {self.display_name}]"

        event = self._build_assistant_event(reply)
        index = self.family_adapter.finalize_assistant_turn(
            self.conversation,
            assistant_index=self._active_tool_turn_index,
            content=event["content"],
        )
        if index not in self._turn_assistant_message_indices:
            self._turn_assistant_message_indices.append(index)
        _log_terminal_message("HISTORY", self.layer_name, self.conversation[index])
        self._finalize_turn_history()
        self._current_stream_handler = None
        self._current_reply_id = None
        return event

    def _maybe_retry_empty_followup(self, response, tool_config: dict | None = None):
        raw_content = str(getattr(response, "content", "") or "")
        visible_content = _strip_display_artifacts(raw_content).strip()
        if visible_content:
            return None
        if getattr(response, "tool_calls", None):
            return None
        if self._active_tool_turn_index is None:
            return None
        if self._empty_followup_retry_used:
            return None

        self._empty_followup_retry_used = True
        self._append_conversation_message({
            "role": "system",
            "content": (
                "You just received tool results for this turn. Continue the task now. "
                "Either call the next tool or answer the user with a non-empty response."
            ),
        })
        try:
            return self._call_brain(tool_config, query_text=self.current_turn_query, allow_tools=True)
        except Exception as exc:
            logger.error("Brain error on empty follow-up retry for %s: %s", self.layer_name, exc)
            return None

    def _handle_brain_response(self, response, tool_config: dict | None = None) -> dict:
        self._log_brain_response(response)
        if not response.tool_calls and response.content:
            active_config = tool_config or self.config
            active_brain_config = brain.get_layer_brain_config(active_config, self.layer_name)
            provider_name = str(active_brain_config.get("provider") or "").strip().lower()
            model_name = str(active_brain_config.get("model") or "").strip().lower()
            recovery_enabled = provider_name in {"gemma", "ollama"} and ("gemma" in model_name or provider_name == "gemma")
            if recovery_enabled:
                cleaned, recovered, diagnostics = recover_gemma_ollama_tool_calls(
                    response.content,
                    provider="ollama_gemma_native" if provider_name == "gemma" else "ollama_local_model",
                    allowed_tool_schemas=self._current_exposed_tool_schemas,
                )
                for item in diagnostics:
                    logger.info("[%s] %s", self.layer_name, item)
                    debug_tool_schema("RECOVERED_PROVIDER_TOOL_CALL", {"layer": self.layer_name, "route": self._current_tool_route.name, "message": item})
                if recovered:
                    response.tool_calls = [brain_tool_call_from_normalized(call) for call in recovered]
                response.content = cleaned
        if response.tool_calls:
            self.tool_iteration_count += 1
            budget = resolve_budget(self.layer_name)
            legacy_limit = _resolve_max_tool_iterations(tool_config or self.config, self.layer_name)
            if self.tool_iteration_count > min(budget.max_rounds, legacy_limit):
                return self._force_final_response(tool_config)

            assistant_index = self.family_adapter.append_assistant_tool_call_message(
                self.conversation,
                content=response.content or "",
                tool_calls=self._serialize_tool_calls(response.tool_calls),
            )
            self._active_tool_turn_index = assistant_index
            self._turn_assistant_message_indices.append(assistant_index)
            _log_terminal_message("HISTORY", self.layer_name, self.conversation[assistant_index])
            self.pending_tool_calls = list(response.tool_calls)
            self.pending_tool_index = 0
            return self._process_tool_queue(tool_config)

        retried_response = self._maybe_retry_empty_followup(response, tool_config)
        if retried_response is not None:
            return self._handle_brain_response(retried_response, tool_config)

        event = self._build_assistant_event(response.content or "[No response]")
        if self.layer_name == "assistant" and self._active_tool_turn_index is not None and event.get("content") == "[No response]":
            return self._force_final_response(tool_config)
        index = self.family_adapter.finalize_assistant_turn(
            self.conversation,
            assistant_index=self._active_tool_turn_index,
            content=event["content"],
        )
        if index not in self._turn_assistant_message_indices:
            self._turn_assistant_message_indices.append(index)
        _log_terminal_message("HISTORY", self.layer_name, self.conversation[index])
        self._finalize_turn_history()
        self._current_stream_handler = None
        self._current_reply_id = None
        return event

    def _process_tool_queue(self, tool_config: dict | None = None) -> dict:
        while self.pending_tool_index is not None and self.pending_tool_index < len(self.pending_tool_calls):
            tool_call = self.pending_tool_calls[self.pending_tool_index]
            arguments = self._parse_tool_arguments(tool_call)
            converted_tool_call = self._maybe_convert_terminal_read_tool(tool_call, arguments)
            if converted_tool_call is not tool_call:
                self.pending_tool_calls[self.pending_tool_index] = converted_tool_call
                tool_call = converted_tool_call
                arguments = self._parse_tool_arguments(tool_call)
            if tool_call.function.name in {"read_file", "write_file", "append_file", "replace_text_in_file", "edit_code_symbol", "edit_file", "list_files", "search_files"} and isinstance(arguments.get("path"), str):
                clean_path = _extract_explicit_vault_file_path(arguments.get("path") or "")
                if clean_path and clean_path != arguments.get("path"):
                    arguments["path"] = clean_path
                    tool_call.function.arguments = json.dumps(arguments, ensure_ascii=False)
            tool_name = tool_call.function.name
            tool_call_id = tool_call.id

            if VERBOSE_RUNTIME_LOGS:
                print(
                    f"[TOOL CALL] layer={self.layer_name} id={tool_call_id!r} name={tool_name!r} arguments={_truncate_terminal_text(json.dumps(arguments, ensure_ascii=False))!r}",
                    file=sys.stderr,
                    flush=True,
                )

            budget = resolve_budget(self.layer_name)
            self._turn_total_tool_calls += 1
            if self._turn_total_tool_calls > budget.max_calls:
                self._append_tool_result(
                    tool_call_id,
                    f"Tool budget reached for {self.layer_name}; stopped instead of continuing the loop.",
                )
                break

            if tool_name not in self._current_exposed_tool_names:
                logger.warning(
                    "Rejected route-gated tool '%s' for layer '%s' route '%s'",
                    tool_name,
                    self.layer_name,
                    self._current_tool_route.name,
                )
                self._append_tool_result(tool_call_id, f"Tool {tool_name} is not exposed for this turn.")
                self.pending_tool_index += 1
                continue

            schema = self._current_exposed_tool_schemas.get(tool_name)
            arg_validation = validate_tool_arguments(schema, arguments)
            if not arg_validation.ok:
                self._append_tool_result(tool_call_id, f"Invalid arguments for {tool_name}: {arg_validation.reason}")
                self.pending_tool_index += 1
                continue

            should_block, repeated_payload = self._should_block_repeated_tool_call(tool_name, arguments)
            if should_block:
                logger.warning(
                    "Blocked repeated tool call for layer=%s tool=%s arguments=%s",
                    self.layer_name,
                    tool_name,
                    arguments,
                )
                self._append_tool_result(tool_call_id, repeated_payload)
                self.pending_tool_index += 1
                if self.pending_tool_index >= len(self.pending_tool_calls):
                    break
                continue

            tool_signature = self._tool_signature(tool_name, arguments)
            self._turn_tool_call_counts[tool_signature] = self._turn_tool_call_counts.get(tool_signature, 0) + 1

            if not is_tool_allowed(tool_name, self.layer_name, config=tool_config or self.config):
                logger.warning("Rejected tool '%s' for layer '%s'", tool_name, self.layer_name)
                self._append_tool_result(tool_call_id, "This tool is not available in this layer.")
                self.pending_tool_index += 1
                continue

            if needs_confirmation(tool_name, self.layer_name, config=tool_config or self.config):
                return {
                    "type": "tool_confirmation_requested",
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "state": "awaiting_approval",
                    "layer": self.layer_name,
                    "layer_display_name": self.display_name,
                }

            logger.info("[%s] Executing tool: %s(%s)", self.layer_name, tool_name, arguments)
            if tool_name in {"add_reminder", "add_recurring_reminder"} and self.current_turn_query:
                arguments = {**arguments, "current_user_message": self.current_turn_query}
            result = execute_tool(tool_name, arguments, self.layer_name, config=tool_config or self.config)
            logger.info("[%s] Tool result: %s", self.layer_name, result[:200])
            if _tool_result_looks_like_failure(str(result)):
                error_key = self._tool_signature(tool_name, arguments) + "::" + str(result)[:200]
                self._turn_repeated_errors[error_key] = self._turn_repeated_errors.get(error_key, 0) + 1
                if self._turn_repeated_errors[error_key] >= 2:
                    result = f"{result}\n[Stopped: the same tool error repeated with no corrected arguments.]"
            self._append_tool_result(tool_call_id, result)
            if tool_name == "run_terminal":
                command = str(arguments.get("command") or "").lower()
                if "wttr.in" in command:
                    self._last_weather_tool_result = str(result)
            self._turn_tool_results[tool_signature] = str(result)
            if tool_result_is_progress(tool_name, arguments, str(result), self._turn_progress_results):
                self._turn_progress_results.add(f"{tool_name}:{str(result)[:500]}")
            self._record_tool_execution(tool_name, result, tool_args=arguments)
            self.pending_tool_index += 1

        self.pending_tool_calls = []
        self.pending_tool_index = None
        try:
            response = self._call_brain(tool_config, query_text=self.current_turn_query, allow_tools=True)
        except Exception as exc:
            print(
                f"[TOOL FOLLOWUP ERROR] layer={self.layer_name} error={exc!r}",
                file=sys.stderr,
                flush=True,
            )
            logger.error("Brain error on follow-up for %s: %s", self.layer_name, exc)
            event = self._build_assistant_event(f"[Error getting response from {self.display_name}]")
            index = self.family_adapter.finalize_assistant_turn(
                self.conversation,
                assistant_index=self._active_tool_turn_index,
                content=event["content"],
            )
            if index not in self._turn_assistant_message_indices:
                self._turn_assistant_message_indices.append(index)
            _log_terminal_message("HISTORY", self.layer_name, self.conversation[index])
            self._finalize_turn_history()
            self._current_stream_handler = None
            self._current_reply_id = None
            return event
        return self._handle_brain_response(response, tool_config)

    def submit_user_message(
        self,
        user_input: str,
        tool_config: dict | None = None,
        attach_vision: bool = True,
        memory_source_text: str | None = None,
        stream_handler=None,
        reply_id: str | None = None,
        image_base64: str | None = None,
    ) -> dict:
        if self.has_pending_work():
            raise RuntimeError(f"{self.display_name} cannot accept a new message while tool work is pending.")

        user_text = str(user_input or "").strip()
        self._reset_turn_state(user_text)
        self.last_user_text_for_memory = user_text if memory_source_text is None else str(memory_source_text or "").strip()
        self._current_stream_handler = stream_handler
        self._current_reply_id = reply_id or f"{self.layer_name}-{time.time_ns()}"

        msg = {"role": "user", "content": user_text}
        if image_base64:
            if brain.layer_supports_image_input(tool_config or self.config, self.layer_name):
                msg["images"] = [image_base64]
            else:
                logger.info(
                    "[%s] Ignoring image payload because selected model does not support image input.",
                    self.layer_name,
                )
        self._append_conversation_message(msg)

        shortcut_event = self._maybe_handle_runtime_shortcut(user_text, tool_config)
        if shortcut_event is not None:
            self._current_stream_handler = None
            self._current_reply_id = None
            return shortcut_event

        try:
            response = self._call_brain(tool_config, query_text=user_text, allow_tools=True)
        except Exception:
            self.conversation.pop()
            self._current_stream_handler = None
            self._current_reply_id = None
            raise

        return self._handle_brain_response(response, tool_config)

    def resolve_tool_decision(self, approved: bool, tool_config: dict | None = None) -> dict:
        tool_call = self._current_tool_call()
        if not tool_call:
            raise RuntimeError("No pending tool confirmation to resolve.")

        tool_name = tool_call.function.name
        tool_call_id = tool_call.id
        arguments = self._parse_tool_arguments(tool_call)

        if approved:
            logger.info("[%s] User approved tool: %s", self.layer_name, tool_name)
            result = execute_tool(tool_name, arguments, self.layer_name, config=tool_config or self.config)
            logger.info("[%s] Tool result: %s", self.layer_name, result[:200])
            self._append_tool_result(tool_call_id, result)
            self._record_tool_execution(tool_name, result, tool_args=arguments)
        else:
            logger.info("[%s] User denied tool: %s", self.layer_name, tool_name)
            self._append_tool_result(tool_call_id, "User denied this action.")

        self.pending_tool_index += 1
        return self._process_tool_queue(tool_config)

class LayeredRuntime:
    """Owns the persistent companion plus any active worker layer."""

    def __init__(self):
        self.config = brain.load_config()
        soul_module.ensure_soul_active(self.config)
        memory.init_memory()
        _ensure_schedule_file()
        self._companion_warmup_thread: threading.Thread | None = None
        self._companion_warmup_cancel = threading.Event()

        self.companion_session = LayerSession(self.config, "companion", include_memory=True)
        self.companion_name = self.companion_session.display_name
        self._warm_companion_runtime_async()

        # Check dream trigger conditions in background ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â non-blocking
        dream.maybe_trigger_dream(self.companion_session.client, self.config)

        # Assistant session kept alive for the whole app session so history
        # accumulates across invocations (reset only on app restart or config_reload).
        self.assistant_session = LayerSession(self.config, "assistant", include_memory=True)

        self.active_worker_session: LayerSession | None = None
        self.pending_confirmation_session: LayerSession | None = None

        self.tts = None
        self.stt = None
        self._bridge_emit = None
        self._refresh_voice_engines()
        self._send_initial_keepalive()

    def get_companion_system_prompt(self) -> str:
        return str(self.companion_session.conversation[0].get("content") or "")

    def _warm_voice_engines_async(self) -> None:
        tts_engine = self.tts
        if not tts_engine:
            return

        def _worker() -> None:
            try:
                if tts_engine.is_available():
                    tts_engine._ensure_loaded()
            except Exception:
                logger.debug("Voice warmup failed", exc_info=True)

        self._launch_background_task("voice-warmup", _worker)

    def _refresh_voice_engines(self) -> None:
        voice_config = self.config.get("voice", {})
        self.tts = tts_module.KokoroTTS(self.config) if voice_config.get("tts_provider") == "kokoro" else None
        self.stt = stt_module.WhisperSTT(self.config) if voice_config.get("stt_provider") == "whisper" else None
        self._warm_voice_engines_async()

    def _ollama_model_for_layer(self, layer_name: str) -> str | None:
        """Return the Ollama model name for a layer, or None if not an Ollama layer."""
        brain_cfg = brain.get_layer_brain_config(self.config, layer_name)
        if not is_ollama_backed_provider(str(brain_cfg.get("provider") or "").lower()):
            return None
        return str(brain_cfg.get("model") or "").strip() or None

    def _send_initial_keepalive(self) -> None:
        for layer in ("companion", "assistant"):
            model = self._ollama_model_for_layer(layer)
            if model:
                print(
                    f"[KEEPALIVE] layer={layer} model={model} value={KEEPALIVE_ACTIVE} reason=bridge_ready",
                    file=sys.stderr, flush=True,
                )
                self._launch_background_task(
                    f"keepalive-init-{layer}",
                    set_model_keepalive, model, KEEPALIVE_ACTIVE,
                )

    def _warm_companion_runtime_async(self) -> None:
        config_snapshot = self.config
        client = self.companion_session.client

        def _worker() -> None:
            try:
                memory.preload_retrieval_runtime(config_snapshot, cancel_event=self._companion_warmup_cancel)
            except Exception:
                logger.exception("Memory preload failed")

            if self._companion_warmup_cancel.is_set():
                logger.debug("Skipping companion model warmup because a user turn has started.")
                return

            try:
                provider_name = brain.get_layer_brain_config(config_snapshot, "companion").get("provider")
                if is_ollama_backed_provider(provider_name):
                    client.chat.completions.create(
                        messages=[{"role": "user", "content": "OK"}],
                        stream=False,
                        temperature=0.0,
                        max_tokens=1,
                    )
                else:
                    client.health_check()
            except Exception:
                logger.debug("Companion brain warmup failed", exc_info=True)

        self._companion_warmup_thread = self._launch_background_task("companion-warmup", _worker)

    def _cancel_companion_warmup_for_user_turn(self) -> None:
        thread = self._companion_warmup_thread
        if thread and thread.is_alive():
            self._companion_warmup_cancel.set()

    def set_bridge_emitter(self, emit_func) -> None:
        self._bridge_emit = emit_func

    def _launch_background_task(self, name: str, target, *args) -> threading.Thread:
        thread = threading.Thread(
            target=target,
            args=args,
            name=name,
            daemon=True,
        )
        thread.start()
        return thread

    def _emit_bridge_event(self, payload: dict) -> None:
        if self._bridge_emit is None:
            return
        try:
            self._bridge_emit(payload)
        except Exception:
            logger.exception("Failed to emit bridge event: %s", payload.get("type"))

    def _next_reply_id(self, layer_name: str) -> str:
        return f"{layer_name}-{time.time_ns()}"

    def _refresh_session_runtime(self, session: LayerSession | None) -> None:
        if session is None:
            return
        session.config = self.config
        session.layer_config = brain.get_layer_config(self.config, session.layer_name)
        session.tool_profile = session.layer_config.get("permission_profile", session.layer_name)
        session.display_name = brain.get_layer_display_name(self.config, session.layer_name)
        try:
            session.client = brain.create_client(self.config, session.layer_name)
            session.family_adapter = getattr(getattr(session.client, "provider", None), "family_adapter", get_family_adapter(brain.get_layer_brain_config(self.config, session.layer_name)))
        except Exception:
            logger.exception("Failed to refresh %s client; keeping existing client", session.layer_name)
        try:
            session.reload_identity()
        except Exception:
            logger.exception("Failed to refresh %s identity prompt; keeping existing system prompt", session.layer_name)

    def _refresh_config(self) -> dict:
        self.config = brain.load_config()
        soul_module.ensure_soul_active(self.config)
        self._refresh_session_runtime(self.companion_session)
        self._refresh_session_runtime(self.assistant_session)
        self._refresh_session_runtime(self.active_worker_session)
        self.companion_name = self.companion_session.display_name
        self._refresh_voice_engines()
        return self.config

    def _emit_stream_chunk(self, message_id: str, layer_name: str, chunk: str) -> None:
        clean_chunk = _sanitize_text_for_utf8(chunk)
        if not clean_chunk:
            return
        self._emit_bridge_event({
            "type": "assistant_chunk",
            "message_id": message_id,
            "layer": layer_name,
            "content": clean_chunk,
        })

    def _attach_tts_to_payload(self, payload: dict) -> dict:
        if not payload.get("content"):
            return payload
        tts_engine = self.tts
        if not (tts_engine and tts_engine.is_available()):
            return payload
        try:
            result = tts_engine.synthesize(payload["content"])
        except Exception:
            logger.exception("Assistant TTS synthesis failed")
            return payload
        if not result:
            return payload
        audio_b64, sample_rate = result
        enriched = dict(payload)
        enriched["audio_b64"] = audio_b64
        enriched["sample_rate"] = sample_rate
        return enriched

    def _schedule_memory_save_for_companion(self, event: dict) -> None:
        memory_inputs = self._collect_companion_memory_inputs(event)
        if memory_inputs is None:
            return
        user_msg, reply = memory_inputs
        client = self.companion_session.client
        config_snapshot = self.config

        def _worker(saved_user_msg: str, saved_reply: str, saved_client, saved_config: dict) -> None:
            try:
                self._save_memories_for_companion(saved_user_msg, saved_reply, saved_client, saved_config)
            except Exception:
                logger.exception("Background memory save failed")

        self._launch_background_task("companion-memory-save", _worker, user_msg, reply, client, config_snapshot)

    def _busy(self) -> bool:
        return (
            self.pending_confirmation_session is not None
            or self.companion_session.has_pending_work()
            or self.active_worker_session is not None
        )

    def _start_worker_layer(
        self,
        layer_name: str,
        content: str,
        tool_config: dict | None = None,
        image_base64: str | None = None,
    ) -> list[dict]:
        layer_config = brain.get_layer_config(self.config, layer_name)
        if not layer_config.get("enabled", True):
            raise RuntimeError(f"{brain.get_layer_display_name(self.config, layer_name)} is disabled.")

        # Reuse the persistent assistant session so history accumulates across invocations.
        if layer_name == "assistant":
            self.active_worker_session = self.assistant_session
        else:
            self.active_worker_session = LayerSession(self.config, layer_name, include_memory=True)
        effective_tool_config = tool_config or self.config

        started_event = {
            "type": "assistant_started",
            "layer": layer_name,
            "layer_display_name": brain.get_layer_display_name(self.config, layer_name),
            "state": "thinking",
        }
        worker_event = self.active_worker_session.submit_user_message(
            content,
            tool_config=effective_tool_config,
            attach_vision=True,
            memory_source_text=content,
            image_base64=image_base64,
        )
        return [started_event] + self._handle_session_event(self.active_worker_session, worker_event, effective_tool_config)

    def _finish_worker_session(
        self,
        session: LayerSession,
        event: dict,
    ) -> list[dict]:
        layer_name = session.layer_name
        layer_display_name = brain.get_layer_display_name(self.config, layer_name)

        completion_event = {
            "type": "layer_completed",
            "layer": layer_name,
            "layer_display_name": layer_display_name,
            "state": "idle",
        }

        self.active_worker_session = None
        return [completion_event, self._attach_tts_to_payload(dict(event))]

    def _collect_companion_memory_inputs(self, event: dict) -> tuple[str, str] | None:
        """Capture the exact user/reply pair for background memory extraction."""
        history = self.companion_session.conversation
        # Find the last user message before this reply
        user_msg = ""
        for msg in reversed(history[:-1]):  # skip the assistant message just appended
            if msg.get("role") == "user":
                user_msg = self.companion_session.last_user_text_for_memory or extract_user_text(msg.get("content") or "")
                if user_msg:
                    break

        reply = event.get("content") or ""
        if not user_msg or not reply:
            return None
        return user_msg, reply

    def _save_memories_for_companion(self, user_msg: str, reply: str, client, config: dict) -> None:
        """
        Extract notable facts from the latest companion exchange and write them
        to memory.md. Best-effort and never user-facing.
        """
        try:
            memory.extract_and_save_memories(
                user_msg,
                reply,
                client,
                config,
            )
        except Exception as exc:
            logger.warning("Memory save failed (non-fatal): %s", exc)

        try:
            dream.increment_conversation_count()
        except Exception as exc:
            logger.warning("Dream counter increment failed (non-fatal): %s", exc)

    def _handle_session_event(self, session: LayerSession, event: dict, tool_config: dict | None = None) -> list[dict]:
        event_type = event.get("type")

        if event_type == "tool_confirmation_requested":
            self.pending_confirmation_session = session
            return session._drain_side_events() + [event]

        if event_type != "assistant_message":
            return session._drain_side_events() + [event]

        if session is self.companion_session and VERBOSE_RUNTIME_LOGS:
            print(
                f"[ASSISTANT] {_quote_terminal_text(str(event.get('content', '') or ''))}",
                file=sys.stderr, flush=True,
            )

        event = self._attach_tts_to_payload(event)

        if session is self.companion_session:
            self._schedule_memory_save_for_companion(event)
            try:
                session.maybe_compact_session()
            except Exception:
                logger.warning("Session compaction failed for %s; keeping raw history.", session.layer_name, exc_info=True)
            return session._drain_side_events() + [event]

        try:
            session.maybe_compact_session()
        except Exception:
            logger.warning("Session compaction failed for %s; keeping raw history.", session.layer_name, exc_info=True)
        return session._drain_side_events() + self._finish_worker_session(session, event)

    def submit_user_message(self, user_input: str, image_base64: str | None = None) -> list[dict]:
        if self._busy():
            raise RuntimeError("Cannot accept a new message while layered work is still in progress.")
        self._cancel_companion_warmup_for_user_turn()
        if VERBOSE_RUNTIME_LOGS:
            print(f"[USER] {_quote_terminal_text(user_input)}", file=sys.stderr, flush=True)
        tool_config = self._refresh_config()
        self.companion_session.config = tool_config
        reply_id = self._next_reply_id("companion")
        event = self.companion_session.submit_user_message(
            user_input,
            tool_config=tool_config,
            memory_source_text=user_input,
            stream_handler=lambda chunk, active_reply_id=reply_id: self._emit_stream_chunk(active_reply_id, "companion", chunk),
            reply_id=reply_id,
            image_base64=image_base64,
        )
        return self._handle_session_event(self.companion_session, event, tool_config)

    def invoke_layer(self, layer_name: str, content: str, image_base64: str | None = None) -> list[dict]:
        if self._busy():
            raise RuntimeError("Cannot invoke another layer while work is already in progress.")
        self._cancel_companion_warmup_for_user_turn()
        canonical = brain.normalize_layer_name(layer_name)
        if canonical == "companion":
            return self.submit_user_message(content, image_base64=image_base64)
        if canonical != "assistant":
            raise ValueError(f"Unsupported layer invocation: {layer_name}")
        tool_config = self._refresh_config()
        return self._start_worker_layer(
            canonical,
            content=content,
            tool_config=tool_config,
            image_base64=image_base64,
        )

    def resolve_tool_decision(self, approved: bool) -> list[dict]:
        if not self.pending_confirmation_session:
            raise RuntimeError("No pending tool confirmation to resolve.")
        session = self.pending_confirmation_session
        self.pending_confirmation_session = None
        event = session.resolve_tool_decision(approved, tool_config=self.config)
        return self._handle_session_event(session, event, self.config)

    def reset_session(self, layer_name: str, preserve_summary: bool = True) -> dict:
        if self._busy():
            raise RuntimeError("Cannot reset a session while work is already in progress.")
        canonical = str(layer_name or "").strip().lower()
        targets: list[LayerSession]
        if canonical == "all":
            targets = [self.companion_session, self.assistant_session]
        elif brain.normalize_layer_name(canonical) == "assistant":
            targets = [self.assistant_session]
        else:
            targets = [self.companion_session]
        summary_paths: list[str] = []
        result_layer = canonical or "companion"
        for session in targets:
            result = session.reset_session(preserve_summary=preserve_summary)
            summary_paths.extend(result.get("summary_paths", []))
            result_layer = result.get("layer", result_layer)
        return {
            "layer": canonical if canonical == "all" else result_layer,
            "summary_paths": summary_paths,
            "preserve_summary": bool(preserve_summary),
        }


def confirm_tool_execution(tool_name: str, arguments: dict, layer_display_name: str = "") -> bool:
    """Ask the user to confirm a tool execution in terminal mode."""
    label = f" [{layer_display_name}]" if layer_display_name else ""
    print("\n--- Tool Confirmation ---")
    print(f"  Tool{label}: {tool_name}")
    print(f"  Args: {json.dumps(arguments, indent=2)}")
    print("-------------------------")

    while True:
        answer = input("Approve? (y/n): ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("Please enter y or n.")


def _drain_terminal_events(runtime: LayeredRuntime, events: list[dict]) -> str | None:
    final_reply = None

    while events:
        event = events.pop(0)
        event_type = event.get("type")

        if event_type == "tool_confirmation_requested":
            approved = confirm_tool_execution(
                event["tool_name"],
                event.get("arguments", {}),
                event.get("layer_display_name", ""),
            )
            events = runtime.resolve_tool_decision(approved) + events
            continue

        if event_type == "assistant_message":
            final_reply = event.get("content", "[No response]")
            continue

        if event_type == "error":
            final_reply = event.get("message", "[Unknown error]")
            continue

        if event_type.endswith("_started"):
            label = event.get("layer_display_name") or event.get("layer") or "Worker"
            print(f"[{label} started]")
            continue

        if event_type == "layer_completed":
            label = event.get("layer_display_name") or event.get("layer") or "Worker"
            print(f"[{label} completed]")
            continue

    return final_reply


def run_terminal() -> None:
    runtime = LayeredRuntime()

    print(f"\n{'=' * 50}")
    print("  OpenCompanion - Terminal Mode")
    print(f"  Talking to: {runtime.companion_name}")
    companion_brain = brain.get_layer_brain_config(runtime.config, "companion")
    print(f"  Provider: {companion_brain['provider']} / {companion_brain['model']}")
    print("  Type 'quit' or 'exit' to end")
    print("  Use '/assistant <task>' for direct worker testing")
    print(f"{'=' * 50}\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() in {"quit", "exit"}:
            print(f"\n{runtime.companion_name}: See you later!\n")
            break

        try:
            if user_input.lower().startswith("/assistant "):
                events = runtime.invoke_layer("assistant", user_input[len("/assistant "):].strip())
            else:
                events = runtime.submit_user_message(user_input)
        except Exception as exc:
            print(f"\n[Error: {exc}]\n")
            continue

        reply = _drain_terminal_events(runtime, events)
        if reply:
            print(f"\n{runtime.companion_name}: {reply}\n")


class JsonBridge:
    """
    JSONL transport for the Electron shell.

    Requests:
    - {"type": "user_message", "content": "..."}
    - {"type": "audio_input", "audio_b64": "..."}
    - {"type": "tool_decision", "approved": true|false}
    - {"type": "invoke_layer", "layer": "companion"|"assistant", "content": "..."}
    - {"type": "check_models", "tags": ["gemma4:e4b"]}
    - {"type": "pull_model", "tag": "gemma4:e4b"}
    - {"type": "run_dream"}
    - {"type": "shutdown"}
    """

    def __init__(self):
        self.runtime = LayeredRuntime()
        self._emit_lock = threading.Lock()
        self._scheduler_module = _load_scheduler_module()
        self.runtime.set_bridge_emitter(self.emit)
        self.wrapper_state = {
            "last_interaction_ts": time.time(),
            "runtime_state": "idle",
            "runtime_state_updated_ts": time.time(),
            "companion_system_prompt": self.runtime.get_companion_system_prompt(),
            "companion_client": self.runtime.companion_session.client,
        }
        if not TEST_MODE or SCHEDULER_ENABLED_IN_TEST_MODE:
            self._start_scheduler()
        if heartbeat.get_heartbeat_config(self.runtime.config).get("enabled", False):
            heartbeat.start_heartbeat(self.runtime.config, self._heartbeat_emit, self.wrapper_state)

    def emit(self, payload: dict) -> None:
        with self._emit_lock:
            if isinstance(payload, dict) and payload.get("state"):
                self.wrapper_state["runtime_state"] = str(payload.get("state") or "idle")
                self.wrapper_state["runtime_state_updated_ts"] = time.time()
            safe_payload = _sanitize_json_payload(payload)
            sys.stdout.write(json.dumps(safe_payload, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    def _build_runtime_payload(self, event_type: str = "ready") -> dict:
        return {
            "type": event_type,
            "state": "idle",
            "companion_name": self.runtime.companion_name,
            "config": {
                "ui": self.runtime.config.get("ui", {}),
                "voice": self.runtime.config.get("voice", {}),
                "layers": brain.get_public_layer_config(self.runtime.config),
            },
            "voice_capabilities": {
                "backend_tts": bool(self.runtime.tts and self.runtime.tts.is_available()),
                "backend_stt": bool(self.runtime.stt and self.runtime.stt.is_available()),
            },
        }

    def _normalize_background_event(self, payload: dict | None, default_layer: str = "companion") -> dict:
        event = dict(payload or {})
        if event.get("type") == "assistant_message":
            if "content" not in event and "text" in event:
                event["content"] = event.pop("text")
            event.setdefault("state", "idle")
            event.setdefault("layer", default_layer)
            event.setdefault("message_id", self.runtime._next_reply_id(default_layer))
            event = self.runtime._attach_tts_to_payload(event)
        elif event.get("type") == "reminder_fired":
            prefix = f"[Missed - {event.get('original_time')}] " if event.get("missed") else ""
            event.setdefault("content", f"{prefix}Reminder: {event.get('text', '')}")
            event.setdefault("state", "idle")
            event.setdefault("layer", default_layer)
            event = self.runtime._attach_tts_to_payload(event)
        elif event.get("type") == "reminders_missed_batch":
            items = event.get("items") if isinstance(event.get("items"), list) else []
            lines = "\n".join(
                f"- {item.get('text', '')} (was {item.get('original_time')})"
                for item in items
                if isinstance(item, dict)
            )
            event.setdefault("content", f"You had some reminders while the app was closed:\n{lines}")
            event.setdefault("state", "idle")
            event.setdefault("layer", default_layer)
            event = self.runtime._attach_tts_to_payload(event)
        return event

    def _heartbeat_emit(self, payload: dict) -> None:
        event = self._normalize_background_event(payload, default_layer="companion")
        self.emit(event)

    def _scheduler_emit(self, payload: dict) -> None:
        event = self._normalize_background_event(payload, default_layer="companion")
        self.emit(event)

    def _emit_events(self, events: list[dict]) -> None:
        for event in events:
            self.emit(event)

    def _refresh_wrapper_state(self) -> None:
        self.wrapper_state["companion_system_prompt"] = self.runtime.get_companion_system_prompt()
        self.wrapper_state["companion_client"] = self.runtime.companion_session.client

    def _start_scheduler(self) -> None:
        _ensure_schedule_file()
        _invoke_optional_scheduler_hook(
            self._scheduler_module,
            ("start_scheduler", "startup_scheduler", "start"),
            config=self.runtime.config,
            emit_fn=self._scheduler_emit,
            emit=self._scheduler_emit,
            wrapper_state=self.wrapper_state,
            state=self.wrapper_state,
        )

    def _reload_scheduler(self) -> None:
        if TEST_MODE and not SCHEDULER_ENABLED_IN_TEST_MODE:
            self._shutdown_scheduler()
            return
        _ensure_schedule_file()
        reloaded = _invoke_optional_scheduler_hook(
            self._scheduler_module,
            ("reload_scheduler", "restart_scheduler", "reload", "restart"),
            config=self.runtime.config,
            emit_fn=self._scheduler_emit,
            emit=self._scheduler_emit,
            wrapper_state=self.wrapper_state,
            state=self.wrapper_state,
        )
        if reloaded:
            return
        self._shutdown_scheduler()
        self._start_scheduler()

    def _shutdown_scheduler(self) -> None:
        _invoke_optional_scheduler_hook(
            self._scheduler_module,
            ("stop_scheduler", "shutdown_scheduler", "stop", "shutdown"),
            config=self.runtime.config,
            emit_fn=self._scheduler_emit,
            emit=self._scheduler_emit,
            wrapper_state=self.wrapper_state,
            state=self.wrapper_state,
        )

    def _shutdown_heartbeat(self) -> None:
        try:
            heartbeat.stop_heartbeat()
        except Exception:
            logger.exception("Failed to stop heartbeat cleanly during shutdown")

    def handle_request(self, payload: dict) -> bool:
        request_type = payload.get("type")

        if request_type == "shutdown":
            self._shutdown_scheduler()
            self._shutdown_heartbeat()
            self.emit({"type": "idle", "state": "idle"})
            return False

        if request_type == "user_message":
            content = (payload.get("content") or "").strip()
            if not content:
                raise ValueError("user_message requires non-empty content")
            image_base64 = payload.get("image_base64") or None
            self.wrapper_state["last_interaction_ts"] = time.time()
            self.emit({"type": "busy", "state": "thinking"})
            events = self.runtime.submit_user_message(content, image_base64=image_base64)
            self._refresh_wrapper_state()
            self._emit_events(events)
            if events and events[-1]["type"] == "assistant_message":
                self.wrapper_state["last_interaction_ts"] = time.time()
                self.emit({"type": "idle", "state": "idle"})
            return True

        if request_type == "invoke_layer":
            layer_name = str(payload.get("layer") or "").strip()
            content = (payload.get("content") or "").strip()
            if not layer_name or not content:
                raise ValueError("invoke_layer requires non-empty layer and content")
            image_base64 = payload.get("image_base64") or None
            self.wrapper_state["last_interaction_ts"] = time.time()
            self.emit({"type": "busy", "state": "thinking"})
            events = self.runtime.invoke_layer(layer_name, content, image_base64=image_base64)
            self._refresh_wrapper_state()
            self._emit_events(events)
            if events and events[-1]["type"] == "assistant_message":
                self.wrapper_state["last_interaction_ts"] = time.time()
                self.emit({"type": "idle", "state": "idle"})
            return True

        if request_type == "audio_input":
            audio_b64 = (payload.get("audio_b64") or "").strip()
            if not audio_b64:
                raise ValueError("audio_input requires non-empty audio_b64")
            if not self.runtime.stt or not self.runtime.stt.is_available():
                self.emit({
                    "type": "stt_error",
                    "message": "Backend STT is not available.",
                    "state": "idle",
                })
                self.emit({"type": "idle", "state": "idle"})
                return True

            self.emit({"type": "busy", "state": "thinking"})
            transcript = self.runtime.stt.transcribe_b64(audio_b64)
            if not transcript:
                self.emit({
                    "type": "stt_error",
                    "message": "Could not transcribe audio.",
                    "state": "idle",
                })
                self.emit({"type": "idle", "state": "idle"})
                return True

            self.emit({"type": "stt_result", "text": transcript})
            self.wrapper_state["last_interaction_ts"] = time.time()
            events = self.runtime.submit_user_message(transcript)
            self._refresh_wrapper_state()
            self._emit_events(events)
            if events and events[-1]["type"] == "assistant_message":
                self.wrapper_state["last_interaction_ts"] = time.time()
                self.emit({"type": "idle", "state": "idle"})
            return True

        if request_type == "tool_decision":
            approved = bool(payload.get("approved"))
            self.emit({"type": "busy", "state": "resuming"})
            events = self.runtime.resolve_tool_decision(approved)
            self._refresh_wrapper_state()
            self._emit_events(events)
            if events and events[-1]["type"] == "assistant_message":
                self.wrapper_state["last_interaction_ts"] = time.time()
                self.emit({"type": "idle", "state": "idle"})
            return True

        if request_type == "heartbeat_restart":
            self.runtime._refresh_config()
            self._refresh_wrapper_state()
            heartbeat.restart_heartbeat(self.runtime.config, self._heartbeat_emit, self.wrapper_state)
            return True

        if request_type == "heartbeat_start":
            self.runtime._refresh_config()
            self._refresh_wrapper_state()
            heartbeat.start_heartbeat(self.runtime.config, self._heartbeat_emit, self.wrapper_state)
            return True

        if request_type == "heartbeat_stop":
            heartbeat.stop_heartbeat()
            return True

        if request_type == "config_reload":
            self.runtime._refresh_config()
            self._refresh_wrapper_state()
            self._reload_scheduler()
            self.emit(self._build_runtime_payload("config_reloaded"))
            return True

        if request_type == "check_models":
            raw_models = payload.get("tags", payload.get("models"))
            if isinstance(raw_models, str):
                requested_models = [raw_models]
            elif isinstance(raw_models, list):
                requested_models = [str(item or "").strip() for item in raw_models]
            else:
                requested_models = [
                    model for model in (
                        self.runtime._ollama_model_for_layer("companion"),
                        self.runtime._ollama_model_for_layer("assistant"),
                    )
                    if model
                ]

            seen_models: set[str] = set()
            clean_models: list[str] = []
            for model in requested_models:
                if not model:
                    continue
                model_name = _validate_ollama_model_name(model)
                key = _normalize_ollama_model_key(model_name)
                if key in seen_models:
                    continue
                seen_models.add(key)
                clean_models.append(model_name)

            available_models = list_ollama_models()
            present = _ollama_present_flags(clean_models, available_models)
            result = {
                "type": "check_models_result",
                "models": [
                    {"tag": model, "name": model, "present": bool(present.get(model))}
                    for model in clean_models
                ],
                "present": present,
                "available_models": available_models,
                "state": "idle",
            }
            request_id = str(payload.get("request_id") or "").strip()
            if request_id:
                result["request_id"] = request_id
            self.emit(result)
            self.emit({"type": "idle", "state": "idle"})
            return True

        if request_type == "pull_model":
            model_name = _validate_ollama_model_name(payload.get("tag") or payload.get("model") or payload.get("name") or "")
            request_id = str(payload.get("request_id") or "").strip() or None
            self.emit({
                "type": "busy",
                "state": "pulling",
                "model": model_name,
                **({"request_id": request_id} if request_id else {}),
            })
            try:
                pull_ollama_model(model_name, self.emit, request_id=request_id)
                self.emit({
                    "type": "pull_model_result",
                    "tag": model_name,
                    "model": model_name,
                    "success": True,
                    "state": "idle",
                    **({"request_id": request_id} if request_id else {}),
                })
                self.emit({
                    "type": "pull_progress",
                    "tag": model_name,
                    "model": model_name,
                    "status": "success",
                    "percent": 100,
                    "state": "idle",
                    **({"request_id": request_id} if request_id else {}),
                })
            except Exception as exc:
                logger.exception("Ollama model pull failed")
                self.emit({
                    "type": "pull_progress",
                    "tag": model_name,
                    "model": model_name,
                    "status": "error",
                    "error": str(exc),
                    "state": "idle",
                    **({"request_id": request_id} if request_id else {}),
                })
                self.emit({
                    "type": "pull_model_result",
                    "tag": model_name,
                    "model": model_name,
                    "success": False,
                    "error": str(exc),
                    "state": "idle",
                    **({"request_id": request_id} if request_id else {}),
                })
            self.emit({"type": "idle", "state": "idle"})
            return True

        if request_type == "run_dream":
            self.runtime._refresh_config()
            self._refresh_wrapper_state()
            self.emit({"type": "busy", "state": "thinking"})
            try:
                dream.run_dream(self.runtime.companion_session.client, self.runtime.config)
                self._refresh_wrapper_state()
                self.emit({"type": "dream_completed", "state": "idle"})
            except Exception as exc:
                logger.exception("Dream run failed")
                self.emit({
                    "type": "dream_failed",
                    "message": str(exc),
                    "state": "idle",
                })
            self.emit({"type": "idle", "state": "idle"})
            return True

        if request_type == "session_reset":
            layer_name = str(payload.get("layer") or "companion").strip().lower() or "companion"
            preserve_summary = bool(payload.get("preserve_summary", True))
            result = self.runtime.reset_session(layer_name, preserve_summary=preserve_summary)
            self._refresh_wrapper_state()
            self.emit({
                "type": "session_reset",
                "layer": result.get("layer", layer_name),
                "summary_paths": result.get("summary_paths", []),
                "preserve_summary": preserve_summary,
                "state": "idle",
            })
            self.emit({"type": "idle", "state": "idle"})
            return True

        if request_type == "dnd_start":
            until = float(payload.get("until") or 0)
            if until <= time.time():
                raise ValueError("dnd_start: 'until' must be a future Unix timestamp")
            heartbeat.set_dnd(until)
            for layer in ("companion", "assistant"):
                model = self.runtime._ollama_model_for_layer(layer)
                if model:
                    print(
                        f"[KEEPALIVE] layer={layer} model={model} value={KEEPALIVE_UNLOAD} reason=dnd_start",
                        file=sys.stderr, flush=True,
                    )
                    self.runtime._launch_background_task(
                        f"keepalive-dnd-unload-{layer}",
                        set_model_keepalive, model, KEEPALIVE_UNLOAD,
                    )
            return True

        if request_type == "dnd_cancel":
            heartbeat.cancel_dnd()
            for layer in ("companion", "assistant"):
                model = self.runtime._ollama_model_for_layer(layer)
                if model:
                    print(
                        f"[KEEPALIVE] layer={layer} model={model} value={KEEPALIVE_ACTIVE} reason=dnd_cancel",
                        file=sys.stderr, flush=True,
                    )
                    self.runtime._launch_background_task(
                        f"keepalive-dnd-restore-{layer}",
                        set_model_keepalive, model, KEEPALIVE_ACTIVE,
                    )
            return True

        raise ValueError(f"Unknown request type: {request_type}")

    def run(self) -> None:
        self.emit(self._build_runtime_payload())

        try:
            for line in sys.stdin:
                raw = line.strip()
                if not raw:
                    continue

                try:
                    payload = json.loads(raw)
                    should_continue = self.handle_request(payload)
                except Exception as exc:
                    logger.exception("JSON bridge error")
                    self.emit({"type": "error", "message": str(exc), "state": "error"})
                    self.emit({"type": "idle", "state": "idle"})
                    should_continue = True

                if not should_continue:
                    break
        finally:
            self._shutdown_scheduler()
            self._shutdown_heartbeat()


def parse_args():
    parser = argparse.ArgumentParser(description="OpenCompanion backend wrapper")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Run the JSONL stdio bridge instead of terminal mode.",
    )
    parser.add_argument(
        "--generate-soul-json",
        type=str,
        help="Generate soul files from a JSON config payload, then exit.",
    )
    parser.add_argument(
        "--preserve-companion",
        action="store_true",
        help="When generating soul files, preserve an existing soul_companion.md file.",
    )
    parser.add_argument(
        "--prefetch-stt",
        action="store_true",
        help="Download and initialize the configured whisper model, then exit.",
    )
    return parser.parse_args()


def prefetch_stt() -> int:
    config = brain.load_config()
    stt = stt_module.WhisperSTT(config)
    if not stt.is_available():
        logger.warning("Whisper STT is not available (pywhispercpp not installed)")
        return 1

    stt._ensure_loaded()
    logger.info("Whisper STT prefetch complete")
    return 0


def generate_soul_from_json(raw_json: str, preserve_companion: bool = False) -> int:
    config = json.loads(raw_json)
    skip_existing = {"soul_companion.md"} if preserve_companion else None
    soul_module.generate_soul_files(config, skip_existing=skip_existing)
    logger.info("Soul files generated successfully")
    return 0


if __name__ == "__main__":
    args = parse_args()
    if args.generate_soul_json:
        sys.exit(generate_soul_from_json(args.generate_soul_json, args.preserve_companion))
    if args.prefetch_stt:
        sys.exit(prefetch_stt())
    if args.json:
        logging.getLogger().setLevel(logging.WARNING)
        JsonBridge().run()
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        finally:
            os._exit(0)
    else:
        run_terminal()
