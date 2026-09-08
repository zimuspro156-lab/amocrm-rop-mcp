#!/usr/bin/env python3
"""Fill ChatGPT OAuth settings in .env and print the MCP login password once."""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from oauth_server import password_hash  # noqa: E402

DEFAULT_LOGIN = "admin"


def _parse_env(text: str) -> list[str]:
    return text.splitlines()


def _get(lines: list[str], key: str) -> str:
    prefix = key + "="
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix) :]
    return ""


def _set(lines: list[str], key: str, value: str, overwrite: bool = False) -> list[str]:
    prefix = key + "="
    found = False
    updated: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(prefix):
            found = True
            current = stripped[len(prefix) :]
            if overwrite or current == "":
                updated.append(f"{key}={value}")
            else:
                updated.append(line)
        else:
            updated.append(line)
    if not found:
        updated.append(f"{key}={value}")
    return updated


def _normalize_origin(url: str) -> str:
    origin = url.strip().rstrip("/")
    if origin.endswith("/mcp"):
        origin = origin[:-4].rstrip("/")
    parsed = urlparse(origin)
    if parsed.scheme != "https" or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username:
        raise ValueError("Укажите HTTPS origin без пути, например https://amocrm-mcp.example.com")
    return origin


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate ChatGPT OAuth credentials for amoCRM MCP")
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--public-url", default="")
    parser.add_argument("--non-interactive", action="store_true")
    args = parser.parse_args()

    env_path = Path(args.env_file)
    example = ROOT / ".env.example"
    if not env_path.exists():
        if not example.exists():
            print("✗ .env.example is missing")
            return 1
        env_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Created {env_path} from example")

    lines = _parse_env(env_path.read_text(encoding="utf-8"))
    public_url = args.public_url.strip() or _get(lines, "MCP_PUBLIC_URL")
    if not public_url and not args.non_interactive and sys.stdin.isatty():
        public_url = input("Публичный HTTPS адрес без /mcp (например https://amocrm-mcp.example.com): ").strip()
    generated_password = ""
    if public_url:
        try:
            public_url = _normalize_origin(public_url)
        except ValueError as exc:
            print(f"✗ {exc}")
            return 1
        lines = _set(lines, "MCP_PUBLIC_URL", public_url, overwrite=True)
        if not _get(lines, "OAUTH_ISSUER"):
            lines = _set(lines, "OAUTH_ISSUER", public_url, overwrite=True)
        host = urlparse(public_url).hostname or ""
        if host and not _get(lines, "MCP_ALLOWED_HOSTS"):
            lines = _set(lines, "MCP_ALLOWED_HOSTS", f"{host},{host}:*")

    if not _get(lines, "OAUTH_CLIENT_ID"):
        lines = _set(lines, "OAUTH_CLIENT_ID", "chatgpt-amocrm-mcp")
    if not _get(lines, "MCP_LOGIN"):
        lines = _set(lines, "MCP_LOGIN", DEFAULT_LOGIN)

    if not _get(lines, "MCP_PASSWORD_HASH"):
        generated_password = secrets.token_urlsafe(24)
        lines = _set(lines, "MCP_PASSWORD_HASH", password_hash(generated_password), overwrite=True)

    env_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    try:
        env_path.chmod(0o640)
    except OSError:
        pass

    login = _get(lines, "MCP_LOGIN") or DEFAULT_LOGIN
    if generated_password:
        print()
        print(f"Логин страницы OAuth: {login}")
        print("Пароль подключения MCP (сохраните в менеджер паролей; показывается один раз):")
        print(generated_password)
        print()
        print("Это пароль ChatGPT → MCP, не токен amoCRM.")
    else:
        print(f"OAuth login already set ({login}). Password hash was not rotated.")
        print("To rotate: clear MCP_PASSWORD_HASH in .env and run this script again.")

    if _get(lines, "MCP_PUBLIC_URL"):
        print(f"Адрес подключения ChatGPT: {_get(lines, 'MCP_PUBLIC_URL')}/mcp")
        print("После создания connector скопируйте Callback URL в OAUTH_REDIRECT_URI и перезапустите сервис.")
    else:
        print("Задайте MCP_PUBLIC_URL в .env (HTTPS без /mcp) и перезапустите setup.")
    return 0


if __name__ == "__main__":
    if "--rotate-password" in sys.argv:
        password = getpass.getpass("New MCP password: ").strip()
        if not password:
            raise SystemExit("Password is empty")
        print(password_hash(password))
        raise SystemExit(0)
    raise SystemExit(main())
