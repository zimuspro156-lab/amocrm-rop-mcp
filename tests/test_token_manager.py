"""Token manager refresh and storage tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from exceptions import AmoCRMAuthError
from token_manager import MemoryTokenStorage, TokenManager, TokenPair


def _pair(*, expired: bool = False) -> TokenPair:
    delta = timedelta(seconds=-10) if expired else timedelta(hours=2)
    return TokenPair(
        access_token="old-access",
        refresh_token="old-refresh",
        expires_at=datetime.now(timezone.utc) + delta,
    )


class _Transport(httpx.AsyncBaseTransport):
    def __init__(self, payload: dict[str, object], status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.calls = 0
        self.bodies: list[bytes] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        self.bodies.append(request.content)
        return httpx.Response(self.status, json=self.payload)


@pytest.mark.asyncio
async def test_refresh_saves_new_pair() -> None:
    storage = MemoryTokenStorage(_pair(expired=True))
    transport = _Transport(
        {
            "token_type": "Bearer",
            "expires_in": 3600,
            "access_token": "new-access",
            "refresh_token": "new-refresh",
        }
    )
    http = httpx.AsyncClient(transport=transport)
    manager = TokenManager(
        subdomain="example",
        client_id="id",
        client_secret="secret",
        redirect_uri="https://localhost",
        storage=storage,
        http_client=http,
        skew_seconds=120,
    )
    tokens = await manager.ensure_fresh()
    assert tokens.access_token == "new-access"
    assert tokens.refresh_token == "new-refresh"
    saved = await storage.load()
    assert saved is not None
    assert saved.refresh_token == "new-refresh"
    await http.aclose()


@pytest.mark.asyncio
async def test_fresh_token_is_not_refreshed() -> None:
    storage = MemoryTokenStorage(_pair(expired=False))
    transport = _Transport({"access_token": "x", "refresh_token": "y", "expires_in": 1})
    http = httpx.AsyncClient(transport=transport)
    manager = TokenManager(
        subdomain="example",
        client_id="id",
        client_secret="secret",
        redirect_uri="https://localhost",
        storage=storage,
        http_client=http,
    )
    tokens = await manager.ensure_fresh()
    assert tokens.access_token == "old-access"
    assert transport.calls == 0
    await http.aclose()


@pytest.mark.asyncio
async def test_failed_refresh_raises_auth_error() -> None:
    storage = MemoryTokenStorage(_pair(expired=True))
    transport = _Transport({"hint": "invalid"}, status=400)
    http = httpx.AsyncClient(transport=transport)
    manager = TokenManager(
        subdomain="example",
        client_id="id",
        client_secret="secret",
        redirect_uri="https://localhost",
        storage=storage,
        http_client=http,
    )
    with pytest.raises(AmoCRMAuthError):
        await manager.ensure_fresh()
    await http.aclose()


def test_token_pair_does_not_expose_secrets_in_repr_fields_only() -> None:
    pair = _pair()
    dumped = pair.to_dict()
    assert "access_token" in dumped
    assert dumped["access_token"] == "old-access"
