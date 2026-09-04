"""Pipeline snapshot tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from services import ROPService
from tools.common import READ, dump, tool_guard


def register(mcp: MCPServer, get_service: Callable[[], ROPService]) -> None:
    @mcp.tool(title="Pipeline summary", annotations=READ)
    @tool_guard
    async def pipeline_summary(
        pipeline_id: int | None = None,
        manager_id: int | None = None,
    ) -> dict[str, Any]:
        """Summarize a sales pipeline by stage: lead count, value and average check.

        Use this tool when the user asks where money sits in the funnel, how many deals are in a
        stage, or wants a snapshot of a pipeline. This is a current snapshot, not historical conversion.
        If pipeline_id is omitted, the default or main pipeline is used.
        """
        return dump(await get_service().pipeline_summary(pipeline_id, manager_id))
