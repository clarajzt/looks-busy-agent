"""HMAC-signed, single-use, time-limited links that bind a web page visit to a Feishu open_id."""

from __future__ import annotations

import base64
import hmac
import hashlib
import secrets
from typing import Optional

from .db import Database

DEFAULT_TTL = 15 * 60
PURPOSES = ("oauth", "mail")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


class LinkSigner:
    def __init__(self, key: bytes, db: Database) -> None:
        self._key = key
        self._db = db

    def _sign(self, nonce: str, purpose: str) -> str:
        return _b64(hmac.new(self._key, f"{purpose}:{nonce}".encode("utf-8"), hashlib.sha256).digest())[:32]

    def make(self, open_id: str, purpose: str, ttl_seconds: int = DEFAULT_TTL) -> str:
        if purpose not in PURPOSES:
            raise ValueError(f"unknown purpose {purpose!r}")
        nonce = _b64(secrets.token_bytes(18))
        self._db.create_link(nonce, open_id, purpose, ttl_seconds)
        return f"{nonce}.{self._sign(nonce, purpose)}"

    def consume(self, token: str, purpose: str) -> Optional[str]:
        """Returns open_id when the token is well-formed, correctly signed, unexpired and unused."""
        if not token or "." not in token or purpose not in PURPOSES:
            return None
        nonce, signature = token.rsplit(".", 1)
        if not hmac.compare_digest(signature, self._sign(nonce, purpose)):
            return None
        return self._db.consume_link(nonce, purpose)
