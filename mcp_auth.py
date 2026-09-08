"""MCP incoming auth: ChatGPT OAuth AS + optional static bearer fallback."""

from __future__ import annotations

import hmac
import logging

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

from config import Settings
from oauth_server import McpOAuth

logger = logging.getLogger(__name__)


class StaticBearerTokenVerifier:
    """Verifies a shared bearer token. Use only when a full IdP is not available yet."""

    def __init__(self, token: str) -> None:
        self._token = token.encode("utf-8")

    async def verify_token(self, token: str) -> AccessToken | None:
        provided = token.encode("utf-8")
        if len(provided) != len(self._token) or not hmac.compare_digest(provided, self._token):
            return None
        return AccessToken(token=token, client_id="mcp-client", scopes=["mcp"])


class OAuthAccessTokenVerifier:
    def __init__(self, oauth: McpOAuth) -> None:
        self._oauth = oauth

    async def verify_token(self, token: str) -> AccessToken | None:
        return self._oauth.verify_access_token(token)


def build_auth(
    settings: Settings, oauth: McpOAuth | None = None
) -> tuple[TokenVerifier | None, AuthSettings | None]:
    if oauth is not None:
        issuer = oauth.issuer
        resource = oauth.resource
        verifier: TokenVerifier = OAuthAccessTokenVerifier(oauth)
        auth = AuthSettings(
            issuer_url=AnyHttpUrl(issuer),
            resource_server_url=AnyHttpUrl(resource),
            required_scopes=["mcp"],
        )
        return verifier, auth

    if not settings.mcp_auth_token.strip():
        return None, None

    issuer = settings.mcp_auth_issuer_url.strip() or settings.oauth_issuer_url
    resource = settings.mcp_resource_url
    if not issuer:
        logger.warning("MCP_AUTH_TOKEN is set without issuer URL; using the public MCP URL as issuer metadata")
        issuer = resource
    verifier = StaticBearerTokenVerifier(settings.mcp_auth_token)
    auth = AuthSettings(
        issuer_url=AnyHttpUrl(issuer),
        resource_server_url=AnyHttpUrl(resource),
        required_scopes=["mcp"],
    )
    return verifier, auth
