"""Manager aggregation, conversion and pipeline snapshot tests."""

from __future__ import annotations

from analytics import (
    aggregate_manager_metrics,
    average_check,
    calculate_conversion,
    calculate_revenue,
    group_leads_by_manager,
    pipeline_snapshot,
)


def test_revenue_and_average_check() -> None:
    leads = [{"price": 100}, {"price": 300}, {"price": None}]
    assert calculate_revenue(leads) == 400
    assert average_check(400, 2) == 200
    assert average_check(400, 0) == 0


def test_conversion_none_when_no_new_leads() -> None:
    assert calculate_conversion(5, 0) is None
    assert calculate_conversion(1, 4) == 25.0


def test_group_leads_by_manager() -> None:
    leads = [
        {"id": 1, "responsible_user_id": 10},
        {"id": 2, "responsible_user_id": 10},
        {"id": 3, "responsible_user_id": 11},
    ]
    grouped = group_leads_by_manager(leads)
    assert len(grouped[10]) == 2
    assert len(grouped[11]) == 1


def test_aggregate_manager_metrics() -> None:
    metrics = aggregate_manager_metrics(
        manager_id=10,
        manager_name="Ivan",
        new_leads=[{"id": 1}, {"id": 2}],
        active_leads=[{"id": 3, "status_id": 10, "closest_task_at": 0}, {"id": 4, "status_id": 10, "closest_task_at": 9}],
        won_leads=[{"id": 5, "price": 1000}],
        lost_leads=[{"id": 6}],
        overdue_tasks=[{"id": 7}],
        stale_leads=[{"id": 3}],
        completed_tasks=[{"id": 8}, {"id": 9}],
    )
    assert metrics["new_leads"] == 2
    assert metrics["won_revenue"] == 1000
    assert metrics["leads_without_next_task"] == 1
    assert metrics["completed_tasks"] == 2
    assert metrics["manager_name"] == "Ivan"


def test_pipeline_snapshot() -> None:
    statuses = [
        {"id": 10, "name": "New"},
        {"id": 142, "name": "Won"},
        {"id": 143, "name": "Lost"},
    ]
    leads = [
        {"id": 1, "status_id": 10, "price": 100},
        {"id": 2, "status_id": 10, "price": 300},
        {"id": 3, "status_id": 142, "price": 500},
    ]
    snapshot = pipeline_snapshot(leads, statuses)
    new = snapshot[0]
    assert new["leads_count"] == 2
    assert new["leads_value"] == 400
    assert new["average_lead_value"] == 200
    assert snapshot[1]["is_won"] is True
    assert snapshot[2]["is_lost"] is True
