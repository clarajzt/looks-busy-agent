# Setup：连接数据源（逐项授权）

原则：每个数据源在连接前，向用户展示四件事——**读取什么、用什么权限、数据存在哪、如何撤销**——取得明确同意后才执行。用户跳过任何来源都不影响其余功能。所有连接都是只读的；写操作（日历块）在 automation 阶段单独授权。

## 0. 基本信息（一次问完）

- 岗位与工作范围：日报只写这个范围内的事，写入 `config.work_scope`，按用户岗位来，2-4 条即可。示例配置里的三条是占位（以 `<` 开头），必须替换。例子：投资机构同事可写「融资与 LP 服务」「项目沟通、筛选、尽调与内部评审」「投后管理与信息更新」；职能岗可写「行政与差旅」「财务报销与对账」「法务合同流转」。
- 时区（默认 `Asia/Shanghai`）与每日运行时间（默认 `21:00`，用户可改）。
- 当前使用的 Agent 与运行形态（Codex、Claude Code Desktop/CLI、TRAE/TraeWork、WorkBuddy、Cola 或其他）。
- 日报保存目录（默认 `~/.local/share/looks-busy-agent/reports`）。
- 要启用哪些数据源：飞书日历 / 工作邮箱（腾讯企业邮箱）/ 本地文件 / Agent 对话记录。

## 1. 飞书日历（lark-cli，用户扫码授权，只读）

先向用户说明：Agent 将通过飞书官方 CLI 以**用户本人身份**读取个人日历；只申请两个只读 scope：`calendar:calendar:readonly` 与 `calendar:calendar.event:read`（不要用 `--domain calendar`，它会一并申请创建/删除日程等写权限）；授权在飞书官方页面完成，随时可在飞书「设置 → 安全 → 授权管理」撤销。

lark-cli 需要先绑定一个飞书应用。两种方式，**优先 A**：

### A. 公司统一应用（推荐给非技术同事）

管理员一次性在飞书开放平台创建企业自建应用并开通上述两个权限（步骤见仓库 README「管理员一次性准备」），然后把 App ID 和 App Secret 通过安全渠道交给同事。同事侧：

1. 安装：`lark-cli --version`。未安装则需要 Node.js（macOS：从 https://nodejs.org 下载 LTS 安装包，或 `brew install node`），再 `npm install -g @larksuite/cli`。
2. 绑定应用（**Secret 不经过对话**）：让用户本人打开「终端」运行 `lark-cli config init`，按提示选择「已有应用」并粘贴 App ID / App Secret；lark-cli 会把 Secret 存到本机安全存储。Agent 不要代替用户输入 Secret，也不要把它写进任何文件。
3. 默认身份改为用户：`lark-cli config default-as user`。不做这一步时 lark-cli 默认以 bot 身份调用，读不到个人日历。
4. 登录（Device Flow，Agent 友好写法）：

   ```bash
   lark-cli auth login --scope "calendar:calendar:readonly calendar:calendar.event:read" --no-wait --json
   ```

   从输出取 verification URL 和 device code：URL 原样展示给用户，并用 `lark-cli auth qrcode "<url>" --ascii` 附上二维码；用户在手机/浏览器里确认后，再执行 `lark-cli auth login --device-code <code>` 完成。
5. 只读冒烟：`lark-cli calendar +agenda --as user --jq '.meta.count'`（只输出数量；不要用裸 `--jq length`，那数的是信封的键）。只向用户报告「连接成功，今天有 N 个日程」，不要把日程详情打印进终端记录。

### B. 没有公司应用：个人自建应用

后台运行 `lark-cli config init --new`，从输出提取授权链接原样展示（可配 `lark-cli auth qrcode`），用户在浏览器里创建自己的应用；之后从上面第 3 步继续。企业若限制普通成员创建应用，此路会失败，请回到 A 找管理员。

### 判定与停止

- 成功以 JSON 信封的 `ok == true` 为准（不是进程退出码）。
- 提示 `missing_scopes`、应用未发布或管理员审批 pending：停止该来源，把开放平台/审批链接给用户，不要循环重试。
- 每日运行读取日历时同样使用 `--as user`，并带当天 `--start/--end`（ISO 8601，含时区偏移）。

同机已安装 `lark-shared` 等官方 lark skills 时，认证细节以它们为准。

## 2. 腾讯企业邮箱（IMAP，只读）

先向用户说明：只读收件箱和已发送在当天的邮件头与正文摘要，使用 `BODY.PEEK` 不改已读状态、不动附件；密码只进系统 Keychain，不进配置文件、聊天记录或日志。

以下操作让**用户自己**完成（不要让密码经过对话）：

1. 网页登录企业邮箱 → 「设置 → 收发信设置」开启 IMAP/SMTP 服务。
2. 「设置 → 安全设置」生成**客户端专用密码**（企业开启安全登录时必须用专用密码，登录密码无效）。
3. macOS 在终端把密码存入 Keychain（`-w` 不带值会隐藏式交互输入）：

   ```bash
   security add-generic-password -a "you@company.com" -s looks-busy-agent-email -w
   ```

4. Windows 在 PowerShell 存入凭据管理器（`/pass` 不带值会隐藏式输入）：

   ```powershell
   cmdkey /generic:looks-busy-agent-email /user:you@company.com /pass
   ```

   其他系统使用当前 Agent 支持的 secret store，或由用户在启动 Agent 前设置 `password_env` 指向的环境变量；不得把密码写入配置或 Skill。
5. 在 config 的 `sources.email` 填 `username`（邮箱地址）；`host` 默认 `imap.exmail.qq.com:993`。凭据查找顺序：macOS Keychain service（若可用）→ 环境变量（`password_env`）。

冒烟测试：`python3 scripts/collect_email.py --config <path> --check` —— 只登录并确认邮箱可选取，不拉正文。失败时常见原因：未开 IMAP、用了登录密码而非专用密码。

## 3. 本地文件

让用户**明确列出**要扫描的目录（如 `~/work`），写入 `sources.local_files.roots`。规则：

- 只看这些目录里最近 `lookback_hours`（默认 24h）内修改的文档类文件（md/doc/pdf/xls/ppt/代码等）。
- 永远排除：隐藏文件、`.env`、含 `key/secret/credential/token` 的文件、`node_modules`、浏览器与系统资料目录。
- 目录是 git 仓库时优先 `git log --since` 读当天提交信息，比文件 mtime 更准。

## 4. Agent 对话记录

默认 `current_thread_only: true`，只用当前 Agent 已提供的对话上下文。用户想跨会话汇总时：

- 优先使用当前 Agent 自带的任务/历史读取能力；不要猜测未确认的内部存储格式。
- 若没有历史读取 API，只在用户明确同意后读取该产品公开文档确认的会话目录。让用户选择项目与时间范围，只读当天部分；向用户说明会话里可能有敏感内容，日报只提炼工作事项、不引用原文。

## 5. 写入配置并校验

从 [config.example.json](config.example.json) 复制到 `~/.config/looks-busy-agent/config.json`，按上面结果修改，然后：

```bash
python3 scripts/check_config.py --config ~/.config/looks-busy-agent/config.json
python3 scripts/doctor.py          # 一键体检：配置、飞书、邮箱、定时、最近日报；只报状态不报内容
```

## 6. 预览运行

以 run 模式跑一次：产出日报草稿 + 拟创建的未来日历块清单（只预览，不写入）。用户确认口径（详略、匿名程度、语气）后，才按 [automation.md](automation.md) 启用定时与日历写入。
