"""Unit tests for timezone-aware date parsing."""

from __future__ import annotations

from datetime import datetime

import pytest

from dates import (
    datetime_to_unix,
    end_of_day,
    ensure_aware,
    is_reasonable_task_due,
    parse_datetime,
    parse_period,
    start_of_day,
    unix_to_datetime,
)

TZ = "Europe/Moscow"


def test_unix_roundtrip() -> None:
    original = parse_datetime("2026-09-04 12:00:00", TZ)
    restored = unix_to_datetime(datetime_to_unix(original), TZ)
    assert restored is not None
    assert restored == original


def test_naive_datetime_is_localized() -> None:
    naive = datetime(2026, 9, 4, 10, 0, 0)
    aware = ensure_aware(naive, TZ)
    assert aware.tzinfo is not None
    assert aware.hour == 10


def test_date_only_becomes_midnight() -> None:
    value = parse_datetime("2026-09-04", TZ)
    assert value.hour == 0
    assert value.tzinfo is not None


def test_period_defaults_to_today() -> None:
    start, finish = parse_period(None, None, TZ)
    now = parse_datetime("now", TZ)
    assert start.date() == now.date()
    assert finish.date() == now.date()
    assert start.hour == 0
    assert finish.hour == 23


def test_period_date_only_covers_full_days() -> None:
    start, finish = parse_period("2026-09-01", "2026-09-04", TZ)
    assert start == start_of_day(parse_datetime("2026-09-01", TZ), TZ)
    assert finish == end_of_day(parse_datetime("2026-09-04", TZ), TZ)


def test_period_rejects_inverted_range() -> None:
    with pytest.raises(ValueError):
        parse_period("2026-09-10", "2026-09-01", TZ)


def test_relative_tomorrow() -> None:
    today = parse_datetime("today", TZ)
    tomorrow = parse_datetime("tomorrow", TZ)
    assert (tomorrow.date() - today.date()).days == 1


def test_reasonable_task_due() -> None:
    now = parse_datetime("2026-09-04 12:00:00", TZ)
    ok, _ = is_reasonable_task_due(parse_datetime("2026-09-05 12:00:00", TZ), now)
    assert ok
    bad, reason = is_reasonable_task_due(parse_datetime("2026-09-03 12:00:00", TZ), now)
    assert not bad
    assert "past" in reason
