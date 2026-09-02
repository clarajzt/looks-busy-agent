#!/usr/bin/env bash
set -euo pipefail

repo_archive="${LOOKS_BUSY_AGENT_ARCHIVE_URL:-https://github.com/clarajzt/looks-busy-agent/archive/refs/heads/main.tar.gz}"
skill_name="looks-busy-agent"
shared_skills_dir="${AGENT_SKILLS_HOME:-${HOME}/.agents/skills}"
canonical_dir="${shared_skills_dir}/${skill_name}"

for command_name in curl tar mktemp ln; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "${command_name}" >&2
    exit 1
  fi
done

if [[ -e "${canonical_dir}" || -L "${canonical_dir}" ]]; then
  printf 'Not overwriting existing skill: %s\n' "${canonical_dir}" >&2
  printf 'Remove or rename it yourself, then run this installer again.\n' >&2
  exit 2
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
chmod 755 "${canonical_dir}/scripts/"*.py 2>/dev/null || true

link_for_agent() {
  local label="$1"
  local skills_root="$2"
  local target="${skills_root}/${skill_name}"

  mkdir -p "${skills_root}"
  if [[ -e "${target}" || -L "${target}" ]]; then
    printf 'Skipped %-12s existing path: %s\n' "${label}" "${target}"
    return
  fi
  ln -s "${canonical_dir}" "${target}"
  printf 'Enabled %-12s %s\n' "${label}" "${target}"
}

link_for_agent "Codex" "${CODEX_HOME:-${HOME}/.codex}/skills"
link_for_agent "Claude Code" "${HOME}/.claude/skills"
link_for_agent "TRAE IDE" "${HOME}/.trae-cn/skills"
link_for_agent "TRAE CLI" "${HOME}/.traecli/skills"
link_for_agent "WorkBuddy" "${HOME}/.workbuddy/skills"
link_for_agent "CodeBuddy" "${HOME}/.codebuddy/skills"

package_dir="${HOME}/.local/share/${skill_name}"
if command -v zip >/dev/null 2>&1; then
  mkdir -p "${package_dir}"
  (
    cd "${shared_skills_dir}"
    zip -qr "${package_dir}/${skill_name}.zip" "${skill_name}"
  )
  printf 'WorkBuddy/TRAE upload package: %s\n' "${package_dir}/${skill_name}.zip"
fi

printf '\nInstalled canonical skill: %s\n' "${canonical_dir}"
printf 'Invoke in Codex:      使用 $looks-busy-agent 帮我开始设置日报。\n'
printf 'Invoke in Claude:     /looks-busy-agent 帮我开始设置日报。\n'
printf 'Invoke elsewhere:     使用 looks-busy-agent 帮我开始设置日报。\n'
