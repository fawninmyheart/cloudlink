import base64
import hashlib
import hmac
import os
import sqlite3
from datetime import datetime, timezone

from app.config import Settings


PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 260_000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )
    return f"{PASSWORD_SCHEME}${PASSWORD_ITERATIONS}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations_text, salt_text, digest_text = encoded.split("$", 3)
        iterations = int(iterations_text)
    except ValueError:
        return False
    if scheme != PASSWORD_SCHEME:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        _unb64(salt_text),
        iterations,
    )
    return hmac.compare_digest(_b64(digest), digest_text)


def get_admin_password_hash(conn: sqlite3.Connection, username: str) -> str:
    row = conn.execute(
        "SELECT password_hash FROM admin_credentials WHERE username = ?",
        (username,),
    ).fetchone()
    return row["password_hash"] if row else ""


def set_admin_password(
    conn: sqlite3.Connection,
    username: str,
    password: str,
) -> None:
    conn.execute(
        """
        INSERT INTO admin_credentials (username, password_hash, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            password_hash = excluded.password_hash,
            updated_at = excluded.updated_at
        """,
        (username, hash_password(password), utc_now()),
    )


def verify_admin_credentials(
    conn: sqlite3.Connection,
    username: str,
    password: str,
    settings: Settings,
) -> bool:
    if not hmac.compare_digest(username, settings.admin_username):
        return False
    stored_hash = get_admin_password_hash(conn, settings.admin_username)
    if stored_hash:
        return verify_password(password, stored_hash)
    return bool(settings.admin_password) and hmac.compare_digest(
        password,
        settings.admin_password,
    )
