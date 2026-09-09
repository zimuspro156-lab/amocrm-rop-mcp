#!/usr/bin/env python3
"""Download official cloudflared into this project's runtime/ only."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import ssl
import sys
import urllib.request
from pathlib import Path

APP_DIR = Path("/opt/amocrm-rop-mcp")
TARGET = APP_DIR / "runtime" / "cloudflared"
GITHUB_API = "https://api.github.com/repos/cloudflare/cloudflared/releases/latest"
USER_AGENT = "amocrm-rop-mcp-installer"


def die(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    if os.geteuid() != 0:
        die("Run as root via ./install-cloudflare.sh")
    if Path.cwd().resolve() != APP_DIR:
        die(f"Run from {APP_DIR}")
    machine = platform.machine()
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(machine)
    if not arch:
        die(f"Unsupported architecture: {machine}")
    if TARGET.exists():
        print(f"Using existing {TARGET}")
        return 0

    request = urllib.request.Request(GITHUB_API, headers={"User-Agent": USER_AGENT})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(request, context=ctx, timeout=30) as response:
        release = json.loads(response.read().decode("utf-8"))
    asset = next(
        (item for item in release.get("assets") or [] if item.get("name") == f"cloudflared-linux-{arch}"),
        None,
    )
    url = str(asset.get("browser_download_url") or "") if asset else ""
    digest = str(asset.get("digest") or "") if asset else ""
    if not url.startswith("https://github.com/cloudflare/cloudflared/releases/download/"):
        die("GitHub did not provide an official cloudflared download URL")
    if not digest.startswith("sha256:") or len(digest) != 71:
        die("GitHub did not provide SHA256 for cloudflared")

    download = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(download, context=ctx, timeout=180) as response:
        payload = response.read()
    actual = hashlib.sha256(payload).hexdigest()
    if actual != digest.split(":", 1)[1]:
        die("cloudflared SHA256 mismatch")

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    tmp = TARGET.with_suffix(".tmp")
    tmp.write_bytes(payload)
    tmp.chmod(0o755)
    tmp.replace(TARGET)
    print(f"cloudflared {release.get('tag_name')}: SHA256 verified, installed only at {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
