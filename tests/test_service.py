"""Service-layer tests with a fake amoCRM client."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from config import Settings
from exceptions import AmoCRMValidationError
from services import ROPService


class FakeClient:
    def __init__(self) -> None:
        self.pipelines = [
            {
                "id": 1,
                "name": "Sales",
                "is_main": True,
                "_embedded": {
                    "statuses": [
                        {"id": 10, "name": "New"},
                        {"id": 20, "name": "Proposal"},
                        {"id": 142, "name": "Won"},
                        {"id": 143, "name": "Lost"},
                    ]
                },
            }
        ]
        self.users = [{"id": 10, "name": "Ivan"}, {"id": 11, "name": "Petr"}]
        self.leads_by_id = {
            123: {
                "id": 123,
                "name": "Romashka",
                "price": 100,
                "pipeline_id": 1,
                "status_id": 10,
                "responsible_user_id": 10,
                "updated_at": 1,
                "created_at": 1,
                "closest_task_at": 0,
            }
        }
        self.updated: list[tuple[int, dict[str, Any]]] = []

    async def get_users(self, *, force: bool = False) -> list[dict[str, Any]]:
        return self.users

    async def get_pipelines(self, *, force: bool = False) -> list[dict[str, Any]]:
        return self.pipelines

    async def get_lead(self, lead_id: int, *, with_fields: str | None = None) -> dict[str, Any]:
        return dict(self.leads_by_id[lead_id])

    async def update_lead(self, lead_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        self.updated.append((lead_id, dict(payload)))
        lead = self.leads_by_id[lead_id]
        lead.update(payload)
        return lead


@pytest.mark.asyncio
async def test_move_lead_rejects_status_from_another_pipeline() -> None:
    service = ROPService(FakeClient(), Settings())  # type: ignore[arg-type]
    with pytest.raises(AmoCRMValidationError):
        await service.move_lead(123, status_id=999, pipeline_id=1)


@pytest.mark.asyncio
async def test_move_lead_returns_before_after() -> None:
    client = FakeClient()
    service = ROPService(client, Settings())  # type: ignore[arg-type]
    result = await service.move_lead(123, status_id=20)
    dumped = result.model_dump(by_alias=True)
    assert dumped["success"] is True
    assert dumped["from"]["status"] == "New"
    assert dumped["to"]["status"] == "Proposal"
    assert client.updated[0][1]["status_id"] == 20


@pytest.mark.asyncio
async def test_pipeline_summary_counts() -> None:
    client = FakeClient()
    client.get_leads = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {"id": 1, "status_id": 10, "price": 100, "pipeline_id": 1, "responsible_user_id": 10},
            {"id": 2, "status_id": 142, "price": 500, "pipeline_id": 1, "responsible_user_id": 10},
            {"id": 3, "status_id": 143, "price": 50, "pipeline_id": 1, "responsible_user_id": 11},
        ]
    )
    service = ROPService(client, Settings())  # type: ignore[arg-type]
    summary = await service.pipeline_summary(pipeline_id=1)
    assert summary.total_active == 1
    assert summary.total_won == 1
    assert summary.total_lost == 1
    assert summary.statuses[0].leads_count == 1
