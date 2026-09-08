"""Application settings loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from version import APP_NAME, __version__

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_TOKEN_PATH = DATA_DIR / "tokens.json"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    amocrm_subdomain: str = ""
    amocrm_client_id: str = ""
    amocrm_client_secret: str = ""
    amocrm_redirect_uri: str = "https://localhost"
    amocrm_access_token: str = ""
    amocrm_refresh_token: str = ""

    app_timezone: str = "Europe/Moscow"
    default_stale_days: int = Field(default=2, ge=1, le=365)
    default_pipeline_id: int | None = None
    amocrm_lost_reason_field_id: int | None = None

    mcp_host: str = "127.0.0.1"
    mcp_port: int = Field(default=8000, ge=1, le=65535)
    mcp_path: str = "/mcp"

    mcp_public_url: str = ""
    mcp_auth_token: str = ""
    mcp_auth_issuer_url: str = ""
    mcp_allowed_hosts: str = ""
    mcp_disable_dns_rebinding_protection: bool = False

    oauth_issuer: str = ""
    oauth_client_id: str = "chatgpt-amocrm-mcp"
    oauth_redirect_uri: str = ""
    oauth_access_token_ttl: int = Field(default=3600, ge=60)
    oauth_refresh_token_ttl: int = Field(default=2592000, ge=300)
    oauth_auth_code_ttl: int = Field(default=300, ge=30)
    mcp_login: str = ""
    mcp_password_hash: str = ""

    log_level: str = "INFO"
    http_timeout_seconds: float = Field(default=30.0, gt=0)
    http_max_retries: int = Field(default=3, ge=1, le=8)
    token_refresh_skew_seconds: int = Field(default=120, ge=10)
    metadata_cache_ttl_seconds: int = Field(default=300, ge=0)
    max_list_items: int = Field(default=5000, ge=50, le=20000)
    tokens_path: Path = DEFAULT_TOKEN_PATH

    @field_validator("amocrm_subdomain", mode="before")
    @classmethod
    def _strip_subdomain(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        cleaned = value.strip().lower()
        cleaned = cleaned.removeprefix("https://").removeprefix("http://")
        cleaned = cleaned.split("/")[0]
        cleaned = cleaned.removesuffix(".amocrm.ru").removesuffix(".kommo.com")
        return cleaned

    @field_validator("default_pipeline_id", "amocrm_lost_reason_field_id", mode="before")
    @classmethod
    def _empty_optional_int(cls, value: object) -> object:
        if value in ("", None):
            return None
        return value

    @field_validator("mcp_path")
    @classmethod
    def _normalize_path(cls, value: str) -> str:
        path = value.strip() or "/mcp"
        if not path.startswith("/"):
            path = f"/{path}"
        return path.rstrip("/") or "/mcp"

    @field_validator("mcp_public_url", "oauth_issuer", mode="before")
    @classmethod
    def _normalize_public_origin(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        url = value.strip().rstrip("/")
        if url.endswith("/mcp"):
            url = url[:-4].rstrip("/")
        return url

    @property
    def version(self) -> str:
        return __version__

    @property
    def app_name(self) -> str:
        return APP_NAME

    @property
    def api_base_url(self) -> str:
        if not self.amocrm_subdomain:
            raise ValueError("AMOCRM_SUBDOMAIN is not configured")
        return f"https://{self.amocrm_subdomain}.amocrm.ru/api/v4"

    @property
    def oauth_token_url(self) -> str:
        if not self.amocrm_subdomain:
            raise ValueError("AMOCRM_SUBDOMAIN is not configured")
        return f"https://{self.amocrm_subdomain}.amocrm.ru/oauth2/access_token"

    @property
    def allowed_hosts(self) -> list[str]:
        if self.mcp_allowed_hosts.strip():
            return [item.strip() for item in self.mcp_allowed_hosts.split(",") if item.strip()]
        host = self.public_host
        if host:
            return [host, f"{host}:*"]
        return []

    @property
    def oauth_issuer_url(self) -> str:
        return (self.oauth_issuer or self.mcp_public_url).rstrip("/")

    @property
    def mcp_resource_url(self) -> str:
        origin = self.mcp_public_url.rstrip("/")
        if not origin:
            return f"http://{self.mcp_host}:{self.mcp_port}{self.mcp_path}"
        return origin + self.mcp_path

    @property
    def public_host(self) -> str:
        url = self.mcp_public_url.strip()
        if not url:
            return ""
        cleaned = url.removeprefix("https://").removeprefix("http://")
        return cleaned.split("/")[0].split(":")[0]

    @property
    def oauth_redirect_uris(self) -> list[str]:
        return [item.strip() for item in self.oauth_redirect_uri.split(",") if item.strip()]

    @property
    def oauth_store_dir(self) -> Path:
        return self.tokens_path.parent

    def mcp_oauth_enabled(self) -> bool:
        return bool(self.mcp_public_url.strip() and self.mcp_login.strip() and self.mcp_password_hash.strip())

    def require_oauth_app(self) -> None:
        missing: list[str] = []
        if not self.amocrm_subdomain:
            missing.append("AMOCRM_SUBDOMAIN")
        if not self.amocrm_client_id:
            missing.append("AMOCRM_CLIENT_ID")
        if not self.amocrm_client_secret:
            missing.append("AMOCRM_CLIENT_SECRET")
        if not self.amocrm_redirect_uri:
            missing.append("AMOCRM_REDIRECT_URI")
        if missing:
            from exceptions import ConfigurationError

            raise ConfigurationError("Missing required amoCRM OAuth settings: " + ", ".join(missing))

    def mcp_auth_enabled(self) -> bool:
        return self.mcp_oauth_enabled() or bool(self.mcp_auth_token.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
