"""Manager performance and comparison tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from services import ROPService
from tools.common import READ, dump, tool_guard


def register(mcp: MCPServer, get_service: Callable[[], ROPService]) -> None:
    @mcp.tool(title="Manager performance", annotations=READ)
    @tool_guard
    async def manager_performance(
        manager_id: int,
        date_from: str | None = None,
        date_to: str | None = None,
        pipeline_id: int | None = None,
    ) -> dict[str, Any]:
        """Return results and current workload for one sales manager.

        Use this tool when the user asks how a specific manager performed: closed deals, revenue,
        conversion, overdue tasks, stale leads or deals without a next action. Activity volume is
        not treated as performance. calculation_notes explains how each metric is derived.
        """
        return dump(await get_service().manager_performance(manager_id, date_from, date_to, pipeline_id))

    @mcp.tool(title="Compare managers", annotations=READ)
    @tool_guard
    async def compare_managers(
        manager_ids: list[int],
        date_from: str | None = None,
        date_to: str | None = None,
        pipeline_id: int | None = None,
    ) -> dict[str, Any]:
        """Compare two or more managers on the same result and workload metrics.

        Use this tool when the user asks to compare managers over a period. Provide at least two
        distinct manager_ids. Do not assign automatic good/bad rankings; return comparable numbers.
        """
        return dump(await get_service().compare_managers(manager_ids, date_from, date_to, pipeline_id))
