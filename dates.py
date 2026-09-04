"""Timezone-aware date helpers for amoCRM Unix timestamps and user input."""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?$")
UNIX_SECONDS = re.compile(r"^\d{9,12}$")
RELATIVE_DAYS = re.compile(r"^(?:in\s+)?(\d+)\s+days?$", re.IGNORECASE)


def app_tz(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def now_tz(tz_name: str) -> datetime:
    return datetime.now(tz=app_tz(tz_name))


def ensure_aware(value: datetime, tz_name: str) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=app_tz(tz_name))
    return value.astimezone(app_tz(tz_name))


def unix_to_datetime(timestamp: int | float | None, tz_name: str) -> datetime | None:
    if timestamp in (None, 0, "0"):
        return None
    return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).astimezone(app_tz(tz_name))


def datetime_to_unix(value: datetime) -> int:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return int(value.timestamp())


def isoformat_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def start_of_day(value: datetime, tz_name: str) -> datetime:
    local = ensure_aware(value, tz_name)
    return datetime.combine(local.date(), time.min, tzinfo=app_tz(tz_name))


def end_of_day(value: datetime, tz_name: str) -> datetime:
    local = ensure_aware(value, tz_name)
    return datetime.combine(local.date(), time.max.replace(microsecond=0), tzinfo=app_tz(tz_name))


def parse_datetime(value: str | int | float | datetime | None, tz_name: str) -> datetime:
    """Parse a user/LLM datetime into an aware datetime in APP_TIMEZONE."""
    if value is None or value == "":
        return now_tz(tz_name)
    if isinstance(value, datetime):
        return ensure_aware(value, tz_name)
    if isinstance(value, (int, float)):
        return unix_to_datetime(value, tz_name) or now_tz(tz_name)

    raw = str(value).strip()
    lowered = raw.lower()
    tz = app_tz(tz_name)
    current = now_tz(tz_name)

    if lowered in {"now", "сейчас"}:
        return current
    if lowered in {"today", "сегодня"}:
        return start_of_day(current, tz_name)
    if lowered in {"tomorrow", "завтра"}:
        return start_of_day(current + timedelta(days=1), tz_name)
    if lowered in {"yesterday", "вчера"}:
        return start_of_day(current - timedelta(days=1), tz_name)

    relative = RELATIVE_DAYS.match(raw)
    if relative:
        return current + timedelta(days=int(relative.group(1)))

    if UNIX_SECONDS.match(raw):
        parsed = unix_to_datetime(int(raw), tz_name)
        if parsed is None:
            raise ValueError(f"Invalid unix timestamp: {raw}")
        return parsed

    if DATE_ONLY.match(raw):
        return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=tz)

    normalized = raw.replace("T", " ")
    if DATE_TIME.match(raw) or DATE_TIME.match(normalized):
        if normalized.count(":") == 1:
            return datetime.strptime(normalized, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        return datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)

    parsed_iso = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return ensure_aware(parsed_iso, tz_name)


def parse_period(
    date_from: str | int | datetime | None,
    date_to: str | int | datetime | None,
    tz_name: str,
) -> tuple[datetime, datetime]:
    """Return inclusive [from, to] bounds. Missing period means the current local day."""
    current = now_tz(tz_name)
    if date_from in (None, "") and date_to in (None, ""):
        return start_of_day(current, tz_name), end_of_day(current, tz_name)

    start = start_of_day(parse_datetime(date_from or current, tz_name), tz_name)
    if date_to in (None, ""):
        finish = end_of_day(parse_datetime(date_from or current, tz_name), tz_name)
    else:
        finish_raw = parse_datetime(date_to, tz_name)
        raw = str(date_to).strip() if date_to is not None else ""
        finish = end_of_day(finish_raw, tz_name) if DATE_ONLY.match(raw) or raw.lower() in {
            "today",
            "сегодня",
            "yesterday",
            "вчера",
            "tomorrow",
            "завтра",
        } else finish_raw

    if finish < start:
        raise ValueError("date_to must be greater than or equal to date_from")
    return start, finish


def days_between(earlier: datetime, later: datetime) -> float:
    return (later - earlier).total_seconds() / 86400.0


def is_reasonable_task_due(due: datetime, now: datetime) -> tuple[bool, str]:
    if due < now - timedelta(hours=1):
        return False, "complete_till is in the past"
    if due > now + timedelta(days=730):
        return False, "complete_till is more than 2 years in the future"
    return True, "ok"
