"""Department-level overview tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from services import ROPService
from tools.common import READ, dump, tool_guard


def register(mcp: MCPServer, get_service: Callable[[], ROPService]) -> None:
    @mcp.tool(title="Sales department overview", annotations=READ)
    @tool_guard
    async def sales_overview(
        date_from: str | None = None,
        date_to: str | None = None,
        pipeline_id: int | None = None,
        manager_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Return a management snapshot of the sales department.

        Use this tool when the user asks how the team is doing today, this week or this month,
        wants new/won/lost counts, revenue, overdue tasks, stale leads, or a per-manager breakdown.
        If no period is provided, the current day in APP_TIMEZONE is used. Do not invent qualitative
        judgments such as who is lazy; return the numbers and let the model interpret them.
        """
        return dump(await get_service().sales_overview(date_from, date_to, pipeline_id, manager_ids))
