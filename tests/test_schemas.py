"""Output schema tests for LLM-facing models."""

from __future__ import annotations

from schemas import CompareManagersInput, MoveLeadResult, SalesOverview, StaleLead


def test_sales_overview_schema() -> None:
    payload = {
        "period": {"date_from": "2026-09-04T00:00:00+03:00", "date_to": "2026-09-04T23:59:59+03:00", "timezone": "Europe/Moscow"},
        "new_leads": 1,
        "active_leads": 2,
        "won_leads": 3,
        "lost_leads": 0,
        "won_revenue": 1000,
        "overdue_tasks": 4,
        "leads_without_next_task": 1,
        "stale_leads": 1,
        "managers": [
            {
                "manager_id": 10,
                "manager_name": "Ivan",
                "new_leads": 1,
                "active_leads": 2,
                "won_leads": 3,
                "lost_leads": 0,
                "won_revenue": 1000,
                "overdue_tasks": 1,
                "stale_leads": 0,
                "leads_without_next_task": 1,
            }
        ],
    }
    model = SalesOverview.model_validate(payload)
    dumped = model.model_dump()
    assert "_embedded" not in dumped
    assert dumped["managers"][0]["manager_name"] == "Ivan"


def test_move_lead_uses_from_to_aliases() -> None:
    result = MoveLeadResult.model_validate(
        {
            "success": True,
            "lead_id": 123,
            "from": {"pipeline": "Sales", "status": "New"},
            "to": {"pipeline": "Sales", "status": "Proposal"},
        }
    )
    dumped = result.model_dump(by_alias=True)
    assert dumped["from"]["status"] == "New"
    assert dumped["to"]["status"] == "Proposal"


def test_stale_lead_compact_shape() -> None:
    lead = StaleLead.model_validate(
        {
            "lead_id": 1,
            "lead_name": "Romashka",
            "price": 500000,
            "pipeline": {"id": 1, "name": "Sales"},
            "status": {"id": 10, "name": "Talks"},
            "manager": {"id": 10, "name": "Ivan"},
            "updated_at": "2026-09-01T00:00:00+03:00",
            "last_activity_at": "2026-09-01T00:00:00+03:00",
            "days_without_activity": 3.0,
            "closest_task_at": None,
            "has_overdue_task": True,
            "activity_source": "events",
        }
    )
    dumped = lead.model_dump()
    assert dumped["lead_id"] == 1
    assert "links" not in dumped


def test_compare_managers_requires_two_ids() -> None:
    try:
        CompareManagersInput(manager_ids=[1])
        raised = False
    except Exception:
        raised = True
    assert raised
