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
from datetime import date, timedelta
from email import message_from_bytes, policy
from email.header import decode_header
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime
from html import unescape
from pathlib import Path


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
            chunks.append(part.decode(charset or "utf-8", errors="replace"))
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
    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")


def body_text(message: Message, max_chars: int) -> str:
    plain: list[str] = []
    html: list[str] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.is_multipart() or "attachment" in (part.get("Content-Disposition") or "").lower():
            continue
        if part.get_content_type() == "text/plain":
            plain.append(part_text(part))
        elif part.get_content_type() == "text/html":
            html.append(html_to_text(part_text(part)))
    return ("\n\n".join(plain).strip() or "\n\n".join(html).strip())[:max_chars]


def message_date(message: Message) -> str | None:
    try:
        parsed = parsedate_to_datetime(message.get("Date"))
        return parsed.isoformat() if parsed else None
    except (TypeError, ValueError, OverflowError):
        return None


def fetch_mailbox(
    conn: imaplib.IMAP4_SSL,
    mailbox: str,
    day: date,
    max_messages: int,
    max_chars: int,
) -> list[dict]:
    status, _ = conn.select(f'"{mailbox}"', readonly=True)
    if status != "OK":
        raise RuntimeError(f"cannot select mailbox: {mailbox}")
    status, result = conn.search(
        None,
        "SINCE",
        day.strftime("%d-%b-%Y"),
        "BEFORE",
        (day + timedelta(days=1)).strftime("%d-%b-%Y"),
    )
    if status != "OK":
        raise RuntimeError(f"search failed: {mailbox}")
    records: list[dict] = []
    for message_id in (result[0] or b"").split()[-max_messages:]:
        status, payload = conn.fetch(message_id, "(BODY.PEEK[])")
        if status != "OK":
            continue
        raw = next((item[1] for item in payload if isinstance(item, tuple)), None)
        if not isinstance(raw, bytes):
            continue
        message = message_from_bytes(raw, policy=policy.default)
        stable_id = message.get("Message-ID") or f"{mailbox}:{message_id.decode()}"
        records.append(
            {
                "source": "imap",
                "mailbox": mailbox,
                "direction": "sent" if "sent" in mailbox.lower() else "received",
                "message_key": hashlib.sha256(stable_id.encode()).hexdigest()[:20],
                "date": message_date(message),
                "subject": decode_text(message.get("Subject")),
                "from": addresses(message.get("From")),
                "to": addresses(message.get("To")),
                "cc": addresses(message.get("Cc")),
                "body": body_text(message, max_chars),
                "has_attachments": any(
                    "attachment" in (part.get("Content-Disposition") or "").lower()
                    for part in message.walk()
                ),
            }
        )
    return records


def private_write(path: Path, payload: dict) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    path.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-messages", type=int, default=200)
    parser.add_argument("--max-body-chars", type=int, default=6000)
    parser.add_argument("--check", action="store_true", help="login and mailbox smoke test only")
    args = parser.parse_args()

    config = json.loads(args.config.expanduser().read_text(encoding="utf-8"))
    email_config = config.get("sources", {}).get("email", {})
    host = email_config.get("host") or "imap.exmail.qq.com"
    port = int(email_config.get("port") or 993)
    username, password = resolve_credentials(email_config)

    day = date.fromisoformat(args.date)
    mailboxes = email_config.get("mailboxes") or ["INBOX", "Sent Messages"]
    records: list[dict] = []
    with imaplib.IMAP4_SSL(host, port, timeout=30) as conn:
        conn.login(username, password)
        if args.check:
            for mailbox in mailboxes:
                status, _ = conn.select(f'"{mailbox}"', readonly=True)
                print(f"check: {mailbox}: {'ok' if status == 'OK' else 'failed'}")
            print("check: login ok（未读取邮件正文）")
            return
        for mailbox in mailboxes:
            records.extend(fetch_mailbox(conn, mailbox, day, args.max_messages, args.max_body_chars))
    records.sort(key=lambda item: item.get("date") or "")

    report_dir = Path(
        config.get("report", {}).get("output_dir", "~/.local/share/looks-busy-agent/reports")
    ).expanduser()
    output = args.output or report_dir.parent / "raw" / args.date / "email.json"
    private_write(
        output,
        {
            "source": "imap",
            "date": args.date,
            "read_only": True,
            "attachments_downloaded": False,
            "records": records,
        },
    )
    print(f"email connected; messages={len(records)}; output={output.expanduser()}")


if __name__ == "__main__":
    main()
