#!/usr/bin/env python3
"""Exchange an amoCRM authorization code for access and refresh tokens."""

from __future__ import annotations

import asyncio
import getpass
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
    except Exception as exc:
        print(f"✗ Configuration error: {exc}")
        return 1

    print("amoCRM OAuth setup")
    print(f"  subdomain : {settings.amocrm_subdomain}")
    print(f"  client_id : {settings.amocrm_client_id[:6]}…")
    print(f"  redirect  : {settings.amocrm_redirect_uri}")
    print()
    print("Paste the temporary Authorization Code from the amoCRM integration page.")
    print("The code is short-lived. Tokens will be stored in data/tokens.json, not printed.")
    code = getpass.getpass("Authorization code: ").strip()
    if not code:
        print("✗ Authorization code is empty")
        return 1

    storage = FileTokenStorage(settings.tokens_path)
    manager = TokenManager(
        subdomain=settings.amocrm_subdomain,
        client_id=settings.amocrm_client_id,
        client_secret=settings.amocrm_client_secret,
        redirect_uri=settings.amocrm_redirect_uri,
        storage=storage,
        skew_seconds=settings.token_refresh_skew_seconds,
    )
    client = AmoCRMClient(
        subdomain=settings.amocrm_subdomain,
        token_manager=manager,
        timeout=settings.http_timeout_seconds,
        max_retries=settings.http_max_retries,
    )
    try:
        await manager.exchange_authorization_code(code)
        account = await client.get_account()
        name = account.get("name") or account.get("id") or "unknown"
        print("✓ Tokens saved")
        print("✓ API connection successful")
        print(f"✓ Account reachable: {name}")
        print(f"✓ Storage: {settings.tokens_path}")
        return 0
    except Exception as exc:
        print(f"✗ OAuth setup failed: {exc}")
        return 1
    finally:
        await client.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
