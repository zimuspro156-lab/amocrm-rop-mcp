"""Async amoCRM API v4 client. Internal HTTP layer, not MCP tools."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from urllib.parse import urlparse

import httpx

from cache import TTLCache
from exceptions import (
    AmoCRMAuthError,
    AmoCRMConflictError,
    AmoCRMError,
    AmoCRMNotFoundError,
    AmoCRMPermissionError,
    AmoCRMRateLimitError,
    AmoCRMValidationError,
)
from token_manager import TokenManager

logger = logging.getLogger(__name__)

RETRY_STATUSES = frozenset({429, 502, 503, 504})
SAFE_METHODS = frozenset({"GET", "HEAD"})
USER_AGENT = "amocrm-rop-mcp/1.0"


def flatten_params(params: Mapping[str, Any] | None, prefix: str = "") -> list[tuple[str, str]]:
    """Convert nested dict/list filters into amoCRM query tuples."""
    if not params:
        return []
    items: list[tuple[str, str]] = []
    for key, value in params.items():
        full_key = f"{prefix}[{key}]" if prefix else str(key)
        if value is None:
            continue
        if isinstance(value, Mapping):
            items.extend(flatten_params(value, full_key))
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                if isinstance(item, Mapping):
                    items.extend(flatten_params(item, f"{full_key}[{index}]"))
                else:
                    items.append((f"{full_key}[]", str(item)))
        elif isinstance(value, bool):
            items.append((full_key, "1" if value else "0"))
        else:
            items.append((full_key, str(value)))
    return items


def map_http_error(status_code: int, message: str, retry_after: float | None = None) -> AmoCRMError:
    if status_code == 400:
        return AmoCRMValidationError(message or "amoCRM rejected the request", status_code=status_code)
    if status_code == 401:
        return AmoCRMAuthError(message or "amoCRM authorization failed", status_code=status_code)
    if status_code == 403:
        return AmoCRMPermissionError(message or "amoCRM integration does not have sufficient permissions", status_code=status_code)
    if status_code == 404:
        return AmoCRMNotFoundError(message or "Requested amoCRM entity was not found", status_code=status_code)
    if status_code == 409:
        return AmoCRMConflictError(message or "amoCRM reported a conflict", status_code=status_code)
    if status_code == 429:
        return AmoCRMRateLimitError(message or "amoCRM rate limit exceeded", status_code=status_code, retry_after=retry_after)
    return AmoCRMError(message or f"amoCRM request failed with HTTP {status_code}", status_code=status_code)


def _error_message(payload: Any, fallback: str) -> str:
    if isinstance(payload, Mapping):
        title = payload.get("title") or payload.get("detail") or payload.get("hint")
        if title:
            return str(title)
        if "errors" in payload:
            return fallback
    return fallback


class AmoCRMClient:
    def __init__(
        self,
        *,
        subdomain: str,
        token_manager: TokenManager,
        timeout: float = 30.0,
        max_retries: int = 3,
        metadata_ttl: int = 300,
        max_list_items: int = 5000,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.subdomain = subdomain
        self.token_manager = token_manager
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_list_items = max_list_items
        self._http = http_client
        self._owns_http = http_client is None
        self._users_cache: TTLCache[list[dict[str, Any]]] = TTLCache(metadata_ttl)
        self._pipelines_cache: TTLCache[list[dict[str, Any]]] = TTLCache(metadata_ttl)
        self._event_types_cache: TTLCache[list[dict[str, Any]]] = TTLCache(metadata_ttl)

    @property
    def base_url(self) -> str:
        return f"https://{self.subdomain}.amocrm.ru/api/v4"

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self.timeout, headers={"User-Agent": USER_AGENT})
        return self._http

    async def aclose(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None
        await self.token_manager.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | Sequence[tuple[str, str]] | None = None,
        json: Any = None,
        retry_on_write: bool = False,
    ) -> Any:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        query = params if isinstance(params, Sequence) else flatten_params(params)
        attempts = self.max_retries
        last_error: Exception | None = None
        refreshed = False

        for attempt in range(1, attempts + 1):
            token = await self.token_manager.get_access_token()
            headers = {"Authorization": f"Bearer {token}"}
            client = await self._client()
            try:
                response = await client.request(method, url, params=query, json=json, headers=headers)
            except httpx.TimeoutException as exc:
                last_error = exc
                if attempt >= attempts or method.upper() not in SAFE_METHODS and not retry_on_write:
                    raise AmoCRMError("amoCRM request timed out") from exc
                await self._backoff(attempt, None)
                continue
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt >= attempts or method.upper() not in SAFE_METHODS and not retry_on_write:
                    raise AmoCRMError(f"Network error while calling amoCRM: {exc.__class__.__name__}") from exc
                await self._backoff(attempt, None)
                continue

            endpoint = urlparse(url).path
            logger.info(
                "amocrm method=%s endpoint=%s status=%s attempt=%s",
                method,
                endpoint,
                response.status_code,
                attempt,
            )

            if response.status_code == 204:
                return {}

            if response.status_code == 401 and not refreshed:
                logger.info("amoCRM returned 401; refreshing token and retrying once")
                await self.token_manager.refresh()
                refreshed = True
                continue

            if response.status_code in RETRY_STATUSES and attempt < attempts:
                retry_after = _retry_after(response)
                if method.upper() not in SAFE_METHODS and not retry_on_write and response.status_code != 429:
                    try:
                        payload = response.json()
                    except ValueError:
                        payload = {}
                    message = _error_message(payload, f"amoCRM request failed with HTTP {response.status_code}")
                    raise map_http_error(response.status_code, message, retry_after)
                logger.info("Retrying amoCRM request after HTTP %s", response.status_code)
                await self._backoff(attempt, retry_after)
                continue

            if response.status_code >= 400:
                payload: Any
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
                message = _error_message(payload, f"amoCRM request failed with HTTP {response.status_code}")
                raise map_http_error(response.status_code, message, _retry_after(response))

            if not response.content:
                return {}
            data = response.json()
            if isinstance(data, dict) and "_embedded" in data:
                logger.debug("amocrm entities received for %s", endpoint)
            return data

        if last_error is not None:
            raise AmoCRMError(f"amoCRM request failed: {last_error.__class__.__name__}") from last_error
        raise AmoCRMError("amoCRM request failed after retries")

    async def _backoff(self, attempt: int, retry_after: float | None) -> None:
        delay = retry_after if retry_after is not None else (0.4 * (2 ** (attempt - 1)))
        delay += random.uniform(0, 0.2)
        await asyncio.sleep(min(delay, 8.0))

    async def paginate(
        self,
        path: str,
        embedded_key: str,
        *,
        params: Mapping[str, Any] | None = None,
        max_items: int | None = None,
        page_limit: int = 250,
    ) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        page = 1
        cap = max_items or self.max_list_items
        base_params = dict(params or {})
        while len(collected) < cap:
            page_params = dict(base_params)
            page_params["page"] = page
            page_params["limit"] = min(page_limit, cap - len(collected), 250)
            data = await self.request("GET", path, params=page_params)
            items = _embedded_list(data, embedded_key)
            if not items:
                break
            collected.extend(items)
            logger.debug("pagination path=%s page=%s count=%s total=%s", path, page, len(items), len(collected))
            links = data.get("_links") if isinstance(data, dict) else None
            has_next = isinstance(links, dict) and "next" in links
            if not has_next:
                break
            page += 1
        return collected[:cap]

    async def get_account(self) -> dict[str, Any]:
        data = await self.request("GET", "/account")
        return data if isinstance(data, dict) else {}

    async def get_leads(
        self,
        *,
        filters: Mapping[str, Any] | None = None,
        query: str | None = None,
        with_fields: str | None = None,
        order: Mapping[str, str] | None = None,
        max_items: int | None = None,
        page_limit: int = 250,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if filters:
            params["filter"] = dict(filters)
        if query:
            params["query"] = query
        if with_fields:
            params["with"] = with_fields
        if order:
            params["order"] = dict(order)
        return await self.paginate("/leads", "leads", params=params, max_items=max_items, page_limit=page_limit)

    async def get_lead(self, lead_id: int, *, with_fields: str | None = None) -> dict[str, Any]:
        params = {"with": with_fields} if with_fields else None
        data = await self.request("GET", f"/leads/{lead_id}", params=params)
        return data if isinstance(data, dict) else {}

    async def create_leads(self, leads: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        data = await self.request("POST", "/leads", json=list(leads))
        return _embedded_list(data, "leads")

    async def update_lead(self, lead_id: int, payload: Mapping[str, Any]) -> dict[str, Any]:
        body = dict(payload)
        body["id"] = lead_id
        data = await self.request("PATCH", "/leads", json=[body], retry_on_write=True)
        items = _embedded_list(data, "leads")
        return items[0] if items else (data if isinstance(data, dict) else {})

    async def get_tasks(
        self,
        *,
        filters: Mapping[str, Any] | None = None,
        order: Mapping[str, str] | None = None,
        max_items: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if filters:
            params["filter"] = dict(filters)
        if order:
            params["order"] = dict(order)
        return await self.paginate("/tasks", "tasks", params=params, max_items=max_items)

    async def get_task(self, task_id: int) -> dict[str, Any]:
        data = await self.request("GET", f"/tasks/{task_id}")
        return data if isinstance(data, dict) else {}

    async def create_tasks(self, tasks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        data = await self.request("POST", "/tasks", json=list(tasks))
        return _embedded_list(data, "tasks")

    async def update_task(self, task_id: int, payload: Mapping[str, Any]) -> dict[str, Any]:
        body = dict(payload)
        body["id"] = task_id
        data = await self.request("PATCH", "/tasks", json=[body], retry_on_write=True)
        items = _embedded_list(data, "tasks")
        return items[0] if items else (data if isinstance(data, dict) else {})

    async def complete_task(self, task_id: int, result_text: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"is_completed": True}
        if result_text:
            payload["result"] = {"text": result_text}
        return await self.update_task(task_id, payload)

    async def get_users(self, *, force: bool = False) -> list[dict[str, Any]]:
        cached = None if force else self._users_cache.get()
        if cached is not None:
            return cached
        users = await self.paginate("/users", "users", page_limit=250)
        return self._users_cache.set(users)

    async def get_user(self, user_id: int) -> dict[str, Any]:
        for user in await self.get_users():
            if int(user.get("id") or 0) == user_id:
                return user
        data = await self.request("GET", f"/users/{user_id}")
        return data if isinstance(data, dict) else {}

    async def get_pipelines(self, *, force: bool = False) -> list[dict[str, Any]]:
        cached = None if force else self._pipelines_cache.get()
        if cached is not None:
            return cached
        data = await self.request("GET", "/leads/pipelines")
        pipelines = _embedded_list(data, "pipelines")
        return self._pipelines_cache.set(pipelines)

    async def get_pipeline(self, pipeline_id: int) -> dict[str, Any]:
        for pipeline in await self.get_pipelines():
            if int(pipeline.get("id") or 0) == pipeline_id:
                return pipeline
        data = await self.request("GET", f"/leads/pipelines/{pipeline_id}")
        return data if isinstance(data, dict) else {}

    async def get_statuses(self, pipeline_id: int | None = None) -> list[dict[str, Any]]:
        pipelines = await self.get_pipelines()
        statuses: list[dict[str, Any]] = []
        for pipeline in pipelines:
            if pipeline_id is not None and int(pipeline.get("id") or 0) != pipeline_id:
                continue
            embedded = pipeline.get("_embedded") if isinstance(pipeline, dict) else None
            items = embedded.get("statuses") if isinstance(embedded, dict) else []
            for status in items or []:
                item = dict(status)
                item["pipeline_id"] = int(pipeline.get("id") or 0)
                item["pipeline_name"] = str(pipeline.get("name") or "")
                statuses.append(item)
        return statuses

    async def get_events(
        self,
        *,
        filters: Mapping[str, Any] | None = None,
        max_items: int | None = None,
        page_limit: int = 100,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if filters:
            params["filter"] = dict(filters)
        return await self.paginate("/events", "events", params=params, max_items=max_items, page_limit=page_limit)

    async def get_event_types(self, *, force: bool = False) -> list[dict[str, Any]]:
        cached = None if force else self._event_types_cache.get()
        if cached is not None:
            return cached
        data = await self.request("GET", "/events/types")
        types = _embedded_list(data, "events_types") or _embedded_list(data, "event_types")
        return self._event_types_cache.set(types)

    async def get_notes(
        self,
        entity_type: str,
        entity_id: int,
        *,
        max_items: int | None = 250,
    ) -> list[dict[str, Any]]:
        return await self.paginate(
            f"/{entity_type}/{entity_id}/notes",
            "notes",
            max_items=max_items,
            page_limit=250,
        )

    async def add_note(self, entity_type: str, entity_id: int, text: str, *, note_type: str = "common") -> dict[str, Any]:
        payload = [{"note_type": note_type, "params": {"text": text}}]
        data = await self.request("POST", f"/{entity_type}/{entity_id}/notes", json=payload)
        notes = _embedded_list(data, "notes")
        return notes[0] if notes else (data if isinstance(data, dict) else {})

    async def get_links(self, entity_type: str, entity_id: int) -> list[dict[str, Any]]:
        data = await self.request("GET", f"/{entity_type}/{entity_id}/links")
        return _embedded_list(data, "links")

    async def get_events_for_leads(self, lead_ids: Sequence[int], *, max_per_batch: int = 100) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        unique_ids = list(dict.fromkeys(int(lead_id) for lead_id in lead_ids if lead_id))
        for chunk in _chunks(unique_ids, 10):
            batch = await self.get_events(
                filters={"entity": "lead", "entity_id": chunk},
                max_items=max_per_batch,
            )
            events.extend(batch)
        return events


def _embedded_list(data: Any, key: str) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    embedded = data.get("_embedded")
    if not isinstance(embedded, dict):
        return []
    items = embedded.get(key) or []
    return [item for item in items if isinstance(item, dict)]


def _retry_after(response: httpx.Response) -> float | None:
    header = response.headers.get("Retry-After")
    if not header:
        return None
    try:
        return float(header)
    except ValueError:
        return None


def _chunks(items: Sequence[int], size: int) -> Iterable[list[int]]:
    for index in range(0, len(items), size):
        yield list(items[index : index + size])
