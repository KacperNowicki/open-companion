from __future__ import annotations

import calendar
import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, time as datetime_time, timedelta

try:
    from croniter import croniter  # type: ignore
except ImportError:  # pragma: no cover - optional dependency in some test envs
    croniter = None

try:
    from app.backend.runtime_paths import MEMORY_DIR, PROFILE_ROOT, VAULT_DIR, ensure_runtime_dirs
except ImportError:  # pragma: no cover - fallback for direct module execution
    from runtime_paths import MEMORY_DIR, PROFILE_ROOT, VAULT_DIR, ensure_runtime_dirs

logger = logging.getLogger("scheduler")

TODO_PATH = VAULT_DIR / "todo.md"
SCHEDULE_PATH = PROFILE_ROOT / "companion" / "schedule.md"
STATE_PATH = MEMORY_DIR / "schedule_state.json"
TICK_SECONDS = 30
STARTER_SCHEDULE_MD = """# Schedule

Add recurring reminders below. The companion can help you add entries.

<!-- Format: - [ ] Description | every PATTERN HH:MM -->
<!-- Examples:
- Weekly review: every Monday 09:00
- Birthday reminder: every year on Apr 21 10:00
- Quarterly tax payment: every 3 months on the 1st 09:00
-->
"""

_TODO_REMINDER_RE = re.compile(
    r"^\s*-\s*\[(?P<checked>[ xX])\]\s*(?P<text>.*?)\s*\|\s*remind:\s*(?P<when>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})(?P<suffix>.*)$"
)
_SCHEDULE_REMINDER_RE = re.compile(
    r"^\s*-\s*\[(?P<checked>[ xX])\]\s*(?P<text>.*?)\s*\|\s*(?P<dsl>every.+?)\s*$",
    re.IGNORECASE,
)
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_MONTH_NAMES = {
    **_MONTHS,
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_DOW = {
    "monday": 1,
    "mon": 1,
    "tuesday": 2,
    "tue": 2,
    "wednesday": 3,
    "wed": 3,
    "thursday": 4,
    "thu": 4,
    "friday": 5,
    "fri": 5,
    "saturday": 6,
    "sat": 6,
    "sunday": 0,
    "sun": 0,
}

_scheduler_lock = threading.RLock()
_scheduler_thread: threading.Thread | None = None
_scheduler_stop = threading.Event()
_scheduler_emit = None
_todo_cache_signature: tuple[str, bool, int, int] | None = None
_todo_cache_reminders: list["Reminder"] = []
_schedule_cache_signature: tuple[str, bool, int, int] | None = None
_schedule_cache_reminders: list["Reminder"] = []


@dataclass(slots=True)
class Reminder:
    reminder_id: str
    text: str
    due_at: datetime
    source: str
    recurring: bool = False
    cron_expression: str | None = None
    interval_weeks: int = 1
    interval_years: int = 1
    line_number: int | None = None
    fired_marked: bool = False
    original_due_at: datetime | None = None


def _path_signature(path) -> tuple[str, bool, int, int]:
    try:
        resolved = str(path.resolve())
    except OSError:
        resolved = str(path)
    try:
        stat = path.stat()
    except OSError:
        return resolved, False, 0, 0
    return resolved, True, stat.st_mtime_ns, stat.st_size


def _clone_reminder(reminder: Reminder) -> Reminder:
    return Reminder(
        reminder_id=reminder.reminder_id,
        text=reminder.text,
        due_at=reminder.due_at,
        source=reminder.source,
        recurring=reminder.recurring,
        cron_expression=reminder.cron_expression,
        interval_weeks=reminder.interval_weeks,
        interval_years=reminder.interval_years,
        line_number=reminder.line_number,
        fired_marked=reminder.fired_marked,
        original_due_at=reminder.original_due_at,
    )


def _clone_reminders(reminders: list[Reminder]) -> list[Reminder]:
    return [_clone_reminder(reminder) for reminder in reminders]


def _invalidate_reminder_caches() -> None:
    global _todo_cache_signature, _todo_cache_reminders
    global _schedule_cache_signature, _schedule_cache_reminders

    with _scheduler_lock:
        _todo_cache_signature = None
        _todo_cache_reminders = []
        _schedule_cache_signature = None
        _schedule_cache_reminders = []


def _now() -> datetime:
    return datetime.now()


def _format_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="seconds")


def _parse_datetime(raw_value: str | None) -> datetime | None:
    clean = str(raw_value or "").strip()
    if not clean:
        return None
    normalized = clean.replace(" ", "T", 1) if " " in clean and "T" not in clean else clean
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _strip_ordinals(value: str) -> str:
    return re.sub(r"\b(\d{1,2})(st|nd|rd|th)\b", r"\1", value, flags=re.IGNORECASE)


def _format_ordinal(value: int) -> str:
    if 10 <= value % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


def _normalize_natural_text(value: str | None) -> str:
    clean = str(value or "").strip().lower()
    clean = clean.replace(",", " ")
    clean = re.sub(r"\s+", " ", clean)
    return _strip_ordinals(clean).strip()


def _parse_time_phrase(value: str, *, default: datetime_time | None = None) -> datetime_time | None:
    clean = _normalize_natural_text(value)
    if not clean:
        return default
    daypart_times = (
        ("after lunch", datetime_time(13, 0)),
        ("before bed", datetime_time(22, 30)),
        ("later today", datetime_time(18, 0)),
        ("tonight", datetime_time(21, 0)),
        ("this evening", datetime_time(19, 0)),
        ("evening", datetime_time(19, 0)),
        ("this afternoon", datetime_time(13, 0)),
        ("afternoon", datetime_time(13, 0)),
        ("this morning", datetime_time(9, 0)),
        ("morning", datetime_time(9, 0)),
        ("noon", datetime_time(12, 0)),
        ("midnight", datetime_time(0, 0)),
    )
    for phrase, parsed in daypart_times:
        if re.search(rf"\b{re.escape(phrase)}\b", clean):
            return parsed

    def _time_from_match(match: re.Match[str]) -> datetime_time:
        hour = int(match.group("hour"))
        minute = int(match.group("minute") or 0)
        period = str(match.group("period") or "").replace(".", "")
        if not 0 <= minute <= 59:
            raise ValueError("minute must be between 0 and 59")
        if period:
            if not 1 <= hour <= 12:
                raise ValueError("12-hour time must use an hour from 1 to 12")
            if period == "pm" and hour != 12:
                hour += 12
            elif period == "am" and hour == 12:
                hour = 0
        elif not 0 <= hour <= 23:
            raise ValueError("hour must be between 0 and 23")
        return datetime_time(hour, minute)

    explicit = re.search(r"\b(?:at|by|around)\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<period>a\.?m\.?|p\.?m\.?)?\b", clean)
    if explicit:
        return _time_from_match(explicit)

    invalid_error: ValueError | None = None
    matches = list(re.finditer(r"\b(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<period>a\.?m\.?|p\.?m\.?)?\b", clean))
    for match in matches:
        try:
            return _time_from_match(match)
        except ValueError as exc:
            invalid_error = exc
            if match.group("minute") or match.group("period"):
                raise
    if invalid_error is not None:
        raise invalid_error
    return default


def _parse_isoish_datetime(clean: str) -> datetime | None:
    match = re.search(
        r"\b(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})(?:\s+(?:at\s+)?)?(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<period>a\.?m\.?|p\.?m\.?)?\b",
        clean,
    )
    if not match:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    period = str(match.group("period") or "").replace(".", "")
    if not 0 <= minute <= 59:
        raise ValueError("minute must be between 0 and 59")
    if period:
        if not 1 <= hour <= 12:
            raise ValueError("12-hour time must use an hour from 1 to 12")
        if period == "pm" and hour != 12:
            hour += 12
        elif period == "am" and hour == 12:
            hour = 0
    elif not 0 <= hour <= 23:
        raise ValueError("hour must be between 0 and 23")
    try:
        return datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            hour,
            minute,
        )
    except ValueError as exc:
        raise ValueError(f"invalid date/time: {exc}") from exc


def _next_weekday(now_value: datetime, weekday: int, *, force_next: bool = False) -> datetime:
    python_weekday = 6 if weekday == 0 else weekday - 1
    days_ahead = (python_weekday - now_value.weekday()) % 7
    if days_ahead == 0 and force_next:
        days_ahead = 7
    return now_value + timedelta(days=days_ahead)


def _parse_month_day(clean: str, now_value: datetime) -> datetime | None:
    month_pattern = "|".join(sorted((re.escape(name) for name in _MONTH_NAMES), key=len, reverse=True))
    patterns = (
        rf"\b(?P<month>{month_pattern}) (?P<day>\d{{1,2}})(?: (?P<year>\d{{4}}))?\b",
        rf"\b(?P<day>\d{{1,2}}) (?P<month>{month_pattern})(?: (?P<year>\d{{4}}))?\b",
    )
    for pattern in patterns:
        match = re.search(pattern, clean)
        if not match:
            continue
        month = _MONTH_NAMES[match.group("month")]
        day = int(match.group("day"))
        year = int(match.group("year") or now_value.year)
        if not 1 <= day <= calendar.monthrange(year, month)[1]:
            raise ValueError("day must be valid for the requested month")
        candidate = now_value.replace(year=year, month=month, day=day, hour=0, minute=0, second=0, microsecond=0)
        if not match.group("year") and candidate.date() < now_value.date():
            candidate = candidate.replace(year=year + 1)
        return candidate
    return None


def _parse_natural_datetime(raw_value: str | None, *, default_time: datetime_time | None = None) -> datetime | None:
    parsed_iso = _parse_datetime(raw_value)
    if parsed_iso is not None:
        return parsed_iso

    clean = _normalize_natural_text(raw_value)
    if not clean:
        return None
    now_value = _now().replace(second=0, microsecond=0)
    isoish = _parse_isoish_datetime(clean)
    if isoish is not None:
        return isoish

    relative = re.search(r"\bin (?P<amount>\d+) (?P<unit>minutes?|mins?|hours?|hrs?|days?|weeks?)\b", clean)
    if relative:
        amount = int(relative.group("amount"))
        unit = relative.group("unit")
        if amount < 1:
            raise ValueError("relative reminder interval must be at least 1")
        if unit.startswith(("minute", "min")):
            return now_value + timedelta(minutes=amount)
        if unit.startswith(("hour", "hr")):
            return now_value + timedelta(hours=amount)
        if unit.startswith("day"):
            return now_value + timedelta(days=amount)
        if unit.startswith("week"):
            return now_value + timedelta(weeks=amount)

    parsed_time = _parse_time_phrase(clean, default=default_time)
    if parsed_time is None:
        return None

    if re.search(r"\btoday\b", clean) or re.search(r"\blater today\b", clean) or re.search(r"\btonight\b", clean):
        base = now_value
    elif re.search(r"\btomorrow\b", clean):
        base = now_value + timedelta(days=1)
    elif re.search(r"\bnext week\b", clean):
        base = now_value + timedelta(days=7)
    else:
        base = _parse_month_day(clean, now_value)
        if base is None:
            weekday_match = re.search(r"\bnext (?P<weekday>mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)\b", clean)
            if weekday_match:
                weekday = _weekday_number(weekday_match.group("weekday"))
                if weekday is None:
                    return None
                base = _next_weekday(now_value, weekday, force_next=True)
            else:
                weekday_match = re.search(r"\b(?P<weekday>mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)\b", clean)
                if weekday_match:
                    weekday = _weekday_number(weekday_match.group("weekday"))
                    if weekday is None:
                        return None
                    base = _next_weekday(now_value, weekday, force_next=True)
                else:
                    return None
    return base.replace(hour=parsed_time.hour, minute=parsed_time.minute, second=0, microsecond=0)


def _stable_reminder_id(text: str) -> str:
    clean = str(text or "").strip()
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()[:8]


def add_reminder(text: str, due_at: str) -> Reminder:
    clean_text = re.sub(r"\s+", " ", str(text or "").strip())
    if not clean_text:
        raise ValueError("reminder text is required")
    parsed_due = _parse_natural_datetime(due_at)
    if parsed_due is None:
        raise ValueError("due_at must be an ISO datetime or a clear natural time like tomorrow at 9 AM")
    if parsed_due <= _now().replace(second=0, microsecond=0):
        raise ValueError("due_at must be in the future")

    ensure_runtime_dirs()
    TODO_PATH.parent.mkdir(parents=True, exist_ok=True)
    due_text = parsed_due.strftime("%Y-%m-%d %H:%M")
    line = f"- [ ] {clean_text} | remind: {due_text}"
    existing = TODO_PATH.read_text(encoding="utf-8") if TODO_PATH.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    TODO_PATH.write_text(existing + line + "\n", encoding="utf-8")
    _invalidate_reminder_caches()
    return Reminder(
        reminder_id=_stable_reminder_id(clean_text),
        text=clean_text,
        due_at=parsed_due,
        source="todo",
        recurring=False,
        original_due_at=parsed_due,
    )


def add_recurring_reminder(text: str, schedule: str) -> Reminder:
    clean_text = re.sub(r"\s+", " ", str(text or "").strip())
    clean_schedule = _normalize_recurring_schedule(schedule)
    if not clean_text:
        raise ValueError("reminder text is required")
    if not clean_schedule:
        raise ValueError("schedule is required")

    cron_expression, interval_weeks, interval_years = _parse_dsl_to_cron(clean_schedule)
    _ensure_schedule_file()
    line = f"- [ ] {clean_text} | {clean_schedule}"
    existing = SCHEDULE_PATH.read_text(encoding="utf-8") if SCHEDULE_PATH.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    SCHEDULE_PATH.write_text(existing + line + "\n", encoding="utf-8")
    _invalidate_reminder_caches()
    return Reminder(
        reminder_id=_stable_reminder_id(clean_text),
        text=clean_text,
        due_at=_now(),
        source="schedule",
        recurring=True,
        cron_expression=cron_expression,
        interval_weeks=interval_weeks,
        interval_years=interval_years,
        original_due_at=None,
    )


def _ensure_schedule_file() -> None:
    ensure_runtime_dirs()
    SCHEDULE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not SCHEDULE_PATH.exists():
        SCHEDULE_PATH.write_text(STARTER_SCHEDULE_MD, encoding="utf-8")
        _invalidate_reminder_caches()


def _load_state() -> dict[str, dict]:
    ensure_runtime_dirs()
    if not STATE_PATH.exists():
        return {}
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Failed to read scheduler state from %s", STATE_PATH, exc_info=True)
        return {}
    if not isinstance(payload, dict):
        return {}

    normalized: dict[str, dict] = {}
    for reminder_id, raw in payload.items():
        if not isinstance(raw, dict):
            continue
        normalized[str(reminder_id)] = {
            "last_fired": str(raw.get("last_fired") or "").strip() or None,
            "snoozed_until": str(raw.get("snoozed_until") or "").strip() or None,
            "missed_count": int(raw.get("missed_count") or 0),
        }
    return normalized


def _save_state(state: dict[str, dict]) -> None:
    ensure_runtime_dirs()
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = STATE_PATH.with_name(f".{STATE_PATH.name}.tmp")
    temp_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    temp_path.replace(STATE_PATH)


def _state_entry(state: dict[str, dict], reminder_id: str) -> dict:
    return state.setdefault(
        reminder_id,
        {
            "last_fired": None,
            "snoozed_until": None,
            "missed_count": 0,
        },
    )


def _weekday_number(raw_weekday: str) -> int | None:
    return _DOW.get(str(raw_weekday or "").strip().lower())


def _canonical_time_text(raw_value: str, *, default: datetime_time = datetime_time(9, 0)) -> str:
    parsed = _parse_time_phrase(raw_value, default=default)
    if parsed is None:
        parsed = default
    return f"{parsed.hour:02d}:{parsed.minute:02d}"


def _normalize_weekday_text(raw_weekday: str) -> str | None:
    weekday = _weekday_number(raw_weekday)
    if weekday is None:
        return None
    names = {
        0: "Sunday",
        1: "Monday",
        2: "Tuesday",
        3: "Wednesday",
        4: "Thursday",
        5: "Friday",
        6: "Saturday",
    }
    return names[weekday]


def _normalize_recurring_schedule(raw_schedule: str | None) -> str:
    clean = _normalize_natural_text(raw_schedule)
    if not clean:
        return ""
    if not clean.startswith(("daily", "every", "weekday", "weekdays")):
        embedded = re.search(r"\b(daily\b.*|every\b.*)", clean)
        if embedded:
            clean = embedded.group(1).strip()
    clean = re.split(r"\s+(?:to|for|remind me|remind)\b", clean, maxsplit=1)[0].strip()

    if clean in {"daily", "every day", "every morning"}:
        return f"every day {_canonical_time_text(clean)}"
    if clean.startswith("daily "):
        return f"every day {_canonical_time_text(clean)}"

    match = re.fullmatch(r"every day(?: at)? (?P<time>.+)", clean)
    if match:
        return f"every day {_canonical_time_text(match.group('time'))}"

    match = re.fullmatch(r"every (?P<days>\d+) days(?: at)? (?P<time>.+)", clean)
    if match:
        return f"every {int(match.group('days'))} days {_canonical_time_text(match.group('time'))}"

    if clean in {"weekday", "weekdays", "every weekday", "every weekdays"}:
        return f"every weekday {_canonical_time_text(clean)}"
    match = re.fullmatch(r"(?:every )?weekdays?(?: at)? (?P<time>.+)", clean)
    if match:
        return f"every weekday {_canonical_time_text(match.group('time'))}"

    weekday_words = r"mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?"
    match = re.fullmatch(rf"every other (?P<weekday>{weekday_words})(?: at)?(?: (?P<time>.+))?", clean)
    if match:
        weekday_name = _normalize_weekday_text(match.group("weekday"))
        if weekday_name:
            return f"every other {weekday_name} {_canonical_time_text(match.group('time') or '')}"

    match = re.fullmatch(rf"every (?P<weeks>\d+) weeks on (?P<weekday>{weekday_words})(?: at)? (?P<time>.+)", clean)
    if match:
        weekday_name = _normalize_weekday_text(match.group("weekday"))
        if weekday_name:
            return f"every {int(match.group('weeks'))} weeks on {weekday_name} {_canonical_time_text(match.group('time'))}"

    match = re.fullmatch(rf"every week on (?P<weekday>{weekday_words})(?: at)? (?P<time>.+)", clean)
    if match:
        weekday_name = _normalize_weekday_text(match.group("weekday"))
        if weekday_name:
            return f"every week on {weekday_name} {_canonical_time_text(match.group('time'))}"

    match = re.fullmatch(rf"every (?P<weekday>{weekday_words})(?: at)?(?: (?P<time>.+))?", clean)
    if match:
        weekday_name = _normalize_weekday_text(match.group("weekday"))
        if weekday_name:
            return f"every {weekday_name} {_canonical_time_text(match.group('time') or '')}"

    match = re.fullmatch(r"every month on (?:the )?(?P<day>\d{1,2})(?: at)? (?P<time>.+)", clean)
    if match:
        return f"every month on the {_format_ordinal(int(match.group('day')))} {_canonical_time_text(match.group('time'))}"

    match = re.fullmatch(r"every (?P<months>\d+) months on (?:the )?(?P<day>\d{1,2})(?: at)? (?P<time>.+)", clean)
    if match:
        return f"every {int(match.group('months'))} months on the {_format_ordinal(int(match.group('day')))} {_canonical_time_text(match.group('time'))}"

    month_words = "|".join(sorted((re.escape(name) for name in _MONTH_NAMES), key=len, reverse=True))
    match = re.fullmatch(rf"every year on (?P<month>{month_words}) (?P<day>\d{{1,2}})(?: at)? (?P<time>.+)", clean)
    if match:
        month = calendar.month_abbr[_MONTH_NAMES[match.group("month")]]
        return f"every year on {month} {int(match.group('day'))} {_canonical_time_text(match.group('time'))}"

    match = re.fullmatch(rf"every (?P<years>\d+) years on (?P<month>{month_words}) (?P<day>\d{{1,2}})(?: at)? (?P<time>.+)", clean)
    if match:
        month = calendar.month_abbr[_MONTH_NAMES[match.group("month")]]
        return f"every {int(match.group('years'))} years on {month} {int(match.group('day'))} {_canonical_time_text(match.group('time'))}"

    return re.sub(r"\s+", " ", str(raw_schedule or "").strip())


def _parse_dsl_to_cron(dsl: str) -> tuple[str, int, int]:
    clean = re.sub(r"\s+", " ", str(dsl or "").strip().lower())

    def _time(match: re.Match[str]) -> tuple[int, int]:
        hour = int(match.group("hour"))
        minute = int(match.group("minute"))
        if not 0 <= hour <= 23:
            raise ValueError("hour must be between 0 and 23")
        if not 0 <= minute <= 59:
            raise ValueError("minute must be between 0 and 59")
        return hour, minute

    def _positive_interval(match: re.Match[str], name: str) -> int:
        value = int(match.group(name))
        if value < 1:
            raise ValueError(f"{name} interval must be at least 1")
        return value

    def _month_day(match: re.Match[str], month: int | None = None) -> int:
        day = int(match.group("day"))
        max_day = calendar.monthrange(2000, month)[1] if month else 31
        if not 1 <= day <= max_day:
            raise ValueError("day must be valid for the schedule")
        return day

    match = re.fullmatch(r"every day (?:at )?(?P<hour>\d{1,2}):(?P<minute>\d{2})", clean)
    if match:
        hour, minute = _time(match)
        return f"{minute} {hour} * * *", 1, 1

    match = re.fullmatch(r"every weekday (?:at )?(?P<hour>\d{1,2}):(?P<minute>\d{2})", clean)
    if match:
        hour, minute = _time(match)
        return f"{minute} {hour} * * 1-5", 1, 1

    match = re.fullmatch(r"every (?P<days>\d+) days (?:at )?(?P<hour>\d{1,2}):(?P<minute>\d{2})", clean)
    if match:
        days = _positive_interval(match, "days")
        hour, minute = _time(match)
        return f"{minute} {hour} */{days} * *", 1, 1

    match = re.fullmatch(r"every week on (?P<weekday>[a-z]+) (?:at )?(?P<hour>\d{1,2}):(?P<minute>\d{2})", clean)
    if match:
        weekday = _weekday_number(match.group("weekday"))
        if weekday is None:
            raise ValueError("unknown weekday")
        hour, minute = _time(match)
        return f"{minute} {hour} * * {weekday}", 1, 1

    match = re.fullmatch(r"every (?P<weeks>\d+) weeks on (?P<weekday>[a-z]+) (?:at )?(?P<hour>\d{1,2}):(?P<minute>\d{2})", clean)
    if match:
        weekday = _weekday_number(match.group("weekday"))
        if weekday is None:
            raise ValueError("unknown weekday")
        weeks = _positive_interval(match, "weeks")
        hour, minute = _time(match)
        return f"{minute} {hour} * * {weekday}", weeks, 1

    match = re.fullmatch(r"every other (?P<weekday>[a-z]+) (?:at )?(?P<hour>\d{1,2}):(?P<minute>\d{2})", clean)
    if match:
        weekday = _weekday_number(match.group("weekday"))
        if weekday is None:
            raise ValueError("unknown weekday")
        hour, minute = _time(match)
        return f"{minute} {hour} * * {weekday}", 2, 1

    match = re.fullmatch(r"every (?P<weekday>[a-z]+) (?:at )?(?P<hour>\d{1,2}):(?P<minute>\d{2})", clean)
    if match:
        weekday = _weekday_number(match.group("weekday"))
        if weekday is None:
            raise ValueError("unknown weekday")
        hour, minute = _time(match)
        return f"{minute} {hour} * * {weekday}", 1, 1

    match = re.fullmatch(
        r"every month on the (?P<day>\d{1,2})(?:st|nd|rd|th) (?:at )?(?P<hour>\d{1,2}):(?P<minute>\d{2})",
        clean,
    )
    if match:
        day = _month_day(match)
        hour, minute = _time(match)
        return f"{minute} {hour} {day} * *", 1, 1

    match = re.fullmatch(
        r"every (?P<months>\d+) months on the (?P<day>\d{1,2})(?:st|nd|rd|th) (?:at )?(?P<hour>\d{1,2}):(?P<minute>\d{2})",
        clean,
    )
    if match:
        months = _positive_interval(match, "months")
        day = _month_day(match)
        hour, minute = _time(match)
        return f"{minute} {hour} {day} */{months} *", 1, 1

    match = re.fullmatch(r"every year on (?P<month>[a-z]{3,9}) (?P<day>\d{1,2}) (?:at )?(?P<hour>\d{1,2}):(?P<minute>\d{2})", clean)
    if match:
        month = _MONTHS.get(match.group("month")[:3])
        if month is None:
            raise ValueError("unknown month")
        day = _month_day(match, month)
        hour, minute = _time(match)
        return f"{minute} {hour} {day} {month} *", 1, 1

    match = re.fullmatch(r"every (?P<years>\d+) years on (?P<month>[a-z]{3,9}) (?P<day>\d{1,2}) (?:at )?(?P<hour>\d{1,2}):(?P<minute>\d{2})", clean)
    if match:
        month = _MONTHS.get(match.group("month")[:3])
        if month is None:
            raise ValueError("unknown month")
        years = _positive_interval(match, "years")
        day = _month_day(match, month)
        hour, minute = _time(match)
        return f"{minute} {hour} {day} {month} *", 1, years

    raise ValueError("unsupported schedule DSL")


def _week_interval_matches(value: datetime, interval_weeks: int) -> bool:
    if interval_weeks <= 1:
        return True
    anchor = datetime(1970, 1, 5, value.hour, value.minute)
    delta_days = (value.date() - anchor.date()).days
    week_index = delta_days // 7
    return week_index % interval_weeks == 0


def _year_interval_matches(value: datetime, interval_years: int) -> bool:
    if interval_years <= 1:
        return True
    return (value.year - 1970) % interval_years == 0


def _latest_recurring_occurrence(reminder: Reminder, now_value: datetime) -> datetime | None:
    if croniter is None or not reminder.cron_expression:
        return None
    iterator = croniter(reminder.cron_expression, now_value)
    attempts = 0
    while attempts < 512:
        candidate = iterator.get_prev(datetime)
        if _week_interval_matches(candidate, reminder.interval_weeks) and _year_interval_matches(candidate, reminder.interval_years):
            return candidate
        attempts += 1
    logger.warning("Could not resolve recurring occurrence for %s after %s attempts", reminder.text, attempts)
    return None


def _cached_todo_reminders() -> list[Reminder]:
    global _todo_cache_signature, _todo_cache_reminders

    signature = _path_signature(TODO_PATH)
    with _scheduler_lock:
        if signature == _todo_cache_signature:
            return _clone_reminders(_todo_cache_reminders)

    if not signature[1]:
        parsed: list[Reminder] = []
    else:
        parsed = []
        try:
            lines = TODO_PATH.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []

        for index, line in enumerate(lines, start=1):
            match = _TODO_REMINDER_RE.match(line)
            if not match:
                continue
            if match.group("checked").lower() == "x":
                continue
            text = str(match.group("text") or "").strip()
            due_at = _parse_datetime(match.group("when"))
            if not text or due_at is None:
                logger.warning("Skipping invalid todo reminder on line %s of %s", index, TODO_PATH)
                continue

            parsed.append(
                Reminder(
                    reminder_id=_stable_reminder_id(text),
                    text=text,
                    due_at=due_at,
                    source="todo",
                    recurring=False,
                    line_number=index,
                    fired_marked="~fired" in str(match.group("suffix") or ""),
                    original_due_at=due_at,
                )
            )

    with _scheduler_lock:
        _todo_cache_signature = signature
        _todo_cache_reminders = _clone_reminders(parsed)
    return _clone_reminders(parsed)


def _todo_reminders(state: dict[str, dict]) -> list[Reminder]:
    reminders: list[Reminder] = []
    for reminder in _cached_todo_reminders():
        entry = state.get(reminder.reminder_id, {})
        snoozed_until = _parse_datetime(entry.get("snoozed_until"))
        if reminder.fired_marked and snoozed_until is None:
            continue
        if snoozed_until is not None:
            reminder.due_at = snoozed_until
        reminders.append(reminder)
    return reminders


def _schedule_reminders() -> list[Reminder]:
    return _cached_schedule_reminders()


def _cached_schedule_reminders() -> list[Reminder]:
    global _schedule_cache_signature, _schedule_cache_reminders

    _ensure_schedule_file()
    signature = _path_signature(SCHEDULE_PATH)
    with _scheduler_lock:
        if signature == _schedule_cache_signature:
            return _clone_reminders(_schedule_cache_reminders)

    reminders: list[Reminder] = []
    in_html_comment = False
    try:
        lines = SCHEDULE_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []

    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if "<!--" in stripped:
            in_html_comment = True
        if in_html_comment:
            if "-->" in stripped:
                in_html_comment = False
            continue
        match = _SCHEDULE_REMINDER_RE.match(line)
        if not match:
            continue
        if match.group("checked").lower() == "x":
            continue

        text = str(match.group("text") or "").strip()
        dsl = str(match.group("dsl") or "").strip()
        if not text or not dsl:
            continue
        try:
            cron_expression, interval_weeks, interval_years = _parse_dsl_to_cron(dsl)
        except ValueError as exc:
            logger.warning("Skipping schedule line %s in %s: %s", index, SCHEDULE_PATH, exc)
            continue

        reminders.append(
            Reminder(
                reminder_id=_stable_reminder_id(text),
                text=text,
                due_at=_now(),
                source="schedule",
                recurring=True,
                cron_expression=cron_expression,
                interval_weeks=interval_weeks,
                interval_years=interval_years,
                original_due_at=None,
            )
        )

    with _scheduler_lock:
        _schedule_cache_signature = signature
        _schedule_cache_reminders = _clone_reminders(reminders)
    return _clone_reminders(reminders)


def _mark_todo_fired(reminder: Reminder) -> None:
    if reminder.source != "todo" or reminder.line_number is None or reminder.fired_marked or not TODO_PATH.exists():
        return

    lines = TODO_PATH.read_text(encoding="utf-8").splitlines()
    line_index = reminder.line_number - 1
    if line_index < 0 or line_index >= len(lines):
        return
    line = lines[line_index]
    if "~fired" in line:
        return
    lines[line_index] = f"{line} ~fired"
    TODO_PATH.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    _invalidate_reminder_caches()
    reminder.fired_marked = True


def _effective_due(reminder: Reminder, state: dict[str, dict], now_value: datetime) -> datetime | None:
    entry = state.get(reminder.reminder_id, {})
    last_fired = _parse_datetime(entry.get("last_fired"))
    snoozed_until = _parse_datetime(entry.get("snoozed_until"))

    if snoozed_until is not None:
        if last_fired is None or last_fired < snoozed_until:
            return snoozed_until
        return None

    if not reminder.recurring:
        if reminder.fired_marked:
            return None
        return reminder.due_at

    occurrence = _latest_recurring_occurrence(reminder, now_value)
    if occurrence is None:
        return None
    if last_fired is not None and occurrence <= last_fired:
        return None
    return occurrence


def _update_fired_state(reminder: Reminder, fired_at: datetime, *, missed: bool) -> None:
    with _scheduler_lock:
        state = _load_state()
        entry = _state_entry(state, reminder.reminder_id)
        entry["last_fired"] = _format_iso(fired_at)
        entry["snoozed_until"] = None
        entry["missed_count"] = int(entry.get("missed_count") or 0) + 1 if missed else 0
        _save_state(state)

    if reminder.source == "todo":
        _mark_todo_fired(reminder)


def _emit(payload: dict) -> None:
    if _scheduler_emit is None:
        return
    try:
        _scheduler_emit(payload)
    except Exception:
        logger.exception("Failed to emit scheduler event %s", payload.get("type"))


def _fire_due_reminder(reminder: Reminder, fired_at: datetime, *, missed: bool) -> None:
    _update_fired_state(reminder, fired_at, missed=missed)
    payload = {
        "type": "reminder_fired",
        "id": reminder.reminder_id,
        "text": reminder.text,
        "missed": missed,
    }
    if missed:
        payload["original_time"] = _format_iso(fired_at)
    _emit(payload)


def _fire_missed_batch(missed_items: list[tuple[Reminder, datetime]]) -> None:
    if not missed_items:
        return
    if len(missed_items) == 1:
        reminder, fired_at = missed_items[0]
        _fire_due_reminder(reminder, fired_at, missed=True)
        return

    items_payload = []
    for reminder, fired_at in missed_items:
        _update_fired_state(reminder, fired_at, missed=True)
        items_payload.append(
            {
                "id": reminder.reminder_id,
                "text": reminder.text,
                "original_time": _format_iso(fired_at),
            }
        )
    _emit({"type": "reminders_missed_batch", "items": items_payload})


def _collect_missed_reminders(now_value: datetime) -> list[tuple[Reminder, datetime]]:
    with _scheduler_lock:
        state = _load_state()
    reminders = _todo_reminders(state) + _schedule_reminders()
    missed: list[tuple[Reminder, datetime]] = []
    for reminder in reminders:
        due_at = _effective_due(reminder, state, now_value)
        if due_at is None or due_at > now_value:
            continue
        missed.append((reminder, due_at))
    return missed


def _process_due_reminders() -> None:
    now_value = _now()
    with _scheduler_lock:
        state = _load_state()
    reminders = _todo_reminders(state) + _schedule_reminders()
    for reminder in reminders:
        due_at = _effective_due(reminder, state, now_value)
        if due_at is None or due_at > now_value:
            continue
        _fire_due_reminder(reminder, due_at, missed=False)


def _run_loop() -> None:
    while not _scheduler_stop.wait(TICK_SECONDS):
        try:
            _process_due_reminders()
        except Exception:
            logger.exception("Scheduler tick failed unexpectedly")


def start_scheduler(config, emit_fn) -> None:
    del config  # scheduler uses runtime path constants today
    stop_scheduler()
    _ensure_schedule_file()

    global _scheduler_emit, _scheduler_thread
    _scheduler_emit = emit_fn
    _scheduler_stop.clear()

    try:
        missed = _collect_missed_reminders(_now())
        _fire_missed_batch(missed)
    except Exception:
        logger.exception("Scheduler missed-reminder recovery failed")

    _scheduler_thread = threading.Thread(target=_run_loop, name="scheduler", daemon=True)
    _scheduler_thread.start()


def stop_scheduler() -> None:
    global _scheduler_thread
    _scheduler_stop.set()
    thread = _scheduler_thread
    _scheduler_thread = None
    if thread and thread.is_alive():
        thread.join(timeout=1.5)


def snooze_reminder(reminder_id, until_iso) -> None:
    reminder_key = str(reminder_id or "").strip()
    if not reminder_key:
        raise ValueError("reminder_id is required")
    until_value = _parse_datetime(until_iso)
    if until_value is None:
        raise ValueError("until_iso must be a valid ISO datetime string")

    with _scheduler_lock:
        state = _load_state()
        entry = _state_entry(state, reminder_key)
        entry["snoozed_until"] = _format_iso(until_value)
        _save_state(state)
