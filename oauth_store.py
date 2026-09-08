"""SQLite persistence for ChatGPT → MCP OAuth codes and tokens."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


def _now() -> int:
    return int(time.time())


class OAuthStore:
    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        try:
            data_dir.chmod(0o700)
        except OSError:
            pass
        self.file = data_dir / "oauth.sqlite"
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.file), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        try:
            self.file.chmod(0o600)
        except OSError:
            pass
        self._db.execute("PRAGMA journal_mode = DELETE")
        self._db.execute("PRAGMA busy_timeout = 3000")
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS oauth_codes (
              token_hash TEXT PRIMARY KEY,
              client_id TEXT NOT NULL,
              redirect_uri TEXT NOT NULL,
              code_challenge TEXT NOT NULL,
              code_challenge_method TEXT NOT NULL,
              scope TEXT NOT NULL,
              resource TEXT,
              pending_id TEXT,
              used INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL,
              expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS oauth_access_tokens (
              token_hash TEXT PRIMARY KEY,
              client_id TEXT NOT NULL,
              scope TEXT NOT NULL,
              family TEXT NOT NULL,
              revoked INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL,
              expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS oauth_refresh_tokens (
              token_hash TEXT PRIMARY KEY,
              client_id TEXT NOT NULL,
              scope TEXT NOT NULL,
              family TEXT NOT NULL,
              revoked INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL,
              expires_at INTEGER NOT NULL
            );
            """
        )

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def purge_expired(self) -> None:
        t = _now()
        with self._lock:
            self._db.execute("DELETE FROM oauth_codes WHERE expires_at < ?", (t,))
            self._db.execute("DELETE FROM oauth_access_tokens WHERE expires_at < ?", (t,))
            self._db.execute("DELETE FROM oauth_refresh_tokens WHERE expires_at < ?", (t,))

    def save_code(self, row: dict[str, Any]) -> None:
        self.purge_expired()
        with self._lock:
            self._db.execute(
                """
                INSERT INTO oauth_codes (
                  token_hash, client_id, redirect_uri, code_challenge,
                  code_challenge_method, scope, resource, pending_id, used, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    row["token_hash"],
                    row["client_id"],
                    row["redirect_uri"],
                    row["code_challenge"],
                    row["code_challenge_method"],
                    row["scope"],
                    row.get("resource"),
                    row.get("pending_id"),
                    row["created_at"],
                    row["expires_at"],
                ),
            )

    def peek_code(self, token_hash: str) -> dict[str, Any]:
        self.purge_expired()
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM oauth_codes WHERE token_hash = ?", (token_hash,)
            ).fetchone()
        if row is None:
            return {"ok": False, "reason": "missing"}
        if row["used"]:
            return {"ok": False, "reason": "replay", "row": dict(row)}
        if row["expires_at"] < _now():
            return {"ok": False, "reason": "expired"}
        return {"ok": True, "row": dict(row)}

    def consume_code(self, token_hash: str) -> dict[str, Any]:
        self.purge_expired()
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    "SELECT * FROM oauth_codes WHERE token_hash = ?", (token_hash,)
                ).fetchone()
                if row is None:
                    self._db.execute("COMMIT")
                    return {"ok": False, "reason": "missing"}
                if row["used"]:
                    self._db.execute("COMMIT")
                    return {"ok": False, "reason": "replay", "row": dict(row)}
                if row["expires_at"] < _now():
                    self._db.execute("COMMIT")
                    return {"ok": False, "reason": "expired"}
                updated = self._db.execute(
                    "UPDATE oauth_codes SET used = 1 WHERE token_hash = ? AND used = 0",
                    (token_hash,),
                )
                self._db.execute("COMMIT")
                if updated.rowcount != 1:
                    return {"ok": False, "reason": "replay", "row": dict(row)}
                return {"ok": True, "row": dict(row)}
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    def save_tokens(
        self,
        *,
        access_hash: str,
        refresh_hash: str,
        client_id: str,
        scope: str,
        family: str,
        now_sec: int,
        access_ttl: int,
        refresh_ttl: int,
    ) -> None:
        with self._lock:
            self._db.execute(
                """
                INSERT INTO oauth_access_tokens (
                  token_hash, client_id, scope, family, revoked, created_at, expires_at
                ) VALUES (?, ?, ?, ?, 0, ?, ?)
                """,
                (access_hash, client_id, scope, family, now_sec, now_sec + access_ttl),
            )
            self._db.execute(
                """
                INSERT INTO oauth_refresh_tokens (
                  token_hash, client_id, scope, family, revoked, created_at, expires_at
                ) VALUES (?, ?, ?, ?, 0, ?, ?)
                """,
                (refresh_hash, client_id, scope, family, now_sec, now_sec + refresh_ttl),
            )

    def get_access(self, token_hash: str) -> dict[str, Any] | None:
        self.purge_expired()
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM oauth_access_tokens WHERE token_hash = ?", (token_hash,)
            ).fetchone()
        return dict(row) if row is not None else None

    def rotate_refresh(self, old_hash: str, next_tokens: dict[str, Any]) -> dict[str, Any]:
        self.purge_expired()
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    "SELECT * FROM oauth_refresh_tokens WHERE token_hash = ?", (old_hash,)
                ).fetchone()
                if row is None:
                    self._db.execute("COMMIT")
                    return {"ok": False, "reason": "missing"}
                if row["revoked"]:
                    self._db.execute(
                        "UPDATE oauth_access_tokens SET revoked = 1 WHERE family = ?",
                        (row["family"],),
                    )
                    self._db.execute(
                        "UPDATE oauth_refresh_tokens SET revoked = 1 WHERE family = ?",
                        (row["family"],),
                    )
                    self._db.execute("COMMIT")
                    return {"ok": False, "reason": "replay", "row": dict(row)}
                if row["expires_at"] < _now():
                    self._db.execute("COMMIT")
                    return {"ok": False, "reason": "expired"}
                self._db.execute(
                    "UPDATE oauth_refresh_tokens SET revoked = 1 WHERE token_hash = ?",
                    (old_hash,),
                )
                self._db.execute(
                    """
                    INSERT INTO oauth_access_tokens (
                      token_hash, client_id, scope, family, revoked, created_at, expires_at
                    ) VALUES (?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        next_tokens["access_hash"],
                        row["client_id"],
                        row["scope"],
                        row["family"],
                        next_tokens["now_sec"],
                        next_tokens["now_sec"] + next_tokens["access_ttl"],
                    ),
                )
                self._db.execute(
                    """
                    INSERT INTO oauth_refresh_tokens (
                      token_hash, client_id, scope, family, revoked, created_at, expires_at
                    ) VALUES (?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        next_tokens["refresh_hash"],
                        row["client_id"],
                        row["scope"],
                        row["family"],
                        next_tokens["now_sec"],
                        next_tokens["now_sec"] + next_tokens["refresh_ttl"],
                    ),
                )
                self._db.execute("COMMIT")
                return {"ok": True, "row": dict(row)}
            except Exception:
                self._db.execute("ROLLBACK")
                raise
