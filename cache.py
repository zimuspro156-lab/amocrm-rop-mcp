"""Small in-memory TTL cache for rarely changing amoCRM metadata."""

from __future__ import annotations

import time
from typing import Generic, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    def __init__(self, ttl_seconds: int = 300) -> None:
        self.ttl_seconds = ttl_seconds
        self._value: T | None = None
        self._expires_at: float = 0.0

    def get(self) -> T | None:
        if self._value is None:
            return None
        if self.ttl_seconds <= 0:
            return None
        if time.monotonic() >= self._expires_at:
            self._value = None
            return None
        return self._value

    def set(self, value: T) -> T:
        self._value = value
        self._expires_at = time.monotonic() + self.ttl_seconds
        return value

    def clear(self) -> None:
        self._value = None
        self._expires_at = 0.0
