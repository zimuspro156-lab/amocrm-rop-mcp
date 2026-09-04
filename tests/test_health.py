from health import health_payload
from version import APP_NAME, __version__


def test_health_payload() -> None:
    payload = health_payload()
    assert payload["status"] == "healthy"
    assert payload["service"] == APP_NAME
    assert payload["version"] == __version__
