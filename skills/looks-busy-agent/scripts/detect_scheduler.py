#!/usr/bin/env python3
"""Detect the coding agents on this machine and recommend a daily scheduler.

Read-only. Priority (decided 2026-09-02): the current agent's native persistent
scheduler first; another installed agent's native scheduler second; the OS
scheduler (macOS launchd / Windows Task Scheduler) only as a fallback; manual
`run` last.

Usage: python3 scripts/detect_scheduler.py [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
from pathlib import Path

HOME = Path.home()

# provider -> (label, delivery, note)
PROVIDERS = {
    "codex_heartbeat": ("Codex App heartbeat", "当前 Codex 任务", "创建后回读卡片与 ACTIVE 状态"),
    "claude_desktop": ("Claude Code Desktop scheduled task", "对应 Claude 任务", "需确认任务能访问本地文件与 Keychain"),
    "trae_automation": ("TRAE / TraeWork 自动化", "TraeWork 任务结果", "在 Work 模式创建，确认 Skill 与目录权限"),
    "workbuddy_automation": ("WorkBuddy 自动化任务", "WorkBuddy 任务，可选推送小程序", "选择本 Skill 与工作空间"),
    "cola_alarm": ("Cola 智能闹钟", "创建闹钟的会话", "Cola 桌面端需保持运行"),
    "launchd": ("macOS launchd（scripts/schedule_launchd.sh）", "本地日报文件", "OS 兜底：睡眠中错过会在唤醒后补跑，关机则丢"),
    "schtasks": ("Windows 任务计划（scripts/schedule_windows.ps1）", "本地日报文件", "OS 兜底：需同时启用 唤醒运行 + 错过后尽快运行"),
    "manual": ("仅手动 run", "当前会话", "本机没有可用的持久调度器"),
}


def exists(*parts: str) -> bool:
    return Path(*parts).expanduser().exists()


def detect_agents() -> list[dict]:
    system = platform.system()
    apps = Path("/Applications")
    found: list[dict] = []

    def add(name: str, provider: str | None, evidence: list[str]) -> None:
        if evidence:
            found.append({"agent": name, "native_scheduler": provider, "evidence": evidence})

    codex_evidence = [e for e, ok in (
        ("~/.codex", exists(HOME / ".codex")),
        ("codex CLI in PATH", bool(shutil.which("codex"))),
        ("Codex.app", system == "Darwin" and (apps / "Codex.app").exists()),
        ("~/.codex/automations (Codex App heartbeat)", exists(HOME / ".codex" / "automations")),
        ("Codex App data dir", system == "Darwin" and exists(HOME / "Library" / "Application Support" / "Codex")),
        ("CODEX_HOME set", bool(os.environ.get("CODEX_HOME"))),
    ) if ok]
    codex_app = any(e.startswith(("Codex.app", "~/.codex/automations", "Codex App data dir")) for e in codex_evidence)
    add("codex", "codex_heartbeat" if codex_app else None, codex_evidence)

    claude_evidence = [e for e, ok in (
        ("~/.claude", exists(HOME / ".claude")),
        ("claude CLI in PATH", bool(shutil.which("claude"))),
        ("Claude.app", system == "Darwin" and (apps / "Claude.app").exists()),
        ("CLAUDECODE session env", bool(os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_ENTRYPOINT"))),
    ) if ok]
    claude_desktop = "Claude.app" in claude_evidence
    add("claude", "claude_desktop" if claude_desktop else None, claude_evidence)

    add("trae", "trae_automation", [e for e, ok in (
        ("~/.trae-cn", exists(HOME / ".trae-cn")),
        ("~/.trae", exists(HOME / ".trae")),
        ("~/.traecli", exists(HOME / ".traecli")),
        ("Trae app", system == "Darwin" and any(apps.glob("Trae*.app"))),
    ) if ok])
    add("workbuddy", "workbuddy_automation", [e for e, ok in (
        ("~/.workbuddy", exists(HOME / ".workbuddy")),
        ("WorkBuddy app", system == "Darwin" and any(apps.glob("WorkBuddy*.app"))),
    ) if ok])
    add("codebuddy", None, [e for e, ok in (("~/.codebuddy", exists(HOME / ".codebuddy")),) if ok])
    add("cola", "cola_alarm", [e for e, ok in (
        ("Cola app", system == "Darwin" and any(apps.glob("Cola*.app"))),
        ("~/.cola", exists(HOME / ".cola")),
    ) if ok])
    return found


def current_agent() -> str | None:
    env = os.environ
    if env.get("CLAUDECODE") or env.get("CLAUDE_CODE_ENTRYPOINT"):
        return "claude"
    if env.get("CODEX_SANDBOX") or env.get("CODEX_THREAD_ID") or env.get("CODEX_HOME"):
        return "codex"
    if any(key.startswith("TRAE_") for key in env):
        return "trae"
    if any(key.startswith("WORKBUDDY_") for key in env):
        return "workbuddy"
    return None


def recommend(system: str, agents: list[dict], current: str | None) -> tuple[str, str]:
    by_name = {item["agent"]: item for item in agents}
    if current and by_name.get(current, {}).get("native_scheduler"):
        provider = by_name[current]["native_scheduler"]
        return provider, f"当前 Agent（{current}）自带持久调度器"
    others = [item for item in agents if item.get("native_scheduler") and item["agent"] != current]
    if others:
        item = others[0]
        return item["native_scheduler"], f"本机已装 {item['agent']}，它有原生调度器；当前 Agent 没有，可切换过去创建定时任务"
    if system == "Darwin":
        return "launchd", "没有可用的 Agent 原生调度器，使用 macOS launchd 兜底"
    if system == "Windows":
        return "schtasks", "没有可用的 Agent 原生调度器，使用 Windows 任务计划兜底"
    return "manual", f"{system} 上暂无受支持的调度器，只能手动 run"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    system = platform.system()
    agents = detect_agents()
    current = current_agent()
    provider, reason = recommend(system, agents, current)
    label, delivery, note = PROVIDERS[provider]
    result = {
        "os": system,
        "current_agent": current,
        "agents_found": agents,
        "recommended": provider,
        "label": label,
        "delivery": delivery,
        "note": note,
        "reason": reason,
        "fallback_order": ["<当前 Agent 原生>", "<其他已装 Agent 原生>", "launchd" if system == "Darwin" else "schtasks" if system == "Windows" else "-", "manual"],
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f"系统: {system}    当前 Agent: {current or '未识别'}")
    print("已发现的 Agent:")
    for item in agents or [{"agent": "(无)", "native_scheduler": None, "evidence": []}]:
        native = item["native_scheduler"] or "无原生调度器"
        print(f"  - {item['agent']:<10} {native:<22} {', '.join(item['evidence'])}")
    print(f"\n推荐调度器: {provider}  —  {label}")
    print(f"理由: {reason}")
    print(f"交付: {delivery}；{note}")


if __name__ == "__main__":
    main()
