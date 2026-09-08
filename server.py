"""amoCRM ROP MCP server: Streamable HTTP + business tools."""

from __future__ import annotations

import logging
from typing import Any

import uvicorn
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from amocrm_client import AmoCRMClient
from config import Settings, get_settings
from health import health_payload
from logging_config import setup_logging
from mcp_auth import build_auth
from oauth_server import McpOAuth
from services import ROPService
from token_manager import FileTokenStorage, TokenManager
from tools import register_tools
from version import APP_NAME, SERVICE_TITLE, __version__

logger = logging.getLogger(__name__)

INSTRUCTIONS = """
You are connected to amoCRM ROP MCP, a sales-management toolkit for a head of sales.

Use business tools, never invent amoCRM HTTP calls:
- sales_overview: department snapshot for a period (defaults to today)
- pipeline_summary: current funnel snapshot by stage
- manager_performance / compare_managers: results vs current workload
- find_stale_leads: active deals without meaningful activity
- find_overdue_tasks: incomplete tasks past due
- find_leads_without_tasks: active deals with no next action
- lead_history: chronological deal timeline
- search_leads: locate a deal before acting
- create_task / add_note / move_lead: the only write operations

Do not rank people as good or bad. Return numbers and let the user interpret them.
If a numeric id is unknown, search first. Won status id is 142, lost status id is 143.
""".strip()

_service: ROPService | None = None
_oauth: McpOAuth | None = None
_settings: Settings = get_settings()


def get_service() -> ROPService:
    global _service
    if _service is None:
        settings = get_settings()
        settings.require_oauth_app()
        storage = FileTokenStorage(settings.tokens_path)
        manager = TokenManager(
            subdomain=settings.amocrm_subdomain,
            client_id=settings.amocrm_client_id,
            client_secret=settings.amocrm_client_secret,
            redirect_uri=settings.amocrm_redirect_uri,
            storage=storage,
            initial_access_token=settings.amocrm_access_token,
            initial_refresh_token=settings.amocrm_refresh_token,
            skew_seconds=settings.token_refresh_skew_seconds,
        )
        client = AmoCRMClient(
            subdomain=settings.amocrm_subdomain,
            token_manager=manager,
            timeout=settings.http_timeout_seconds,
            max_retries=settings.http_max_retries,
            metadata_ttl=settings.metadata_cache_ttl_seconds,
            max_list_items=settings.max_list_items,
        )
        _service = ROPService(client, settings)
    return _service


def get_oauth() -> McpOAuth | None:
    global _oauth
    settings = get_settings()
    if _oauth is None and settings.mcp_oauth_enabled():
        _oauth = McpOAuth.from_settings(settings)
    return _oauth


def _register_oauth_routes(mcp_server: MCPServer, oauth: McpOAuth) -> None:
    for path, methods, handler in oauth.handlers():
        mcp_server.custom_route(path, methods=methods)(handler)


def create_mcp() -> MCPServer:
    settings = get_settings()
    oauth = get_oauth()
    verifier, auth = build_auth(settings, oauth)
    kwargs: dict[str, Any] = {
        "version": __version__,
        "instructions": INSTRUCTIONS,
        "log_level": settings.log_level,
    }
    if verifier is not None and auth is not None:
        kwargs["token_verifier"] = verifier
        kwargs["auth"] = auth
    elif not settings.mcp_auth_enabled() and settings.mcp_host not in {"127.0.0.1", "localhost", "::1"}:
        logger.warning(
            "MCP is binding to %s without ChatGPT OAuth. Do not expose /mcp to the internet.",
            settings.mcp_host,
        )
    mcp_server = MCPServer(SERVICE_TITLE, **kwargs)
    register_tools(mcp_server, get_service)
    if oauth is not None:
        _register_oauth_routes(mcp_server, oauth)

    @mcp_server.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> Response:
        return JSONResponse(health_payload())

    @mcp_server.custom_route("/ready", methods=["GET"])
    async def ready(_request: Request) -> Response:
        settings_now = get_settings()
        token_file = settings_now.tokens_path.exists()
        configured = bool(settings_now.amocrm_subdomain and settings_now.amocrm_client_id)
        ready_ok = configured and (token_file or bool(settings_now.amocrm_refresh_token))
        return JSONResponse(
            {
                "status": "ready" if ready_ok else "not_ready",
                "service": APP_NAME,
                "version": __version__,
                "amocrm_configured": configured,
                "tokens_present": token_file or bool(settings_now.amocrm_refresh_token),
            },
            status_code=200 if ready_ok else 503,
        )

    return mcp_server


def transport_security(settings: Settings) -> TransportSecuritySettings | None:
    if settings.mcp_disable_dns_rebinding_protection:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    if settings.allowed_hosts:
        return TransportSecuritySettings(allowed_hosts=settings.allowed_hosts)
    return None


setup_logging(_settings.log_level)
mcp = create_mcp()
app = mcp.streamable_http_app(
    json_response=True,
    transport_security=transport_security(_settings),
    streamable_http_path=_settings.mcp_path,
)


def main() -> None:
    settings = get_settings()
    logger.info("Starting %s v%s on %s:%s%s", APP_NAME, __version__, settings.mcp_host, settings.mcp_port, settings.mcp_path)
    uvicorn.run(app, host=settings.mcp_host, port=settings.mcp_port, log_level=settings.log_level.lower())


if __name__ == "__main__":
    main()
