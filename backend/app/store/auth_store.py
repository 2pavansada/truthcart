"""Persistent, isolated storage for TruthCart accounts and web sessions."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class AuthUser:
    user_id: str
    name: str
    email: str
    password_hash: str
    created_at: int


class AuthStore:
    """Owns an auth-only SQLite file, separate from the replaceable catalogue DB."""

    def __init__(
        self,
        path: Path,
        session_ttl_seconds: int,
        csrf_ttl_seconds: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = path
        self.session_ttl_seconds = session_ttl_seconds
        self.csrf_ttl_seconds = csrf_ttl_seconds
        self._clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_auth_sessions_expiry ON sessions(expires_at);
                CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON sessions(user_id);
                CREATE TABLE IF NOT EXISTS csrf_tokens (
                    token_hash TEXT PRIMARY KEY,
                    expires_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_auth_csrf_expiry ON csrf_tokens(expires_at);
                """
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def new_opaque_token() -> str:
        return secrets.token_urlsafe(32)

    @staticmethod
    def _user(row: sqlite3.Row | None) -> AuthUser | None:
        if row is None:
            return None
        return AuthUser(
            user_id=row["user_id"],
            name=row["name"],
            email=row["email"],
            password_hash=row["password_hash"],
            created_at=row["created_at"],
        )

    def find_user_by_email(self, normalized_email: str) -> AuthUser | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT user_id, name, email, password_hash, created_at FROM users WHERE email = ?",
                (normalized_email,),
            ).fetchone()
            return self._user(row)
        finally:
            conn.close()

    def create_user(self, name: str, normalized_email: str, password_hash: str) -> AuthUser:
        now = int(self._clock())
        user = AuthUser(
            user_id=uuid.uuid4().hex,
            name=name,
            email=normalized_email,
            password_hash=password_hash,
            created_at=now,
        )
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO users (user_id, name, email, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                (user.user_id, user.name, user.email, user.password_hash, user.created_at),
            )
            conn.commit()
            return user
        finally:
            conn.close()

    def create_session(self, user_id: str, replacing_token: str | None = None) -> tuple[str, int]:
        now = int(self._clock())
        expires_at = now + self.session_ttl_seconds
        token = self.new_opaque_token()
        conn = self._connect()
        try:
            if replacing_token:
                conn.execute("DELETE FROM sessions WHERE token_hash = ?", (self.token_hash(replacing_token),))
            conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            conn.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (self.token_hash(token), user_id, now, expires_at),
            )
            conn.commit()
            return token, expires_at
        finally:
            conn.close()

    def user_for_session(self, token: str | None) -> AuthUser | None:
        if not token:
            return None
        now = int(self._clock())
        token_hash = self.token_hash(token)
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT u.user_id, u.name, u.email, u.password_hash, u.created_at
                FROM sessions s JOIN users u ON u.user_id = s.user_id
                WHERE s.token_hash = ? AND s.expires_at > ?
                """,
                (token_hash, now),
            ).fetchone()
            if row is None:
                conn.execute("DELETE FROM sessions WHERE token_hash = ? AND expires_at <= ?", (token_hash, now))
                conn.commit()
                return None
            return self._user(row)
        finally:
            conn.close()

    def revoke_session(self, token: str | None) -> None:
        if not token:
            return
        conn = self._connect()
        try:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (self.token_hash(token),))
            conn.commit()
        finally:
            conn.close()

    def issue_csrf_token(self) -> tuple[str, int]:
        now = int(self._clock())
        token = self.new_opaque_token()
        expires_at = now + self.csrf_ttl_seconds
        conn = self._connect()
        try:
            conn.execute("DELETE FROM csrf_tokens WHERE expires_at <= ?", (now,))
            conn.execute(
                "INSERT INTO csrf_tokens (token_hash, expires_at) VALUES (?, ?)",
                (self.token_hash(token), expires_at),
            )
            conn.commit()
            return token, expires_at
        finally:
            conn.close()

    def consume_csrf_token(self, token: str) -> bool:
        now = int(self._clock())
        conn = self._connect()
        try:
            cursor = conn.execute(
                "DELETE FROM csrf_tokens WHERE token_hash = ? AND expires_at > ?",
                (self.token_hash(token), now),
            )
            conn.commit()
            return cursor.rowcount == 1
        finally:
            conn.close()
