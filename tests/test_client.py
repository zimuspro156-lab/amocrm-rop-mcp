"""Pagination, retry and HTTP error mapping tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from amocrm_client import AmoCRMClient, flatten_params, map_http_error
from exceptions import (
    AmoCRMAuthError,
    AmoCRMNotFoundError,
    AmoCRMPermissionError,
    AmoCRMRateLimitError,
    AmoCRMValidationError,
)
from token_manager import MemoryTokenStorage, TokenManager, TokenPair


def _tokens() -> TokenPair:
    return TokenPair(
        access_token="access",
        refresh_token="refresh",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
    )


def _manager(http: httpx.AsyncClient) -> TokenManager:
    return TokenManager(
        subdomain="example",
        client_id="id",
        client_secret="secret",
        redirect_uri="https://localhost",
        storage=MemoryTokenStorage(_tokens()),
        http_client=http,
    )


def test_flatten_nested_filters() -> None:
    params = flatten_params(
        {
            "filter": {
                "pipeline_id": 7,
                "created_at": {"from": 1, "to": 2},
                "responsible_user_id": [10, 11],
                "statuses": [{"pipeline_id": 7, "status_id": 142}],
            }
        }
    )
    mapping = dict(params)
    assert ("filter[pipeline_id]", "7") in params
    assert mapping["filter[created_at][from]"] == "1"
    assert ("filter[responsible_user_id][]", "10") in params
    assert mapping["filter[statuses][0][status_id]"] == "142"


def test_error_mapping() -> None:
    assert isinstance(map_http_error(400, "bad"), AmoCRMValidationError)
    assert isinstance(map_http_error(401, "auth"), AmoCRMAuthError)
    assert isinstance(map_http_error(403, "no"), AmoCRMPermissionError)
    assert isinstance(map_http_error(404, "missing"), AmoCRMNotFoundError)
    assert isinstance(map_http_error(429, "slow"), AmoCRMRateLimitError)


class _Transport(httpx.AsyncBaseTransport):
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = responses
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


@pytest.mark.asyncio
async def test_pagination_stops_without_next() -> None:
    page1 = {
        "_page": 1,
        "_links": {"next": {"href": "https://example.amocrm.ru/api/v4/leads?page=2"}},
        "_embedded": {"leads": [{"id": 1}, {"id": 2}]},
    }
    page2 = {"_page": 2, "_links": {"self": {"href": "x"}}, "_embedded": {"leads": [{"id": 3}]}}
    transport = _Transport(
        [
            httpx.Response(200, json=page1),
            httpx.Response(200, json=page2),
        ]
    )
    http = httpx.AsyncClient(transport=transport)
    client = AmoCRMClient(subdomain="example", token_manager=_manager(http), http_client=http)
    leads = await client.get_leads()
    assert [lead["id"] for lead in leads] == [1, 2, 3]
    assert transport.calls == 2
    await http.aclose()


@pytest.mark.asyncio
async def test_retry_on_502_then_success() -> None:
    transport = _Transport(
        [
            httpx.Response(502, json={"title": "bad gateway"}),
            httpx.Response(200, json={"_embedded": {"leads": [{"id": 9}]}}),
        ]
    )
    http = httpx.AsyncClient(transport=transport)
    client = AmoCRMClient(subdomain="example", token_manager=_manager(http), http_client=http, max_retries=3)
    leads = await client.get_leads()
    assert leads[0]["id"] == 9
    assert transport.calls == 2
    await http.aclose()


@pytest.mark.asyncio
async def test_401_refreshes_token_and_retries() -> None:
    http = httpx.AsyncClient(transport=_Transport([]))
    manager = _manager(http)
    manager.refresh = AsyncMock(return_value=_tokens())  # type: ignore[method-assign]
    transport = _Transport(
        [
            httpx.Response(401, json={"title": "Unauthorized"}),
            httpx.Response(200, json={"id": 5, "name": "Lead"}),
        ]
    )
    http._transport = transport
    client = AmoCRMClient(subdomain="example", token_manager=manager, http_client=http)
    lead = await client.get_lead(5)
    assert lead["id"] == 5
    manager.refresh.assert_awaited()
    await http.aclose()


@pytest.mark.asyncio
async def test_post_is_not_retried_on_502() -> None:
    transport = _Transport([httpx.Response(502, json={"title": "bad gateway"})])
    http = httpx.AsyncClient(transport=transport)
    client = AmoCRMClient(subdomain="example", token_manager=_manager(http), http_client=http, max_retries=3)
    with pytest.raises(Exception):
        await client.create_tasks([{"text": "x"}])
    assert transport.calls == 1
    await http.aclose()
