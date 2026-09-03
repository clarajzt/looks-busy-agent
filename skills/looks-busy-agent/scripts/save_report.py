#!/usr/bin/env python3
"""Deterministically save a generated report as the private copy.

Why: agents sometimes "forget" the save step in unattended runs. Calling this script makes
the private copy a tool call instead of model discipline.

Usage:
  python3 scripts/save_report.py --date 2026-09-02 < report.md
  python3 scripts/save_report.py --date 2026-09-02 --file /tmp/report.md [--force]
Prints the saved path. Refuses to overwrite an existing file unless --force.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

DEFAULT_CONFIG = Path("~/.config/looks-busy-agent/config.json")
DEFAULT_OUTPUT_DIR = "~/.local/share/looks-busy-agent/reports"


def save_report(content: str, output_dir: str, day: str, force: bool = False) -> Path:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise ValueError("--date 需为 YYYY-MM-DD")
    date.fromisoformat(day)
    content = content.strip()
    if len(content) < 20:
        raise ValueError("日报内容为空或过短，未保存")
    target_dir = Path(output_dir).expanduser()
    target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = target_dir / f"{day}.md"
    # Write privately from the first byte; publish only a complete file. A hard
    # link gives no-overwrite semantics without a check-then-write race.
    fd, temporary = tempfile.mkstemp(prefix=".report-", dir=target_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if force:
            os.replace(temporary, target)
        else:
            os.link(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--date", default=date.today().isoformat(), help="YYYY-MM-DD (file name)")
    parser.add_argument("--file", type=Path, help="read the report from this file instead of stdin")
    parser.add_argument("--force", action="store_true", help="overwrite an existing report")
    args = parser.parse_args()

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date):
        sys.exit(f"--date 需为 YYYY-MM-DD，当前 {args.date!r}")
    output_dir = DEFAULT_OUTPUT_DIR
    config_path = args.config.expanduser()
    if config_path.is_file():
        try:
            output_dir = json.loads(config_path.read_text(encoding="utf-8")).get("report", {}).get("output_dir") or output_dir
        except (json.JSONDecodeError, AttributeError, TypeError):
            sys.exit("配置无效，未保存")
    elif args.config != DEFAULT_CONFIG:
        sys.exit("指定配置不存在，未保存")
    content = args.file.expanduser().read_text(encoding="utf-8") if args.file else sys.stdin.read()
    try:
        target = save_report(content, output_dir, args.date, args.force)
    except FileExistsError:
        sys.exit("日报已存在；要覆盖请加 --force")
    except (ValueError, OSError, TypeError) as exc:
        sys.exit(f"保存失败: {exc}")
    print(f"saved: {target}")


if __name__ == "__main__":
    main()
