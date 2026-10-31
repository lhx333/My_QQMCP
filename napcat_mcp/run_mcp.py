"""
NapCat MCP Server 启动入口。

默认以 Streamable HTTP 远程传输运行（单端点 /mcp）；需要兼容旧宿主时可显式使用
`--transport stdio`。支持直接被 Claude Desktop / Cursor / Windsurf / Antigravity 等
MCP 宿主通过远程 URL 接入。
"""
import os
import sys
import asyncio
import logging
from logging.handlers import RotatingFileHandler
import argparse
import signal

# 确保项目根目录在 sys.path 中
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 强制将 Windows 下的 stdin / stdout 设为 UTF-8，确保 JSON-RPC 中文无乱码
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 日志严禁输出到 stdout（stdio 模式 stdout 专用于 MCP JSON-RPC 通信）
from napcat_mcp.config import USER_DATA_DIR  # noqa: E402
LOG_FILE = os.environ.get('OMNIQQ_LOG_FILE') or os.path.join(USER_DATA_DIR, 'logs', 'napcat_mcp.log')
log_handlers = [logging.StreamHandler(sys.stderr)]
try:
    os.makedirs(os.path.dirname(os.path.abspath(LOG_FILE)), exist_ok=True)
    log_handlers.append(RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=3, encoding='utf-8'))
except OSError as exc:
    sys.stderr.write(f'OmniQQ 文件日志不可用: {exc}\n')
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s]: %(message)s",
    handlers=log_handlers
)

from napcat_mcp.server import NapCatMCPServer, export_antigravity_schemas  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="NapCatQQ (OneBot 11) Model Context Protocol (MCP) Server")
    parser.add_argument(
        "--transport",
        choices=("streamable-http", "stdio"),
        default=os.environ.get("NAPCAT_MCP_TRANSPORT", "streamable-http"),
        help="MCP 传输方式，默认 streamable-http 远程模式；stdio 仅用于兼容旧宿主"
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("NAPCAT_MCP_HOST", "127.0.0.1"),
        help="Streamable HTTP 监听地址，默认 127.0.0.1（远程部署可设为 0.0.0.0）"
    )
    parser.add_argument(
        "--port", type=int,
        default=int(os.environ.get("NAPCAT_MCP_PORT", "8000")),
        help="Streamable HTTP 监听端口，默认 8000"
    )
    parser.add_argument(
        "--path",
        default=os.environ.get("NAPCAT_MCP_PATH", "/mcp"),
        help="Streamable HTTP 单端点路径，默认 /mcp"
    )
    parser.add_argument(
        "--auth-token",
        default=os.environ.get("NAPCAT_MCP_AUTH_TOKEN", ""),
        help="可选 Bearer Token；非回环地址部署时强烈建议设置"
    )
    parser.add_argument(
        "--allowed-origins",
        default=os.environ.get("NAPCAT_MCP_ALLOWED_ORIGINS", ""),
        help="允许的浏览器 Origin，逗号分隔；默认仅允许 localhost/127.0.0.1"
    )
    parser.add_argument(
        "--stateless",
        action="store_true",
        default=os.environ.get("NAPCAT_MCP_STATELESS", "").lower() in ("1", "true", "yes"),
        help="无状态模式：不签发 Mcp-Session-Id，所有请求共享默认会话"
    )
    parser.add_argument(
        "--session-timeout", type=float,
        default=float(os.environ.get("NAPCAT_MCP_SESSION_TIMEOUT", "1800")),
        help="空闲会话清理秒数，默认 1800"
    )
    parser.add_argument(
        "--export-schemas",
        type=str,
        default="",
        help="导出工具 JSON Schema 到指定目录后退出"
    )
    return parser


async def _run_http(server: NapCatMCPServer, args) -> None:
    from napcat_mcp.transport import StreamableHTTPTransport

    allowed_origins = [origin.strip() for origin in (args.allowed_origins or '').split(',')
                       if origin.strip()]
    auth_token = args.auth_token or None
    if args.host not in ('127.0.0.1', 'localhost', '::1') and not auth_token:
        sys.stderr.write(
            "[WARN] 正在非回环地址上运行且未设置 --auth-token，"
            "任何能访问该端口的人都可以控制你的 QQ。请设置 bearer token 或使用反向代理鉴权。\n"
        )

    server.client.start_background_ws()
    transport = StreamableHTTPTransport(
        server=server,
        host=args.host,
        port=args.port,
        path=args.path,
        auth_token=auth_token,
        allowed_origins=allowed_origins,
        stateless=args.stateless,
        session_timeout=args.session_timeout,
    )
    await transport.start()
    logger = logging.getLogger("NapCatMCP.Run")
    logger.info("Streamable HTTP MCP 端点: http://%s:%s%s", args.host, transport.port, args.path)
    if auth_token:
        logger.info("已启用 Bearer Token 鉴权")
    if args.stateless:
        logger.info("已启用无状态模式（不签发 Mcp-Session-Id）")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_stop(*_args):
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except (NotImplementedError, RuntimeError, ValueError):
            pass

    try:
        await stop_event.wait()
    finally:
        await transport.stop()
        await server.client.close()


def main():
    parser = _build_parser()
    args = parser.parse_args()

    if args.export_schemas:
        count = export_antigravity_schemas(args.export_schemas)
        sys.stderr.write(f"[OK] 成功导出 {count} 个 MCP 工具 Schema 到: {args.export_schemas}\n")
        return

    server = NapCatMCPServer()
    try:
        if args.transport == 'stdio':
            asyncio.run(server.run_stdio())
        else:
            asyncio.run(_run_http(server, args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
