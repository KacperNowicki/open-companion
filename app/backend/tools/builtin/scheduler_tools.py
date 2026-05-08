from __future__ import annotations

try:
    from app.backend import scheduler
except ImportError:  # pragma: no cover - fallback for direct module execution
    import scheduler  # type: ignore


def add_reminder(text: str = "", due_at: str = "", current_user_message: str = "", config=None) -> str:
    """
    Add a one-off reminder to the scheduler, or return the missing details
    needed to complete the reminder on a later turn.
    due_at: ISO datetime or natural English e.g. '2026-05-02T10:00:00',
    'tomorrow at 9 AM', 'next Friday noon', or 'May 5 at 8 AM'
    """
    del config
    clean_text = str(text or "").strip()
    clean_due_at = str(due_at or "").strip()
    clean_user_message = str(current_user_message or "").strip()
    missing = []
    if not clean_text:
        missing.append("what to remind the user about")
    if not clean_due_at:
        missing.append("when to remind the user")
    if missing:
        return (
            "Reminder details incomplete: need "
            + " and ".join(missing)
            + ". Ask the user for the missing detail, then call add_reminder again with text and due_at."
        )
    try:
        if clean_user_message:
            parsed_from_user = scheduler._parse_natural_datetime(clean_user_message)
            if parsed_from_user is not None:
                clean_due_at = parsed_from_user.isoformat(timespec="minutes")
        reminder = scheduler.add_reminder(clean_text, clean_due_at)
    except Exception as exc:
        return (
            f"Reminder details incomplete: {exc}. Ask the user for a clear future date/time, "
            "then call add_reminder again with text and due_at."
        )
    due_text = reminder.due_at.isoformat(timespec="minutes")
    return f"Reminder added: {reminder.text} at {due_text} (id: {reminder.reminder_id})."


def add_recurring_reminder(text: str = "", schedule: str = "", current_user_message: str = "", config=None) -> str:
    """
    Add a recurring reminder to schedule.md, or return the missing details
    needed to complete the reminder on a later turn.
    schedule examples:
    - daily 5pm
    - every morning
    - every other Monday at 9 AM
    - every month on the 15th 09:00
    - every year on April 21 at 10 AM
    """
    del config
    clean_text = str(text or "").strip()
    clean_schedule = str(schedule or "").strip()
    clean_user_message = str(current_user_message or "").strip()
    missing = []
    if not clean_text:
        missing.append("what to remind the user about")
    if not clean_schedule:
        missing.append("the recurring schedule")
    if missing:
        return (
            "Recurring reminder details incomplete: need "
            + " and ".join(missing)
            + ". Ask the user for the missing detail, then call add_recurring_reminder again with text and schedule."
        )
    try:
        if clean_user_message:
            normalized_from_user = scheduler._normalize_recurring_schedule(clean_user_message)
            if normalized_from_user:
                try:
                    scheduler._parse_dsl_to_cron(normalized_from_user)
                    clean_schedule = normalized_from_user
                except Exception:
                    pass
        clean_schedule = scheduler._normalize_recurring_schedule(clean_schedule)
        reminder = scheduler.add_recurring_reminder(clean_text, clean_schedule)
    except Exception as exc:
        return (
            f"Recurring reminder details incomplete: {exc}. Ask the user for a clearer schedule such as "
            "'daily 5pm', 'every other Monday at 9 AM', 'every month on the 15th at noon', "
            "or 'every year on April 21 at 10 AM', then call add_recurring_reminder again."
        )
    return f"Recurring reminder added: {reminder.text} | {clean_schedule} (id: {reminder.reminder_id})."


def snooze_reminder(reminder_id: str, until: str, config=None) -> str:
    """
    Snooze a reminder until a specific datetime.
    until: ISO datetime string e.g. '2026-04-22T09:00:00'
    Returns confirmation string.
    """
    del config
    clean_id = str(reminder_id or "").strip()
    clean_until = str(until or "").strip()
    if not clean_id:
        return "Reminder ID is required."
    if not clean_until:
        return "Snooze time is required."
    scheduler.snooze_reminder(clean_id, clean_until)
    return f"Reminder snoozed until {clean_until}."
