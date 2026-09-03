# 自动化：跨 Coding Agent 调度与飞书日历写入

## 原则

内容生成遵循同一份 `SKILL.md`；安装发现、定时运行和结果交付按当前 Agent 适配。默认每天当地时间 21:00 运行，用户可改。用户随时要求生成日报时，立即执行 `run`，不影响下一次定时任务。

setup 必须先识别当前运行环境，再选择本环境真实可用的调度器。不得因为某个平台没有 Codex heartbeat 就断言整个 Skill 不可用，也不得只写配置或说明文字后声称自动化已启用。

## 调度适配器（有序决策）

先运行 `python3 scripts/detect_scheduler.py`（只读探测，`--json` 给机器读），按它的 `recommended` 走。优先级固定为：

1. **当前 Agent 的原生持久调度器**（Codex App heartbeat、TraeWork 自动化、WorkBuddy 自动化、Cola 闹钟、Claude Desktop 任务）。
2. **同机其他已安装 Agent 的原生调度器**：告诉用户可以切到那个 Agent 创建定时任务。
3. **OS 兜底**：macOS `scripts/schedule_launchd.sh`，Windows `scripts/schedule_windows.ps1`。只在 1、2 都没有时使用。
4. **仅手动 `run`**。

只配置当前环境需要的一种：

| 环境 | 首选调度器 | 默认交付 | 关键限制 |
|---|---|---|---|
| Codex App | 当前任务 heartbeat | 当前 Codex 任务 | 创建后回读卡片与 ACTIVE 状态 |
| Claude Code Desktop | Desktop scheduled task | 对应 Claude 任务 | 选择可访问本地文件的 Desktop 任务 |
| Claude Code CLI / Codex CLI（macOS） | `scripts/schedule_launchd.sh`（launchd 无头运行） | 日报文件 + 运行日志；下次打开 Agent 时可读 | 睡眠中错过会在唤醒后补跑，关机则丢；`/schedule` cloud routine 不能读取本机邮箱、Keychain 或 lark-cli，不要用它 |
| Claude Code CLI / Codex CLI（Windows） | `scripts/schedule_windows.ps1`（任务计划无头运行） | 同上 | 已启用「唤醒运行」+「错过后尽快运行」；Codex CLI 在 Windows 官方仍标实验性，优先 Claude Code；Windows 任务计划未在真机验证；Python 需可用且安装 tzdata |
| TRAE / TraeWork（豆包编程入口） | 自动化定时任务 | TraeWork 任务结果 | 在 Work 模式创建，并确认 Skill 与本地目录权限 |
| WorkBuddy | 自动化任务 | WorkBuddy 任务；可选推送小程序 | 选择本 Skill、工作空间与“推送到小程序”设置 |
| Cola | 智能闹钟 | 创建闹钟的会话 | Cola 桌面端需保持运行；定时任务可调用已安装 Skill |
| 其他 Agent Skills 客户端 | 其原生持久调度器 | 原生任务结果 | 必须验证能访问所需本地来源 |

若当前产品只有普通聊天、不能读取本地 Skill、不能运行脚本或没有定时能力，只支持手动 `run`。例如“豆包”若指普通聊天 App 而不是 TRAE/TraeWork，就不能完成本地邮箱、飞书 CLI 和无人值守定时闭环。

## macOS 本地定时（launchd，任意 CLI Agent 通用兜底）

需要读本机邮箱、Keychain 或 lark-cli 时，云端 routine 都做不到；macOS 上用随 Skill 附带的脚本：

```bash
bash scripts/schedule_launchd.sh install                 # 时间取 config.run_time，Agent 自动检测 claude/codex
bash scripts/schedule_launchd.sh install --time 22:30 --agent claude
bash scripts/schedule_launchd.sh run-once                # 立即跑一次（试跑 / 补日报）
bash scripts/schedule_launchd.sh status
bash scripts/schedule_launchd.sh uninstall
```

脚本生成 `~/Library/LaunchAgents/com.looks-busy-agent.daily.plist` 和 wrapper `~/.local/share/looks-busy-agent/run_daily.sh`。实际运行统一交给 `scripts/run_daily.py`：

- 以配置时区确定日期、选定日历与起止时间，每次创建独立的 `raw/<日期>/run-*/` 快照；失败或停用的来源不会复用旧数据。
- Claude 仅开放 Read/Glob/Grep，禁用继承的 MCP 配置；Codex 使用 read-only sandbox、`--skip-git-repo-check` 和 `--ignore-user-config`，不继承用户配置中的 MCP 服务（保留既有规则文件与账户登录，不加载自定义 provider/profile）。两者均不持久保存生成会话。模型只返回日报文本，程序检查日期和栏目后，通过 `save_report.py` 的共享保存函数写入私有文件。
- 只有文件保存成功才返回成功。错误退出、空回复或缺少栏目均返回非零；运行状态在 `logs/last_run.json`，不记录来源正文或凭据。同一天已有日报时直接返回既有路径；需要改稿请在交互会话明确覆盖。
- OS 兜底只输出日历候选；日历写入由交互会话或已授权的原生 Agent 任务执行。独立 CLI 任务没有原会话历史，必须有邮箱、日历或本地工作目录等可用来源。
- CLI 运行最多 30 分钟。原始快照按 `privacy.raw_retention_days` 清理。OS 定时跟随系统时区，安装时校验其与配置时区是否一致；不一致时改用支持指定时区的原生调度器。

安装成功后把 `config.schedule` 写成 `{"provider": "launchd", "agent": "claude", "job_id": "com.looks-busy-agent.daily"}`，并 `run-once` 试跑一次确认有日报文件产生。launchd 只落盘不推送，向用户说明「到点会生成，打开 Agent 说『看今天的日报』即可读」；要推送到飞书需另行授权。睡眠：`StartCalendarInterval` 在睡眠中错过的触发会在唤醒后合并补跑一次（man launchd.plist），只有关机才真正错过。

## Windows 本地定时（任务计划，同样是兜底）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\schedule_windows.ps1 install -Time 21:00 -Agent claude
powershell -ExecutionPolicy Bypass -File scripts\schedule_windows.ps1 run-once
powershell -ExecutionPolicy Bypass -File scripts\schedule_windows.ps1 status | uninstall
```

与 launchd 共用 run_daily.py：wrapper 在 `%LOCALAPPDATA%\looks-busy-agent\run_daily.ps1`，先预采集，再只读生成并由程序保存；不依赖 PowerShell 的长参数拼接。任务同时启用 WakeToRun 与 StartWhenAvailable（缺一个笔记本睡眠时就跑不了；从睡眠唤醒还要 BIOS 允许定时唤醒）。邮箱密码用 `cmdkey /generic:looks-busy-agent-email /user:<邮箱> /pass` 存入凭据管理器，`collect_email.py` 通过系统 API 读回。`config.schedule.provider` 写 `schtasks`。

## 定时 Prompt

使用当前 Agent 的自然调用方式，不假设所有平台都支持 `$skill-name`：

```text
使用 looks-busy-agent 的 run 模式，读取已授权的当天工作来源，生成经过相关性筛选和敏感信息处理的工作日报，在本任务或会话中交付，并用 scripts/save_report.py --date <今天> 保存到已配置的私有目录（必须看到脚本打印的 saved: 路径）。日报只包含工作推进与未来计划，不披露所用工具或自动化过程。不得未经授权对外发送；仅在已单独授权时执行幂等的未来日历写入。某个来源不可用时使用其他已授权来源继续生成，并注明覆盖范围。
```

## 必须实际创建并验证

1. 查找当前环境中名为「看起来很忙日报」的既有任务；存在则更新，不存在则创建，避免重复。
2. 使用 `config.timezone` 和 `config.run_time` 创建每日任务，状态设为启用。
3. 做一次“立即运行”或最小测试运行，确认任务确实加载本 Skill、能访问已授权来源并产生日报，且 `report.output_dir` 里出现了 `<日期>.md`（没有文件 = 保存步骤没执行，把 save_report.py 写进定时 Prompt）。
4. 回读任务，核对名称、时间、时区、交付位置和启用状态；把平台名、调度类型和任务 ID 写入 `config.schedule`。
5. 只有创建、试跑和回读都成功后才能告诉用户自动日报已启用。失败时保留手动 `run`，明确缺的是安装发现、来源权限、调度还是交付。

## 交付适配

- 首选当前 Agent 的任务/会话结果，因为无需新增外发权限。
- 平台支持移动端结果同步时，可由用户主动开启，例如 WorkBuddy 小程序或 Cola 渠道。
- 若用户要求发到飞书个人、群聊或邮箱，先确认固定接收方和发送权限；这属于额外外发授权。
- 只保存本地文件不等于“用户已收到”。若平台没有会话交付能力，必须选择用户确认过的通知或外发渠道，或者明确标记为“仅生成、未自动送达”。

## 飞书日历写入（需单独授权，幂等）

首次启用前展示拟创建的事件清单（标题、时间、说明），取得用户确认，并把 `calendar.write_enabled` 设为 `true`。此后自动运行遵守：

1. 只创建或更新描述中带 `calendar.marker` 的未来工作块；绝不修改、移动或删除用户或他人创建的日程。
2. 写前读取目标日已有日程；同名带标记的块已存在则跳过，与既有日程冲突则跳过或换时段。
3. 创建命令按当前 lark-cli 版本的帮助信息执行，事件标题使用概括表述，描述末尾附标记。
4. 每天最多 `calendar.max_blocks` 个块，只写未来 `plan_horizon_days` 天。
5. 自动运行遇到高风险确认时停止该写操作，留给下次交互会话处理。

## 变更控制与留痕

新增数据源、扩大本地文件目录、增加飞书权限、改变外部接收方或启用新写操作，都必须重新取得用户确认。

每次运行记录时间、Agent/调度器、来源状态、生成文件路径、交付状态和日历写入结果。原始采集数据按 `privacy.raw_retention_days` 清理。
