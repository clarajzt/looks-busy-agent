#!/usr/bin/env python3
"""Dependency-free release smoke test for the looks-busy-agent skill."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "looks-busy-agent"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    installer = ROOT / "install.sh"
    installer_check = subprocess.run(
        ["bash", "-n", str(installer)],
        capture_output=True,
        text=True,
    )
    require(installer_check.returncode == 0, installer_check.stdout + installer_check.stderr)

    with tempfile.TemporaryDirectory() as install_temp:
        sandbox = Path(install_temp)
        archive_root = sandbox / "archive" / "looks-busy-agent-main" / "skills"
        archive_root.mkdir(parents=True)
        shutil.copytree(SKILL, archive_root / "looks-busy-agent")
        archive = sandbox / "release.tar.gz"
        with tarfile.open(archive, "w:gz") as package:
            package.add(sandbox / "archive" / "looks-busy-agent-main", arcname="looks-busy-agent-main")

        home = sandbox / "home"
        home.mkdir()
        (home / ".workbuddy").mkdir()  # an installed product: must get a link
        shared = sandbox / "shared-skills"
        codex_home = sandbox / "codex"
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(home),
                "AGENT_SKILLS_HOME": str(shared),
                "CODEX_HOME": str(codex_home),
                "LOOKS_BUSY_AGENT_ARCHIVE_URL": archive.as_uri(),
            }
        )
        installed = subprocess.run(
            ["bash", str(installer)], capture_output=True, text=True, env=env
        )
        require(installed.returncode == 0, installed.stdout + installed.stderr)
        canonical = shared / "looks-busy-agent"
        require((canonical / "SKILL.md").is_file(), "canonical shared skill was not installed")
        adapter_roots = [
            codex_home / "skills",
            home / ".claude" / "skills",
            home / ".workbuddy" / "skills",
        ]
        for adapter_root in adapter_roots:
            link = adapter_root / "looks-busy-agent"
            require(link.is_symlink(), f"missing agent adapter link: {link}")
            require(link.resolve() == canonical.resolve(), f"wrong adapter target: {link}")
        for absent in (".trae-cn", ".traecli", ".codebuddy"):
            require(not (home / absent).exists(), f"installer must not create {absent} for a product that is not installed")
        require("python3:" in installed.stdout and "lark-cli:" in installed.stdout, "environment report missing")

        duplicate = subprocess.run(
            ["bash", str(installer)], capture_output=True, text=True, env=env
        )
        require(duplicate.returncode == 2, "duplicate install must refuse to overwrite")
        require("--upgrade" in duplicate.stderr, "duplicate install must point at --upgrade")

        marker = canonical / "references" / "old-version-marker.txt"
        marker.write_text("old", encoding="utf-8")
        upgraded = subprocess.run(
            ["bash", str(installer), "--upgrade"], capture_output=True, text=True, env=env
        )
        require(upgraded.returncode == 0, upgraded.stdout + upgraded.stderr)
        require((canonical / "SKILL.md").is_file(), "upgrade lost the skill")
        require(not marker.exists(), "upgrade did not replace the old tree")
        backups = list((home / ".local" / "share" / "looks-busy-agent" / "backups").glob("*"))
        require(len(backups) == 1 and (backups[0] / "references" / "old-version-marker.txt").is_file(), "upgrade must keep a backup")
        require(not list(shared.glob("looks-busy-agent.bak-*")), "backup must not live inside the skills directory")
        for adapter_root in adapter_roots:
            link = adapter_root / "looks-busy-agent"
            require(link.is_symlink() and link.resolve() == canonical.resolve(), f"adapter link broken after upgrade: {link}")

        # A failed update must leave the working install and discovery links intact.
        failed_env = dict(env, LOOKS_BUSY_AGENT_ARCHIVE_URL=(sandbox / "missing.tar.gz").as_uri())
        failed = subprocess.run(["bash", str(installer), "--upgrade"], capture_output=True, text=True, env=failed_env)
        require(failed.returncode != 0, "missing archive must fail")
        require((canonical / "SKILL.md").is_file(), "failed upgrade removed working install")
        for adapter_root in adapter_roots:
            require((adapter_root / "looks-busy-agent").resolve() == canonical.resolve(), "failed upgrade broke a discovery link")

        # launchd wrapper renders without scheduling or running anything
        config_dir = home / ".config" / "looks-busy-agent"
        config_dir.mkdir(parents=True)
        shutil.copy(SKILL / "references" / "config.example.json", config_dir / "config.json")
        rendered = subprocess.run(
            ["bash", str(canonical / "scripts" / "schedule_launchd.sh"), "render", "--agent", "claude"],
            capture_output=True, text=True, env=env,
        )
        require(rendered.returncode == 0, rendered.stdout + rendered.stderr)
        wrapper = Path(rendered.stdout.strip())
        require(wrapper.is_file() and str(wrapper).startswith(str(home)), f"wrapper not under sandbox home: {wrapper}")
        wrapper_text = wrapper.read_text(encoding="utf-8")
        for needle in ("run_daily.py", "--agent claude", "--config", "looks-busy-agent"):
            require(needle in wrapper_text, f"wrapper missing {needle!r}")
        require("dangerously-skip-permissions" not in wrapper_text, "wrapper must not skip permissions")
        require(not (home / "Library" / "LaunchAgents").exists(), "render must not install a launchd job")

    skill_md = SKILL / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    require(text.startswith("---\n"), "SKILL.md frontmatter missing")
    require("name: looks-busy-agent" in text, "skill name mismatch")
    require("description:" in text, "skill description missing")

    for relative in re.findall(r"\]\(([^)]+)\)", text):
        if relative.startswith(("http://", "https://")):
            continue
        require((SKILL / relative).exists(), f"broken local link: {relative}")

    config = SKILL / "references" / "config.example.json"
    parsed = json.loads(config.read_text(encoding="utf-8"))
    require(
        parsed["report"]["delivery"] == "agent_conversation",
        "default delivery must be the originating agent conversation",
    )
    require(parsed["run_time"] == "21:00", "default run time must be 21:00")
    require(parsed["schedule"]["enabled"] is True, "daily schedule must default on")
    require(parsed["schedule"]["provider"] == "auto", "scheduler must be detected at setup")
    require(parsed["report"]["sections"] == ["今日推进", "明日计划"], "unexpected report sections")
    require(
        "必须实际创建并验证"
        in (SKILL / "references" / "automation.md").read_text(encoding="utf-8"),
        "automation setup must require creation and verification",
    )
    require(parsed["calendar"]["write_enabled"] is False, "calendar write must default off")

    checker = SKILL / "scripts" / "check_config.py"
    checked = subprocess.run(
        [sys.executable, str(checker), "--config", str(config)],
        capture_output=True,
        text=True,
    )
    require(checked.returncode == 0, checked.stdout + checked.stderr)

    with tempfile.TemporaryDirectory() as temp_dir:
        bad = Path(temp_dir) / "config.json"
        bad_config = dict(parsed)
        bad_config["password"] = "must-not-be-accepted"
        bad.write_text(json.dumps(bad_config), encoding="utf-8")
        rejected = subprocess.run(
            [sys.executable, str(checker), "--config", str(bad)],
            capture_output=True,
            text=True,
        )
        require(rejected.returncode != 0, "embedded secret was not rejected")

    detected = subprocess.run(
        [sys.executable, str(SKILL / "scripts" / "detect_scheduler.py"), "--json"], capture_output=True, text=True
    )
    require(detected.returncode == 0, detected.stdout + detected.stderr)
    detection = json.loads(detected.stdout)
    require(
        detection["recommended"] in {"codex_heartbeat", "claude_desktop", "trae_automation", "workbuddy_automation", "cola_alarm", "launchd", "schtasks", "manual"},
        f"unexpected scheduler recommendation: {detection['recommended']}",
    )
    windows_script = (SKILL / "scripts" / "schedule_windows.ps1").read_text(encoding="utf-8")
    for needle in ("-WakeToRun", "-StartWhenAvailable", "run_daily.py", "cmdkey /generic:looks-busy-agent-email"):
        require(needle in windows_script, f"schedule_windows.ps1 missing {needle!r}")
    require("dangerously-skip-permissions" not in windows_script, "windows wrapper must not skip permissions")
    for doc in (ROOT / "README.md", SKILL / "references" / "automation.md", SKILL / "scripts" / "schedule_launchd.sh"):
        require("不补跑" not in doc.read_text(encoding="utf-8"), f"{doc.name}: launchd does catch up after sleep; fix the claim")

    with tempfile.TemporaryDirectory() as temp_dir:
        cfg = Path(temp_dir) / "config.json"
        cfg.write_text(json.dumps({"report": {"output_dir": str(Path(temp_dir) / "reports")}}), encoding="utf-8")
        saver = [sys.executable, str(SKILL / "scripts" / "save_report.py"), "--config", str(cfg), "--date", "2026-09-02"]
        saved = subprocess.run(saver, input="【AI日记】2026-09-02（周三）\n\n今日推进\n1. 推进 1 轮内部评审\n", capture_output=True, text=True)
        require(saved.returncode == 0 and "saved:" in saved.stdout, "save_report failed: " + saved.stdout + saved.stderr)
        require((Path(temp_dir) / "reports" / "2026-09-02.md").is_file(), "save_report did not write the file")
        again = subprocess.run(saver, input="【AI日记】2026-09-02（周三）\n\n今日推进\n1. 另一版\n", capture_output=True, text=True)
        require(again.returncode != 0, "save_report must refuse to overwrite without --force")
        empty = subprocess.run(saver + ["--force"], input="short", capture_output=True, text=True)
        require(empty.returncode != 0, "save_report must reject empty reports")

    for script in ("collect_email.py", "doctor.py", "detect_scheduler.py", "save_report.py"):
        helped = subprocess.run(
            [sys.executable, str(SKILL / "scripts" / script), "--help"],
            capture_output=True,
            text=True,
        )
        require(helped.returncode == 0, f"{script}: " + helped.stdout + helped.stderr)
    launchd_check = subprocess.run(
        ["bash", "-n", str(SKILL / "scripts" / "schedule_launchd.sh")], capture_output=True, text=True
    )
    require(launchd_check.returncode == 0, launchd_check.stdout + launchd_check.stderr)

    with tempfile.TemporaryDirectory() as temp_dir:
        scheduled = dict(parsed)
        for provider, job in (("launchd", "com.looks-busy-agent.daily"), ("schtasks", "looks-busy-agent-daily")):
            scheduled["schedule"] = {"enabled": True, "provider": provider, "agent": "claude", "job_id": job, "delivery_verified": True}
            path = Path(temp_dir) / "config.json"
            path.write_text(json.dumps(scheduled), encoding="utf-8")
            accepted = subprocess.run(
                [sys.executable, str(checker), "--config", str(path)], capture_output=True, text=True
            )
            require(accepted.returncode == 0, f"{provider} must be an accepted schedule provider: " + accepted.stdout)

        doctor = subprocess.run(
            [sys.executable, str(SKILL / "scripts" / "doctor.py"), "--config", str(Path(temp_dir) / "missing.json"), "--json"],
            capture_output=True, text=True,
        )
        require(doctor.returncode == 1, "doctor must exit 1 when config is missing")
        require(json.loads(doctor.stdout)["ok"] is False, "doctor json must report ok=false")

    for reference in ("setup.md", "automation.md"):
        text_ref = (SKILL / "references" / reference).read_text(encoding="utf-8")
        require("--domain calendar" not in text_ref.replace("不要用 `--domain calendar`", ""), f"{reference} must not request the full calendar domain")
    require("--as user" in (SKILL / "references" / "setup.md").read_text(encoding="utf-8"), "setup must read the calendar as the user identity")

    release_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in ROOT.rglob("*")
        if path.is_file()
        and ".git" not in path.parts
        and "__pycache__" not in path.parts
        and path.name != ".canaries.local"
    )
    legacy_skill_name = "work" + "-diary-agent"
    require(legacy_skill_name not in release_text, "legacy skill name is still present")
    ai_tools_heading = "AI " + "工具使用"
    require(ai_tools_heading not in release_text, "AI tooling section is still present")

    # Generic sentinels are built at runtime so the scanner does not match its
    # own source. Private canaries (a maintainer's name, mailbox, employer terms)
    # must never live in this public repository: supply them via the
    # LOOKS_BUSY_CANARIES environment variable (comma-separated) or the
    # git-ignored file tests/.canaries.local (one per line).
    forbidden = ["FEISHU_" + "APP_SECRET", "EXMAIL_" + "PASS", "password" + "="]
    private = [
        value.strip()
        for value in os.environ.get("LOOKS_BUSY_CANARIES", "").split(",")
        if value.strip()
    ]
    local_canaries = ROOT / "tests" / ".canaries.local"
    if local_canaries.is_file():
        private += [
            line.strip()
            for line in local_canaries.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
    if not private:
        print("note: no private canaries configured (LOOKS_BUSY_CANARIES / tests/.canaries.local)")
    for value in forbidden + private:
        require(value not in release_text, f"private identifier leaked: {value}")

    example_text = config.read_text(encoding="utf-8")
    for term in ("LP", "投后", "尽调"):
        require(term not in example_text, f"config.example.json must stay generic, found {term!r}")

    print("smoke test passed")


if __name__ == "__main__":
    main()
