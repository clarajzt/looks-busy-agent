"""SQLite storage (WAL, one connection per thread) and thin repository helpers."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  open_id TEXT PRIMARY KEY,
  tz TEXT NOT NULL,
  run_time TEXT NOT NULL,
  work_scope TEXT NOT NULL DEFAULT '[]',
  enabled INTEGER NOT NULL DEFAULT 1,
  consent_calendar_at TEXT,
  consent_mail_at TEXT,
  denylist TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_seen_at TEXT
);
CREATE TABLE IF NOT EXISTS credentials (
  id INTEGER PRIMARY KEY,
  open_id TEXT NOT NULL REFERENCES users(open_id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('feishu_calendar','imap')),
  ciphertext BLOB NOT NULL,
  key_version INTEGER NOT NULL,
  meta TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'active',
  last_ok_at TEXT,
  last_error TEXT,
  last_notified_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (open_id, kind)
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  open_id TEXT NOT NULL REFERENCES users(open_id) ON DELETE CASCADE,
  run_date TEXT NOT NULL,
  slot TEXT NOT NULL,
  trigger TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT,
  sources TEXT,
  llm_model TEXT,
  input_tokens INTEGER,
  output_tokens INTEGER,
  redline TEXT,
  error TEXT,
  UNIQUE (open_id, run_date, slot)
);
CREATE TABLE IF NOT EXISTS reports (
  run_id INTEGER PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
  open_id TEXT NOT NULL,
  run_date TEXT NOT NULL,
  content_md TEXT NOT NULL,
  delivered_at TEXT,
  message_id TEXT
);
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY,
  open_id TEXT NOT NULL REFERENCES users(open_id) ON DELETE CASCADE,
  run_date TEXT NOT NULL,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_links (
  nonce TEXT PRIMARY KEY,
  open_id TEXT NOT NULL,
  purpose TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT
);
CREATE TABLE IF NOT EXISTS processed_events (
  event_id TEXT PRIMARY KEY,
  seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._local = threading.local()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    # --- connections -------------------------------------------------------
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            if str(self.path) != ":memory:":
                conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    def init_schema(self) -> None:
        conn = self.conn()
        conn.executescript(SCHEMA)
        current = self.kv_get("schema_version")
        if current is None:
            self.kv_set("schema_version", str(SCHEMA_VERSION))
        elif int(current) > SCHEMA_VERSION:
            raise RuntimeError(f"database schema {current} is newer than this code ({SCHEMA_VERSION})")

    # --- kv ----------------------------------------------------------------
    def kv_get(self, key: str) -> Optional[str]:
        row = self.conn().execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def kv_set(self, key: str, value: str) -> None:
        self.conn().execute("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    # --- users -------------------------------------------------------------
    def upsert_user(self, open_id: str, tz: str, run_time: str) -> Dict[str, Any]:
        now = iso(utcnow())
        self.conn().execute(
            "INSERT INTO users(open_id,tz,run_time,created_at,updated_at,last_seen_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(open_id) DO UPDATE SET last_seen_at=excluded.last_seen_at",
            (open_id, tz, run_time, now, now, now),
        )
        return self.get_user(open_id)  # type: ignore[return-value]

    def get_user(self, open_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn().execute("SELECT * FROM users WHERE open_id=?", (open_id,)).fetchone()
        return self._user(row) if row else None

    def list_users(self, enabled_only: bool = False) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM users" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY created_at"
        return [self._user(row) for row in self.conn().execute(sql).fetchall()]

    def update_user(self, open_id: str, **fields: Any) -> None:
        allowed = {"tz", "run_time", "work_scope", "enabled", "consent_calendar_at", "consent_mail_at", "denylist"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"unknown user fields: {sorted(bad)}")
        if not fields:
            return
        values = [json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for v in fields.values()]
        assignments = ", ".join(f"{k}=?" for k in fields)
        self.conn().execute(f"UPDATE users SET {assignments}, updated_at=? WHERE open_id=?", (*values, iso(utcnow()), open_id))

    def delete_user(self, open_id: str) -> bool:
        cur = self.conn().execute("DELETE FROM users WHERE open_id=?", (open_id,))
        return cur.rowcount > 0

    @staticmethod
    def _user(row: sqlite3.Row) -> Dict[str, Any]:
        user = dict(row)
        user["work_scope"] = json.loads(user.get("work_scope") or "[]")
        user["denylist"] = json.loads(user.get("denylist") or "[]")
        user["enabled"] = bool(user["enabled"])
        return user

    # --- credentials -------------------------------------------------------
    def set_credential(self, open_id: str, kind: str, ciphertext: bytes, key_version: int, meta: Dict[str, Any]) -> None:
        now = iso(utcnow())
        self.conn().execute(
            "INSERT INTO credentials(open_id,kind,ciphertext,key_version,meta,status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,'active',?,?) ON CONFLICT(open_id,kind) DO UPDATE SET "
            "ciphertext=excluded.ciphertext, key_version=excluded.key_version, meta=excluded.meta, "
            "status='active', last_error=NULL, updated_at=excluded.updated_at",
            (open_id, kind, ciphertext, key_version, json.dumps(meta, ensure_ascii=False), now, now),
        )

    def get_credential(self, open_id: str, kind: str) -> Optional[Dict[str, Any]]:
        row = self.conn().execute("SELECT * FROM credentials WHERE open_id=? AND kind=?", (open_id, kind)).fetchone()
        if not row:
            return None
        cred = dict(row)
        cred["meta"] = json.loads(cred.get("meta") or "{}")
        return cred

    def list_credentials(self, kind: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        clauses, params = [], []
        if kind:
            clauses.append("kind=?"); params.append(kind)
        if status:
            clauses.append("status=?"); params.append(status)
        sql = "SELECT * FROM credentials" + (" WHERE " + " AND ".join(clauses) if clauses else "")
        out = []
        for row in self.conn().execute(sql, params).fetchall():
            cred = dict(row); cred["meta"] = json.loads(cred.get("meta") or "{}"); out.append(cred)
        return out

    def set_credential_status(self, open_id: str, kind: str, status: str, error: Optional[str] = None, ok: bool = False) -> None:
        now = iso(utcnow())
        self.conn().execute(
            "UPDATE credentials SET status=?, last_error=?, last_ok_at=CASE WHEN ? THEN ? ELSE last_ok_at END, updated_at=? "
            "WHERE open_id=? AND kind=?",
            (status, error, 1 if ok else 0, now, now, open_id, kind),
        )

    def update_credential_meta(self, open_id: str, kind: str, meta: Dict[str, Any]) -> None:
        self.conn().execute(
            "UPDATE credentials SET meta=?, updated_at=? WHERE open_id=? AND kind=?",
            (json.dumps(meta, ensure_ascii=False), iso(utcnow()), open_id, kind),
        )

    def mark_credential_notified(self, open_id: str, kind: str) -> None:
        self.conn().execute("UPDATE credentials SET last_notified_at=? WHERE open_id=? AND kind=?", (iso(utcnow()), open_id, kind))

    def delete_credential(self, open_id: str, kind: str) -> bool:
        return self.conn().execute("DELETE FROM credentials WHERE open_id=? AND kind=?", (open_id, kind)).rowcount > 0

    # --- runs / reports ----------------------------------------------------
    def create_run(self, open_id: str, run_date: str, slot: str, trigger: str) -> Optional[int]:
        """Returns run id, or None when that slot already exists (idempotent)."""
        try:
            cur = self.conn().execute(
                "INSERT INTO runs(open_id,run_date,slot,trigger,started_at) VALUES(?,?,?,?,?)",
                (open_id, run_date, slot, trigger, iso(utcnow())),
            )
        except sqlite3.IntegrityError:
            return None
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, **fields: Any) -> None:
        allowed = {"sources", "llm_model", "input_tokens", "output_tokens", "redline", "error"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"unknown run fields: {sorted(bad)}")
        values = [json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for v in fields.values()]
        assignments = "".join(f", {k}=?" for k in fields)
        self.conn().execute(f"UPDATE runs SET status=?, finished_at=?{assignments} WHERE id=?", (status, iso(utcnow()), *values, run_id))

    def has_daily_run(self, open_id: str, run_date: str) -> bool:
        return self.conn().execute("SELECT 1 FROM runs WHERE open_id=? AND run_date=? AND slot='daily'", (open_id, run_date)).fetchone() is not None

    def save_report(self, run_id: int, open_id: str, run_date: str, content_md: str) -> None:
        self.conn().execute(
            "INSERT INTO reports(run_id,open_id,run_date,content_md) VALUES(?,?,?,?) "
            "ON CONFLICT(run_id) DO UPDATE SET content_md=excluded.content_md",
            (run_id, open_id, run_date, content_md),
        )

    def mark_delivered(self, run_id: int, message_id: str) -> None:
        self.conn().execute("UPDATE reports SET delivered_at=?, message_id=? WHERE run_id=?", (iso(utcnow()), message_id, run_id))

    def latest_report(self, open_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn().execute("SELECT * FROM reports WHERE open_id=? ORDER BY run_date DESC, run_id DESC LIMIT 1", (open_id,)).fetchone()
        return dict(row) if row else None

    # --- notes -------------------------------------------------------------
    def add_note(self, open_id: str, run_date: str, text: str) -> None:
        self.conn().execute("INSERT INTO notes(open_id,run_date,text,created_at) VALUES(?,?,?,?)", (open_id, run_date, text, iso(utcnow())))

    def list_notes(self, open_id: str, run_date: str) -> List[str]:
        return [row["text"] for row in self.conn().execute("SELECT text FROM notes WHERE open_id=? AND run_date=? ORDER BY id", (open_id, run_date)).fetchall()]

    # --- links / events ----------------------------------------------------
    def create_link(self, nonce: str, open_id: str, purpose: str, ttl_seconds: int) -> None:
        self.conn().execute(
            "INSERT INTO pending_links(nonce,open_id,purpose,expires_at) VALUES(?,?,?,?)",
            (nonce, open_id, purpose, iso(utcnow() + timedelta(seconds=ttl_seconds))),
        )

    def consume_link(self, nonce: str, purpose: str) -> Optional[str]:
        """Atomically mark the link used; returns open_id or None (unknown/expired/used/wrong purpose)."""
        now = iso(utcnow())
        cur = self.conn().execute(
            "UPDATE pending_links SET used_at=? WHERE nonce=? AND purpose=? AND used_at IS NULL AND expires_at>?",
            (now, nonce, purpose, now),
        )
        if cur.rowcount != 1:
            return None
        row = self.conn().execute("SELECT open_id FROM pending_links WHERE nonce=?", (nonce,)).fetchone()
        return row["open_id"] if row else None

    def mark_event(self, event_id: str) -> bool:
        """True the first time an event id is seen."""
        try:
            self.conn().execute("INSERT INTO processed_events(event_id,seen_at) VALUES(?,?)", (event_id, iso(utcnow())))
        except sqlite3.IntegrityError:
            return False
        return True

    # --- retention ---------------------------------------------------------
    def purge(self, report_days: int, run_days: int = 90) -> Dict[str, int]:
        now = utcnow()
        conn = self.conn()
        counts = {
            "reports": conn.execute("DELETE FROM reports WHERE run_date<?", ((now - timedelta(days=report_days)).date().isoformat(),)).rowcount,
            "notes": conn.execute("DELETE FROM notes WHERE run_date<?", ((now - timedelta(days=report_days)).date().isoformat(),)).rowcount,
            "runs": conn.execute("DELETE FROM runs WHERE started_at<?", (iso(now - timedelta(days=run_days)),)).rowcount,
            "links": conn.execute("DELETE FROM pending_links WHERE expires_at<?", (iso(now - timedelta(days=1)),)).rowcount,
            "events": conn.execute("DELETE FROM processed_events WHERE seen_at<?", (iso(now - timedelta(days=1)),)).rowcount,
        }
        return counts
