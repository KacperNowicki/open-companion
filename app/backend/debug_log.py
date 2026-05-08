from __future__ import annotations

import json
import os
import traceback
from datetime import datetime, timezone
from typing import Any

from runtime_paths import PROJECT_ROOT

DEBUG_ENABLED = os.environ.get("OPENCOMPANION_DEBUG") == "1"
DEBUG_TOOL_SCHEMA_ENABLED = os.environ.get("OPEN_COMPANION_DEBUG_TOOL_SCHEMA") == "1"
DEBUG_LOG_PATH = PROJECT_ROOT / "logs" / "debug.log"
DEBUG_ROTATE_BYTES = 10 * 1024 * 1024


def _payload_to_jsonable(payload: Any) -> Any:
    if isinstance(payload, str):
        stripped = payload.strip()
        if stripped:
            try:
                return json.loads(stripped)
            except Exception:
                return payload
        return payload
    try:
        json.dumps(payload, ensure_ascii=False, default=str)
        return payload
    except Exception:
        return str(payload)


def _message_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
            elif item:
                parts.append(str(item))
        return "\n".join(parts).strip()
    if isinstance(value, dict):
        return _message_text(value.get("content") or value.get("text") or "")
    return ""


def _extract_system_and_message(payload: Any) -> tuple[str, str]:
    body = payload
    if isinstance(body, dict) and isinstance(body.get("body"), dict):
        body = body.get("body")
    if not isinstance(body, dict):
        return "", ""

    system = str(body.get("instructions") or "").strip()
    message = ""

    messages = body.get("messages")
    if isinstance(messages, list):
        for item in messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            text = _message_text(item.get("content"))
            if role == "system" and text and not system:
                system = text
            elif role in {"user", "assistant", "tool"} and text:
                message = text

    inputs = body.get("input")
    if isinstance(inputs, list):
        for item in inputs:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or item.get("type") or "").strip().lower()
            text = _message_text(item.get("content") or item.get("output") or item.get("text"))
            if role in {"user", "message", "function_call_output"} and text:
                message = text

    contents = body.get("contents")
    if isinstance(contents, list):
        for item in contents:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            parts = item.get("parts")
            text = _message_text(parts)
            if role == "user" and text:
                message = text

    return system, message


def _is_streaming_ollama_chunk(event_type: str, payload: Any) -> bool:
    if event_type != "OLLAMA_RAW":
        return False
    data = _payload_to_jsonable(payload)
    if not isinstance(data, dict):
        return False
    return data.get("done") is False


def debug_log(event_type: str, payload: Any) -> None:
    if not DEBUG_ENABLED and not DEBUG_TOOL_SCHEMA_ENABLED:
        return
    if _is_streaming_ollama_chunk(event_type, payload):
        return
    try:
        DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if DEBUG_LOG_PATH.exists() and DEBUG_LOG_PATH.stat().st_size >= DEBUG_ROTATE_BYTES:
            DEBUG_LOG_PATH.replace(DEBUG_LOG_PATH.with_name("debug.log.1"))
        timestamp = datetime.now(timezone.utc).isoformat()
        normalized_payload = _payload_to_jsonable(payload)
        system, message = _extract_system_and_message(normalized_payload)
        entry = {
            "timestamp": timestamp,
            "event": str(event_type),
            "system": system,
            "message": message,
            "payload": normalized_payload,
        }
        with DEBUG_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def debug_log_error(exc: BaseException, context: dict | None = None) -> None:
    if not DEBUG_ENABLED:
        return
    debug_log(
        "ERROR",
        {
            "context": context or {},
            "exception": repr(exc),
            "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        },
    )


def debug_tool_schema(event_type: str, payload: Any) -> None:
    if not DEBUG_TOOL_SCHEMA_ENABLED:
        return
    debug_log(event_type, payload)
