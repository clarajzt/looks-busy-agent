"""JSON-lines logging to stdout with a redaction filter. Report text is never logged."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
import time

SECRET_PATTERNS = re.compile(
    r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+\S+|refresh_token|access_token|password|app_secret|[A-Za-z0-9+/=_\-]{40,})",
    re.IGNORECASE,
)


def hash_open_id(open_id: str) -> str:
    return hashlib.sha256(open_id.encode("utf-8")).hexdigest()[:8]


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if SECRET_PATTERNS.search(message):
            record.msg = SECRET_PATTERNS.sub("<redacted>", message)
            record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key in ("open_id_hash", "run_id", "source", "status", "code"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc"] = record.exc_info[0].__name__ if record.exc_info[0] else "error"
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactFilter())
    root.addHandler(handler)
    root.setLevel(level)
    logging.getLogger("httpx").setLevel("WARNING")
