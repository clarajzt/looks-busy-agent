# Setup：连接数据源（逐项授权）

原则：每个数据源在连接前，向用户展示四件事——**读取什么、用什么权限、数据存在哪、如何撤销**——取得明确同意后才执行。用户跳过任何来源都不影响其余功能。所有连接都是只读的；写操作（日历块）在 automation 阶段单独授权。

## 0. 基本信息（一次问完）

- 岗位与工作范围：日报只写这个范围内的事，写入 `config.work_scope`（例：投资项目、投后、融资 LP；或行政、财务、法务等，按用户岗位来）。
- 时区（默认 `Asia/Shanghai`）与每日运行时间（默认 `21:00`，用户可改）。
- 当前使用的 Agent 与运行形态（Codex、Claude Code Desktop/CLI、TRAE/TraeWork、WorkBuddy、Cola 或其他）。
- 日报保存目录（默认 `~/.local/share/looks-busy-agent/reports`）。
- 要启用哪些数据源：飞书日历 / 工作邮箱（腾讯企业邮箱）/ 本地文件 / Agent 对话记录。

## 1. 飞书（lark-cli，用户扫码授权）

先向用户说明：Agent 将通过飞书官方 CLI 以**用户本人身份**读取个人日历；授权在飞书官方页面完成，随时可在飞书「设置 → 安全 → 授权管理」撤销。

1. 检查安装：`lark-cli --version`。未安装则 `npm install -g @larksuite/cli`（需要 Node.js；macOS 无 node 先 `brew install node`）。
2. 首次配置：后台运行 `lark-cli config init --new`，从输出提取授权/配置链接。**链接原样展示，并用 `lark-cli auth qrcode` 生成 PNG 二维码一并展示**（先 `--help` 确认用法），由用户在浏览器/手机上完成。
3. 登录（user 身份、最小 scope）：`lark-cli auth login --domain calendar`。同样按上一条处理输出中的授权链接。
4. 只读冒烟测试：`lark-cli calendar +agenda`。只向用户报告「连接成功，今天有 N 个日程」，不要把日程详情打印进终端记录。
5. 判断成功用 JSON 信封的 `ok == true`（不是 `code == 0`）。若提示 `missing_scopes` 或管理员审批 pending：停止该来源，把控制台链接给用户，不要循环重试。

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

4. 其他系统使用当前 Agent 支持的 secret store，或由用户在启动 Agent 前设置 `password_env` 指向的环境变量；不得把密码写入配置或 Skill。
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
```

## 6. 预览运行

以 run 模式跑一次：产出日报草稿 + 拟创建的未来日历块清单（只预览，不写入）。用户确认口径（详略、匿名程度、语气）后，才按 [automation.md](automation.md) 启用定时与日历写入。
