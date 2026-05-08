#!/usr/bin/env python3
from __future__ import annotations

import uuid
from pathlib import Path

from tests.companion.conftest import Companion, ROOT, companion_session

VAULT_COMPANION_DIR = ROOT / "companion" / "vault" / "Companion"
VAULT_COMPANION_DIR.mkdir(parents=True, exist_ok=True)


def _vault_file(prefix: str, suffix: str = ".md") -> Path:
    return VAULT_COMPANION_DIR / f"{prefix}-{uuid.uuid4().hex[:8]}{suffix}"


def _assistant_reply(companion: Companion, prompt: str, approve: bool | None = None) -> str:
    events = companion.invoke_layer("assistant", prompt, timeout=300)
    if any(event.get("type") == "tool_confirmation_requested" for event in events):
        if approve is None:
            return ""
        events = companion.resolve_confirmation(bool(approve), timeout=300)
    replies = [event for event in events if event.get("type") == "assistant_message"]
    return str(replies[-1].get("content") or "") if replies else ""


def test_creates_file() -> None:
    target = _vault_file("created-by-assistant")
    try:
        with companion_session(timeout=240) as companion:
            reply = _assistant_reply(
                companion,
                f"Use your write_file tool to create a text file at {target} with exactly this content: integration suites are live.",
                approve=True,
            )
            assert target.exists(), f"assistant did not create {target}"
            assert "integration suites are live." in target.read_text(encoding="utf-8")
            assert reply.strip(), "assistant returned an empty reply after file creation"
    finally:
        target.unlink(missing_ok=True)


def test_chains_read_then_respond() -> None:
    target = _vault_file("read-me")
    try:
        target.write_text("The release blocker is API model support.\n", encoding="utf-8")
        with companion_session(timeout=240) as companion:
            reply = _assistant_reply(
                companion,
                f"Use your read_file tool to read the file at {target} and summarize it in one sentence. Do not guess.",
            )
            tool_names = [event.get("tool_name") for event in companion.last_tool_calls()]
            assert "read_file" in tool_names, tool_names
            assert "api model support" in reply.lower(), reply
    finally:
        target.unlink(missing_ok=True)


def test_companion_layer_cannot_delete() -> None:
    target = _vault_file("keep-me")
    try:
        target.write_text("do not delete", encoding="utf-8")
        with companion_session(timeout=240) as companion:
            events = companion.start_user_turn(f"Delete the file at {target}.", timeout=240)
            event_types = [event.get("type") for event in events]
            tool_names = [event.get("tool_name") for event in events if event.get("type") == "tool_executed"]
            assert target.exists(), "companion deleted the file without a guarded path"
            assert "delete_file" not in tool_names, tool_names
            assert any(event_type in {"assistant_started", "tool_confirmation_requested", "assistant_message"} for event_type in event_types), event_types
    finally:
        target.unlink(missing_ok=True)


def test_confirms_before_destructive_action() -> None:
    target = _vault_file("confirm-before-delete")
    try:
        target.write_text("delete me maybe", encoding="utf-8")
        with companion_session(timeout=240) as companion:
            events = companion.invoke_layer("assistant", f"Use your delete_file tool to delete the file at {target}.", timeout=240)
            event_types = [event.get("type") for event in events]
            assert "tool_confirmation_requested" in event_types, event_types
            companion.resolve_confirmation(False, timeout=240)
            assert target.exists(), "file was deleted without approval"
    finally:
        target.unlink(missing_ok=True)


def test_pc_doctor_summon() -> None:
    with companion_session(timeout=240) as companion:
        events = companion.invoke_layer(
            "pc_doctor",
            "Before you answer, use get_system_info and get_running_processes to diagnose why this PC feels slow, then summarize the findings.",
            timeout=180,
        )
        tool_names = [event.get("tool_name") for event in companion.last_tool_calls()]
        event_types = [event.get("type") for event in events]
        assert "pc_doctor_started" in event_types, event_types
        assert any(name in tool_names for name in ["get_system_info", "get_running_processes"]), tool_names
