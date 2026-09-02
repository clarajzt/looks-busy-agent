"""CLI entry: python -m lba <command>.

Skeleton status (B7 steps 1-2): init-db, gen-key, rotate-key, healthz, doctor, users, purge,
send-test are implemented. serve / once arrive with steps 3-6.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import List

from . import __version__
from .config import ConfigError, Settings
from .crypto import SecretBox
from .db import Database
from .logging_setup import setup_logging

NOT_YET = "尚未实现（骨架阶段，见 server/README.md 的实施顺序）"


def load(settings_needed: bool = True) -> Settings:
    try:
        return Settings.from_env()
    except ConfigError as exc:
        sys.exit(f"config error: {exc}")


def cmd_gen_key(_: argparse.Namespace) -> int:
    print(SecretBox.generate_key())
    return 0


def cmd_init_db(args: argparse.Namespace) -> int:
    settings = load()
    Database(settings.db_path)
    print(f"ok: schema ready at {settings.db_path}")
    return 0


def cmd_healthz(args: argparse.Namespace) -> int:
    settings = load()
    problems = settings.validate(need_lark=False, need_key=True)
    try:
        db = Database(settings.db_path)
        db.kv_get("schema_version")
    except Exception as exc:  # pragma: no cover - only on broken volumes
        problems.append(f"db: {exc}")
    if problems:
        print("unhealthy: " + "; ".join(problems))
        return 1
    print("ok")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    settings = load()
    lines: List[str] = [f"lba {__version__}"]
    problems = settings.validate(need_lark=True, need_llm=True, need_key=True)
    for problem in problems:
        lines.append(f"[缺失] {problem}")
    if settings.is_token_plan:
        lines.append("[提醒] LLM 走的是阿里云 Token Plan：条款仅限 AI 编程工具/Agent 用途，上线给同事前必须换成百炼按量付费 key")
    if settings.master_key:
        try:
            SecretBox(settings.master_key, settings.master_key_previous or None)
            lines.append("[OK]   主密钥可用")
        except Exception as exc:
            lines.append(f"[缺失] 主密钥无效: {exc}")
    try:
        db = Database(settings.db_path)
        users = db.list_users()
        creds = db.list_credentials()
        lines.append(f"[OK]   数据库 {settings.db_path}：用户 {len(users)}，凭据 {len(creds)}"
                     f"（需重授权 {sum(1 for c in creds if c['status'] == 'needs_reauth')}）")
    except Exception as exc:
        lines.append(f"[缺失] 数据库打不开: {exc}")
    if args.live and not problems:
        from .feishu_api import FeishuClient, FeishuError

        client = FeishuClient(settings.lark_app_id, settings.lark_app_secret)
        try:
            client.tenant_token()
            lines.append("[OK]   飞书 tenant_access_token 获取成功")
        except FeishuError as exc:
            lines.append(f"[缺失] 飞书应用凭据无效: {exc.code}")
    print("\n".join(lines))
    return 1 if any(line.startswith("[缺失]") for line in lines) else 0


def cmd_users(args: argparse.Namespace) -> int:
    settings = load()
    db = Database(settings.db_path)
    for user in db.list_users():
        creds = {c["kind"]: c["status"] for c in db.list_credentials() if c["open_id"] == user["open_id"]}
        print(json.dumps({"open_id": user["open_id"][:6] + "…", "tz": user["tz"], "run_time": user["run_time"],
                          "enabled": user["enabled"], "credentials": creds}, ensure_ascii=False))
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    settings = load()
    db = Database(settings.db_path)
    print(json.dumps(db.purge(settings.report_retention_days), ensure_ascii=False))
    return 0


def cmd_rotate_key(args: argparse.Namespace) -> int:
    settings = load()
    if not settings.master_key_previous:
        sys.exit("rotate-key 需要 LBA_MASTER_KEY_PREVIOUS（旧 key）与新的主密钥同时提供")
    box = SecretBox(settings.master_key, settings.master_key_previous)
    db = Database(settings.db_path)
    count = 0
    for cred in db.list_credentials():
        if cred["key_version"] != box.key_version:
            db.set_credential(cred["open_id"], cred["kind"], box.rotate(cred["ciphertext"]), box.key_version, cred["meta"])
            db.set_credential_status(cred["open_id"], cred["kind"], cred["status"], cred.get("last_error"))
            count += 1
    print(f"rotated {count} credential(s); now remove LBA_MASTER_KEY_PREVIOUS")
    return 0


def cmd_send_test(args: argparse.Namespace) -> int:
    settings = load()
    problems = settings.validate(need_lark=True, need_key=False)
    if problems:
        sys.exit("config error: " + "; ".join(problems))
    from .feishu_api import FeishuClient, FeishuError

    client = FeishuClient(settings.lark_app_id, settings.lark_app_secret)
    try:
        message_id = client.send_text(args.open_id, args.text)
    except FeishuError as exc:
        hint = "（用户不在应用可用范围，请在开放平台把可用范围设为全员）" if str(exc.code) == "230013" else ""
        sys.exit(f"send failed: {exc.code} {hint}")
    print(f"sent message_id={message_id}")
    return 0


def cmd_not_yet(args: argparse.Namespace) -> int:
    print(f"{args.command}: {NOT_YET}")
    return 2


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(prog="lba", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("gen-key", help="print a new Fernet master key").set_defaults(func=cmd_gen_key)
    sub.add_parser("init-db", help="create the SQLite schema").set_defaults(func=cmd_init_db)
    sub.add_parser("healthz", help="container health check").set_defaults(func=cmd_healthz)
    doctor = sub.add_parser("doctor", help="config / key / db / (optionally) Feishu credential check")
    doctor.add_argument("--live", action="store_true", help="also fetch a tenant token from Feishu")
    doctor.set_defaults(func=cmd_doctor)
    sub.add_parser("users", help="list enrolled users (open_id truncated)").set_defaults(func=cmd_users)
    sub.add_parser("purge", help="apply retention policy now").set_defaults(func=cmd_purge)
    sub.add_parser("rotate-key", help="re-encrypt credentials with the current master key").set_defaults(func=cmd_rotate_key)
    send = sub.add_parser("send-test", help="DM a text to one open_id (verifies bot capability + 可用范围)")
    send.add_argument("--open-id", required=True)
    send.add_argument("--text", default="看起来很忙 Agent 服务端连通测试")
    send.set_defaults(func=cmd_send_test)
    for name in ("serve", "once"):
        sub.add_parser(name, help=NOT_YET).set_defaults(func=cmd_not_yet)
    args = parser.parse_args(argv)
    setup_logging(Settings.from_env().log_level if args.command not in ("gen-key",) else "WARNING")
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
