"""Overdue task tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from services import ROPService
from tools.common import READ, dump, tool_guard


def register(mcp: MCPServer, get_service: Callable[[], ROPService]) -> None:
    @mcp.tool(title="Find overdue tasks", annotations=READ)
    @tool_guard
    async def find_overdue_tasks(
        manager_id: int | None = None,
        pipeline_id: int | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Find incomplete tasks whose due date is already in the past.

        Use this tool when the user asks about overdue, expired or missed tasks, who has the most
        overdue work, or which deals have broken follow-up. Tasks attached to leads include lead
        name, price, pipeline and status.
        """
        if limit < 1 or limit > 250:
            return {"success": False, "error": "validation_error", "message": "limit must be between 1 and 250"}
        return dump(await get_service().find_overdue_tasks(manager_id, pipeline_id, limit))
