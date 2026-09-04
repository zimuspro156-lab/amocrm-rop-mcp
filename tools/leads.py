"""Lead search, stale deals, history and deals without a next action."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from services import ROPService
from tools.common import READ, dump, tool_guard


def register(mcp: MCPServer, get_service: Callable[[], ROPService]) -> None:
    @mcp.tool(title="Find stale leads", annotations=READ)
    @tool_guard
    async def find_stale_leads(
        days_without_activity: int = 2,
        manager_id: int | None = None,
        pipeline_id: int | None = None,
        status_id: int | None = None,
        min_price: float | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Find active deals with no meaningful activity for the given number of days.

        Use this tool when the user asks about stalled, forgotten, stuck or inactive deals,
        deals without movement, or deals that need manager attention. A deal is stale when it is
        still active (not won/lost) and the last meaningful activity (calls, chats, notes, stage
        changes, completed tasks; Events API when available, otherwise updated_at) is older than
        the threshold. updated_at alone is not treated as guaranteed client communication.
        """
        if days_without_activity < 1:
            return {"success": False, "error": "validation_error", "message": "days_without_activity must be >= 1"}
        if limit < 1 or limit > 250:
            return {"success": False, "error": "validation_error", "message": "limit must be between 1 and 250"}
        return dump(
            await get_service().find_stale_leads(
                days_without_activity=days_without_activity,
                manager_id=manager_id,
                pipeline_id=pipeline_id,
                status_id=status_id,
                min_price=min_price,
                limit=limit,
            )
        )

    @mcp.tool(title="Find leads without next task", annotations=READ)
    @tool_guard
    async def find_leads_without_tasks(
        manager_id: int | None = None,
        pipeline_id: int | None = None,
        status_id: int | None = None,
        min_price: float | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Find active deals that have no scheduled next task.

        Use this tool when the user asks which deals have no next action, no follow-up, or no
        planned task. Won and lost deals are excluded. This is a current snapshot of the funnel.
        """
        if limit < 1 or limit > 250:
            return {"success": False, "error": "validation_error", "message": "limit must be between 1 and 250"}
        return dump(
            await get_service().find_leads_without_tasks(manager_id, pipeline_id, status_id, min_price, limit)
        )

    @mcp.tool(title="Lead history", annotations=READ)
    @tool_guard
    async def lead_history(lead_id: int) -> dict[str, Any]:
        """Build a chronological history of a deal from the lead, events, notes and tasks.

        Use this tool when the user asks what happened with a deal, when it last moved, who changed
        the stage or responsible user, or wants calls, chats, notes and tasks in one timeline.
        """
        return dump(await get_service().lead_history(lead_id))

    @mcp.tool(title="Search leads", annotations=READ)
    @tool_guard
    async def search_leads(
        query: str | None = None,
        lead_id: int | None = None,
        manager_id: int | None = None,
        pipeline_id: int | None = None,
        status_id: int | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Search deals by name, id, manager, pipeline, status or price. Returns a compact list.

        Use this tool to locate a deal before creating a task, adding a note or moving it. Prefer
        this over asking the user for numeric IDs they do not know.
        """
        if limit < 1 or limit > 250:
            return {"success": False, "error": "validation_error", "message": "limit must be between 1 and 250"}
        return dump(
            await get_service().search_leads(
                query=query,
                lead_id=lead_id,
                manager_id=manager_id,
                pipeline_id=pipeline_id,
                status_id=status_id,
                min_price=min_price,
                max_price=max_price,
                limit=limit,
            )
        )
