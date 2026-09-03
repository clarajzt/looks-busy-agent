#!/usr/bin/env bash
# macOS launchd scheduler for looks-busy-agent (works with any CLI coding agent).
#
# Why this exists: cloud routines cannot read the local mailbox, Keychain or
# lark-cli. This installs a per-user launchd job that, at run_time, (1) collects
# read-only snapshots deterministically (email + Feishu agenda -> raw/<date>/),
# then (2) runs read-only generation and saves the final report deterministically
# into report.output_dir. It never asks for new permissions or sends
# anything externally.
#
# Usage:
#   schedule_launchd.sh install [--time HH:MM] [--agent claude|codex]
#   schedule_launchd.sh run-once [--agent claude|codex]
#   schedule_launchd.sh status | uninstall | render
set -euo pipefail
umask 077

LABEL="com.looks-busy-agent.daily"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DATA_DIR="$HOME/.local/share/looks-busy-agent"
CONFIG="$HOME/.config/looks-busy-agent/config.json"
WRAPPER="$DATA_DIR/run_daily.sh"
SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

config_value() {
  # $1 = dotted key, $2 = default
  python3 - "$CONFIG" "$1" "$2" <<'PY' 2>/dev/null || printf '%s' "$2"
import json, sys
path, key, default = sys.argv[1:4]
try:
    node = json.load(open(path, encoding="utf-8"))
    for part in key.split("."):
        node = node[part]
    print(node if isinstance(node, str) else json.dumps(node))
except Exception:
    print(default)
PY
}

write_wrapper() {
  local agent="$1"
  mkdir -p "$DATA_DIR/logs"
  python3 - "$WRAPPER" "$SKILL_DIR/scripts/run_daily.py" "$CONFIG" "$DATA_DIR" "$agent" "$PATH" "${2:-scheduled}" <<'PYWRAP'
import os, shlex, sys
from pathlib import Path
wrapper, runner, config, data, agent, search_path, mode = sys.argv[1:]
command = [sys.executable, runner, "--config", config, "--data-dir", data, "--agent", agent]
if mode == "scheduled":
    command.append("--scheduled")
Path(wrapper).write_text("#!/bin/bash\nset -eu\numask 077\nexport PATH=" + shlex.quote(search_path) + "\nexec " + shlex.join(command) + "\n", encoding="utf-8")
os.chmod(wrapper, 0o700)
PYWRAP
}

cmd="${1:-}"; shift || true
TIME=""; AGENT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --time)  TIME="$2"; shift 2 ;;
    --agent) AGENT="$2"; shift 2 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$TIME" ]] || TIME="$(config_value run_time 21:00)"
if [[ -z "$AGENT" ]]; then
  if command -v claude >/dev/null 2>&1; then AGENT=claude
  elif command -v codex >/dev/null 2>&1; then AGENT=codex
  else AGENT=claude; fi
fi

case "$AGENT" in claude|codex) ;; *) echo "unsupported --agent: $AGENT" >&2; exit 2 ;; esac

case "$cmd" in
  install)
    [[ -f "$CONFIG" ]] || { echo "config not found: $CONFIG (run setup first)" >&2; exit 1; }
    [[ "$TIME" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || { echo "invalid --time: $TIME (HH:MM)" >&2; exit 2; }
    case "$AGENT" in
      claude) command -v claude >/dev/null || { echo "claude CLI not found in PATH" >&2; exit 1; } ;;
      codex)  command -v codex  >/dev/null || { echo "codex CLI not found in PATH" >&2; exit 1; } ;;
      *) echo "unsupported --agent: $AGENT (claude|codex)" >&2; exit 2 ;;
    esac
    python3 "$SKILL_DIR/scripts/run_daily.py" --config "$CONFIG" --check-local-timezone
    write_wrapper "$AGENT"
    HOUR="${TIME%:*}"; MINUTE="${TIME#*:}"
    mkdir -p "$(dirname "$PLIST")"
    python3 - "$PLIST" "$LABEL" "$WRAPPER" "$DATA_DIR" "$HOUR" "$MINUTE" <<'PYPLIST'
import os, plistlib, sys
from pathlib import Path
plist, label, wrapper, data, hour, minute = sys.argv[1:]
with open(plist, "wb") as handle:
    plistlib.dump({"Label": label, "ProgramArguments": ["/bin/bash", wrapper],
                  "StartCalendarInterval": {"Hour": int(hour), "Minute": int(minute)},
                  "StandardOutPath": str(Path(data) / "logs/launchd.out.log"),
                  "StandardErrorPath": str(Path(data) / "logs/launchd.err.log")}, handle)
os.chmod(plist, 0o600)
PYPLIST
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    echo "installed: $LABEL — daily at $TIME via $AGENT"
    echo "wrapper: $WRAPPER"
    echo "config.schedule suggestion: {\"provider\": \"launchd\", \"agent\": \"$AGENT\", \"job_id\": \"$LABEL\"}"
    echo "note: 睡眠中错过会在唤醒后补跑一次；关机会错过当次，次日让 Agent「补生成昨天的日报」。"
    ;;
  run-once)
    [[ -f "$CONFIG" ]] || { echo "config not found: $CONFIG (run setup first)" >&2; exit 1; }
    exec python3 "$SKILL_DIR/scripts/run_daily.py" --config "$CONFIG" --data-dir "$DATA_DIR" --agent "$AGENT"
    ;;
  render)
    # write the wrapper without running or scheduling it (inspection / tests)
    write_wrapper "$AGENT"
    echo "$WRAPPER"
    ;;
  uninstall)
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "uninstalled: $LABEL"
    ;;
  status)
    if [[ -f "$PLIST" ]]; then
      echo "plist: $PLIST"
      launchctl list 2>/dev/null | grep -F "$LABEL" || echo "loaded: no"
      if [[ -f "$DATA_DIR/logs/last_run.json" ]]; then cat "$DATA_DIR/logs/last_run.json"; fi
    else
      echo "not installed"
    fi
    ;;
  *)
    echo "usage: $0 install [--time HH:MM] [--agent claude|codex] | run-once [--agent claude|codex] | render | status | uninstall" >&2
    exit 2
    ;;
esac
