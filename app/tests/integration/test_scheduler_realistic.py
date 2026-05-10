#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[3]
for candidate in (ROOT / "app", ROOT / "app" / "backend", ROOT):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)

from backend.tools.builtin import scheduler_tools


CASES = [
    ("one_off", "Call the clinic tomorrow at 9 AM", {"text": "Call the clinic", "due_at": "2026-05-02T09:00:00"}, "accept"),
    ("one_off", "Pay rent on May 5 at 8 AM", {"text": "Pay rent", "due_at": "2026-05-05T08:00:00"}, "accept"),
    ("one_off", "Take lasagna out of the oven in 45 minutes", {"text": "Take lasagna out of the oven", "due_at": "2026-05-01T18:15:00"}, "accept"),
    ("one_off", "Water balcony plants tonight at 9", {"text": "Water balcony plants", "due_at": "2026-05-01T21:00:00"}, "accept"),
    ("one_off", "Submit the tax form next Friday at 4:30 PM", {"text": "Submit the tax form", "due_at": "2026-05-08T16:30:00"}, "accept"),
    ("one_off", "Renew passport on June 10 at 11", {"text": "Renew passport", "due_at": "2026-06-10T11:00:00"}, "accept"),
    ("one_off", "Pick up package tomorrow at noon", {"text": "Pick up package", "due_at": "2026-05-02T12:00:00"}, "accept"),
    ("one_off", "Wish Sam happy birthday at midnight on May 3", {"text": "Wish Sam happy birthday", "due_at": "2026-05-03T00:00:00"}, "accept"),
    ("one_off", "Bring laptop charger Monday morning", {"text": "Bring laptop charger", "due_at": "2026-05-04T09:00:00"}, "accept"),
    ("one_off", "Start dinner today at 6:15 PM", {"text": "Start dinner", "due_at": "2026-05-01T18:15:00"}, "accept"),
    ("one_off", "Reply to Marta tomorrow after lunch", {"text": "Reply to Marta", "due_at": "2026-05-02T13:00:00"}, "accept"),
    ("one_off", "Put bins out Saturday at 7 AM", {"text": "Put bins out", "due_at": "2026-05-02T07:00:00"}, "accept"),
    ("one_off", "Join visa appointment on May 20 at 14:00", {"text": "Join visa appointment", "due_at": "2026-05-20T14:00:00"}, "accept"),
    ("one_off", "Charge headphones before bed tonight", {"text": "Charge headphones", "due_at": "2026-05-01T22:30:00"}, "accept"),
    ("one_off", "Buy train tickets tomorrow at 5 PM", {"text": "Buy train tickets", "due_at": "2026-05-02T17:00:00"}, "accept"),
    ("recurring", "Take vitamins every day at 9 AM", {"text": "Take vitamins", "schedule": "every day 09:00"}, "accept"),
    ("recurring", "Check school portal every weekday at 8:30", {"text": "Check school portal", "schedule": "every weekday 08:30"}, "accept"),
    ("recurring", "Water herbs every 3 days at 9 AM", {"text": "Water herbs", "schedule": "every 3 days 09:00"}, "accept"),
    ("recurring", "Change bedsheets every 14 days at 8 PM", {"text": "Change bedsheets", "schedule": "every 14 days 20:00"}, "accept"),
    ("recurring", "Plan the week every Monday at 10", {"text": "Plan the week", "schedule": "every Monday 10:00"}, "accept"),
    ("recurring", "Weekly review every Friday at 5 PM", {"text": "Weekly review", "schedule": "every Friday 17:00"}, "accept"),
    ("recurring", "Therapy every 2 weeks on Tuesday at 9:15", {"text": "Therapy", "schedule": "every 2 weeks on Tuesday 09:15"}, "accept"),
    ("recurring", "Deep clean fridge every 6 weeks on Sunday at 7:45 PM", {"text": "Deep clean fridge", "schedule": "every 6 weeks on Sunday 19:45"}, "accept"),
    ("recurring", "Pay rent every month on the 1st at 8 AM", {"text": "Pay rent", "schedule": "every month on the 1st 08:00"}, "accept"),
    ("recurring", "Send invoices every month on the 2nd at 8 AM", {"text": "Send invoices", "schedule": "every month on the 2nd 08:00"}, "accept"),
    ("recurring", "Check budget every month on the 15th at noon", {"text": "Check budget", "schedule": "every month on the 15th 12:00"}, "accept"),
    ("recurring", "Replace water filter every 2 months on the 1st at 9 AM", {"text": "Replace water filter", "schedule": "every 2 months on the 1st 09:00"}, "accept"),
    ("recurring", "Quarterly review every 3 months on the 10th at 2:30 PM", {"text": "Quarterly review", "schedule": "every 3 months on the 10th 14:30"}, "accept"),
    ("recurring", "Birthday reminder every year on April 21 at 10 AM", {"text": "Birthday reminder", "schedule": "every year on Apr 21 10:00"}, "accept"),
    ("recurring", "Renew certification every 2 years on April 21 at 10 AM", {"text": "Renew certification", "schedule": "every 2 years on Apr 21 10:00"}, "accept"),
    ("recurring", "Review long-term goals every 5 years on January 1 at midnight", {"text": "Review long-term goals", "schedule": "every 5 years on Jan 1 00:00"}, "accept"),
    ("one_off", "Todo without time should ask for due_at if routed to reminder tool", {"text": "Buy milk", "due_at": ""}, "missing"),
    ("one_off", "Task without text should ask what to remind about", {"text": "", "due_at": "2026-05-02T09:00:00"}, "missing"),
    ("recurring", "Recurring without text should ask what", {"text": "", "schedule": "every day 09:00"}, "missing"),
    ("recurring", "Recurring without schedule should ask when", {"text": "Stretch", "schedule": ""}, "missing"),
    ("one_off", "Natural tomorrow string passed directly is resolved by the backend", {"text": "Call clinic", "due_at": "tomorrow at 9 AM"}, "accept"),
    ("one_off", "Impossible calendar date should fail", {"text": "Do impossible date thing", "due_at": "2026-02-30T09:00:00"}, "reject"),
    ("one_off", "Impossible hour should fail", {"text": "Do impossible time thing", "due_at": "2026-05-03T25:00:00"}, "reject"),
    ("one_off", "Past datetime should fail", {"text": "Past reminder", "due_at": "2020-01-01T10:00:00"}, "reject"),
    ("recurring", "Every morning is accepted as daily morning", {"text": "Stretch", "schedule": "every morning"}, "accept"),
    ("recurring", "Every day at 9 with word at is accepted", {"text": "Stretch", "schedule": "every day at 09:00"}, "accept"),
    ("recurring", "Every other Monday is accepted as a two-week interval", {"text": "Check in with Alex", "schedule": "every other Monday 09:00"}, "accept"),
    ("recurring", "First Monday of month is unsupported", {"text": "Team retro", "schedule": "every first Monday 09:00"}, "reject"),
    ("recurring", "Last day of month is unsupported", {"text": "Back up files", "schedule": "every last day 22:00"}, "reject"),
    ("recurring", "Month day without ordinal is accepted", {"text": "Check budget", "schedule": "every month on the 15 12:00"}, "accept"),
    ("recurring", "Month day without the is accepted", {"text": "Check budget", "schedule": "every month on 15th 12:00"}, "accept"),
    ("recurring", "Fake weekday should fail", {"text": "Fake day", "schedule": "every Funday 09:00"}, "reject"),
    ("recurring", "Fake month should fail", {"text": "Fake month", "schedule": "every year on Foo 1 09:00"}, "reject"),
    ("recurring", "Day 32 should fail", {"text": "Bad monthly day", "schedule": "every month on the 32nd 09:00"}, "reject"),
    ("recurring", "Hour 25 should fail", {"text": "Bad hour", "schedule": "every day 25:00"}, "reject"),
    ("recurring", "Zero-day interval should fail", {"text": "Bad interval", "schedule": "every 0 days 09:00"}, "reject"),
]


def _classify(result: str) -> str:
    lowered = result.lower()
    if "added" in lowered:
        return "accept"
    if "incomplete: need" in lowered:
        return "missing"
    if "incomplete" in lowered:
        return "reject"
    return "other"


def main() -> int:
    scratch = ROOT / "tests" / "integration" / ".tmp" / f"oc-scheduler-realistic-{uuid.uuid4().hex}"
    scratch.mkdir(parents=True, exist_ok=False)
    original_todo = scheduler_tools.scheduler.TODO_PATH
    original_schedule = scheduler_tools.scheduler.SCHEDULE_PATH
    original_now = scheduler_tools.scheduler._now
    scheduler_tools.scheduler.TODO_PATH = scratch / "todo.md"
    scheduler_tools.scheduler.SCHEDULE_PATH = scratch / "schedule.md"
    scheduler_tools.scheduler._now = lambda: datetime(2026, 5, 1, 17, 30)
    scheduler_tools.scheduler._invalidate_reminder_caches()
    rows = []
    try:
        for index, (kind, label, kwargs, expected) in enumerate(CASES, start=1):
            result = scheduler_tools.add_reminder(**kwargs) if kind == "one_off" else scheduler_tools.add_recurring_reminder(**kwargs)
            actual = _classify(result)
            rows.append((index, actual == expected, kind, expected, actual, label, result))

        case_schedule_path = scheduler_tools.scheduler.SCHEDULE_PATH
        scheduler_tools.scheduler.SCHEDULE_PATH = scratch / "schedule-cache.md"
        scheduler_tools.scheduler._invalidate_reminder_caches()
        scheduler_tools.scheduler.SCHEDULE_PATH.write_text("- [ ] Alpha cache | every day 09:00\n", encoding="utf-8")
        first = scheduler_tools.scheduler._schedule_reminders()
        scheduler_tools.scheduler.SCHEDULE_PATH.write_text("- [ ] Beta cache task | every day 10:00\n", encoding="utf-8")
        second = scheduler_tools.scheduler._schedule_reminders()
        scheduler_tools.scheduler.SCHEDULE_PATH = case_schedule_path
        scheduler_tools.scheduler._invalidate_reminder_caches()
        cache_ok = [item.text for item in first] == ["Alpha cache"] and [item.text for item in second] == ["Beta cache task"]
        rows.append((
            len(rows) + 1,
            cache_ok,
            "cache",
            "refresh",
            "refresh" if cache_ok else f"{[item.text for item in first]} -> {[item.text for item in second]}",
            "Scheduler parse cache refreshes when schedule.md changes",
            "cache refresh check",
        ))
    finally:
        todo_text = scheduler_tools.scheduler.TODO_PATH.read_text(encoding="utf-8") if scheduler_tools.scheduler.TODO_PATH.exists() else ""
        schedule_text = scheduler_tools.scheduler.SCHEDULE_PATH.read_text(encoding="utf-8") if scheduler_tools.scheduler.SCHEDULE_PATH.exists() else ""
        scheduler_tools.scheduler.TODO_PATH = original_todo
        scheduler_tools.scheduler.SCHEDULE_PATH = original_schedule
        scheduler_tools.scheduler._now = original_now
        scheduler_tools.scheduler._invalidate_reminder_caches()
        shutil.rmtree(scratch, ignore_errors=True)

    passed = sum(1 for row in rows if row[1])
    summary = {
        "total": len(rows),
        "passed": passed,
        "failed": len(rows) - passed,
        "todo_reminder_lines": len([line for line in todo_text.splitlines() if line.strip().startswith("- [ ]")]),
        "schedule_lines": len([line for line in schedule_text.splitlines() if line.strip().startswith("- [ ]")]),
    }
    print("SUMMARY", json.dumps(summary, sort_keys=True))
    for index, ok, kind, expected, actual, label, result in rows:
        status = "OK" if ok else "MISMATCH"
        print(f"{index:02d} [{status}] {kind} expected={expected} actual={actual} :: {label}")
        if not ok:
            print(f"    result={result}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
