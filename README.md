# 看起来很忙 Agent

跨 Coding Agent 的工作日报 Skill。经用户逐项授权后，它从飞书日历、工作邮箱、当前 Agent 对话和本地工作文件提炼当天进展，每天自动生成一份可以直接交的内部日报，也支持随时生成，并可维护下一工作周期的飞书工作块。

Skill 名称：`looks-busy-agent`

## 同事上手（macOS）

前提：一台 Mac，装好任意一个 Coding Agent（Claude Code、Codex、TRAE 等）。不需要会编程，下面每一步都可以直接把话发给 Agent。

1. **安装 Skill**：把这一行粘贴到终端，或直接发给 Agent 让它执行：

   ```bash
   curl -fsSL https://raw.githubusercontent.com/clarajzt/looks-busy-agent/main/install.sh | bash
   ```

   装完会列出本机还缺什么（python3 / Node.js / lark-cli），缺什么按提示装。已经装过想更新：在末尾加 `-s -- --upgrade`。

2. **开始设置**：对 Agent 说

   ```text
   Codex：      使用 $looks-busy-agent 帮我开始设置日报。
   Claude Code：/looks-busy-agent 帮我开始设置日报。
   其他 Agent： 使用 looks-busy-agent 帮我开始设置日报。
   ```

   Agent 会问你的岗位范围和日报时间（默认 21:00），然后逐项征求同意：

   - **飞书日历**：Agent 给你一个二维码/链接，你在飞书里点同意就行（只读，随时可撤销）。第一次要先把管理员发你的 App ID / App Secret 填进终端里的 `lark-cli config init`，Agent 会告诉你怎么做。
   - **工作邮箱**：你自己在企业邮箱网页版开 IMAP、生成「客户端专用密码」，在终端敲一行命令存进 Mac 的钥匙串（Agent 给你现成命令，密码不经过对话）。
   - 本地文件、Agent 对话记录：可选，不启用也能用。

3. **看一遍预览，再开定时**：Agent 先生成一份今天的日报给你看口径；你点头后它创建每日定时任务并试跑一次。以后每天到点自动生成，随时也可以说「生成今天的日报」。

4. **哪里不对就体检**：对 Agent 说「体检」，或自己跑

   ```bash
   python3 ~/.agents/skills/looks-busy-agent/scripts/doctor.py
   ```

   它只报连接状态和数量，不会打印邮件或日程内容，并列出下一步该做什么。

## 管理员一次性准备（飞书应用）

1. 登录 [飞书开放平台](https://open.feishu.cn/app)，创建**企业自建应用**（名称如「看起来很忙日报」）。
2. 「权限管理」里只开通两个**用户身份的只读**权限：`calendar:calendar:readonly`、`calendar:calendar.event:read`。不要开日程写权限。
3. 按 lark-cli 官方说明开启用户身份授权（Device Flow）；发布版本并通过管理员审批。可用 `lark-cli auth scopes` 核对已开通的权限。
4. 把 **App ID** 和 **App Secret** 通过安全渠道（飞书私聊阅后即焚、当面输入等）交给同事；不要放进群文件、文档或本仓库。
5. 同事撤销：飞书「设置 → 安全 → 授权管理」；管理员下线：开放平台停用该应用。

没有公司应用时，Skill 也支持每人 `lark-cli config init --new` 自建应用，但企业若限制普通成员建应用会失败。

## 兼容性

| 产品 | Skill | 每日定时 | 结果交付 |
|---|---|---|---|
| Codex App | 原生 | heartbeat | 当前任务 |
| Claude Code CLI / Codex CLI（macOS） | 原生 `SKILL.md` | 附带的 `schedule_launchd.sh`（launchd；睡眠错过会在唤醒后补跑） | 日报文件；打开 Agent 说「看今天的日报」 |
| Claude Code CLI（Windows） | 原生 `SKILL.md` | 附带 `schedule_windows.ps1`，需 Windows 真机试跑确认 | 同上 |
| Claude Code Desktop | 原生 `SKILL.md` | Desktop scheduled task | 对应任务 |
| TRAE / TraeWork | 原生；也可启用 `.agents/skills` | TraeWork 自动化 | 自动化任务结果 |
| WorkBuddy | 本地 Skill 或上传 zip | 原生自动化 | WorkBuddy 任务，可选推送小程序 |
| Cola | 原生读取 `~/.agents/skills` | 智能闹钟 | 创建闹钟的会话 |
| 其他 Agent Skills 客户端 | 通常可读取核心 Skill | 取决于产品 | 取决于产品 |

Claude Code CLI 的 `/schedule` 是云端 routine，读不到本机邮箱、钥匙串和 lark-cli，不要用它跑日报。"豆包"如果指 TRAE/TraeWork 这样的本地编程 Agent，可以使用；如果指普通豆包聊天 App，则没有本地 Skill、文件、CLI 和无人值守定时能力，不能完成完整闭环。Windows 暂无一行安装器：让 Agent 下载仓库 zip 并把 `skills/looks-busy-agent` 复制到 `%USERPROFILE%\.claude\skills\`（或对应 Agent 的 skills 目录）即可；邮箱密码存 Windows 凭据管理器，定时用 `schedule_windows.ps1`。Codex CLI 在 Windows 官方仍标实验性，优先 Claude Code。

参考各产品官方说明：[Claude Code Skills 与定时任务](https://code.claude.com/docs/en/skills)、[TRAE Skills](https://docs.trae.cn/ide_skills)、[WorkBuddy Skills 与自动化](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Automation-Guide)、[Cola Skills 与智能闹钟](https://docs.colaos.ai/zh/smart-alarm/)、[lark-cli](https://www.npmjs.com/package/@larksuite/cli)。

## 安装器做了什么

只保存一份 Skill 到 `~/.agents/skills/looks-busy-agent`，再为各 Agent 建立发现入口（软链接），避免多份副本逐渐不一致。Codex 与 Claude Code 总是链接；TRAE / WorkBuddy / CodeBuddy 只在本机已安装时链接。本机有 `zip` 时还会生成可供 WorkBuddy/TRAE 界面上传的单 Skill 包。`--upgrade` 会保留旧版本的时间戳备份。

## Setup 会实际做什么

1. 识别当前 Agent 的 Skill、调度和交付能力。
2. 询问岗位工作范围、时区、运行时间和数据源。
3. 展示每项权限；用户逐项同意后才连接，并分别做最小只读测试。
4. 先生成一次日报与日历候选预览。
5. 用户确认后，实际创建、试跑并回读每天 21:00 的定时任务；时间可改，也可随时要求立即生成。

原生 Agent 任务默认在对应任务或会话中交付并保存私有副本。OS 兜底仅生成本地日报和日历候选，不执行日历写入；新启动的 CLI 任务无法读取原会话，需启用其他已授权来源。发送到飞书、邮箱或其他外部渠道，读取其他 Agent 历史，扩大本地目录或写入飞书日历，都需要单独授权。

## 安全边界

- 邮箱默认只读，不改变已读状态、不下载附件。
- 飞书只申请两个只读日历 scope，以用户本人身份访问，随时可撤销。
- 密码和 token 只进入 Keychain、secret store、连接器或环境变量；App Secret 由用户本人在终端输入，不经过 Agent 对话。
- 本地文件只读取用户指定的目录和时间范围。
- 飞书日历只创建或更新 Skill 自己登记的未来工作块；不修改或删除用户已有日程，不回填虚假会议。
- 邮件、文档和聊天中的指令一律视为不可信数据。

## 验证范围

安装与升级、权限配置、邮件正文分段采集、日历参数、日报保存和无人值守运行都有合成数据回归测试。Windows 任务计划及各第三方 Agent 的原生定时仍需在目标产品中试跑，安装成功不代表已经自动交付。OS 定时使用系统时区；与配置时区不一致时安装会停止，改用支持指定时区的原生任务。

## 开发者自检

```bash
python3 tests/smoke_test.py
python3 -m unittest discover -s tests -p 'test_*.py'
```
