"""Register all business MCP tools."""

from __future__ import annotations

from collections.abc import Callable

from mcp.server import MCPServer

from services import ROPService
from tools import actions, leads, managers, overview, pipelines, tasks


def register_tools(mcp: MCPServer, get_service: Callable[[], ROPService]) -> None:
    overview.register(mcp, get_service)
    pipelines.register(mcp, get_service)
    managers.register(mcp, get_service)
    leads.register(mcp, get_service)
    tasks.register(mcp, get_service)
    actions.register(mcp, get_service)
