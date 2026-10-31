# OmniQQ-MCP v2.0.0 compatibility and reliability

## Compatibility

| Component | Supported range or behavior |
| --- | --- |
| Python | 3.10 or newer; Windows CI checks 3.10, 3.11 and 3.12. |
| websockets | `>=14,<17`; uses `additional_headers`. |
| MCP transport | Streamable HTTP is the default: single endpoint (`/mcp`), `Mcp-Session-Id`, GET SSE, DELETE, `MCP-Protocol-Version`. Protocol versions `2025-06-18`, `2025-03-26`, `2024-11-05`. |
| MCP stdio | Optional compatibility mode (`--transport stdio`); JSON-RPC 2.0 line protocol with concurrent requests and read-only cancellation. |
| OneBot | OneBot 11 HTTP and WebSocket served by LLOneBot (LLBot CLI/Desktop/LiteLoader plugin). |
| Local runtime | LLOneBot CLI, LLBot Desktop, or LiteLoaderQQNT plugin layout; deployment, QR login, account switching and process management run on Windows/Linux/macOS where LLBot itself is supported. |
| Tool names | 74 LLOneBot-named tools remain; 8 NapCat legacy tool names are registered as compatibility aliases. Core profile exposes 26 tools; full profile exposes 82. |

Automated tests use mocks and temporary local endpoints, including a real local Streamable HTTP endpoint. They do not establish live QQ login, live message delivery, or compatibility with every MCP client and LLOneBot release. Actual Action support depends on the installed LLOneBot version; the static Action catalog is not a live capability probe.

## Transport behavior

Streamable HTTP accepts a single JSON-RPC message per POST and returns `application/json` or 202 for notifications. `initialize` issues `Mcp-Session-Id`; subsequent requests without a session receive 400, and unknown/expired sessions receive 404. DELETE terminates a session. GET opens an SSE keepalive stream, and `/health` reports transport and OneBot WebSocket status. `--stateless` disables session issuance for simple clients.

stdio permits eight active requests and up to 64 pending requests. Read-only requests can be cancelled using `notifications/cancelled`; writes and lifecycle operations finish and return their actual result. If a request is cancelled before its coroutine starts, the server still emits a `-32800` cancellation response.

Recent messages, notices and requests are held in memory per account `self_id`. Switching accounts selects the corresponding cache. Events without `self_id` are ignored once an account is selected. Caches do not persist across process restarts.

Event hooks execute in order through a queue of at most 200 records. Synchronous hooks run in a worker thread. Backpressure pauses reads when the queue is full; hooks are not durable across process crashes.

## User data and authentication

New installations use `%LOCALAPPDATA%\OmniQQ-MCP` on Windows, or `~/.local/share/OmniQQ-MCP` otherwise. Set `LLONEBOT_HOME` to an existing LLBot installation to use its `default_config.json` and `data/` directory. The legacy `current_account.txt` path is read as a migration fallback; NapCat runtime directories are not reused.

Override locations with `LLONEBOT_HOME`, `LLONEBOT_DATA_DIR`, `OMNIQQ_CURRENT_ACCOUNT_FILE`, `OMNIQQ_ONEBOT_TOKEN_FILE`, `OMNIQQ_LOG_FILE` and `OMNIQQ_ACTIVITY_LOG`. Token files and managed account configuration use restrictive Windows ACLs. HTTP and WebSocket use the same Token. On an HTTP 401/403, a candidate from LLOneBot `ob11.connect` configuration is adopted only after a successful read-only login-info probe.

For a remote LLOneBot deployment, set `LLONEBOT_HTTP_URL` and `LLONEBOT_WS_URL`, and provide the matching private Token file. Remote endpoints do not trigger local runtime deployment or process management.

MCP authentication is separate from the OneBot Token. Set `LLONEBOT_MCP_AUTH_TOKEN` or `--auth-token` when binding outside `127.0.0.1`. Browser origins are restricted to localhost by default; use `--allowed-origins` for other web clients. Audit records omit chat text, file contents, full arguments and credentials, but retain numeric targets and message identifiers for diagnostics. Keep logs private. Application logs rotate at 5 MB with three backups; activity audits rotate at 10 MB with three backups.

## Deployment and rollback

Deployment selects the LLBot CLI/Desktop asset for the current platform from `LLOneBot/LuckyLilliaBot` releases, or uses `LLONEBOT_OFFLINE_ZIP`. It extracts into staging, checks for `llbot.js`, `llbot`/`llbot.exe`, `start.sh`, or `bin/llbot`, backs up the existing installation, preserves `data/` (accounts, sessions, Token, logs), writes LLOneBot `ob11.connect` configuration, validates the entry, and records `llbot_version.json`. Copy, configuration or validation failures return `failed` and restore the backup. Successful responses expose the backup directory when available.

Starting LLOneBot never kills the user's standalone QQ processes; LLBot Desktop/CLI owns its QQ instance. Process cleanup matches `llbot`, `llbot.exe`, `pmhq`, `pmhq.exe`, or `node <install>/llbot.js` within known installation roots. Actual QQ login readiness still requires LLBot WebUI or a scanned QR code; check `get_login_info` after starting.
