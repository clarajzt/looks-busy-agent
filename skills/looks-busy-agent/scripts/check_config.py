#!/usr/bin/env python3
"""Validate a looks-busy-agent config file. Read-only; prints findings, exits 1 on errors."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

KNOWN_SOURCES = {"agent_history", "local_files", "feishu", "email"}
FORBIDDEN_KEYS = re.compile(r"(password|secret|token|api_key)$", re.IGNORECASE)
ALLOWED_CRED_KEYS = {"password_env", "password_keychain_service", "username_env"}
SCHEDULE_PROVIDERS = {
    "auto",
    "codex_heartbeat",
    "claude_desktop",
    "claude_routine",
    "trae_automation",
    "workbuddy_automation",
    "cola_alarm",
    "other_native",
}
DELIVERY_MODES = {"agent_conversation", "local_file", "feishu", "email"}


def fail(errors: list[str], msg: str) -> None:
    errors.append(msg)


def check(config: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if config.get("version") != 1:
        fail(errors, "version 必须是 1")
    if not config.get("timezone"):
        fail(errors, "缺少 timezone")
    run_time = config.get("run_time", "")
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(run_time)):
        fail(errors, f"run_time 需为 HH:MM，当前: {run_time!r}")
    if not config.get("work_scope"):
        warnings.append("work_scope 为空：日报将无法做范围过滤，建议在 setup 中补齐")

    schedule = config.get("schedule", {})
    if schedule.get("enabled"):
        provider = schedule.get("provider")
        if provider not in SCHEDULE_PROVIDERS:
            fail(errors, f"未知 schedule.provider: {provider!r}")
        if provider == "auto" or not schedule.get("job_id"):
            warnings.append("schedule 尚未完成：setup 必须选择当前 Agent 的调度器、创建任务并回读 job_id")

    sources = config.get("sources", {})
    unknown = set(sources) - KNOWN_SOURCES
    if unknown:
        warnings.append(f"未知数据源将被忽略: {sorted(unknown)}")
    if not any(s.get("enabled") for s in sources.values() if isinstance(s, dict)):
        warnings.append("没有启用任何数据源，日报只能基于当前对话")

    email = sources.get("email", {})
    if email.get("enabled"):
        if not email.get("host"):
            fail(errors, "email.host 为空（腾讯企业邮箱: imap.exmail.qq.com）")
        if not (email.get("username") or email.get("username_env")):
            fail(errors, "email 需要 username 或 username_env")
        if not (email.get("password_keychain_service") or email.get("password_env")):
            fail(errors, "email 需要 password_keychain_service（推荐）或 password_env")

    local = sources.get("local_files", {})
    if local.get("enabled"):
        roots = local.get("roots") or []
        if not roots:
            fail(errors, "local_files.enabled 但 roots 为空")
        for root in roots:
            path = Path(root).expanduser()
            if path == Path.home():
                fail(errors, "local_files.roots 不允许整个主目录，请指定具体工作目录")
            elif not path.exists():
                warnings.append(f"local_files 目录不存在: {root}")

    calendar = config.get("calendar", {})
    if calendar.get("write_enabled") and not calendar.get("marker"):
        fail(errors, "calendar.write_enabled 时必须设置 marker（幂等标记）")

    delivery = config.get("report", {}).get("delivery")
    if delivery not in DELIVERY_MODES:
        fail(errors, f"未知 report.delivery: {delivery!r}")

    def scan_secrets(node, path="config"):
        if isinstance(node, dict):
            for key, value in node.items():
                if FORBIDDEN_KEYS.search(key) and key not in ALLOWED_CRED_KEYS and value:
                    fail(errors, f"{path}.{key} 疑似明文凭据：凭据只能放 Keychain 或环境变量")
                scan_secrets(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                scan_secrets(value, f"{path}[{i}]")

    scan_secrets(config)
    return errors, warnings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()

    path = args.config.expanduser()
    if not path.exists():
        sys.exit(f"配置文件不存在: {path}")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"JSON 解析失败: {exc}")

    errors, warnings = check(config)
    for msg in warnings:
        print(f"warning: {msg}")
    for msg in errors:
        print(f"error: {msg}")
    if errors:
        sys.exit(1)
    print(f"ok: {path} 校验通过（{len(warnings)} 条提醒）")


if __name__ == "__main__":
    main()
