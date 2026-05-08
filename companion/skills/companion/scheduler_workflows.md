---
purpose: Manage one-off reminders, recurring reminders, and fired-reminder snoozes.
triggers: reminders, remind me, recurring reminders, list reminders, complete reminder, reminder_fired
tools: get_current_time, read_file, append_file, replace_text_in_file, snooze_reminder
inputs: reminder text, date/time or recurrence, optional reminder_id during reminder_fired
outputs: updated reminder files or a concise reminder summary
risk: low
---

# Scheduler Workflows

## When To Use This Skill

Use this skill when the user asks to add, list, complete, or snooze reminders, or when handling a `reminder_fired` event.

## When Not To Use This Skill

Do not use this for ordinary notes, todos with no reminder time, timers, alarms outside the scheduler files, or calendar sync.

## Files Used

One-off reminders live in `todo.md`.
Recurring reminders live in `../schedule.md` relative to the vault root if accessible through vault tools as configured by the runtime.

## Add One-Off Reminder

Resolve relative time before writing. Use `get_current_time` when the request says tomorrow, tonight, next week, or any relative time.

Format:

`- [ ] <Reminder text> | remind: <YYYY-MM-DD HH:mm>`

Use `append_file` with `path: "todo.md"`. If `todo.md` does not exist, create it. Do not create duplicates. Ask one clarification only if the time is missing or impossible.

## Add Recurring Reminder

Format:

`- [ ] <Reminder text> | every <recurrence>`

Use `append_file` for the scheduler file. Preserve the user's recurrence wording when it is clear. Ask one clarification if the recurrence is ambiguous.

## List Reminders

Use `read_file` on `todo.md` and the recurring reminder file. Return unchecked one-off reminders containing `| remind:` and recurring reminders containing `| every`.

## Complete Reminder

Find the matching unchecked line. Use `replace_text_in_file` to replace only that line's `- [ ]` with `- [x]`. Do not edit unrelated reminders.

## Reminder Fired Event

Speak naturally. Mention the reminder text. Ask once whether the user wants to snooze. Do not ask twice about snoozing the same reminder.

## Snooze Reminder

Use `snooze_reminder` only if the user agrees and a `reminder_id` is available. Resolve relative snooze times with `get_current_time` when needed.

## Failure Handling

If a reminder file is missing, create it when adding reminders and say it was created. If listing or completing cannot find a reminder, say that plainly and stop instead of guessing.
