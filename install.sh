#!/usr/bin/env bash
# 看起来很忙 Agent 安装器
#   首次安装：curl -fsSL https://raw.githubusercontent.com/clarajzt/looks-busy-agent/main/install.sh | bash
#   升级：    curl -fsSL https://raw.githubusercontent.com/clarajzt/looks-busy-agent/main/install.sh | bash -s -- --upgrade
set -euo pipefail

repo_archive="${LOOKS_BUSY_AGENT_ARCHIVE_URL:-https://github.com/clarajzt/looks-busy-agent/archive/refs/heads/main.tar.gz}"
skill_name="looks-busy-agent"
shared_skills_dir="${AGENT_SKILLS_HOME:-${HOME}/.agents/skills}"
canonical_dir="${shared_skills_dir}/${skill_name}"
upgrade="${LOOKS_BUSY_AGENT_UPGRADE:-0}"

for arg in "$@"; do
  case "${arg}" in
    --upgrade|-u) upgrade=1 ;;
    -h|--help)
      printf 'Usage: install.sh [--upgrade]\n  --upgrade  replace an existing install (a timestamped backup is kept)\n'
      exit 0 ;;
    *) printf 'Unknown option: %s\n' "${arg}" >&2; exit 64 ;;
  esac
done

for command_name in curl tar mktemp ln; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "${command_name}" >&2
    exit 1
  fi
done

backup_dir=""
if [[ -e "${canonical_dir}" || -L "${canonical_dir}" ]]; then
  if [[ "${upgrade}" != "1" ]]; then
    printf 'Already installed: %s\n' "${canonical_dir}" >&2
    printf 'To upgrade in place, re-run with --upgrade (a backup is kept):\n' >&2
    printf '  curl -fsSL https://raw.githubusercontent.com/clarajzt/looks-busy-agent/main/install.sh | bash -s -- --upgrade\n' >&2
    exit 2
  fi
  if [[ ! -L "${canonical_dir}" && ! -f "${canonical_dir}/SKILL.md" ]]; then
    printf 'Refusing to upgrade: %s does not look like this skill (no SKILL.md).\n' "${canonical_dir}" >&2
    exit 5
  fi
  # Backups live outside the skills directory so agents do not discover a
  # second copy with the same skill name.
  backup_dir="${HOME}/.local/share/${skill_name}/backups/$(date +%Y%m%d%H%M%S)"
  mkdir -p "$(dirname "${backup_dir}")"
  mv "${canonical_dir}" "${backup_dir}"
fi

temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/looks-busy-agent-install.XXXXXX")"
cleanup() {
  rm -rf -- "${temp_dir}"
}
trap cleanup EXIT

archive="${temp_dir}/source.tar.gz"
curl -fsSL "${repo_archive}" -o "${archive}"
tar -xzf "${archive}" -C "${temp_dir}"

source_dir="${temp_dir}/looks-busy-agent-main/skills/${skill_name}"
if [[ ! -f "${source_dir}/SKILL.md" ]]; then
  printf 'Downloaded package does not contain %s/SKILL.md\n' "${skill_name}" >&2
  exit 3
fi
if ! grep -q '^name: looks-busy-agent$' "${source_dir}/SKILL.md"; then
  printf 'Downloaded SKILL.md has an unexpected skill name.\n' >&2
  exit 4
fi

mkdir -p "${shared_skills_dir}"
mv "${source_dir}" "${canonical_dir}"
chmod 755 "${canonical_dir}/scripts/"*.py "${canonical_dir}/scripts/"*.sh 2>/dev/null || true

# Discovery links. Codex and Claude Code are always linked; other products only
# when their home directory already exists, so a fresh machine is not littered
# with hidden folders for tools that were never installed.
link_for_agent() {
  local label="$1"
  local skills_root="$2"
  local target="${skills_root}/${skill_name}"

  mkdir -p "${skills_root}"
  if [[ -L "${target}" && "$(readlink "${target}")" == "${canonical_dir}" ]]; then
    printf 'Linked  %-12s %s (already)\n' "${label}" "${target}"
    return
  fi
  if [[ -e "${target}" || -L "${target}" ]]; then
    printf 'Skipped %-12s existing path: %s\n' "${label}" "${target}"
    return
  fi
  ln -s "${canonical_dir}" "${target}"
  printf 'Linked  %-12s %s\n' "${label}" "${target}"
}

link_if_present() {
  local label="$1"
  local product_home="$2"
  if [[ -d "${product_home}" ]]; then
    link_for_agent "${label}" "${product_home}/skills"
  fi
}

link_for_agent "Codex" "${CODEX_HOME:-${HOME}/.codex}/skills"
link_for_agent "Claude Code" "${HOME}/.claude/skills"
link_if_present "TRAE IDE" "${HOME}/.trae-cn"
link_if_present "TRAE CLI" "${HOME}/.traecli"
link_if_present "WorkBuddy" "${HOME}/.workbuddy"
link_if_present "CodeBuddy" "${HOME}/.codebuddy"

package_dir="${HOME}/.local/share/${skill_name}"
if command -v zip >/dev/null 2>&1; then
  mkdir -p "${package_dir}"
  rm -f "${package_dir}/${skill_name}.zip"
  (
    cd "${shared_skills_dir}"
    zip -qr "${package_dir}/${skill_name}.zip" "${skill_name}"
  )
  printf 'Upload package (WorkBuddy/TRAE UI): %s\n' "${package_dir}/${skill_name}.zip"
fi

# Environment report: nothing here is fatal, it only tells the user what setup
# will still need. On macOS the bare /usr/bin/python3 stub pops a GUI dialog
# when Command Line Tools are missing, so it is detected without being run.
have() { command -v "$1" >/dev/null 2>&1; }
python_status="missing"
if [[ "$(uname -s)" == "Darwin" ]] && ! xcode-select -p >/dev/null 2>&1 \
   && [[ "$(command -v python3 || true)" == "/usr/bin/python3" || -z "$(command -v python3 || true)" ]]; then
  python_status="needs Command Line Tools (run: xcode-select --install)"
elif have python3; then
  python_status="ok ($(python3 --version 2>&1))"
fi
node_status="missing (needed only for Feishu calendar; install Node.js LTS from https://nodejs.org)"
have node && node_status="ok ($(node --version 2>&1))"
lark_status="missing (after Node.js: npm install -g @larksuite/cli)"
have lark-cli && lark_status="ok ($(lark-cli --version 2>&1 | head -1))"

printf '\nInstalled canonical skill: %s\n' "${canonical_dir}"
[[ -n "${backup_dir}" ]] && printf 'Previous version kept at:  %s\n' "${backup_dir}"
printf 'python3:  %s\n' "${python_status}"
printf 'node:     %s\n' "${node_status}"
printf 'lark-cli: %s\n' "${lark_status}"
printf '\nNext step — open your coding agent and say:\n'
printf '  Codex:        使用 $looks-busy-agent 帮我开始设置日报。\n'
printf '  Claude Code:  /looks-busy-agent 帮我开始设置日报。\n'
printf '  Others:       使用 looks-busy-agent 帮我开始设置日报。\n'
printf 'Health check any time: python3 %s/scripts/doctor.py\n' "${canonical_dir}"
