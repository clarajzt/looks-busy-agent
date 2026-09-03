#!/usr/bin/env python3
"""Collect one day's email through read-only IMAP without downloading attachments."""

from __future__ import annotations

import argparse
import hashlib
import imaplib
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from email import message_from_bytes, policy
from email.header import decode_header
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

from check_config import check
from imap_text import decode_bytes, fetch_literal, parse_structure, response_bytes, select_text_parts, transfer_decode


def keychain_password(service: str, account: str) -> str | None:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def windows_credential(target: str) -> str | None:
    """Read a generic credential saved with `cmdkey /generic:<target>` (stdlib ctypes only)."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    advapi32 = ctypes.windll.advapi32  # type: ignore[attr-defined]
    pointer = ctypes.POINTER(CREDENTIAL)()
    if not advapi32.CredReadW(target, 1, 0, ctypes.byref(pointer)):  # 1 = CRED_TYPE_GENERIC
        return None
    try:
        size = pointer.contents.CredentialBlobSize
        blob = ctypes.string_at(pointer.contents.CredentialBlob, size)
        return blob.decode("utf-16-le", errors="replace")
    finally:
        advapi32.CredFree(pointer)


def resolve_credentials(email_config: dict) -> tuple[str, str]:
    username = email_config.get("username") or os.environ.get(
        email_config.get("username_env") or "", ""
    )
    if not username:
        sys.exit("email username 未配置")
    service = email_config.get("password_keychain_service") or "looks-busy-agent-email"
    password = keychain_password(service, username) or windows_credential(service)
    if not password:
        password = os.environ.get(email_config.get("password_env") or "", "")
    if not password:
        sys.exit(
            "找不到邮箱凭据。请由用户本人在终端执行（隐藏式输入客户端专用密码，不经过对话）:\n"
            f'  macOS:   security add-generic-password -a "{username}" -s {service} -w\n'
            f"  Windows: cmdkey /generic:{service} /user:{username} /pass\n"
            f"或在启动 Agent 前设置环境变量 {email_config.get('password_env') or 'LOOKS_BUSY_EMAIL_PASSWORD'}"
        )
    return username, password


def decode_text(value: str | None) -> str:
    if not value:
        return ""
    chunks: list[str] = []
    for part, charset in decode_header(value):
        if isinstance(part, bytes):
            chunks.append(decode_bytes(part, charset))
        else:
            chunks.append(part)
    return "".join(chunks).strip()


def addresses(value: str | None) -> list[dict[str, str]]:
    return [
        {"name": decode_text(name), "email": address.lower()}
        for name, address in getaddresses([value or ""])
        if address
    ]


def html_to_text(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def part_text(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        value = part.get_payload()
        return value if isinstance(value, str) else ""
    return decode_bytes(payload, part.get_content_charset())


def body_text(message: Message, max_chars: int) -> str:
    # Prune entire attached-message/multipart subtrees, not just their parent.
    if message.get_content_disposition() == "attachment" or message.get_filename() or message.get_content_maintype() == "message":
        return ""
    if message.is_multipart():
        return "\n\n".join(body_text(part, max_chars) for part in message.iter_parts())[:max_chars]
    if message.get_content_type() == "text/plain":
        return part_text(message)[:max_chars]
    if message.get_content_type() == "text/html":
        return html_to_text(part_text(message))[:max_chars]
    return ""


def message_date(message: Message) -> str | None:
    try:
        parsed = parsedate_to_datetime(message.get("Date"))
        return parsed.isoformat() if parsed else None
    except (TypeError, ValueError, OverflowError):
        return None


def fetch_mailbox(conn, mailbox: str, day: date, max_messages: int, max_chars: int, timezone: str = "UTC") -> list[dict]:
    status, _ = conn.select('"' + mailbox.replace('\\', '\\\\').replace('"', '\\"') + '"', readonly=True)
    if status != "OK":
        raise RuntimeError("cannot select a configured mailbox")
    # IMAP search dates ignore offsets. Widen first, then filter INTERNALDATE
    # in the configured timezone before fetching any body text.
    status, result = conn.uid("search", None, "SINCE", (day - timedelta(days=1)).strftime("%d-%b-%Y"),
                              "BEFORE", (day + timedelta(days=2)).strftime("%d-%b-%Y"))
    if status != "OK":
        raise RuntimeError("mailbox search failed")
    records = []
    tz = ZoneInfo(timezone)
    for uid in reversed((result[0] or b"").split()):
        status, metadata = conn.uid("fetch", uid, "(INTERNALDATE BODYSTRUCTURE)")
        if status != "OK":
            continue
        match = re.search(rb'INTERNALDATE "([^"\r\n]+)"', response_bytes(metadata), re.I)
        if not match:
            continue
        received = datetime.strptime(match[1].decode("ascii"), "%d-%b-%Y %H:%M:%S %z")
        if received.astimezone(tz).date() != day:
            continue
        headers = fetch_literal(conn, uid, "HEADER.FIELDS (DATE SUBJECT FROM TO CC MESSAGE-ID)", 65536)
        message = message_from_bytes(headers, policy=policy.default)
        warnings = []
        body = ""
        attachments = None
        try:
            parts, attachments = select_text_parts(parse_structure(metadata))
            snippets = []
            for section, kind, charset, encoding in parts:
                remaining = max_chars - sum(len(item) for item in snippets)
                if remaining <= 0:
                    break
                raw = fetch_literal(conn, uid, section, min(remaining * 12, 262144))
                value = transfer_decode(raw, encoding, charset)
                snippets.append((html_to_text(value) if kind == "HTML" else value)[:remaining])
            body = "\n\n".join(snippets)[:max_chars]
        except (ValueError, LookupError, UnicodeError, TypeError, IndexError):
            warnings.append("body unavailable; headers only")
        stable_id = message.get("Message-ID") or f"{mailbox}:{uid.decode()}:{received.isoformat()}"
        records.append({
            "source": "imap", "mailbox": mailbox,
            "direction": "sent" if "sent" in mailbox.lower() else "received",
            "message_key": hashlib.sha256(stable_id.encode()).hexdigest()[:20],
            "date": message_date(message), "received_at": received.isoformat(),
            "subject": decode_text(message.get("Subject")), "from": addresses(message.get("From")),
            "to": addresses(message.get("To")), "cc": addresses(message.get("Cc")),
            "body": body, "has_attachments": attachments, "warnings": warnings,
        })
        if len(records) >= max_messages:
            break
    return records


def private_write(path: Path, payload: dict) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    from run_daily import private_json
    private_json(path, payload)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--date")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-messages", type=int, default=200)
    parser.add_argument("--max-body-chars", type=int, default=6000)
    parser.add_argument("--check", action="store_true", help="login and mailbox smoke test only")
    args = parser.parse_args()

    config = json.loads(args.config.expanduser().read_text(encoding="utf-8"))
    errors, _ = check(config)
    if errors:
        sys.exit("配置无效，请先运行 check_config.py")
    email_config = config.get("sources", {}).get("email", {})
    if email_config.get("enabled") is not True:
        sys.exit("工作邮箱未启用；未访问凭据或服务器")
    if args.max_messages < 1 or args.max_body_chars < 1:
        sys.exit("采集上限必须是正整数")
    host = email_config.get("host") or "imap.exmail.qq.com"
    port = int(email_config.get("port") or 993)
    username, password = resolve_credentials(email_config)

    day = date.fromisoformat(args.date) if args.date else datetime.now(ZoneInfo(config["timezone"])).date()
    mailboxes = email_config.get("mailboxes") or ["INBOX", "Sent Messages"]
    records: list[dict] = []
    with imaplib.IMAP4_SSL(host, port, timeout=30) as conn:
        conn.login(username, password)
        if args.check:
            for mailbox in mailboxes:
                status, _ = conn.select(f'"{mailbox}"', readonly=True)
                if status != "OK":
                    sys.exit("check: configured mailbox unavailable")
            print("check: login ok（未读取邮件正文）")
            return
        for mailbox in mailboxes:
            records.extend(fetch_mailbox(conn, mailbox, day, args.max_messages, args.max_body_chars, config["timezone"]))
    records.sort(key=lambda item: item.get("date") or "")

    report_dir = Path(
        config.get("report", {}).get("output_dir", "~/.local/share/looks-busy-agent/reports")
    ).expanduser()
    output = args.output or report_dir.parent / "raw" / day.isoformat() / "email.json"
    private_write(
        output,
        {
            "source": "imap",
            "date": day.isoformat(),
            "timezone": config["timezone"],
            "read_only": True,
            "attachments_downloaded": False,
            "records": records,
        },
    )
    print(f"email connected; messages={len(records)}; output={output.expanduser()}")


if __name__ == "__main__":
    main()
