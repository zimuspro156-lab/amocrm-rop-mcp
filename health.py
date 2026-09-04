"""Health/readiness helpers used by tests without booting uvicorn."""

from version import APP_NAME, __version__


def health_payload() -> dict[str, str]:
    return {"status": "healthy", "service": APP_NAME, "version": __version__}
