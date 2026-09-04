"""Write tools: create task, move lead, add note."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from services import ROPService
from tools.common import WRITE_CREATE, WRITE_MOVE, dump, tool_guard


def register(mcp: MCPServer, get_service: Callable[[], ROPService]) -> None:
    @mcp.tool(title="Create task", annotations=WRITE_CREATE)
    @tool_guard
    async def create_task(
        entity_id: int,
        responsible_user_id: int,
        complete_till: str,
        text: str,
        entity_type: str = "leads",
        task_type_id: int | None = None,
    ) -> dict[str, Any]:
        """Create a follow-up task on a deal (or another entity) after validating user, entity and due date.

        Use this write tool when the user asks to set a task, reminder or next action. complete_till
        accepts ISO datetime, YYYY-MM-DD HH:MM, unix timestamp, or simple relative values such as tomorrow.
        The due date must not be in the past and not more than two years ahead. This does not delete anything.
        """
        return dump(
            await get_service().create_task(
                entity_id=entity_id,
                responsible_user_id=responsible_user_id,
                complete_till=complete_till,
                text=text,
                entity_type=entity_type,
                task_type_id=task_type_id,
            )
        )

    @mcp.tool(title="Move lead", annotations=WRITE_MOVE)
    @tool_guard
    async def move_lead(
        lead_id: int,
        status_id: int,
        pipeline_id: int | None = None,
    ) -> dict[str, Any]:
        """Move a deal to another pipeline stage after verifying that the target status exists.

        Use this write tool when the user asks to change a deal stage. If the status name is known
        but not the id, first call pipeline_summary or search_leads. The tool refuses to move a deal
        into a status that does not belong to the target pipeline. Returns before/after stage names.
        """
        return dump(await get_service().move_lead(lead_id, status_id, pipeline_id))

    @mcp.tool(title="Add note", annotations=WRITE_CREATE)
    @tool_guard
    async def add_note(lead_id: int, text: str) -> dict[str, Any]:
        """Add a plain-text note to a deal.

        Use this write tool when the user asks to leave a comment, note or internal remark on a deal.
        Only common text notes are created. This does not update other lead fields.
        """
        return dump(await get_service().add_note(lead_id, text))
