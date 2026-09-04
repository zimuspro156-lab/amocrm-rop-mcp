"""Shared MCP tool helpers."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, TypeVar

from mcp.types import ToolAnnotations

from exceptions import AmoCRMError, ConfigurationError

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])

READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)
WRITE_CREATE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)
WRITE_MOVE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True)


def dump(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json", by_alias=True)
    return dict(model)


def tool_guard(func: F) -> F:
    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            return await func(*args, **kwargs)
        except (AmoCRMError, ConfigurationError) as exc:
            logger.warning(
                "tool=%s error=%s",
                func.__name__,
                exc.error if isinstance(exc, AmoCRMError) else "configuration_error",
            )
            return exc.as_dict()
        except ValueError as exc:
            logger.warning("tool=%s validation=%s", func.__name__, str(exc))
            return {"success": False, "error": "validation_error", "message": str(exc)}
        except Exception:
            logger.exception("tool=%s failed with unexpected error", func.__name__)
            return {
                "success": False,
                "error": "internal_error",
                "message": "Internal server error while executing the tool",
            }
        finally:
            logger.info("tool=%s duration_ms=%.1f", func.__name__, (time.perf_counter() - started) * 1000)

    return wrapper  # type: ignore[return-value]
