"""Domain exceptions for amoCRM API and application configuration."""

from __future__ import annotations


class AmoCRMError(Exception):
    """Base error for amoCRM API failures."""

    def __init__(self, message: str, *, status_code: int | None = None, error: str = "amocrm_error") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error = error

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "success": False,
            "error": self.error,
            "message": self.message,
        }
        if self.status_code is not None:
            payload["status_code"] = self.status_code
        return payload


class AmoCRMAuthError(AmoCRMError):
    def __init__(self, message: str = "amoCRM authorization failed", *, status_code: int | None = 401) -> None:
        super().__init__(message, status_code=status_code, error="unauthorized")


class AmoCRMPermissionError(AmoCRMError):
    def __init__(
        self,
        message: str = "amoCRM integration does not have sufficient permissions",
        *,
        status_code: int | None = 403,
    ) -> None:
        super().__init__(message, status_code=status_code, error="insufficient_permissions")


class AmoCRMNotFoundError(AmoCRMError):
    def __init__(self, message: str = "Requested amoCRM entity was not found", *, status_code: int | None = 404) -> None:
        super().__init__(message, status_code=status_code, error="not_found")


class AmoCRMRateLimitError(AmoCRMError):
    def __init__(
        self,
        message: str = "amoCRM rate limit exceeded",
        *,
        status_code: int | None = 429,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code, error="rate_limited")
        self.retry_after = retry_after


class AmoCRMConflictError(AmoCRMError):
    def __init__(self, message: str = "amoCRM reported a conflict", *, status_code: int | None = 409) -> None:
        super().__init__(message, status_code=status_code, error="conflict")


class AmoCRMValidationError(AmoCRMError):
    def __init__(self, message: str, *, status_code: int | None = 400) -> None:
        super().__init__(message, status_code=status_code, error="validation_error")


class ConfigurationError(AmoCRMError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=None, error="configuration_error")
