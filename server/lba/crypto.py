"""Fernet-based secret box for per-user credentials (rotation via MultiFernet)."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Dict, List, Optional

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class SecretBox:
    def __init__(self, current_key: str, previous_key: Optional[str] = None) -> None:
        if not current_key:
            raise ValueError("master key required")
        keys: List[Fernet] = [Fernet(current_key.encode("utf-8"))]
        if previous_key:
            keys.append(Fernet(previous_key.encode("utf-8")))
        self._current = current_key
        self._fernet = MultiFernet(keys)
        # key_version lets rows say which key encrypted them without storing the key.
        self.key_version = int(hashlib.sha256(current_key.encode("utf-8")).hexdigest()[:8], 16)

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode("utf-8")

    def encrypt(self, payload: Dict[str, Any]) -> bytes:
        return self._fernet.encrypt(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def decrypt(self, ciphertext: bytes) -> Dict[str, Any]:
        try:
            raw = self._fernet.decrypt(bytes(ciphertext))
        except InvalidToken as exc:
            raise ValueError("cannot decrypt credential (wrong or rotated-out master key)") from exc
        return json.loads(raw.decode("utf-8"))

    def rotate(self, ciphertext: bytes) -> bytes:
        """Re-encrypt with the current key (payload never leaves this process)."""
        return self._fernet.rotate(bytes(ciphertext))

    def derive(self, purpose: str) -> bytes:
        """Derive a sub-key (e.g. for link signing) from the master key."""
        return hmac.new(self._current.encode("utf-8"), purpose.encode("utf-8"), hashlib.sha256).digest()
