"""Signed chat-session tokens (LLD-14): the holder may use exactly one session until it expires."""

import base64
import hashlib
import hmac
import secrets
import time
import uuid

from app.config.settings import get_settings

_DEV_KEY = secrets.token_bytes(32)          # no NEXTAUTH_SECRET configured: tokens last until the process restarts


def _key() -> bytes:
    s = get_settings()
    secret = s.nextauth_secret.get_secret_value()
    if not secret and s.env != "dev":
        raise RuntimeError("NEXTAUTH_SECRET is required outside dev")
    return hashlib.sha256(b"chat-session:" + (secret.encode() or _DEV_KEY)).digest()


def _sign(body: str) -> str:
    return base64.urlsafe_b64encode(hmac.new(_key(), body.encode(), hashlib.sha256).digest()).decode().rstrip("=")


def issue(session_id: uuid.UUID, ttl_s: int) -> str:
    body = f"{session_id}.{int(time.time()) + ttl_s}"
    return f"{body}.{_sign(body)}"


def valid_for(token: str, session_id: uuid.UUID) -> bool:
    parts = token.split(".")
    if len(parts) != 3 or not parts[1].isdigit():
        return False
    body = f"{parts[0]}.{parts[1]}"
    return hmac.compare_digest(_sign(body), parts[2]) and parts[0] == str(session_id) and int(parts[1]) > time.time()
