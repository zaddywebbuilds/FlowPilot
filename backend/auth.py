"""Password and session handling.

A single shared operator password rather than user accounts: this is a tool one
person (or a small team sharing a login) runs, and accounts would add a user
table, roles and recovery flows that nothing here needs yet.
"""

import hashlib
import hmac
import secrets
import time

import db

ITERATIONS = 240_000
COOKIE_NAME = "flowpilot_session"
MIN_PASSWORD_LENGTH = 8


def _hash_password(password: str, salt: bytes) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def set_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )
    db.set_setting("auth_password", _hash_password(password, secrets.token_bytes(16)))
    # Any existing session was issued against the old password.
    db.execute("DELETE FROM sessions")


def is_configured() -> bool:
    return bool(db.get_setting("auth_password", ""))


def verify_password(password: str) -> bool:
    stored = db.get_setting("auth_password", "")
    if not stored:
        return False
    try:
        _, iterations, salt_hex, _ = stored.split("$")
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    expected = stored.rsplit("$", 1)[1]
    return hmac.compare_digest(candidate.hex(), expected)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(label: str = "") -> tuple[str, int]:
    """Return (token, max_age_seconds). Only the hash is stored."""
    purge_expired()
    token = secrets.token_urlsafe(32)
    max_age = db.session_days() * 86400
    db.execute(
        "INSERT INTO sessions(token_hash, created_at, expires_at, label) VALUES (?,?,?,?)",
        (_token_hash(token), time.time(), time.time() + max_age, label),
    )
    return token, max_age


def validate_token(token: str | None) -> bool:
    if not token:
        return False
    row = db.query_one(
        "SELECT expires_at FROM sessions WHERE token_hash = ?", (_token_hash(token),)
    )
    if not row:
        return False
    if row["expires_at"] < time.time():
        destroy_session(token)
        return False
    return True


def destroy_session(token: str | None) -> None:
    if token:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def purge_expired() -> None:
    db.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))


def active_session_count() -> int:
    purge_expired()
    row = db.query_one("SELECT COUNT(*) AS n FROM sessions")
    return row["n"] if row else 0
