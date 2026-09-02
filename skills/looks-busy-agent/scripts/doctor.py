#!/usr/bin/env python3
"""One-shot health check for looks-busy-agent. Read-only; never prints secrets,
mail contents or calendar details — only connection state and counts.

Usage: python3 scripts/doctor.py [--config ~/.config/looks-busy-agent/config.json] [--json]
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = Path("~/.config/looks-busy-agent/config.json")
READONLY_SCOPES = "calendar:calendar:readonly calendar:calendar.event:read"
LAUNCHD_LABEL = "com.looks-busy-agent.daily"

sys.path.insert(0, str(SCRIPT_DIR))
try:
    from check_config import check as check_config  # type: ignore
except Exception:  # pragma: no cover - only when the file is moved alone
    check_config = None


def run(cmd: list[str], timeout: int = 60) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, "", f"{cmd[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{cmd[0]} timed out after {timeout}s"
    return proc.returncode, proc.stdout, proc.stderr


def parse_json(text: str) -> dict | None:
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def count_items(node) -> int:
    """Count events in a lark-cli envelope without inspecting their content."""
    if isinstance(node, list):
        return len(node)
    if isinstance(node, dict):
        for key in ("events", "items", "data"):
            if key in node:
                return count_items(node[key])
    return 0


class Report:
    def __init__(self) -> None:
        self.items: list[dict] = []
        self.next_steps: list[str] = []

    def add(self, area: str, status: str, detail: str, fix: str | None = None) -> None:
        self.items.append({"area": area, "status": status, "detail": detail})
        if fix and status in {"fail", "warn"}:
            self.next_steps.append(fix)

    def ok(self, area, detail):
        self.add(area, "ok", detail)

    def warn(self, area, detail, fix=None):
        self.add(area, "warn", detail, fix)

    def fail(self, area, detail, fix=None):
        self.add(area, "fail", detail, fix)

    def skip(self, area, detail):
        self.add(area, "skip", detail)


def check_environment(report: Report) -> None:
    version = sys.version_info
    detail = f"python {version.major}.{version.minor} · {platform.system()} {platform.release()}"
    if version < (3, 9):
        report.fail("环境", detail, "python3 需要 3.9 及以上")
    else:
        report.ok("环境", detail)


def load_config(report: Report, path: Path) -> dict | None:
    if not path.exists():
        report.fail("配置", f"未找到 {path}", "先对 Agent 说「使用 looks-busy-agent 帮我开始设置日报」完成 setup")
        return None
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        report.fail("配置", f"JSON 解析失败: {exc}", f"修复 {path} 的 JSON 语法")
        return None
    if check_config is None:
        report.warn("配置", "已读取，但 check_config.py 不可用，跳过校验")
        return config
    errors, warnings = check_config(config)
    if errors:
        report.fail("配置", "; ".join(errors), f"python3 {SCRIPT_DIR / 'check_config.py'} --config {path}")
    elif warnings:
        report.warn("配置", "; ".join(warnings))
    else:
        report.ok("配置", f"{path} 校验通过")
    return config


def check_feishu(report: Report, config: dict) -> None:
    feishu = config.get("sources", {}).get("feishu", {})
    if not feishu.get("enabled"):
        report.skip("飞书日历", "未启用")
        return
    if not shutil.which("lark-cli"):
        report.fail(
            "飞书日历",
            "lark-cli 未安装",
            "安装 Node.js LTS（https://nodejs.org）后执行 npm install -g @larksuite/cli",
        )
        return
    code, out, err = run(["lark-cli", "auth", "status", "--json"])
    status = parse_json(out) or parse_json(err) or {}
    user_identity = (status.get("identities") or {}).get("user") or {}
    logged_in = status.get("ok") is True or user_identity.get("status") == "ready"
    if not logged_in:
        error = status.get("error", {})
        subtype = error.get("subtype") or error.get("type") or f"exit {code}"
        if subtype == "not_configured":
            report.fail(
                "飞书日历",
                "lark-cli 尚未绑定飞书应用",
                "用户本人在终端运行 lark-cli config init，填入管理员提供的 App ID / App Secret（见 setup.md §1）",
            )
        else:
            report.fail(
                "飞书日历",
                f"未登录（{subtype}）",
                f'lark-cli auth login --scope "{READONLY_SCOPES}" --no-wait --json，扫码后 --device-code 完成',
            )
        return
    if status.get("defaultAs") not in (None, "user"):
        report.warn("飞书日历", f"默认身份是 {status.get('defaultAs')}，日历读取需显式 --as user", "lark-cli config default-as user")
    refresh_expires = user_identity.get("refreshExpiresAt")
    if refresh_expires:
        report.ok("飞书授权", f"用户身份已登录，refresh token 有效期至 {refresh_expires[:10]}（到期需重新扫码）")
    # Any one of these lets a user identity read calendar events (per `lark-cli schema`).
    sufficient = {"calendar:calendar:readonly", "calendar:calendar.event:read", "calendar:calendar"}
    granted = set((user_identity.get("scope") or "").split())
    if granted:
        scope_check = {"ok": bool(granted & sufficient)}
    else:
        code, out, err = run(["lark-cli", "auth", "check", "--json", "--scope", READONLY_SCOPES])
        scope_check = parse_json(out) or parse_json(err) or {}
    if scope_check and scope_check.get("ok") is False:
        report.warn(
            "飞书日历",
            "已登录，但缺少只读日历 scope",
            f'lark-cli auth login --scope "{READONLY_SCOPES}"（应用需先由管理员开通这两个权限）',
        )
    code, out, err = run(["lark-cli", "calendar", "+agenda", "--as", "user", "--format", "json"])
    agenda = parse_json(out) or parse_json(err) or {}
    if agenda.get("ok"):
        count = (agenda.get("meta") or {}).get("count")
        if count is None:
            count = count_items(agenda.get("data"))
        report.ok("飞书日历", f"已连接，今天 {count} 个日程")
    else:
        subtype = (agenda.get("error") or {}).get("subtype") or (agenda.get("error") or {}).get("type") or f"exit {code}"
        report.fail(
            "飞书日历",
            f"读取今日日程失败（{subtype}）",
            "检查应用是否发布并通过管理员审批；默认身份需为 user：lark-cli config default-as user",
        )


def check_email(report: Report, config: dict, config_path: Path) -> None:
    email = config.get("sources", {}).get("email", {})
    if not email.get("enabled"):
        report.skip("工作邮箱", "未启用")
        return
    code, out, err = run(
        [sys.executable, str(SCRIPT_DIR / "collect_email.py"), "--config", str(config_path), "--check"],
        timeout=90,
    )
    if code == 0 and "login ok" in out:
        report.ok("工作邮箱", "IMAP 只读登录成功")
        return
    message = (err.strip() or out.strip()).splitlines()
    tail = message[-1] if message else f"exit {code}"
    if "凭据" in tail or "credential" in tail.lower():
        fix = "用户本人在终端执行 security add-generic-password -a <邮箱> -s looks-busy-agent-email -w（输入客户端专用密码）"
    else:
        fix = "确认企业邮箱已开启 IMAP，并且使用的是「客户端专用密码」而不是登录密码"
    report.fail("工作邮箱", tail[:160], fix)


def recommended_scheduler() -> tuple[str, str]:
    try:
        from detect_scheduler import current_agent, detect_agents, recommend  # type: ignore
    except Exception:
        return "", ""
    try:
        return recommend(platform.system(), detect_agents(), current_agent())
    except Exception:
        return "", ""


def check_schedule(report: Report, config: dict) -> None:
    schedule = config.get("schedule", {})
    if not schedule.get("enabled"):
        report.skip("每日定时", "已关闭（只支持手动生成）")
        return
    provider = schedule.get("provider")
    recommended, reason = recommended_scheduler()
    if provider in (None, "auto") or not schedule.get("job_id"):
        hint = f"推荐 {recommended}（{reason}）" if recommended else "让 Agent 按 automation.md 创建并回读定时任务"
        report.warn("每日定时", "尚未创建定时任务", hint)
        return
    if provider in ("launchd", "schtasks") and recommended not in ("", "launchd", "schtasks", "manual"):
        report.warn("每日定时", f"当前用的是 OS 兜底 {provider}，本机其实有原生调度器 {recommended}", f"可考虑切换：{reason}")
    if provider == "schtasks" and platform.system() == "Windows":
        code, out, _ = run(["schtasks", "/Query", "/TN", "looks-busy-agent-daily"])
        if code == 0:
            report.ok("每日定时", f"任务计划已注册，每天 {config.get('run_time', '21:00')}")
        else:
            report.fail("每日定时", "config 说是 schtasks，但任务计划里没有", "powershell -File scripts/schedule_windows.ps1 install")
        return
    if provider == "launchd" and platform.system() == "Darwin":
        plist = Path(f"~/Library/LaunchAgents/{LAUNCHD_LABEL}.plist").expanduser()
        code, out, _ = run(["launchctl", "list"])
        loaded = LAUNCHD_LABEL in out
        if plist.exists() and loaded:
            report.ok("每日定时", f"launchd 已加载，每天 {config.get('run_time', '21:00')}")
        elif plist.exists():
            report.warn("每日定时", "plist 存在但未加载", f"launchctl load {plist}")
        else:
            report.fail("每日定时", "config 说是 launchd，但没有 plist", "bash scripts/schedule_launchd.sh install")
        return
    report.ok("每日定时", f"{provider} · job {schedule.get('job_id')}（由所在 Agent 平台管理）")


def check_last_run(report: Report, config: dict) -> None:
    output_dir = Path(config.get("report", {}).get("output_dir", "~/.local/share/looks-busy-agent/reports")).expanduser()
    reports = sorted(output_dir.glob("*.md")) if output_dir.exists() else []
    log = output_dir.parent / "logs" / "run.log"
    if reports:
        report.ok("最近日报", f"共 {len(reports)} 份，最新 {reports[-1].name}")
    else:
        report.warn("最近日报", f"{output_dir} 里还没有日报", "对 Agent 说「生成今天的日报」做一次预览")
    if log.exists():
        lines = [line for line in log.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
        if lines:
            report.ok("运行日志", lines[-1][:120])


STATUS_MARK = {"ok": "[OK]  ", "warn": "[提醒]", "fail": "[缺失]", "skip": "[跳过]"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()
    config_path = args.config.expanduser()

    report = Report()
    check_environment(report)
    config = load_config(report, config_path)
    if config is not None:
        check_feishu(report, config)
        check_email(report, config, config_path)
        check_schedule(report, config)
        check_last_run(report, config)

    failed = any(item["status"] == "fail" for item in report.items)
    if args.json:
        print(json.dumps({"ok": not failed, "items": report.items, "next_steps": report.next_steps}, ensure_ascii=False, indent=2))
    else:
        print("看起来很忙 Agent · 体检")
        for item in report.items:
            print(f"{STATUS_MARK[item['status']]} {item['area']}: {item['detail']}")
        if report.next_steps:
            print("\n下一步：")
            for index, step in enumerate(report.next_steps, 1):
                print(f"  {index}. {step}")
        else:
            print("\n一切就绪。")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
