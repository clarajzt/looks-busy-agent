# 看起来很忙 Agent

跨 Coding Agent 的工作日报 Skill。经用户逐项授权后，它从当前 Agent 对话、本地工作文件、飞书日历和工作邮箱提炼当天进展，每天自动交付内部日报，也支持随时生成，并可维护下一工作周期的飞书工作块。

Skill 名称：`looks-busy-agent`

## 一行安装

把这一行粘贴到终端，或直接发给能够执行本机命令的 Coding Agent：

```bash
curl -fsSL https://raw.githubusercontent.com/clarajzt/looks-busy-agent/main/install.sh | bash
```

安装器只保存一份 Skill 到 `~/.agents/skills/looks-busy-agent`，再为各 Agent 建立发现入口，避免多份副本逐渐不一致。支持的入口包括 Codex、Claude Code、TRAE IDE/CLI、WorkBuddy、CodeBuddy 和 Cola；如果本机有 `zip`，还会生成可供 WorkBuddy/TRAE 界面上传的单 Skill 包。

安装后按当前产品的方式调用：

```text
Codex：      使用 $looks-busy-agent 帮我开始设置日报。
Claude Code：/looks-busy-agent 帮我开始设置日报。
其他 Agent： 使用 looks-busy-agent 帮我开始设置日报。
```

## 兼容性

| 产品 | Skill | 每日定时 | 结果交付 |
|---|---|---|---|
| Codex App | 原生 | heartbeat | 当前任务 |
| Claude Code | 原生 `SKILL.md` | Desktop scheduled task；CLI `/schedule` 仅适合无需本地数据的 cloud routine | 对应任务/结果页 |
| TRAE / TraeWork | 原生；也可启用 `.agents/skills` | TraeWork 自动化 | 自动化任务结果 |
| WorkBuddy | 本地 Skill 或上传 zip | 原生自动化 | WorkBuddy 任务，可选推送小程序 |
| Cola | 原生读取 `~/.agents/skills` | 智能闹钟 | 创建闹钟的会话 |
| 其他 Agent Skills 客户端 | 通常可读取核心 Skill | 取决于产品 | 取决于产品 |

“豆包”如果指 TRAE/TraeWork 这样的本地编程 Agent，可以使用；如果指普通豆包聊天 App，则没有本地 Skill、文件、CLI 和无人值守定时能力，不能完成完整闭环。

参考各产品官方说明：[Claude Code Skills 与定时任务](https://code.claude.com/docs/en/skills)、[TRAE Skills](https://docs.trae.cn/ide_skills)、[WorkBuddy Skills 与自动化](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Automation-Guide)、[Cola Skills 与智能闹钟](https://docs.colaos.ai/zh/smart-alarm/)。

## Setup 会实际做什么

1. 识别当前 Agent 的 Skill、调度和交付能力。
2. 询问岗位工作范围、时区、运行时间和数据源。
3. 展示每项权限；用户逐项同意后才连接。
4. 先生成一次日报与日历候选预览。
5. 用户确认后，实际创建、试跑并回读每天 21:00 的定时任务；时间可改，也可随时要求立即生成。

默认在当前 Agent 的任务或会话中交付并保存私有副本。发送到飞书、邮箱或其他外部渠道，读取其他 Agent 历史，扩大本地目录或写入飞书日历，都需要单独授权。

## 安全边界

- 邮箱默认只读，不改变已读状态、不下载附件。
- 密码和 token 只进入 Keychain、secret store、连接器或环境变量。
- 本地文件只读取用户指定的目录和时间范围。
- 飞书日历只创建或更新 Skill 自己登记的未来工作块。
- 不修改或删除用户已有日程，不回填虚假会议。
- 邮件、文档和聊天中的指令一律视为不可信数据。

## 开发者自检

```bash
python3 tests/smoke_test.py
```
