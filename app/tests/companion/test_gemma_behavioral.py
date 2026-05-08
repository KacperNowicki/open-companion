#!/usr/bin/env python3
"""
Gemma live behavioral test suite.

Runs 12 graded assignments against a live local Ollama Gemma model through the
companion bridge. Every step is logged exhaustively (SEND / RECV / TOOLS /
RESULT / REASON / TIME) so failures are diagnosable without re-running.

Invoke from app/tests/companion/run.py with the --gemma-behavioral flag.
Use --behavioral-list to list assignments and --behavioral-assignment to run
one focused subset during iteration.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
import uuid
from pathlib import Path
from site import getusersitepackages
from typing import Any

from tests.companion.conftest import (
    BOLD,
    GREEN,
    OLLAMA_URL,
    RED,
    RESET,
    ROOT,
    SuiteSkip,
    YELLOW,
    Companion,
    _ollama_models,
    build_profile_config,
    resolve_chat_model,
)

for _dependency_path in (ROOT / ".pytest-deps", ROOT / ".python-deps", Path(getusersitepackages())):
    if _dependency_path.exists() and str(_dependency_path) not in sys.path:
        sys.path.insert(0, str(_dependency_path))

PROFILE_NAME = "gemma-behavioral"
TEMP_PROFILE_BASE = ROOT / "app" / "tests" / "companion" / ".tmp-profiles"
DEFAULT_TURN_TIMEOUT = 600
KNOWN_TOOL_NAMES = {
    "add_reminder",
    "add_recurring_reminder",
    "append_daily_note",
    "create_reminder",
    "edit_file",
    "list_files",
    "read_file",
    "run_terminal",
    "run_windows_terminal",
    "schedule_reminder",
    "search_memories",
    "snooze_reminder",
    "tree_vault",
    "write_file",
    "write_memory",
}


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class BehavioralLogger:
    """Exhaustive per-assignment logger. No bare asserts, no silent paths."""

    def __init__(self, file_handle: Any | None = None) -> None:
        self.results: list[dict] = []
        self._current: dict | None = None
        self._turn_index = 0
        self._file_handle = file_handle

    def _emit(self, text: str = "") -> None:
        print(_safe_console_text(text), flush=True)
        if self._file_handle is not None:
            self._file_handle.write(f"{text}\n")
            self._file_handle.flush()

    def begin(self, number: int, name: str, layer: str) -> None:
        self._current = {
            "number": number,
            "name": name,
            "layer": layer,
            "start": time.monotonic(),
            "turns": [],
            "passed": None,
            "reason": "",
        }
        self._turn_index = 0
        self._emit()
        self._emit(f"{BOLD}[ASSIGNMENT {number}] {name}{RESET}  (layer={layer})")

    def begin_turn(self) -> int:
        self._turn_index += 1
        if self._current is not None:
            self._current["turns"].append({"turn": self._turn_index, "send": "", "events": []})
        self._emit(f"  {BOLD}[TURN {self._turn_index}]{RESET}")
        return self._turn_index

    def log_send(self, message: str) -> None:
        truncated = message if len(message) <= 4000 else message[:4000] + f"... <truncated {len(message)-4000} chars>"
        if self._current is not None and self._current["turns"]:
            self._current["turns"][-1]["send"] = message
        self._emit(f"    SEND    -> {truncated}")

    def log_recv(self, events: list[dict]) -> None:
        if self._current is not None and self._current["turns"]:
            self._current["turns"][-1]["events"] = list(events)
        try:
            pretty = json.dumps(events, indent=2, ensure_ascii=False, default=str)
        except Exception:
            pretty = repr(events)
        if len(pretty) > 6000:
            pretty = pretty[:6000] + f"\n... <truncated {len(pretty)-6000} chars>"
        self._emit(f"    RECV    <- {pretty}")

    def log_tools(self, events: list[dict]) -> list[dict]:
        tool_events = _collect_tool_calls(events)
        if not tool_events:
            self._emit("    TOOLS   -> (none)")
            return tool_events
        self._emit("    TOOLS   ->")
        for index, event in enumerate(tool_events, start=1):
            name = event.get("tool_name") or "?"
            args = _tool_arguments(event)
            try:
                args_text = json.dumps(args, ensure_ascii=False, default=str)
            except Exception:
                args_text = repr(args)
            if len(args_text) > 800:
                args_text = args_text[:800] + f"... <truncated {len(args_text)-800} chars>"
            source = str(event.get("source") or "tool_executed")
            self._emit(f"      {index}. {name}  args={args_text}  source={source}")
        return tool_events

    def log_file(self, label: str, path: Path) -> None:
        if not path.exists():
            self._emit(f"    FILE    -> {label}: {path}  (does not exist)")
            return
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as exc:
            self._emit(f"    FILE    -> {label}: {path}  (unreadable: {exc})")
            return
        snippet = content if len(content) <= 3000 else content[:3000] + f"\n... <truncated {len(content)-3000} chars>"
        self._emit(f"    FILE    -> {label}: {path}  (bytes={len(content.encode('utf-8'))}, lines={len(content.splitlines())})")
        for line in snippet.splitlines():
            self._emit(f"               | {line}")

    def log_note(self, text: str) -> None:
        for line in str(text).splitlines() or [""]:
            self._emit(f"    NOTE    -> {line}")

    def finish(self, passed: bool, reason: str) -> tuple[bool, str, float]:
        if self._current is None:
            return passed, reason, 0.0
        elapsed = time.monotonic() - self._current["start"]
        self._current["passed"] = passed
        self._current["reason"] = reason
        self._current["elapsed"] = elapsed
        self.results.append(self._current)
        status = f"{GREEN}PASS{RESET}" if passed else f"{RED}FAIL{RESET}"
        self._emit(f"    RESULT  -> {status}")
        self._emit(f"    REASON  -> {reason}")
        self._emit(f"    TIME    -> {elapsed:.1f}s")
        if not passed:
            self._dump_failure_context()
        self._current = None
        return passed, reason, elapsed

    def _dump_failure_context(self) -> None:
        if self._current is None:
            return
        self._emit(f"    {RED}FAILURE CONTEXT{RESET}")
        for turn in self._current["turns"]:
            self._emit(f"      [TURN {turn['turn']}] sent: {turn['send'][:400]}")
            replies = [event for event in turn["events"] if event.get("type") == "assistant_message"]
            last = str(replies[-1].get("content") or "") if replies else "(no assistant_message)"
            self._emit(f"      [TURN {turn['turn']}] last assistant reply:")
            for line in last.splitlines() or [""]:
                self._emit(f"        | {line}")


def _assistant_response_text(events: list[dict]) -> str:
    replies = [event for event in events if event.get("type") == "assistant_message"]
    return _clean_reply_text(str(replies[-1].get("content") or "")) if replies else ""


def _all_response_text(events: list[dict]) -> str:
    return "\n".join(_clean_reply_text(str(event.get("content") or "")) for event in events if event.get("type") == "assistant_message")


def _emit_line(file_handle: Any | None, text: str = "", *, stream: Any = sys.stdout) -> None:
    print(_safe_console_text(text), file=stream, flush=True)
    if file_handle is not None:
        file_handle.write(f"{text}\n")
        file_handle.flush()


def _safe_console_text(text: str) -> str:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return str(text or "").encode(encoding, errors="replace").decode(encoding, errors="replace")


def _clean_reply_text(text: str) -> str:
    cleaned = str(text or "")
    if cleaned.lower().startswith("thought\n"):
        blank_break = cleaned.find("\n\n")
        if blank_break != -1:
            cleaned = cleaned[blank_break + 2:]
    last_channel = cleaned.rfind("<channel|>")
    if last_channel != -1:
        cleaned = cleaned[last_channel + len("<channel|>"):]
    cleaned = cleaned.replace("<|channel|>", " ").replace("<channel|>", " ")
    cleaned = cleaned.replace("<|channel>", " ").replace("|channel>", " ").replace("<channel|", " ")
    return cleaned.strip()


def _normalize_tool_args(args: Any) -> dict:
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        text = args.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except Exception:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _tool_arguments(event: dict) -> dict:
    args = event.get("tool_args")
    if isinstance(args, dict):
        return args
    args = event.get("arguments")
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        parsed = _normalize_tool_args(args)
        if parsed:
            return parsed
    args = event.get("args")
    if isinstance(args, dict):
        return args
    function = event.get("function")
    if isinstance(function, dict):
        parsed = _normalize_tool_args(function.get("arguments"))
        if parsed:
            return parsed
    return {}


def _tool_result_text(event: dict) -> str:
    return str(event.get("result") or "")


def _tool_event_text(event: dict) -> str:
    parts = []
    args = _tool_arguments(event)
    if args:
        try:
            parts.append(json.dumps(args, ensure_ascii=False, default=str))
        except Exception:
            parts.append(repr(args))
    result = _tool_result_text(event)
    if result:
        parts.append(result)
    return "\n".join(parts).lower()


def _normalize_tool_call_payload(payload: Any) -> dict | None:
    if not isinstance(payload, dict):
        return None
    tool_name = str(payload.get("tool_name") or payload.get("name") or "").strip()
    tool_args = payload.get("tool_args")
    function = payload.get("function")
    if isinstance(function, dict):
        tool_name = tool_name or str(function.get("name") or "").strip()
        if tool_args is None:
            tool_args = function.get("arguments")
    if tool_args is None:
        tool_args = payload.get("arguments")
    if tool_args is None:
        tool_args = payload.get("args")
    return {
        "tool_name": tool_name,
        "tool_args": _normalize_tool_args(tool_args),
        "result": _tool_result_text(payload),
        "source": "raw_recv",
    } if tool_name else None


def _extract_embedded_tool_calls(node: Any) -> list[dict]:
    found: list[dict] = []
    if isinstance(node, dict):
        tool_calls = node.get("tool_calls")
        if isinstance(tool_calls, list):
            for item in tool_calls:
                normalized = _normalize_tool_call_payload(item)
                if normalized is not None:
                    found.append(normalized)
        for value in node.values():
            found.extend(_extract_embedded_tool_calls(value))
        return found
    if isinstance(node, list):
        for item in node:
            found.extend(_extract_embedded_tool_calls(item))
        return found
    if isinstance(node, str) and "tool_calls" in node and "\"name\"" in node:
        try:
            parsed = json.loads(node)
        except Exception:
            return found
        found.extend(_extract_embedded_tool_calls(parsed))
    return found


def _collect_tool_calls(events: list[dict]) -> list[dict]:
    collected: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()

    def _append(name: str, args: dict, result: str, source: str) -> None:
        tool_name = str(name or "").strip()
        if not tool_name:
            return
        try:
            args_key = json.dumps(args or {}, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            args_key = repr(args)
        key = (tool_name, args_key, str(result or ""), source)
        if key in seen:
            return
        seen.add(key)
        collected.append({
            "type": "tool_executed",
            "tool_name": tool_name,
            "tool_args": args or {},
            "result": str(result or ""),
            "source": source,
        })

    for event in events:
        if event.get("type") == "tool_executed":
            _append(
                str(event.get("tool_name") or ""),
                _tool_arguments(event),
                _tool_result_text(event),
                "tool_executed",
            )

    for event in _extract_embedded_tool_calls(events):
        _append(
            str(event.get("tool_name") or ""),
            _tool_arguments(event),
            _tool_result_text(event),
            str(event.get("source") or "raw_recv"),
        )

    return collected


def _events_blob(events: list[dict]) -> str:
    try:
        return json.dumps(events, ensure_ascii=False, default=str).lower()
    except Exception:
        return repr(events).lower()


def _raw_tool_name_present(events: list[dict], tool_name: str) -> bool:
    blob = _events_blob(events)
    normalized = re.escape(tool_name.lower())
    return bool(re.search(rf"(?<![a-z0-9_]){normalized}(?![a-z0-9_])", blob))


def _file_contains(path: Path, needle: str) -> bool:
    if not path.exists():
        return False
    try:
        return needle.lower() in path.read_text(encoding="utf-8").lower()
    except Exception:
        return False


def _vault_files_containing(profile_root: Path, needle: str) -> list[Path]:
    hits: list[Path] = []
    vault = _vault_dir(profile_root)
    if not vault.exists():
        return hits
    lowered = needle.lower()
    for candidate in vault.rglob("*"):
        if not candidate.is_file():
            continue
        try:
            body = candidate.read_text(encoding="utf-8")
        except Exception:
            continue
        if lowered in body.lower():
            hits.append(candidate)
    return hits


def _normalize_reply_text(text: str) -> str:
    normalized = _clean_reply_text(text)
    normalized = normalized.replace("<|assistant|>", " ").replace("<assistant|>", " ")
    return normalized.lower()


# ---------------------------------------------------------------------------
# Conversation helpers
# ---------------------------------------------------------------------------

def _send_companion(companion: Companion, message: str, logger: BehavioralLogger) -> list[dict]:
    logger.begin_turn()
    logger.log_send(message)
    events = companion.start_user_turn(message, timeout=DEFAULT_TURN_TIMEOUT)
    events = _drain_confirmations(companion, events, approve=True)
    logger.log_recv(events)
    logger.log_tools(events)
    return events


def _send_assistant(companion: Companion, message: str, logger: BehavioralLogger) -> list[dict]:
    logger.begin_turn()
    logger.log_send(message)
    events = companion.invoke_layer("assistant", message, timeout=DEFAULT_TURN_TIMEOUT)
    events = _drain_confirmations(companion, events, approve=True)
    logger.log_recv(events)
    logger.log_tools(events)
    return events


def _drain_confirmations(companion: Companion, events: list[dict], approve: bool) -> list[dict]:
    combined = list(events)
    while any(event.get("type") == "tool_confirmation_requested" for event in combined[-10:]):
        follow_up = companion.resolve_confirmation(approve, timeout=DEFAULT_TURN_TIMEOUT)
        combined.extend(follow_up)
        if not any(event.get("type") == "tool_confirmation_requested" for event in follow_up):
            break
    return combined


# ---------------------------------------------------------------------------
# Vault / memory helpers
# ---------------------------------------------------------------------------

def _vault_dir(profile_root: Path) -> Path:
    return profile_root / "companion" / "vault"


def _memory_path(profile_root: Path) -> Path:
    return profile_root / "companion" / "memory" / "memory.md"


def _seed_file(profile_root: Path, relative: str, content: str) -> Path:
    target = _vault_dir(profile_root) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def _seed_memory(profile_root: Path, content: str) -> None:
    path = _memory_path(profile_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Assignments
# ---------------------------------------------------------------------------

class Assignment:
    number: int = 0
    name: str = ""
    layer: str = "companion"

    def setup(self, profile_root: Path, logger: BehavioralLogger) -> None:
        return None

    def run(self, companion: Companion, profile_root: Path, logger: BehavioralLogger) -> tuple[bool, str]:
        raise NotImplementedError


class A01_BasicPrompt(Assignment):
    number = 1
    name = "Basic prompt response"
    layer = "companion"

    def run(self, companion, profile_root, logger):
        events = _send_companion(companion, "Hey, what's 2 + 2?", logger)
        reply = _assistant_response_text(events)
        lowered = reply.lower()
        contains_four = "4" in reply or "four" in lowered
        tool_calls = [event for event in events if event.get("type") == "tool_executed"]
        no_bullets = ("\n- " not in reply) and ("\n* " not in reply) and not reply.lstrip().startswith(("- ", "* "))
        no_asterisks = "**" not in reply and "*" not in reply.replace("**", "")
        if not reply.strip():
            return False, "empty assistant reply"
        if not contains_four:
            return False, f"reply did not contain 4 / four: {reply!r}"
        if tool_calls:
            return False, f"unexpected tool calls: {[event.get('tool_name') for event in tool_calls]}"
        if not no_bullets:
            return False, "reply contains markdown bullet points"
        if not no_asterisks:
            return False, "reply contains markdown asterisks"
        return True, "answered '4' as plain prose, no tool calls"


class A02_WriteMemory(Assignment):
    number = 2
    name = "Tool invocation: write memory"
    layer = "companion"

    def run(self, companion, profile_root, logger):
        events = _send_companion(companion, "Remember that my favorite color is electric blue.", logger)
        tool_events = _collect_tool_calls(events)
        write_events = [event for event in tool_events if event.get("tool_name") == "write_memory"]
        memory_path = _memory_path(profile_root)
        logger.log_file("memory.md after turn", memory_path)
        if not memory_path.exists():
            return False, "memory.md was not created"
        body = memory_path.read_text(encoding="utf-8").lower()
        if "electric blue" not in body:
            return False, "memory.md does not contain 'electric blue' after turn"
        raw_write_memory = _raw_tool_name_present(events, "write_memory")
        if not write_events and not raw_write_memory:
            return False, f"write_memory was never detected. Tools: {[event.get('tool_name') for event in tool_events]}"
        if write_events:
            arg_blob = " ".join(_tool_event_text(event) for event in write_events)
            if arg_blob and "electric blue" not in arg_blob and "electric blue" not in body:
                return False, f"write_memory detected but neither tool args/result nor disk contained 'electric blue': {arg_blob}"
        return True, "write_memory called and memory.md persisted 'electric blue'"


class A03_MemoryRecall(Assignment):
    number = 3
    name = "Memory recall across turns"
    layer = "companion"

    def run(self, companion, profile_root, logger):
        events1 = _send_companion(companion, "My dog's name is Biscuit. Please remember that.", logger)
        tool_events1 = _collect_tool_calls(events1)
        wrote_memory = any(event.get("tool_name") == "write_memory" for event in tool_events1) or _raw_tool_name_present(events1, "write_memory")
        if not wrote_memory:
            return False, f"turn 1 did not call write_memory. tools: {[event.get('tool_name') for event in tool_events1]}"
        memory_path = _memory_path(profile_root)
        logger.log_file("memory.md after turn 1", memory_path)
        events2 = _send_companion(companion, "What's my dog's name?", logger)
        reply2 = _assistant_response_text(events2)
        if "biscuit" not in reply2.lower():
            return False, f"turn 2 reply did not contain 'Biscuit': {reply2!r}"
        return True, "wrote memory in turn 1 and recalled 'Biscuit' in turn 2"


class A04_ReadFile(Assignment):
    number = 4
    name = "Basic assistant tool: read file"
    layer = "assistant"

    def setup(self, profile_root, logger):
        path = _seed_file(profile_root, "Projects/hello.txt", "Hello from the vault")
        logger.log_file("seeded hello.txt", path)

    def run(self, companion, profile_root, logger):
        events = _send_assistant(companion, "Read the file Projects/hello.txt and tell me what it says.", logger)
        tool_events = _collect_tool_calls(events)
        read_events = [event for event in tool_events if event.get("tool_name") == "read_file"]
        target = _vault_dir(profile_root) / "Projects" / "hello.txt"
        disk_has_content = _file_contains(target, "Hello from the vault")
        matched_read = any(
            "projects/hello.txt" in _tool_event_text(event) or "hello from the vault" in _tool_event_text(event)
            for event in read_events
        )
        if not read_events:
            return False, f"read_file was never called. tools: {[(event.get('tool_name'), _tool_arguments(event)) for event in tool_events]}"
        if not (matched_read or disk_has_content):
            return False, f"read_file lacked path/content evidence for Projects/hello.txt. tools: {[(event.get('tool_name'), _tool_event_text(event)) for event in read_events]}"
        reply = _normalize_reply_text(_assistant_response_text(events))
        if "hello from the vault" in reply:
            return True, "read_file called for Projects/hello.txt and reply echoed content"
        return True, "read_file evidence matched Projects/hello.txt via tool args/result or disk evidence"


class A05_FolderNavigation(Assignment):
    number = 5
    name = "Folder navigation: list and find"
    layer = "assistant"

    def setup(self, profile_root, logger):
        seeded = [
            ("Projects/alpha.txt", "Project Alpha notes"),
            ("Projects/beta.txt", "Project Beta notes"),
            ("Daily Notes/2026-04-20.md", "Today I worked on testing"),
            ("Archive/old.txt", "This is archived content"),
        ]
        for relative, content in seeded:
            path = _seed_file(profile_root, relative, content)
            logger.log_file(f"seeded {relative}", path)

    def run(self, companion, profile_root, logger):
        events1 = _send_assistant(companion, "List all the top-level folders in the vault.", logger)
        tool_events1 = _collect_tool_calls(events1)
        list_like = [event for event in tool_events1 if event.get("tool_name") in {"run_terminal", "list_files", "tree_vault"}]
        if not list_like:
            return False, f"no list/tree/run_terminal tool was called. tools: {[event.get('tool_name') for event in tool_events1]}"
        reply1 = _assistant_response_text(events1)
        lowered1 = _normalize_reply_text(reply1)
        missing_folders = [name for name in ("projects", "daily notes") if name not in lowered1]
        if missing_folders:
            return False, f"reply did not mention folders {missing_folders}: {reply1!r}"
        events2 = _send_assistant(companion, "Now read the file in Daily Notes from today.", logger)
        tool_events2 = _collect_tool_calls(events2)
        read_events = [event for event in tool_events2 if event.get("tool_name") == "read_file"]
        hit_daily = any(
            "daily notes" in _tool_event_text(event) or "testing" in _tool_event_text(event)
            for event in read_events
        )
        if not hit_daily:
            return False, f"read_file was not called for a Daily Notes path. tools: {[(event.get('tool_name'), _tool_arguments(event)) for event in tool_events2]}"
        reply2 = _normalize_reply_text(_assistant_response_text(events2))
        if "testing" not in reply2:
            return False, f"reply did not contain 'testing': {reply2!r}"
        return True, "listed top-level folders and read today's daily note"


class A06_WriteAndVerify(Assignment):
    number = 6
    name = "Write and verify file"
    layer = "assistant"

    def run(self, companion, profile_root, logger):
        events = _send_assistant(
            companion,
            "Create a file called Projects/test_output.txt with the content: 'Gemma wrote this successfully'",
            logger,
        )
        tool_events = _collect_tool_calls(events)
        write_events = [event for event in tool_events if event.get("tool_name") in {"write_file", "edit_file"}]
        target = _vault_dir(profile_root) / "Projects" / "test_output.txt"
        logger.log_file("Projects/test_output.txt", target)
        matched_write = any(
            "projects/test_output.txt" in _tool_event_text(event) or "gemma wrote this successfully" in _tool_event_text(event)
            for event in write_events
        )
        if not write_events:
            return False, f"write tool was not called for Projects/test_output.txt. tools: {[(event.get('tool_name'), _tool_arguments(event)) for event in tool_events]}"
        if not (matched_write or _file_contains(target, "Gemma wrote this successfully")):
            return False, f"write tool lacked path/content evidence for Projects/test_output.txt. tools: {[(event.get('tool_name'), _tool_event_text(event)) for event in write_events]}"
        if not target.exists():
            return False, "file was not created on disk"
        body = target.read_text(encoding="utf-8").strip()
        expected = "Gemma wrote this successfully"
        if expected.lower() not in body.lower():
            return False, f"file content did not match. expected substring {expected!r}, got {body!r}"
        return True, f"file exists with expected content ({len(body)} bytes)"


class A07_ReminderTool(Assignment):
    number = 7
    name = "Scheduling: add reminder tool"
    layer = "companion"

    def setup(self, profile_root, logger):
        path = _seed_file(profile_root, "todo.md", "")
        logger.log_file("seeded todo.md", path)

    def run(self, companion, profile_root, logger):
        events = _send_companion(companion, "Use add_reminder to remind me to call the dentist on 2026-05-02 at 10:00.", logger)
        tool_events = _collect_tool_calls(events)
        todo_target = _vault_dir(profile_root) / "todo.md"
        todo_has_dentist = _file_contains(todo_target, "dentist")
        reminder_tools = [event for event in tool_events if event.get("tool_name") == "add_reminder"]
        vault_hits = _vault_files_containing(profile_root, "dentist")
        if vault_hits:
            logger.log_note("vault files containing 'dentist': " + ", ".join(str(path.relative_to(_vault_dir(profile_root))) for path in vault_hits))
        logger.log_file("todo.md after turn", todo_target)
        raw_tool_detected = any(_raw_tool_name_present(events, name) for name in KNOWN_TOOL_NAMES)
        if not tool_events and not raw_tool_detected:
            return False, "no tool activity was detected for the reminder request"
        if not reminder_tools:
            return False, f"add_reminder was not called. tools: {[event.get('tool_name') for event in tool_events]}"
        reminder_text = "\n".join(_tool_event_text(event) for event in reminder_tools)
        if "dentist" not in reminder_text.lower() and not todo_has_dentist:
            return False, f"add_reminder lacked dentist evidence. tools: {[(event.get('tool_name'), _tool_event_text(event)) for event in tool_events]}"
        if not todo_has_dentist:
            return False, f"add_reminder ran but todo.md was not updated. tools: {[(event.get('tool_name'), _tool_event_text(event)) for event in reminder_tools]}"
        reply = _assistant_response_text(events)
        confirmation_words = ("remind", "noted", "scheduled", "set", "added", "saved")
        if not any(word in reply.lower() for word in confirmation_words):
            return False, f"reply did not confirm the reminder. reply: {reply!r}"
        return True, "add_reminder updated todo.md with 'dentist' and reply confirmed the reminder"


class A08_MemoryStress(Assignment):
    number = 8
    name = "Memory stress: 200 facts"
    layer = "companion"

    def run(self, companion, profile_root, logger):
        facts = [f"Fact {i}: My lucky number for day {i} is {i * 7}." for i in range(1, 201)]
        intro = "I'm going to tell you 200 facts about me. Please remember all of them."
        message = intro + "\n\n" + "\n".join(facts)
        events1 = _send_companion(companion, message, logger)
        tool_events1 = [event for event in events1 if event.get("type") == "tool_executed"]
        wrote_memory = any(event.get("tool_name") == "write_memory" for event in tool_events1)
        memory_path = _memory_path(profile_root)
        if memory_path.exists():
            text = memory_path.read_text(encoding="utf-8")
            logger.log_note(f"memory.md size after turn 1: {len(text.encode('utf-8'))} bytes, {len(text.splitlines())} lines, write_memory_called={wrote_memory}")
        else:
            logger.log_note(f"memory.md missing after turn 1, write_memory_called={wrote_memory}")
        events2 = _send_companion(companion, "What is my lucky number for day 42?", logger)
        reply2 = _assistant_response_text(events2)
        events3 = _send_companion(companion, "What is my lucky number for day 137?", logger)
        reply3 = _assistant_response_text(events3)
        ok2 = "294" in reply2
        ok3 = "959" in reply3
        if ok2 and ok3:
            return True, "recalled 294 (day 42) and 959 (day 137)"
        missing = []
        if not ok2:
            missing.append(f"day 42 -> 294 (got: {reply2!r})")
        if not ok3:
            missing.append(f"day 137 -> 959 (got: {reply3!r})")
        return False, "; ".join(missing)


class A09_DreamPreservesMemory(Assignment):
    number = 9
    name = "Dream consolidation does not destroy memory"
    layer = "companion"
    seed_facts = [
        "Alice's cat is named Mittens",
        "Alice's favorite food is pasta",
        "Alice's car is a red Honda",
        "Alice codes in JavaScript",
        "Alice lives in Springfield",
    ]

    def setup(self, profile_root, logger):
        memory_path = _memory_path(profile_root)
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        existing = memory_path.read_text(encoding="utf-8") if memory_path.exists() else ""
        addition = "\n".join(f"- {fact}" for fact in self.seed_facts) + "\n"
        if not existing.strip():
            memory_path.write_text(addition, encoding="utf-8")
        else:
            need_append = "\n" + addition
            if not existing.endswith("\n"):
                existing += "\n"
            memory_path.write_text(existing + addition, encoding="utf-8")
        logger.log_file("memory.md before consolidation", memory_path)

    def run(self, companion, profile_root, logger):
        memory_path = _memory_path(profile_root)
        before = memory_path.read_text(encoding="utf-8") if memory_path.exists() else ""
        try:
            client, model, config = _build_dream_client(profile_root)
        except SuiteSkip as exc:
            return False, f"dream prerequisites not available: {exc}"
        try:
            companion.close(restart=False)
        except Exception:
            pass
        backend = ROOT / "app" / "backend"
        if str(backend) not in sys.path:
            sys.path.insert(0, str(backend))
        import dream as dream_module  # type: ignore
        try:
            dream_module.run_dream(client, config)
        except Exception as exc:
            try:
                companion.start()
            except Exception:
                pass
            return False, f"run_dream raised: {type(exc).__name__}: {exc}"
        finally:
            try:
                companion.start()
            except Exception:
                pass
        logger.log_file("memory.md after consolidation", memory_path)
        if not memory_path.exists():
            return False, "memory.md was deleted by consolidation"
        after = memory_path.read_text(encoding="utf-8")
        if not after.strip():
            return False, "memory.md is empty after consolidation"
        lowered = after.lower()
        missing = [fact for fact in self.seed_facts if fact.lower() not in lowered]
        logger.log_note(f"memory.md size before={len(before.encode('utf-8'))} bytes, after={len(after.encode('utf-8'))} bytes")
        if missing:
            return False, f"missing seeded facts after consolidation: {missing}"
        return True, "all 5 seeded facts preserved through dream consolidation"


class A10_NoteRetrieval(Assignment):
    number = 10
    name = "Note retrieval: 6 notes, find the right one"
    layer = "assistant"

    notes = {
        "Notes/shopping_list.md": "Milk, eggs, bread, butter",
        "Notes/meeting_notes.md": "Discussed Q2 budget with the team",
        "Notes/book_recommendations.md": "1984 by Orwell, Dune by Herbert",
        "Notes/fitness_log.md": "Ran 5km on Monday",
        "Notes/travel_plans.md": "Flight to Riverdale on May 3rd",
        "Notes/secret_project.md": "Working on a top secret AI companion",
    }

    def setup(self, profile_root, logger):
        for relative, content in self.notes.items():
            path = _seed_file(profile_root, relative, content)
            logger.log_file(f"seeded {relative}", path)

    def run(self, companion, profile_root, logger):
        events = _send_assistant(
            companion,
            "I have a file called Notes/book_recommendations.md in the vault. Read it and tell me what books are in it.",
            logger,
        )
        tool_events = _collect_tool_calls(events)
        read_events = [event for event in tool_events if event.get("tool_name") == "read_file"]
        reply = _assistant_response_text(events)
        lowered = reply.lower()
        missing = [book for book in ("1984", "dune") if book not in lowered]
        targeted_note = any("notes/book_recommendations.md" in _tool_event_text(event) for event in read_events)
        if not read_events:
            return False, f"read_file was not called for Notes/book_recommendations.md. tools: {[(event.get('tool_name'), _tool_arguments(event)) for event in tool_events]}"
        if not targeted_note:
            return False, f"read_file did not target Notes/book_recommendations.md. tools: {[(event.get('tool_name'), _tool_event_text(event)) for event in read_events]}"
        if missing:
            return False, f"reply missing books {missing}: {reply!r}"
        return True, "read_file targeted book_recommendations.md and the reply listed both books"


class A11_MultiFolderOperation(Assignment):
    number = 11
    name = "Multi-folder operation"
    layer = "assistant"

    def setup(self, profile_root, logger):
        # Re-seed in case Assignment 5's files were consolidated/changed
        for relative, content in (
            ("Projects/alpha.txt", "Project Alpha notes"),
            ("Projects/beta.txt", "Project Beta notes"),
        ):
            path = _seed_file(profile_root, relative, content)
            logger.log_file(f"reseeded {relative}", path)

    def run(self, companion, profile_root, logger):
        events = _send_assistant(
            companion,
            "Use read_file to read Projects/alpha.txt, then use read_file to read Projects/beta.txt, then tell me what each file is about.",
            logger,
        )
        tool_events = _collect_tool_calls(events)
        read_events = [event for event in tool_events if event.get("tool_name") == "read_file"]
        alpha_path = _vault_dir(profile_root) / "Projects" / "alpha.txt"
        beta_path = _vault_dir(profile_root) / "Projects" / "beta.txt"
        matched_reads = any(
            needle in _tool_event_text(event)
            for event in read_events
            for needle in ("projects", "alpha.txt", "beta.txt", "project alpha notes", "project beta notes")
        )
        disk_has_expected = _file_contains(alpha_path, "Project Alpha notes") and _file_contains(beta_path, "Project Beta notes")
        if not read_events:
            return False, f"no read/list tool was called. tools: {[(event.get('tool_name'), _tool_arguments(event)) for event in tool_events]}"
        if not (matched_reads or disk_has_expected):
            return False, f"tool evidence did not cover the seeded project files. tools: {[(event.get('tool_name'), _tool_event_text(event)) for event in read_events]}"
        reply = _normalize_reply_text(_assistant_response_text(events))
        lowered = reply
        missing = [name for name in ("alpha", "beta") if name not in lowered]
        if missing:
            return False, f"reply missing project names {missing}: {reply!r}"
        return True, f"read {len(read_events)} files and summarized Alpha and Beta"


class A12_ToolFailureHonesty(Assignment):
    number = 12
    name = "Tool failure is reported honestly"
    layer = "assistant"

    def run(self, companion, profile_root, logger):
        events = _send_assistant(companion, "Read the file Projects/does_not_exist.txt", logger)
        reply = _assistant_response_text(events)
        lowered = _normalize_reply_text(reply)
        acknowledged = any(phrase in lowered for phrase in (
            "not found", "does not exist", "doesn't exist", "no such file", "couldn't find", "could not find", "unable to find", "no file", "missing",
        ))
        if not acknowledged:
            return False, f"reply did not acknowledge missing file. reply: {reply!r}"
        # Check the model did NOT fabricate plausible content (e.g. echo of seeded Projects content)
        fabricated_markers = ("project alpha notes", "project beta notes", "hello from the vault")
        if any(marker in lowered for marker in fabricated_markers):
            return False, f"reply appears to fabricate file content: {reply!r}"
        return True, "model reported the file was not found and did not fabricate content"


ASSIGNMENTS: list[Assignment] = [
    A01_BasicPrompt(),
    A02_WriteMemory(),
    A03_MemoryRecall(),
    A04_ReadFile(),
    A05_FolderNavigation(),
    A06_WriteAndVerify(),
    A07_ReminderTool(),
    A08_MemoryStress(),
    A09_DreamPreservesMemory(),
    A10_NoteRetrieval(),
    A11_MultiFolderOperation(),
    A12_ToolFailureHonesty(),
]


def _assignment_summary(assignment: Assignment) -> str:
    return f"{assignment.number:>2}: {assignment.name}  (layer={assignment.layer})"


def list_assignments(log_file: Any | None = None) -> None:
    _emit_line(log_file, f"{BOLD}Behavioral assignments{RESET}")
    for assignment in ASSIGNMENTS:
        _emit_line(log_file, f"  {_assignment_summary(assignment)}")


def _normalize_selector_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _select_assignments(selector: str | None, log_file: Any | None = None) -> list[Assignment]:
    selector = str(selector or "").strip()
    if not selector:
        return list(ASSIGNMENTS)

    selected: list[Assignment] = []
    by_number = {assignment.number: assignment for assignment in ASSIGNMENTS}
    for raw_part in selector.split(","):
        part = raw_part.strip()
        if not part:
            continue
        range_match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if range_match:
            start = int(range_match.group(1))
            end = int(range_match.group(2))
            low, high = sorted((start, end))
            selected.extend(assignment for number, assignment in by_number.items() if low <= number <= high)
            continue
        if part.isdigit():
            assignment = by_number.get(int(part))
            if assignment is not None:
                selected.append(assignment)
            continue

        needle = _normalize_selector_text(part)
        if needle:
            selected.extend(
                assignment
                for assignment in ASSIGNMENTS
                if needle in _normalize_selector_text(assignment.name)
            )

    deduped: list[Assignment] = []
    seen: set[int] = set()
    for assignment in selected:
        if assignment.number in seen:
            continue
        deduped.append(assignment)
        seen.add(assignment.number)
    if not deduped:
        _emit_line(log_file, f"{RED}[FATAL]{RESET} No behavioral assignments matched selector: {selector!r}", stream=sys.stderr)
        list_assignments(log_file)
        sys.exit(1)
    return deduped


# ---------------------------------------------------------------------------
# Dream support
# ---------------------------------------------------------------------------

def _build_dream_client(profile_root: Path) -> tuple[Any, str, dict]:
    try:
        import openai  # type: ignore
    except ImportError as exc:
        raise SuiteSkip(f"openai package not importable: {exc}") from exc
    if not hasattr(openai, "OpenAI") or openai.__class__.__module__ == "builtins":
        # The test stub installed by tests/helpers/setup.py would also satisfy hasattr,
        # so check that the OpenAI class is callable enough to chain .chat.completions.create.
        pass
    config = build_profile_config({})
    model = config.get("brain", {}).get("model") or resolve_chat_model()
    client = openai.OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
    # Ensure the test process sees the same MEMORY_DIR as the bridge.
    os.environ["OPEN_COMPANION_TEST_MODE"] = "1"
    os.environ["OPEN_COMPANION_TEST_PROFILE_DIR"] = str(profile_root)
    return client, model, config


# ---------------------------------------------------------------------------
# Suite runner
# ---------------------------------------------------------------------------

def _provider_for_model(model_name: str) -> str:
    explicit = str(os.environ.get("OPEN_COMPANION_BEHAVIORAL_PROVIDER") or "").strip().lower()
    if explicit:
        return explicit
    lowered = str(model_name or "").lower()
    if "qwen" in lowered:
        return "qwen"
    if "gemma" in lowered:
        return "gemma"
    return "ollama"


def _openai_api_key() -> str:
    env_key = (
        os.environ.get("OPEN_COMPANION_ASSISTANT_OPENAI_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    ).strip()
    if env_key:
        return env_key
    try:
        from providers.base import read_keyring_secret  # noqa: WPS433
        return read_keyring_secret(["openai_api_key", "openai"]).strip()
    except Exception:
        return ""


def _ensure_chat_model_or_die(log_file: Any | None = None) -> str:
    explicit_provider = str(os.environ.get("OPEN_COMPANION_BEHAVIORAL_PROVIDER") or "").strip().lower()
    if explicit_provider == "openai":
        if not _openai_api_key():
            _emit_line(
                log_file,
                f"{RED}[FATAL]{RESET} OpenAI behavioral suite requires OPENAI_API_KEY/OPEN_COMPANION_ASSISTANT_OPENAI_API_KEY or a saved openai_api_key credential.",
                stream=sys.stderr,
            )
            sys.exit(1)
        return (
            str(os.environ.get("OPEN_COMPANION_BEHAVIORAL_MODEL") or "").strip()
            or str(os.environ.get("OPEN_COMPANION_ASSISTANT_OPENAI_MODEL") or "").strip()
            or "gpt-5.4-mini"
        )

    try:
        models = _ollama_models(timeout=6)
    except Exception as exc:
        _emit_line(log_file, f"{RED}[FATAL]{RESET} Ollama is not reachable at {OLLAMA_URL}: {exc}", stream=sys.stderr)
        _emit_line(log_file, f"{RED}[FATAL]{RESET} The local behavioral suite requires a live local Ollama. Aborting.", stream=sys.stderr)
        sys.exit(1)
    if not models:
        _emit_line(log_file, f"{RED}[FATAL]{RESET} Ollama is reachable, but no models are installed.", stream=sys.stderr)
        sys.exit(1)
    preferred = str(os.environ.get("OPEN_COMPANION_BEHAVIORAL_MODEL") or "").strip()
    if preferred:
        try:
            return resolve_chat_model(preferred)
        except SuiteSkip as exc:
            _emit_line(log_file, f"{RED}[FATAL]{RESET} Requested behavioral model is unavailable: {preferred} ({exc})", stream=sys.stderr)
            sys.exit(1)
    try:
        return resolve_chat_model("gemma4:e4b")
    except SuiteSkip:
        try:
            return resolve_chat_model()
        except SuiteSkip as exc:
            _emit_line(log_file, f"{RED}[FATAL]{RESET} {exc}", stream=sys.stderr)
            sys.exit(1)


def _prepare_profile(profile_root: Path, chat_model: str) -> None:
    if profile_root.exists():
        shutil.rmtree(profile_root, ignore_errors=True)
    profile_root.mkdir(parents=True, exist_ok=True)
    provider = _provider_for_model(chat_model)
    config_patch = {
        "brain": {
            "provider": provider,
            "model": chat_model,
            "layers": {
                "companion": {"provider": provider, "model": chat_model},
                "assistant": {"provider": provider, "model": chat_model},
            },
        },
        "memory": {
            "enabled": True,
            "write_back_enabled": True,
            "embedding_enabled": False,
            "extraction_model": chat_model,
        },
    }
    config = build_profile_config(config_patch)
    (profile_root / "companion" / "memory").mkdir(parents=True, exist_ok=True)
    (profile_root / "companion" / "vault").mkdir(parents=True, exist_ok=True)
    (profile_root / "companion" / "soul" / "active").mkdir(parents=True, exist_ok=True)
    (profile_root / "config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    _memory_path(profile_root).write_text("", encoding="utf-8")


def run_suite(log_file: Any | None = None, selector: str | None = None, *, list_only: bool = False) -> bool:
    if list_only:
        list_assignments(log_file)
        return True

    selected_assignments = _select_assignments(selector, log_file=log_file)
    chat_model = _ensure_chat_model_or_die(log_file=log_file)
    provider = _provider_for_model(chat_model)
    _emit_line(log_file, f"{BOLD}Behavioral Assignment Suite{RESET}  provider={provider} model={chat_model}")
    if selector:
        selected_text = ", ".join(str(assignment.number) for assignment in selected_assignments)
        _emit_line(log_file, f"selection: {selector!r} -> assignments {selected_text}")
    use_main_profile = os.environ.get("OPEN_COMPANION_TEST_USE_MAIN_PROFILE") == "1"
    profile_root = ROOT.resolve() if use_main_profile else (TEMP_PROFILE_BASE / f"{PROFILE_NAME}-{uuid.uuid4().hex[:8]}").resolve()
    TEMP_PROFILE_BASE.mkdir(parents=True, exist_ok=True)
    config_local_path = profile_root / "config.local.json"
    config_local_backup = config_local_path.read_text(encoding="utf-8") if use_main_profile and config_local_path.exists() else None
    vault_path = profile_root / "companion" / "vault"
    vault_backup_path = TEMP_PROFILE_BASE / f"{PROFILE_NAME}-vault-backup-{uuid.uuid4().hex[:8]}"
    vault_existed = vault_path.exists()
    schedule_path = profile_root / "companion" / "schedule.md"
    schedule_existed = schedule_path.exists()
    schedule_backup = schedule_path.read_text(encoding="utf-8") if use_main_profile and schedule_existed else None
    if use_main_profile and vault_existed:
        shutil.copytree(vault_path, vault_backup_path)
    if use_main_profile:
        (profile_root / "companion" / "memory").mkdir(parents=True, exist_ok=True)
        for path in (profile_root / "companion" / "memory").glob("*"):
            if path.is_file():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
        config_patch_for_disk = {
            "brain": {
                "provider": provider,
                "model": chat_model,
                "layers": {
                    "companion": {"provider": provider, "model": chat_model},
                    "assistant": {"provider": provider, "model": chat_model},
                },
            },
            "memory": {
                "enabled": True,
                "write_back_enabled": True,
                "embedding_enabled": False,
                "extraction_model": chat_model,
            },
        }
        config_for_disk = build_profile_config(config_patch_for_disk)
        config_local_path.write_text(json.dumps(config_for_disk, indent=2) + "\n", encoding="utf-8")
        _memory_path(profile_root).write_text("", encoding="utf-8")
    else:
        _prepare_profile(profile_root, chat_model)
    _emit_line(log_file, f"profile: {profile_root}")
    os.environ["OPEN_COMPANION_TEST_MODE"] = "1"
    os.environ["OPEN_COMPANION_TEST_PROFILE_DIR"] = str(profile_root)

    logger = BehavioralLogger(file_handle=log_file)
    summary_rows: list[tuple[Assignment, bool, str, float]] = []
    config_patch = {
        "brain": {
            "provider": provider,
            "model": chat_model,
            "layers": {
                "companion": {"provider": provider, "model": chat_model},
                "assistant": {"provider": provider, "model": chat_model},
            },
        },
        "memory": {"extraction_model": chat_model},
    }
    companion: Companion | None = None
    try:
        companion = Companion(profile_root, config_patch=config_patch, timeout=DEFAULT_TURN_TIMEOUT, wipe_on_start=not use_main_profile)
        for assignment in selected_assignments:
            logger.begin(assignment.number, assignment.name, assignment.layer)
            try:
                assignment.setup(profile_root, logger)
            except Exception as exc:
                passed, reason, elapsed = logger.finish(False, f"setup failed: {type(exc).__name__}: {exc}")
                summary_rows.append((assignment, passed, reason, elapsed))
                continue
            try:
                passed, reason = assignment.run(companion, profile_root, logger)
            except SuiteSkip as exc:
                passed, reason = False, f"skipped: {exc}"
            except Exception as exc:
                passed, reason = False, f"raised: {type(exc).__name__}: {exc}"
            passed, reason, elapsed = logger.finish(passed, reason)
            summary_rows.append((assignment, passed, reason, elapsed))
            # Reset conversation state between assignments while keeping memory/vault on disk.
            try:
                companion.reset()
            except Exception as exc:
                _emit_line(log_file, f"  {YELLOW}[WARN]{RESET} bridge reset failed: {exc}")
                try:
                    companion.start()
                except Exception:
                    break
    finally:
        if companion is not None:
            try:
                companion.close()
            except Exception:
                pass
        if use_main_profile:
            if config_local_backup is None:
                config_local_path.unlink(missing_ok=True)
            else:
                config_local_path.write_text(config_local_backup, encoding="utf-8")
            if vault_backup_path.exists():
                shutil.rmtree(vault_path, ignore_errors=True)
                shutil.copytree(vault_backup_path, vault_path)
                shutil.rmtree(vault_backup_path, ignore_errors=True)
            elif not vault_existed:
                shutil.rmtree(vault_path, ignore_errors=True)
            if schedule_backup is not None:
                schedule_path.parent.mkdir(parents=True, exist_ok=True)
                schedule_path.write_text(schedule_backup, encoding="utf-8")
            elif not schedule_existed:
                schedule_path.unlink(missing_ok=True)
            memory_dir = profile_root / "companion" / "memory"
            memory_dir.mkdir(parents=True, exist_ok=True)
            (memory_dir / ".gitkeep").touch()
        else:
            shutil.rmtree(profile_root, ignore_errors=True)

    _emit_line(log_file)
    _emit_line(log_file, f"{BOLD}Summary{RESET}")
    pass_count = 0
    for assignment, passed, reason, elapsed in summary_rows:
        status = f"{GREEN}PASS{RESET}" if passed else f"{RED}FAIL{RESET}"
        title = f"Assignment {assignment.number:>2}  {assignment.name}"
        dots = max(2, 50 - len(assignment.name))
        _emit_line(log_file, f"  {title} {'.' * dots} {status}  ({elapsed:.1f}s)")
        if not passed:
            for line in reason.splitlines() or [""]:
                _emit_line(log_file, f"              -> {line}")
        else:
            pass_count += 1
    _emit_line(log_file)
    _emit_line(log_file, f"{BOLD}TOTAL: {pass_count}/{len(summary_rows)} passed{RESET}")
    return pass_count == len(summary_rows)


if __name__ == "__main__":
    success = run_suite()
    sys.exit(0 if success else 1)
