# 版本 B：零安装飞书机器人日报服务（`server/`）

给**没有任何 Coding Agent** 的同事用：只需在飞书里对机器人说「开始」，点两个按钮授权（日历只读、邮箱只读），之后每天 21:00 在飞书私信里收到一份可直接转发的日报草稿。零安装，手机也行。

与版本 A（skill）共用同一份口径：镜像里原样打包 `skills/looks-busy-agent/references/report-policy.md`（作为 LLM system prompt）和 `scripts/collect_email.py`（IMAP 采集），不分叉。

> **状态：骨架（实施顺序第 1-2 步已完成）。** 已有：配置/日志脱敏/SQLite/凭据加密/一次性签名链接/飞书 REST 客户端/CLI（`gen-key init-db healthz doctor users purge rotate-key send-test`）+ 17 个单元测试。未有：Web 注册页、每日流水线、调度器、长连接聊天指令（第 3-6 步）。`serve`/`once` 现在会明确报"尚未实现"。

## 架构一句话

单个 Python 进程（长连接收消息 / HTTP 两个页面 / 30 秒 tick 调度 / 单 worker 顺序跑用户）+ SQLite；每用户凭据 Fernet 加密落库，原始日历/邮件**不落盘**；日报只私信给本人；出错只提醒一次，永不循环重试。

## 部署前需要准备（第 0 步，由管理员完成）

| 项 | 说明 |
|---|---|
| 域名 + DNS | 一个可控 DNS 的域名或子域（如 `report.example.com`）。没有备案 → 变体 B：服务跑在 `:8443`，证书用 DNS-01 签发（Caddyfile 默认 alidns 插件，需阿里云 DNS 的 AccessKey；换 DNS 商改插件名即可）。备选变体 C：`cloudflared` 隧道，无需开端口，但手机端大陆可达性可能不稳 |
| 飞书应用 | 在版本 A 的企业自建应用上扩展：加「机器人」能力；权限 `calendar:calendar:readonly`、`calendar:calendar.event:read`、`offline_access`、`im:message:send_as_bot`、订阅 `im.message.receive_v1` 时控制台要求的读消息权限；事件订阅选**长连接**；安全设置 → 重定向 URL 填 `LBA_PUBLIC_BASE_URL/oauth/callback`；可用范围全员；发布并通过审批 |
| LLM key | 百炼**按量付费**普通 key（标准商用条款）。Token Plan 只能开发期用，`doctor` 会标红 |
| 主密钥 | `python -m lba gen-key` → 存到 `secrets/lba_master_key`（chmod 600） |
| 主机内存 | 为本服务预留约 260M（服务限 200M + Caddy 64M）；主机紧张时压缩其他容器限额或启用 swap |

实测提醒：飞书用户 refresh_token 在本租户有效期 **7 天**（不是 30 天），服务端每天预刷新一次（`LBA_KEEPALIVE_DAYS=1`）。

## 本地开发

```bash
cd server
python3 -m unittest discover -s tests -t . -v          # 无网络，全部用 fake
export LBA_SKILL_DIR=../skills/looks-busy-agent LBA_DB_PATH=./data/dev.sqlite3 LBA_MASTER_KEY=$(python3 -m lba gen-key)
python3 -m lba init-db && python3 -m lba doctor        # 缺的配置会逐条列出
python3 -m lba send-test --open-id ou_xxx              # 有真实 App ID/Secret 时验证机器人能力与可用范围
```

## 部署（云主机，变体 B）

```bash
# 独立 compose 项目目录，不影响主机上其他服务
git clone https://github.com/clarajzt/looks-busy-agent ~/looks-busy-agent
mkdir -p ~/looks-busy-server/{data,secrets} && cd ~/looks-busy-server
cp ~/looks-busy-agent/server/{docker-compose.yml,Caddyfile} . && cp ~/looks-busy-agent/server/.env.example .env
python3 -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())" > secrets/lba_master_key && chmod 600 .env secrets/lba_master_key
# 编辑 .env：LBA_LARK_APP_ID/SECRET、LBA_PUBLIC_BASE_URL=https://report.example.com:8443、LBA_CADDY_DOMAIN、ALICLOUD_ACCESS_KEY_*、LBA_LLM_*
docker compose build && docker compose up -d && docker compose exec lba-server python -m lba doctor --live
```

安全组只开 8443。日志 `docker logs lba-server`（JSON 行，open_id 已哈希，不含日报内容）。

## 实施顺序（剩余）

3. Web + 注册（`web.py` `links.py` `cards.py`）：需要第 0 步的域名与飞书应用
4. 流水线（`sources/` `prompt.py` `llm.py` `redline.py` `pipeline.py`）
5. 调度器（`scheduler.py`）
6. 长连接 + 聊天指令（`feishu_ws.py` `commands.py`）
7. 测试整合、8. 部署试用 3 天、9. 试点 → 全员

完整设计见仓库外的计划文档（数据模型、用户旅程、红线检查、风险表）。

## 依赖说明

- `httpx`：飞书与 LLM 的全部 REST 调用（超时/连接池/可注入 MockTransport 测试）
- `cryptography`：Fernet 凭据加密与密钥轮换（stdlib 无 AES）
- `lark-oapi`：仅用于长连接（WebSocket）收事件，避免自己实现协议；第 6 步才引入
