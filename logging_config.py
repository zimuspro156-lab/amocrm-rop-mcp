"""Application logging without secrets."""

from __future__ import annotations

import logging
import re
from typing import Any

SECRET_PATTERNS = (
    re.compile(r"(Authorization:\s*Bearer)\s+\S+", re.IGNORECASE),
    re.compile(r"(access_token|refresh_token|client_secret|authorization|password_hash|MCP_PASSWORD)([\"']?\s*[:=]\s*[\"']?)[^\"'\s&]+", re.IGNORECASE),
    re.compile(r"(Bearer)\s+[A-Za-z0-9._\-]+", re.IGNORECASE),
)


class SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact(str(record.msg))
        if record.args:
            if isinstance(record.args, dict):
                record.args = {key: _redact(str(value)) if isinstance(value, str) else value for key, value in record.args.items()}
            else:
                record.args = tuple(_redact(str(arg)) if isinstance(arg, str) else arg for arg in record.args)
        return True


def _redact(value: str) -> str:
    redacted = value
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(r"\1 [REDACTED]", redacted)
    return redacted


def setup_logging(level: str = "INFO") -> None:
    numeric = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=numeric,
            format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        )
    else:
        root.setLevel(numeric)
    secret_filter = SecretFilter()
    for handler in root.handlers:
        handler.addFilter(secret_filter)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def safe_error(exc: BaseException) -> dict[str, Any]:
    return {"type": type(exc).__name__, "message": str(exc)}
