<#
.SYNOPSIS
  Windows Task Scheduler fallback for looks-busy-agent (CLI coding agents).

.DESCRIPTION
  Mirrors scripts/schedule_launchd.sh. Registers a per-user daily task that
  (1) pre-collects read-only snapshots (email via collect_email.py, Feishu
  agenda via lark-cli --as user) into %LOCALAPPDATA%\looks-busy-agent\raw\<date>\
  and (2) runs the agent headless with a minimal tool whitelist to write the
  report. Both "Wake the computer to run this task" (WakeToRun) and "Run task
  as soon as possible after a scheduled start is missed" (StartWhenAvailable)
  are enabled — a laptop asleep at 21:00 needs both.

  Credentials: store the mailbox app-password with
    cmdkey /generic:looks-busy-agent-email /user:<mailbox> /pass
  collect_email.py reads it back through the Credential Manager API.

  NOTE: Windows scheduling has not been verified on a real machine yet.
  Run `render` and `run-once` first; Python 3.9+ and tzdata are required.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File schedule_windows.ps1 install -Time 21:00 -Agent claude
  powershell -ExecutionPolicy Bypass -File schedule_windows.ps1 run-once
  powershell -ExecutionPolicy Bypass -File schedule_windows.ps1 render | status | uninstall
#>
param(
  [Parameter(Position = 0)][ValidateSet('install', 'run-once', 'render', 'status', 'uninstall')][string]$Command = 'status',
  [string]$Time = '',
  [ValidateSet('claude', 'codex', '')][string]$Agent = ''
)
$ErrorActionPreference = 'Stop'

$TaskName = 'looks-busy-agent-daily'
$DataDir = Join-Path $env:LOCALAPPDATA 'looks-busy-agent'
$ConfigPath = Join-Path $env:USERPROFILE '.config\looks-busy-agent\config.json'
$Wrapper = Join-Path $DataDir 'run_daily.ps1'
$SkillDir = Split-Path -Parent $PSScriptRoot

function Get-ConfigValue([string]$Key, [string]$Default) {
  if (-not (Test-Path $ConfigPath)) { return $Default }
  try {
    $node = Get-Content $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($part in $Key.Split('.')) { $node = $node.$part }
    if ($null -eq $node) { return $Default }
    return [string]$node
  } catch { return $Default }
}

function Resolve-Agent {
  if ($Agent) { return $Agent }
  if (Get-Command claude -ErrorAction SilentlyContinue) { return 'claude' }
  if (Get-Command codex -ErrorAction SilentlyContinue) { return 'codex' }
  return 'claude'
}

function Write-Wrapper([string]$UseAgent) {
  New-Item -ItemType Directory -Force -Path (Join-Path $DataDir 'logs') | Out-Null
  $runner = (Join-Path $SkillDir 'scripts\run_daily.py').Replace("'", "''")
  $config = $ConfigPath.Replace("'", "''")
  $data = $DataDir.Replace("'", "''")
  $body = "& python '$runner' --config '$config' --data-dir '$data' --agent '$UseAgent' --scheduled`r`nexit `$LASTEXITCODE`r`n"
  Set-Content -Path $Wrapper -Value $body -Encoding UTF8
}

$UseAgent = Resolve-Agent
if (-not $Time) { $Time = Get-ConfigValue 'run_time' '21:00' }

switch ($Command) {
  'install' {
    if (-not (Test-Path $ConfigPath)) { throw "config not found: $ConfigPath (run setup first)" }
    if ($Time -notmatch '^([01][0-9]|2[0-3]):[0-5][0-9]$') { throw "invalid -Time: $Time (HH:MM)" }
    if (-not (Get-Command $UseAgent -ErrorAction SilentlyContinue)) { throw "$UseAgent CLI not found in PATH" }
    & python (Join-Path $SkillDir 'scripts\run_daily.py') --config $ConfigPath --check-local-timezone
    if ($LASTEXITCODE -ne 0) { throw 'configuration/timezone check failed' }
    Write-Wrapper $UseAgent
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Wrapper`""
    $trigger = New-ScheduledTaskTrigger -Daily -At $Time
    $settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 40) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description '看起来很忙 Agent 每日日报' -Force | Out-Null
    Write-Output "installed: $TaskName — daily at $Time via $UseAgent"
    Write-Output "wrapper: $Wrapper"
    Write-Output "config.schedule suggestion: {`"provider`": `"schtasks`", `"agent`": `"$UseAgent`", `"job_id`": `"$TaskName`"}"
    Write-Output 'note: 已启用「唤醒运行」+「错过后尽快运行」；笔记本需在 BIOS 允许定时唤醒才能从睡眠中启动。'
  }
  'run-once' {
    if (-not (Test-Path $ConfigPath)) { throw "config not found: $ConfigPath (run setup first)" }
    & python (Join-Path $SkillDir 'scripts\run_daily.py') --config $ConfigPath --data-dir $DataDir --agent $UseAgent
    exit $LASTEXITCODE
  }
  'render' {
    Write-Wrapper $UseAgent
    Write-Output $Wrapper
  }
  'status' {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $task) { Write-Output 'not installed'; break }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Output "task: $TaskName  state: $($task.State)  next: $($info.NextRunTime)  last: $($info.LastRunTime) ($($info.LastTaskResult))"
    $log = Join-Path $DataDir 'logs\last_run.json'
    if (Test-Path $log) { Get-Content $log }
  }
  'uninstall' {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "uninstalled: $TaskName"
  }
}
