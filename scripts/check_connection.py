#!/usr/bin/env python3
"""Verify .env, tokens and amoCRM API access without printing secrets."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from amocrm_client import AmoCRMClient  # noqa: E402
from config import get_settings  # noqa: E402
from logging_config import setup_logging  # noqa: E402
from token_manager import FileTokenStorage, TokenManager  # noqa: E402


async def run() -> int:
    settings = get_settings()
    setup_logging(settings.log_level)
    try:
        settings.require_oauth_app()
        print("✓ Configuration loaded")
    except Exception as exc:
        print(f"✗ Configuration loaded: {exc}")
        return 1

    storage = FileTokenStorage(settings.tokens_path)
    tokens = await storage.load()
    if tokens is None and not (settings.amocrm_access_token and settings.amocrm_refresh_token):
        print("✗ amoCRM authorized: no tokens. Run python scripts/oauth_setup.py")
        return 1

    manager = TokenManager(
        subdomain=settings.amocrm_subdomain,
        client_id=settings.amocrm_client_id,
        client_secret=settings.amocrm_client_secret,
        redirect_uri=settings.amocrm_redirect_uri,
        storage=storage,
        initial_access_token=settings.amocrm_access_token,
        initial_refresh_token=settings.amocrm_refresh_token,
        skew_seconds=settings.token_refresh_skew_seconds,
    )
    client = AmoCRMClient(
        subdomain=settings.amocrm_subdomain,
        token_manager=manager,
        timeout=settings.http_timeout_seconds,
        max_retries=settings.http_max_retries,
        metadata_ttl=settings.metadata_cache_ttl_seconds,
    )
    try:
        await manager.ensure_fresh()
        print("✓ amoCRM authorized")
        account = await client.get_account()
        print(f"✓ API connection successful ({account.get('name') or account.get('id') or 'account'})")
        pipelines = await client.get_pipelines()
        print(f"✓ Pipelines available: {len(pipelines)}")
        users = await client.get_users()
        print(f"✓ Users available: {len(users)}")
        print("✓ MCP backend ready")
        return 0
    except Exception as exc:
        print(f"✗ Check failed: {exc}")
        return 1
    finally:
        await client.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
