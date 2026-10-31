# OmniQQ-MCP: LLOneBot QQ 自动化控制与 OneBot 11 MCP Server

[![MCP Streamable HTTP](https://img.shields.io/badge/MCP-Streamable%20HTTP-blue)](https://modelcontextprotocol.io)
[![LLOneBot](https://img.shields.io/badge/LLOneBot-OneBot%2011-00BFFF)](https://github.com/LLOneBot/LuckyLilliaBot)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-green)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

**OmniQQ-MCP (`omniqq-mcp`)** 是基于 **LLOneBot（LuckyLillia / LLBot）** 与 **OneBot 11 协议**的 QQ 控制与事件读取 MCP Server。

本版本完成两项核心迁移：

1. **后端从 NapCatQQ 迁移到 LLOneBot**：支持 LLBot CLI / Desktop / LiteLoaderQQNT 插件目录，自动写入 LLOneBot `default_config.json` 与 `data/config_<uin>.json`，并通过 LLBot WebUI（默认 3080）完成扫码登录；
2. **传输从本地 stdio 改为 Streamable HTTP 远程 MCP**：默认在 `http://127.0.0.1:8000/mcp` 暴露单端点，支持 `Mcp-Session-Id` 会话、GET SSE、DELETE 终止会话、`MCP-Protocol-Version` 协商、Bearer Token 鉴权与 CORS。

同时保留 **全部 74 个具名工具** 与 **8 个 NapCat 旧工具名兼容别名**，已有提示词/客户端无需修改即可继续调用；需要兼容旧宿主时仍可用 `--transport stdio` 启动。

通过本工具，Claude Desktop、Cursor、Windsurf、Cline、Antigravity、OpenAI Agents 等任意支持 MCP 的 AI 智能体可以远程获得**操控 QQ 的双手与实时感知的眼睛**——涵盖私聊/群聊收发、合并转发、群管禁言踢人、群文件上传下载、图片 OCR、好友与资料管理、二维码扫码登录、多 QQ 账号热切换、环境自检与自动部署。

---

## 🔍 核心检索关键词 (Keywords / Topics)

`omniqq` `omniqq-mcp` `mcp` `model-context-protocol` `mcp-server` `streamable-http` `remote-mcp` `llonebot` `llbot` `lucky-lillia` `onebot` `onebot11` `qq-bot` `ntqq` `ai-agent` `cursor-mcp` `antigravity` `chatbot` `python-mcp`

---

## ✨ 功能特色

### 1. 🦾 LLOneBot 原生后端
- **多形态安装兼容**：自动识别 LLBot CLI（`bin/llbot/default_config.json`）、Desktop（`llbot.exe`）与 LiteLoaderQQNT 插件（`llbot.js` / `data/config_<uin>.json`）布局；
- **精确配置写入**：按 LLOneBot v8.x 配置结构启用 `ob11.enable`，并维护 `ob11.connect` 中的 HTTP（3000）与 WebSocket（3001）服务、Token 和 `reportSelfMessage`；
- **进程控制安全**：只终止安装目录内的 `llbot` / `llbot.exe` / `pmhq` / `node llbot.js` 进程，**不会**在启动时误杀用户日常使用的 QQ；
- **WebUI 与二维码**：同时支持终端二维码、`data/temp/qrcode.png` 缓存与 LLBot WebUI（3080）扫码登录；
- **自动部署/升级**：从 `LLOneBot/LuckyLilliaBot` GitHub Release 按平台选择 `LLBot-CLI-linux-*` / `LLBot-Desktop-win-*` / `LuckyLillia.zip`，失败自动回滚。

### 2. 🌐 Streamable HTTP 远程 MCP（默认传输）
- **单端点**：`POST /mcp`（JSON-RPC）、`GET /mcp`（SSE 保活流）、`DELETE /mcp`（终止会话）、`GET /health`（健康检查）；
- **会话管理**：`initialize` 返回 `Mcp-Session-Id`；无效或过期会话返回 404；支持 `--stateless` 无状态模式；
- **协议协商**：支持 `2025-06-18` / `2025-03-26` / `2024-11-05`，并校验 `MCP-Protocol-Version`；
- **安全边界**：可选 `--auth-token` Bearer 鉴权、Origin 校验、默认仅监听 `127.0.0.1`；
- **stdio 兼容**：`--transport stdio` 保留原有逐行 JSON-RPC 模式，方便旧宿主或本地调试。

### 3. 🧰 全量工具与旧名兼容
- **26 个默认 core 工具**：20 个 LLOneBot 核心工具 + 6 个高频 NapCat 旧名兼容别名；
- **82 个 full 工具**：74 个具名工具 + 8 个旧名别名，全部支持 `tools/call`；
- **万能透传**：`call_llonebot_api`（旧名 `call_napcat_api`）可调用 LLOneBot 全部 OneBot 11 标准与扩展 Action，并内置 `list_supported_llonebot_actions` 目录；
- **完整 MCP 元数据**：所有工具均带 `annotations`、`outputSchema`、参数 Schema 与使用/替代/副作用说明。

### 4. ⚡ 实时事件与多账号
- **WebSocket 环形缓存**：后台维护 LLOneBot 正向 WS 连接，按 `self_id` 隔离缓存最近 200 条消息、通知与请求；
- **多账号热切换**：扫描 `data/config_*.json` 与 `qq-session-*.json`，支持免扫码切换、默认账号持久化；
- **事件钩子**：`MessageBuffer.register_event_hook(callback)` 支持同步/异步回调，按顺序投递并带背压保护。

---

## 🧰 工具矩阵 (Tools Overview)

| 模块分类 | 数量 | 代表性工具 |
| :--- | :---: | :--- |
| **1. 环境运维、登录与多账号** | 9 | `check_llonebot_environment`, `deploy_or_update_llonebot`, `list_qq_accounts`, `switch_qq_account`, `login_new_qq_by_qrcode`, `get_login_qrcode_image`, `start_llonebot_service`, `stop_llonebot_service`, `restart_llonebot_service` |
| **2. 消息收发与社交互动** | 15 | `send_private_msg`, `send_group_msg`, `send_msg`, `delete_msg`, `get_msg`, `get_forward_msg`, `send_private_forward_msg`, `send_group_forward_msg`, `send_like`, `send_poke`, `set_msg_emoji_like`, `mark_msg_as_read`, `set_essence_msg`, `delete_essence_msg`, `get_essence_msg_list` |
| **3. 群组管理与治理控制** | 14 | `set_group_kick`, `set_group_ban`, `set_group_whole_ban`, `set_group_admin`, `set_group_card`, `set_group_name`, `set_group_special_title`, `set_group_leave`, `set_group_add_request`, `set_group_todo`, `set_group_portrait`, `get_group_at_all_remain`, `get_group_shut_list`, `get_group_system_msg` |
| **4. 文件与富媒体操作** | 11 | `upload_private_file`, `upload_group_file`, `delete_group_file`, `delete_group_folder`, `get_group_root_files`, `get_group_files_by_folder`, `get_group_file_url`, `get_private_file_url`, `get_group_file_system_info`, `ocr_image`, `download_file` |
| **5. 好友关系与账号设置** | 10 | `get_friend_list`, `get_friends_with_category`, `get_stranger_info`, `delete_friend`, `set_friend_remark`, `set_friend_add_request`, `set_qq_profile`, `set_qq_avatar`, `set_self_longnick`, `set_online_status` |
| **6. 信息与历史记录查询** | 10 | `get_login_info`, `get_group_list`, `get_group_info`, `get_group_member_info`, `get_group_member_list`, `get_group_honor_info`, `get_friend_msg_history`, `get_group_msg_history`, `get_recent_contact`, `get_llonebot_status` |
| **7. 实时消息与通知感知** | 3 | `get_recent_messages`, `get_recent_notices_and_requests`, `clear_recent_messages_buffer` |
| **8. 全量接口万能透传** | 2 | `call_llonebot_api`, `list_supported_llonebot_actions` |

### NapCat 旧工具名兼容别名

| 旧名（仍然可用） | 新名 |
| :--- | :--- |
| `check_napcat_environment` | `check_llonebot_environment` |
| `deploy_or_update_napcat` | `deploy_or_update_llonebot` |
| `start_napcat_service` | `start_llonebot_service` |
| `stop_napcat_service` | `stop_llonebot_service` |
| `restart_napcat_service` | `restart_llonebot_service` |
| `call_napcat_api` | `call_llonebot_api` |
| `list_supported_napcat_actions` | `list_supported_llonebot_actions` |
| `get_napcat_status` | `get_llonebot_status` |

默认 `tools/list` 暴露 26 个 core 工具（含 6 个高频旧名别名）；设置 `OMNIQQ_TOOL_PROFILE=full` 后暴露 82 个工具（含全部 8 个旧名别名）。旧名未列出时仍可通过 `tools/call` 直接调用。

---

## 🚀 快速开始

### 1. 安装依赖

```bash
python -m pip install -r requirements.txt
# 或
python -m pip install .
```

依赖仅为 `aiohttp`、`websockets>=14,<17`、`psutil`。入口命令为 `omniqq-mcp`。

### 2. 启动 LLOneBot 后端

任选一种方式，确保 OneBot HTTP 3000 / WebSocket 3001 可访问：

- **LLBot CLI / Desktop**：从 [LLOneBot Releases](https://github.com/LLOneBot/LuckyLilliaBot/releases) 下载，首次通过 WebUI `http://127.0.0.1:3080` 扫码登录；
- **Docker**：将容器内的 OneBot HTTP `3000` / WebSocket `3001` / WebUI `3080` 映射到宿主机，再用 `LLONEBOT_HTTP_URL` / `LLONEBOT_WS_URL` 指向这些端口；
- **已有安装**：设置 `LLONEBOT_HOME` 指向 LLBot 安装目录，或设置 `LLONEBOT_HTTP_URL` / `LLONEBOT_WS_URL` 连接远程实例。

> LLOneBot 的 WebUI 密码位于 `bin/llbot/data/webui_token.txt`（旧插件布局为 `data/webui_token.txt`）。

### 3. 启动 MCP Server（Streamable HTTP）

```bash
python llonebot_mcp/run_mcp.py \
  --host 127.0.0.1 \
  --port 8000 \
  --path /mcp
```

默认 URL：`http://127.0.0.1:8000/mcp`。非回环地址部署时请设置鉴权：

```bash
python llonebot_mcp/run_mcp.py --host 0.0.0.0 --port 8000 --auth-token "your-secret-token"
```

健康检查：`curl http://127.0.0.1:8000/health`。

需要兼容旧宿主时：

```bash
python llonebot_mcp/run_mcp.py --transport stdio
```

### 4. 在 MCP 客户端中挂载

通用远程配置（不同客户端的字段名可能为 `url` / `type`，以客户端文档为准）：

```json
{
  "mcpServers": {
    "omniqq-mcp": {
      "type": "streamable-http",
      "url": "http://127.0.0.1:8000/mcp",
      "headers": {
        "Authorization": "Bearer your-secret-token"
      }
    }
  }
}
```

未启用 `--auth-token` 时可省略 `headers`。

### 5. 首次自然语言交互示例

- *“检查一下我的 LLOneBot 环境状态，如果没部署帮我自动部署一下”*
- *“启动二维码扫码登录一个新的 QQ 账号”*
- *“查看已保存的 QQ 账号，并切换到另一个账号上线”*
- *“看看我最近收到了哪些 QQ 消息，给群 12345678 发一条通知”*

---

## ⚙️ 环境变量

| 变量 | 说明 | 默认值 |
| :--- | :--- | :--- |
| `LLONEBOT_HOME` / `LLBOT_HOME` | LLOneBot 安装目录（含 `llbot.js` / `bin/llbot` / `start.sh`） | `%LOCALAPPDATA%\OmniQQ-MCP\llbot` 或 `~/.local/share/OmniQQ-MCP/llbot` |
| `LLONEBOT_DATA_DIR` | 覆盖 LLBot 数据目录（`config_<uin>.json`、`webui_token.txt`） | `<config_root>/data` |
| `LLONEBOT_HTTP_URL` / `LLONEBOT_WS_URL` | 外部 LLOneBot OneBot 端点 | `http://127.0.0.1:3000` / `ws://127.0.0.1:3001` |
| `LLONEBOT_HTTP_PORT` / `LLONEBOT_WS_PORT` | 本地端口 | `3000` / `3001` |
| `LLONEBOT_WEBUI_PORT` | LLBot WebUI 端口 | `3080` |
| `LLONEBOT_LAUNCH_COMMAND` | 自定义启动命令（如 `./start.sh`、Docker exec） | 自动探测 |
| `LLONEBOT_OFFLINE_ZIP` | 离线安装包路径 | `<LLONEBOT_HOME>/LLBot-offline.zip` |
| `LLONEBOT_MCP_HOST` / `LLONEBOT_MCP_PORT` | Streamable HTTP 监听地址/端口 | `127.0.0.1` / `8000` |
| `LLONEBOT_MCP_PATH` | 单端点路径 | `/mcp` |
| `LLONEBOT_MCP_AUTH_TOKEN` | Bearer Token | 空 |
| `LLONEBOT_MCP_ALLOWED_ORIGINS` | 允许的浏览器 Origin，逗号分隔 | 仅 localhost/127.0.0.1 |
| `LLONEBOT_MCP_STATELESS` | `1/true` 启用无状态模式 | 关闭 |
| `LLONEBOT_MCP_SESSION_TIMEOUT` | 会话空闲超时秒数 | `1800` |
| `OMNIQQ_TOOL_PROFILE` | `core`（默认）或 `full` | `core` |
| `OMNIQQ_ONEBOT_TOKEN_FILE` | OneBot Token 私有文件 | 用户数据目录 |
| `OMNIQQ_LOG_FILE` / `OMNIQQ_ACTIVITY_LOG` | 应用日志 / 审计日志 | 用户数据目录 `logs/` |

为平滑迁移，旧的 `NAPCAT_HTTP_URL`、`NAPCAT_WS_URL`、`NAPCAT_HTTP_PORT`、`NAPCAT_WS_PORT` 仍可作为回退变量读取。

---

## 🔐 安全说明

- 默认只监听 `127.0.0.1`；对外暴露时必须设置 `--auth-token`，并建议放在反向代理/内网之后。
- `Origin` 默认仅允许 localhost；跨域浏览器接入需显式配置 `--allowed-origins`。
- OneBot Token 保存在用户数据目录并通过 LLOneBot 配置同步；请勿提交到仓库或放入提示词。
- 审计日志只记录工具名、数字目标、状态码与消息 ID，不记录消息正文、文件内容、完整参数或凭据。
- 部署/升级前需 `confirmed=False` 预览并获得用户明确同意后，再以 `confirmed=True` 执行；失败自动回滚。

---

## v2.0.0 更新（2026-10-31）

- **后端迁移**：NapCatQQ → LLOneBot（LuckyLillia/LLBot），支持 CLI / Desktop / LiteLoader 插件布局与 WebUI 扫码。
- **传输迁移**：默认改为 MCP Streamable HTTP 单端点（会话、SSE、DELETE、协议版本协商、Bearer 鉴权、CORS）；stdio 仅作兼容模式。
- **工具兼容**：74 个具名工具全部保留；8 个 NapCat 旧工具名注册为兼容别名；默认 core 26 个、full 82 个。
- **配置与部署**：写入 LLOneBot `ob11.connect` 结构；GitHub Release 按平台选择 CLI/Desktop 包；保留 `data` 目录并支持失败回滚。
- **安全修复**：不再在启动时误杀 QQ；进程清理精确匹配 LLBot/PMHQ 安装路径；Streamable HTTP 会话、Origin 与鉴权边界加固。

完整历史见 [CHANGELOG.md](./CHANGELOG.md)，兼容性与回滚说明见 [可靠性说明](docs/RELIABILITY_REVIEW.md)。

## 📄 开源协议

本项目基于 [MIT License](./LICENSE) 开源。
