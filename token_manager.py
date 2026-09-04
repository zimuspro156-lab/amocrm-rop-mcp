"""OAuth token storage and refresh for amoCRM."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from exceptions import AmoCRMAuthError, ConfigurationError

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_at: datetime
    token_type: str = "Bearer"

    def is_expired(self, skew_seconds: int = 120) -> bool:
        return datetime.now(timezone.utc) + timedelta(seconds=skew_seconds) >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at.astimezone(timezone.utc).isoformat(),
            "token_type": self.token_type,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TokenPair:
        expires_at = payload.get("expires_at")
        if isinstance(expires_at, (int, float)):
            expiry = datetime.fromtimestamp(expires_at, tz=timezone.utc)
        else:
            expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
        return cls(
            access_token=str(payload["access_token"]),
            refresh_token=str(payload["refresh_token"]),
            expires_at=expiry.astimezone(timezone.utc),
            token_type=str(payload.get("token_type", "Bearer")),
        )

    @classmethod
    def from_oauth_response(cls, payload: dict[str, Any]) -> TokenPair:
        expires_in = int(payload.get("expires_in") or 86400)
        return cls(
            access_token=str(payload["access_token"]),
            refresh_token=str(payload["refresh_token"]),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
            token_type=str(payload.get("token_type", "Bearer")),
        )


class TokenStorage(ABC):
    """Replaceable token persistence. File storage is the MVP implementation."""

    @abstractmethod
    async def load(self) -> TokenPair | None:
        raise NotImplementedError

    @abstractmethod
    async def save(self, tokens: TokenPair) -> None:
        raise NotImplementedError

    @abstractmethod
    async def clear(self) -> None:
        raise NotImplementedError


class FileTokenStorage(TokenStorage):
    def __init__(self, path: Path) -> None:
        self.path = path

    async def load(self) -> TokenPair | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return TokenPair.from_dict(payload)
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.error("Failed to load token storage: %s", type(exc).__name__)
            return None

    async def save(self, tokens: TokenPair) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(tokens.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(self.path)
        _restrict_permissions(self.path)
        logger.info("Saved refreshed amoCRM token pair to storage")

    async def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()


class MemoryTokenStorage(TokenStorage):
    def __init__(self, tokens: TokenPair | None = None) -> None:
        self._tokens = tokens

    async def load(self) -> TokenPair | None:
        return self._tokens

    async def save(self, tokens: TokenPair) -> None:
        self._tokens = tokens

    async def clear(self) -> None:
        self._tokens = None


class TokenManager:
    def __init__(
        self,
        *,
        subdomain: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        storage: TokenStorage,
        initial_access_token: str = "",
        initial_refresh_token: str = "",
        skew_seconds: int = 120,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.subdomain = subdomain
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.storage = storage
        self.skew_seconds = skew_seconds
        self._http = http_client
        self._owns_http = http_client is None
        self._lock = asyncio.Lock()
        self._memory = self._from_env(initial_access_token, initial_refresh_token)

    def _from_env(self, access_token: str, refresh_token: str) -> TokenPair | None:
        if access_token and refresh_token:
            return TokenPair(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=12),
            )
        return None

    @property
    def token_url(self) -> str:
        return f"https://{self.subdomain}.amocrm.ru/oauth2/access_token"

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30.0)
        return self._http

    async def aclose(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    async def current(self) -> TokenPair | None:
        stored = await self.storage.load()
        if stored is not None:
            self._memory = stored
            return stored
        return self._memory

    async def get_access_token(self) -> str:
        tokens = await self.ensure_fresh()
        return tokens.access_token

    async def exchange_authorization_code(self, code: str) -> TokenPair:
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "authorization_code",
            "code": code.strip(),
            "redirect_uri": self.redirect_uri,
        }
        tokens = await self._request_tokens(payload)
        await self.storage.save(tokens)
        self._memory = tokens
        logger.info("Exchanged amoCRM authorization code for a token pair")
        return tokens

    async def ensure_fresh(self) -> TokenPair:
        async with self._lock:
            tokens = await self.current()
            if tokens is None:
                raise AmoCRMAuthError("No amoCRM tokens found. Run python scripts/oauth_setup.py")
            if not tokens.is_expired(self.skew_seconds):
                return tokens
            logger.info("amoCRM access token is near expiry; refreshing")
            return await self._refresh_locked(tokens)

    async def refresh(self, tokens: TokenPair | None = None) -> TokenPair:
        async with self._lock:
            current = tokens or await self.current()
            if current is None:
                raise AmoCRMAuthError("Cannot refresh amoCRM tokens: refresh token is missing")
            return await self._refresh_locked(current)

    async def _refresh_locked(self, tokens: TokenPair) -> TokenPair:
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": tokens.refresh_token,
            "redirect_uri": self.redirect_uri,
        }
        refreshed = await self._request_tokens(payload)
        await self.storage.save(refreshed)
        self._memory = refreshed
        logger.info("Refreshed amoCRM access/refresh token pair")
        return refreshed

    async def _request_tokens(self, payload: dict[str, str]) -> TokenPair:
        if not self.subdomain or not self.client_id or not self.client_secret:
            raise ConfigurationError("amoCRM OAuth application settings are incomplete")
        client = await self._client()
        try:
            response = await client.post(self.token_url, json=payload)
        except httpx.HTTPError as exc:
            raise AmoCRMAuthError(f"Failed to reach amoCRM OAuth endpoint: {exc.__class__.__name__}") from exc
        if response.status_code >= 400:
            logger.error("amoCRM OAuth request failed with HTTP %s", response.status_code)
            raise AmoCRMAuthError("amoCRM OAuth request failed", status_code=response.status_code)
        data = response.json()
        if "access_token" not in data or "refresh_token" not in data:
            raise AmoCRMAuthError("amoCRM OAuth response did not include a token pair")
        return TokenPair.from_oauth_response(data)


def _restrict_permissions(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        logger.debug("Could not restrict token file permissions on this platform")
