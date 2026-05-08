#!/usr/bin/env python3
from __future__ import annotations

import shutil
import sys
import uuid
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = ROOT / "app"
BACKEND_DIR = APP_ROOT / "backend"
SCRATCH_ROOT = ROOT / "tests" / "integration" / ".tmp"

for candidate in (str(APP_ROOT), str(BACKEND_DIR), str(ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from tests.helpers.setup import fail, ok, summary

import tool_registry
from backend.tools.builtin import memory_tools
from backend.tools.builtin import sandbox as sandbox_tools
from backend.tools.builtin import scheduler_tools
from backend.tools.builtin import system as system_tools


def _temp_dir() -> Path:
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    path = SCRATCH_ROOT / f"oc-tool-tests-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def _fake_subprocess_run(*args, **kwargs):
    command = args[0] if args else kwargs.get("args", [])
    command_text = " ".join(str(part) for part in command)
    if "wsl" in command_text and "-l" in command_text:
        return SimpleNamespace(returncode=0, stdout=("  NAME                   STATE\n* OpenCompanion-Sandbox   Running\n").encode("utf-16-le"), stderr=b"")
    if "wsl" in command_text and "-d" in command_text:
        return SimpleNamespace(returncode=0, stdout="sandbox ok", stderr="")
    if "Get-Date" in command_text or "cmd /c" in command_text:
        return SimpleNamespace(returncode=0, stdout="windows ok", stderr="")
    return SimpleNamespace(returncode=0, stdout="ok", stderr="")


def _tool_patch_stack() -> ExitStack:
    stack = ExitStack()
    stack.enter_context(mock.patch.object(sandbox_tools.subprocess, "run", side_effect=_fake_subprocess_run))
    stack.enter_context(mock.patch.object(sandbox_tools.shutil, "which", return_value="wsl"))
    stack.enter_context(mock.patch.object(system_tools.subprocess, "run", side_effect=_fake_subprocess_run))
    stack.enter_context(mock.patch.object(memory_tools.memory_store, "init_memory", lambda: None))
    stack.enter_context(mock.patch.object(memory_tools.memory_store, "append_memory", lambda entry: None))
    stack.enter_context(mock.patch.object(memory_tools.memory_store, "retrieve_topic_bundle_memories", return_value="=== Relevant Memories ===\n- remembered thing"))
    stack.enter_context(mock.patch.object(memory_tools.memory_store, "load_all_memories", return_value="- remembered thing"))
    return stack


def test_every_registry_tool_has_expected_coverage() -> None:
    name = "tools: registry contains the current vault and memory kernel"
    registry_names = {tool["name"] for tool in tool_registry.load_registry(force=True)}
    expected = {
        "run_terminal", "run_windows_terminal", "read_file", "write_file", "append_file",
        "list_files", "search_files", "replace_text_in_file", "edit_code_symbol", "edit_file",
        "write_memory", "search_memories", "help_skill", "list_skills", "write_skill", "delete_skill",
        "add_reminder", "add_recurring_reminder",
    }
    assert expected.issubset(registry_names), registry_names
    ok(name)


def test_default_model_tool_payload_is_base_kernel_only() -> None:
    name = "tools: default model payload exposes only the base kernel"
    exposed = [tool["name"] for tool in tool_registry.get_tools_by_layer("assistant", config={})]
    assert exposed == [
        "write_memory",
        "search_memories",
        "read_file",
        "write_file",
        "append_file",
        "list_files",
        "search_files",
        "replace_text_in_file",
        "add_reminder",
        "add_recurring_reminder",
        "run_windows_terminal",
        "run_terminal",
    ], exposed
    ok(name)


def test_happy_paths() -> None:
    name = "tools: core tool happy paths work"
    with _tool_patch_stack():
        assert "sandbox ok" in sandbox_tools.run_terminal("echo hi")
        assert "windows ok" in system_tools.run_windows_terminal("Get-Date")
        assert "Saved memory" in memory_tools.write_memory("remember this", topic="facts")
        assert "remembered thing" in memory_tools.search_memories("remember")
    ok(name)


def test_reminder_tool_supports_partial_and_complete_turns() -> None:
    name = "tools: add_reminder supports partial and complete reminder turns"
    temp = _temp_dir()
    original_todo_path = scheduler_tools.scheduler.TODO_PATH
    original_now = scheduler_tools.scheduler._now
    try:
        scheduler_tools.scheduler.TODO_PATH = temp / "todo.md"
        scheduler_tools.scheduler._now = lambda: datetime(2026, 5, 1, 17, 30)
        partial = scheduler_tools.add_reminder(text="call clinic")
        assert "Reminder details incomplete" in partial
        assert "when to remind" in partial
        result = scheduler_tools.add_reminder(text="call clinic", due_at="tomorrow at 9 AM")
        assert "Reminder added" in result
        assert "2026-05-02T09:00" in result
        result = scheduler_tools.add_reminder(text="take lasagna out", due_at="in 45 minutes")
        assert "Reminder added" in result
        assert "2026-05-01T18:15" in result
        result = scheduler_tools.add_reminder(
            text="call clinic",
            due_at="2026-05-21T09:00:00",
            current_user_message="Remind me to call the clinic tomorrow at 9 AM.",
        )
        assert "2026-05-02T09:00" in result
        body = scheduler_tools.scheduler.TODO_PATH.read_text(encoding="utf-8")
        assert "- [ ] call clinic | remind: 2026-05-02 09:00" in body
        assert "- [ ] take lasagna out | remind: 2026-05-01 18:15" in body
    finally:
        scheduler_tools.scheduler.TODO_PATH = original_todo_path
        scheduler_tools.scheduler._now = original_now
        shutil.rmtree(temp, ignore_errors=True)
    ok(name)


def test_recurring_reminder_tool_supports_partial_and_complete_turns() -> None:
    name = "tools: add_recurring_reminder writes schedule entries"
    temp = _temp_dir()
    original_schedule_path = scheduler_tools.scheduler.SCHEDULE_PATH
    try:
        scheduler_tools.scheduler.SCHEDULE_PATH = temp / "schedule.md"
        partial = scheduler_tools.add_recurring_reminder(text="take vitamins")
        assert "Recurring reminder details incomplete" in partial
        assert "recurring schedule" in partial
        result = scheduler_tools.add_recurring_reminder(text="take vitamins", schedule="every other Monday at 9 AM")
        assert "Recurring reminder added" in result
        result = scheduler_tools.add_recurring_reminder(
            text="take vitamins",
            schedule="every 5 years on Jan 1 00:00",
            current_user_message="Remind me every day at 9 AM to take vitamins.",
        )
        assert "Recurring reminder added" in result
        result = scheduler_tools.add_recurring_reminder(text="pay rent", schedule="every month on the 1st at 8 AM")
        assert "every month on the 1st 08:00" in result
        result = scheduler_tools.add_recurring_reminder(text="send invoices", schedule="every month on the 2nd at 8 AM")
        assert "every month on the 2nd 08:00" in result
        body = scheduler_tools.scheduler.SCHEDULE_PATH.read_text(encoding="utf-8")
        assert "- [ ] take vitamins | every other Monday 09:00" in body
        assert "- [ ] take vitamins | every day 09:00" in body
        assert "- [ ] pay rent | every month on the 1st 08:00" in body
        assert "- [ ] send invoices | every month on the 2nd 08:00" in body
        cron_expression, interval_weeks, interval_years = scheduler_tools.scheduler._parse_dsl_to_cron("every 2 years on Apr 21 10:00")
        assert cron_expression == "0 10 21 4 *"
        assert interval_weeks == 1
        assert interval_years == 2
        cron_expression, interval_weeks, interval_years = scheduler_tools.scheduler._parse_dsl_to_cron("every day at 09:00")
        assert cron_expression == "0 9 * * *"
        assert interval_weeks == 1
        assert interval_years == 1
        cron_expression, interval_weeks, interval_years = scheduler_tools.scheduler._parse_dsl_to_cron("every other Monday 09:00")
        assert cron_expression == "0 9 * * 1"
        assert interval_weeks == 2
        assert interval_years == 1
        for bad_schedule in ("every month on the 32nd 09:00", "every day 25:00", "every 0 days 09:00"):
            try:
                scheduler_tools.scheduler._parse_dsl_to_cron(bad_schedule)
            except ValueError:
                pass
            else:
                raise AssertionError(f"expected invalid schedule to fail: {bad_schedule}")
    finally:
        scheduler_tools.scheduler.SCHEDULE_PATH = original_schedule_path
        shutil.rmtree(temp, ignore_errors=True)
    ok(name)


def test_starter_schedule_examples_do_not_create_reminders() -> None:
    name = "tools: starter schedule examples are inert"
    temp = _temp_dir()
    original_schedule_path = scheduler_tools.scheduler.SCHEDULE_PATH
    try:
        scheduler_tools.scheduler.SCHEDULE_PATH = temp / "schedule.md"
        scheduler_tools.scheduler.SCHEDULE_PATH.write_text(
            scheduler_tools.scheduler.STARTER_SCHEDULE_MD,
            encoding="utf-8",
        )
        assert scheduler_tools.scheduler._schedule_reminders() == []
        scheduler_tools.scheduler.SCHEDULE_PATH.write_text(
            "# Schedule\n\n<!--\n- [ ] Commented example | every Monday 09:00\n-->\n\n- [ ] Real reminder | every Tuesday 10:00\n",
            encoding="utf-8",
        )
        reminders = scheduler_tools.scheduler._schedule_reminders()
        assert [reminder.text for reminder in reminders] == ["Real reminder"]
    finally:
        scheduler_tools.scheduler.SCHEDULE_PATH = original_schedule_path
        shutil.rmtree(temp, ignore_errors=True)
    ok(name)


def test_error_paths() -> None:
    name = "tools: core tool error paths stay user-friendly"
    with _tool_patch_stack():
        assert "required" in tool_registry.execute_tool("run_terminal", {}, "assistant")
        assert "required" in tool_registry.execute_tool("run_windows_terminal", {}, "assistant")
        assert "required" in tool_registry.execute_tool("write_memory", {}, "assistant")
        assert "required" in tool_registry.execute_tool("search_memories", {}, "assistant")
        assert "Saved memory" in memory_tools.write_memory("x", topic="unknown")
    ok(name)


def test_confirmation_gates_for_windows_terminal_only() -> None:
    name = "tools: only Windows host commands require confirmation"
    assert not tool_registry.needs_confirmation("run_terminal", "assistant", config={})
    assert tool_registry.needs_confirmation(
        "run_windows_terminal",
        "assistant",
        config={},
    )
    assert not tool_registry.needs_confirmation("read_file", "assistant", config={})
    assert not tool_registry.needs_confirmation("write_file", "assistant", config={})
    assert not tool_registry.needs_confirmation("append_file", "assistant", config={})
    assert not tool_registry.needs_confirmation("replace_text_in_file", "assistant", config={})
    assert not tool_registry.needs_confirmation("add_reminder", "assistant", config={})
    assert not tool_registry.needs_confirmation("add_recurring_reminder", "assistant", config={})
    assert not tool_registry.needs_confirmation("write_memory", "assistant", config={})
    assert not tool_registry.needs_confirmation("search_memories", "assistant", config={})
    ok(name)


def test_behavioral_assignment_selector_supports_focused_runs() -> None:
    name = "tools: behavioral runner supports focused assignment selectors"
    from tests.companion import test_gemma_behavioral as behavioral

    assert [assignment.number for assignment in behavioral._select_assignments("7")] == [7]
    assert [assignment.number for assignment in behavioral._select_assignments("7-9")] == [7, 8, 9]
    assert [assignment.number for assignment in behavioral._select_assignments("7,reminder")] == [7]
    assert [assignment.number for assignment in behavioral._select_assignments("note")] == [10]
    ok(name)


def run_all() -> bool:
    tests = [
        test_every_registry_tool_has_expected_coverage,
        test_default_model_tool_payload_is_base_kernel_only,
        test_happy_paths,
        test_reminder_tool_supports_partial_and_complete_turns,
        test_recurring_reminder_tool_supports_partial_and_complete_turns,
        test_starter_schedule_examples_do_not_create_reminders,
        test_error_paths,
        test_confirmation_gates_for_windows_terminal_only,
        test_behavioral_assignment_selector_supports_focused_runs,
    ]
    for test in tests:
        try:
            test()
        except Exception as exc:
            fail(test.__name__, str(exc))
    return summary()


def main() -> int:
    return 0 if run_all() else 1


if __name__ == "__main__":
    sys.exit(main())
