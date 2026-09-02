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
import re
import sys
from datetime import date
from pathlib import Path

DEFAULT_CONFIG = Path("~/.config/looks-busy-agent/config.json")
DEFAULT_OUTPUT_DIR = "~/.local/share/looks-busy-agent/reports"


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
        except json.JSONDecodeError:
            pass
    content = (args.file.read_text(encoding="utf-8") if args.file else sys.stdin.read()).strip()
    if len(content) < 20:
        sys.exit("日报内容为空或过短，未保存")

    target_dir = Path(output_dir).expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        target_dir.chmod(0o700)
    except OSError:
        pass
    target = target_dir / f"{args.date}.md"
    if target.exists() and not args.force:
        sys.exit(f"已存在 {target}；要覆盖请加 --force")
    target.write_text(content + "\n", encoding="utf-8")
    try:
        target.chmod(0o600)
    except OSError:
        pass
    print(f"saved: {target}")


if __name__ == "__main__":
    main()
