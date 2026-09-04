"""Official MCP OAuth 2.1 resource-server adapter."""

from __future__ import annotations

import hmac
import logging

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

from config import Settings

logger = logging.getLogger(__name__)


class StaticBearerTokenVerifier:
    """Verifies a shared bearer token. Use only when a full IdP is not available yet."""

    def __init__(self, token: str) -> None:
        self._token = token.encode("utf-8")

    async def verify_token(self, token: str) -> AccessToken | None:
        provided = token.encode("utf-8")
        if len(provided) != len(self._token) or not hmac.compare_digest(provided, self._token):
            return None
        return AccessToken(token=token, client_id="mcp-client", scopes=["amocrm:rop"])


def build_auth(settings: Settings) -> tuple[TokenVerifier | None, AuthSettings | None]:
    if not settings.mcp_auth_enabled():
        return None, None
    issuer = settings.mcp_auth_issuer_url.strip()
    resource = settings.mcp_public_url.strip() or f"http://{settings.mcp_host}:{settings.mcp_port}{settings.mcp_path}"
    if not issuer:
        logger.warning("MCP_AUTH_TOKEN is set without MCP_AUTH_ISSUER_URL; using the public MCP URL as issuer metadata")
        issuer = resource
    verifier: TokenVerifier = StaticBearerTokenVerifier(settings.mcp_auth_token)
    auth = AuthSettings(
        issuer_url=AnyHttpUrl(issuer),
        resource_server_url=AnyHttpUrl(resource),
        required_scopes=["amocrm:rop"],
    )
    return verifier, auth
