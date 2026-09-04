"""Overdue task calculation tests."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from analytics import calculate_overdue, group_tasks_by_manager, leads_without_next_task

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=ZoneInfo("Europe/Moscow"))
NOW_TS = int(NOW.timestamp())


def test_completed_task_is_not_overdue() -> None:
    tasks = [{"id": 1, "is_completed": True, "complete_till": NOW_TS - 3600}]
    assert calculate_overdue(tasks, NOW) == []


def test_future_task_is_not_overdue() -> None:
    tasks = [{"id": 2, "is_completed": False, "complete_till": NOW_TS + 3600}]
    assert calculate_overdue(tasks, NOW) == []


def test_past_open_task_is_overdue() -> None:
    tasks = [{"id": 3, "is_completed": False, "complete_till": NOW_TS - 90, "responsible_user_id": 7}]
    overdue = calculate_overdue(tasks, NOW)
    assert len(overdue) == 1
    assert overdue[0]["overdue_seconds"] == 90


def test_group_overdue_by_manager() -> None:
    tasks = [
        {"id": 1, "is_completed": False, "complete_till": NOW_TS - 10, "responsible_user_id": 1},
        {"id": 2, "is_completed": False, "complete_till": NOW_TS - 20, "responsible_user_id": 1},
        {"id": 3, "is_completed": False, "complete_till": NOW_TS - 30, "responsible_user_id": 2},
    ]
    overdue = calculate_overdue(tasks, NOW)
    grouped = group_tasks_by_manager(overdue)
    assert len(grouped[1]) == 2
    assert len(grouped[2]) == 1


def test_leads_without_next_task() -> None:
    leads = [
        {"id": 1, "status_id": 10, "closest_task_at": 0},
        {"id": 2, "status_id": 10, "closest_task_at": NOW_TS + 1000},
        {"id": 3, "status_id": 142, "closest_task_at": 0},
    ]
    missing = leads_without_next_task(leads)
    assert [item["id"] for item in missing] == [1]
