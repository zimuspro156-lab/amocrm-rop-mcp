"""Stale lead calculation tests. No amoCRM HTTP."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from analytics import calculate_stale, is_active_lead, last_activity_timestamp

TZ = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=TZ)


def _ts(days_ago: int) -> int:
    return int((NOW - timedelta(days=days_ago)).timestamp())


def test_closed_lead_is_not_stale() -> None:
    lead = {"id": 1, "status_id": 142, "updated_at": _ts(10)}
    assert not is_active_lead(lead)
    stale = calculate_stale([lead], now=NOW, days_without_activity=2)
    assert stale == []


def test_updated_at_fallback() -> None:
    lead = {"id": 2, "status_id": 10, "updated_at": _ts(5), "created_at": _ts(20)}
    stale = calculate_stale([lead], now=NOW, days_without_activity=2)
    assert len(stale) == 1
    assert stale[0]["activity_source"] == "updated_at"
    assert stale[0]["days_without_activity"] >= 5


def test_recent_lead_is_not_stale() -> None:
    lead = {"id": 3, "status_id": 10, "updated_at": _ts(1)}
    stale = calculate_stale([lead], now=NOW, days_without_activity=2)
    assert stale == []


def test_events_override_updated_at() -> None:
    lead = {"id": 4, "status_id": 10, "updated_at": _ts(1), "created_at": _ts(30)}
    events = {4: [{"type": "incoming_call", "created_at": _ts(8)}]}
    stale = calculate_stale([lead], now=NOW, days_without_activity=2, events_by_lead=events)
    assert len(stale) == 1
    assert stale[0]["activity_source"] == "events"
    ts, source = last_activity_timestamp(lead, events[4])
    assert source == "events"
    assert ts == _ts(8)


def test_non_meaningful_events_are_ignored() -> None:
    lead = {"id": 5, "status_id": 10, "updated_at": _ts(3)}
    events = {5: [{"type": "lead_linked", "created_at": int(NOW.timestamp())}]}
    stale = calculate_stale([lead], now=NOW, days_without_activity=2, events_by_lead=events)
    assert len(stale) == 1
    assert stale[0]["activity_source"] == "updated_at"


def test_lost_leads_excluded() -> None:
    lead = {"id": 6, "status_id": 143, "updated_at": _ts(40)}
    assert calculate_stale([lead], now=NOW, days_without_activity=2) == []
