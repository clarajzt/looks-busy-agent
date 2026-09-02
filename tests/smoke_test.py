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
            home / ".trae-cn" / "skills",
            home / ".traecli" / "skills",
            home / ".workbuddy" / "skills",
            home / ".codebuddy" / "skills",
        ]
        for adapter_root in adapter_roots:
            link = adapter_root / "looks-busy-agent"
            require(link.is_symlink(), f"missing agent adapter link: {link}")
            require(link.resolve() == canonical.resolve(), f"wrong adapter target: {link}")

        duplicate = subprocess.run(
            ["bash", str(installer)], capture_output=True, text=True, env=env
        )
        require(duplicate.returncode == 2, "duplicate install must refuse to overwrite")

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

    imap_help = subprocess.run(
        [sys.executable, str(SKILL / "scripts" / "collect_email.py"), "--help"],
        capture_output=True,
        text=True,
    )
    require(imap_help.returncode == 0, imap_help.stdout + imap_help.stderr)

    release_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in ROOT.rglob("*")
        if path.is_file() and ".git" not in path.parts and "__pycache__" not in path.parts
    )
    legacy_skill_name = "work" + "-diary-agent"
    require(legacy_skill_name not in release_text, "legacy skill name is still present")
    ai_tools_heading = "AI " + "工具使用"
    require(ai_tools_heading not in release_text, "AI tooling section is still present")

    # Build sentinels at runtime so the scanner does not match its own source.
    forbidden = [
        "FEISHU_" + "APP_SECRET",
        "EXMAIL_" + "PASS",
    ]
    for value in forbidden:
        require(value not in release_text, f"private identifier leaked: {value}")

    print("smoke test passed")


if __name__ == "__main__":
    main()
