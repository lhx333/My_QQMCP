"""
MCP Streamable HTTP 传输实现（基于 aiohttp）。

协议要点：
- 单端点同时支持 POST（JSON-RPC 请求/通知）、GET（SSE 服务器消息流）与 DELETE（终止会话）；
- initialize 响应通过 `Mcp-Session-Id` 建立会话，后续请求需携带该头；
- POST 响应使用 `application/json`，通知/客户端响应返回 202 Accepted；
- 校验 `Origin`、`MCP-Protocol-Version`，并可选 Bearer Token 鉴权。
"""
import asyncio
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlsplit

from aiohttp import web

logger = logging.getLogger("LLOneBotMCP.Transport")

SESSION_HEADER = "Mcp-Session-Id"
PROTOCOL_HEADER = "MCP-Protocol-Version"
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


@dataclass
class _Session:
    session_id: str
    protocol_version: Optional[str] = None
    created_at: float = field(default_factory=time.monotonic)
    last_seen: float = field(default_factory=time.monotonic)
    active: dict = field(default_factory=dict)
    pending: set = field(default_factory=set)
    slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(8))
    closed: bool = False
    closed_event: asyncio.Event = field(default_factory=asyncio.Event)


class StreamableHTTPTransport:
    """MCP Streamable HTTP 单端点服务器。"""

    def __init__(
        self,
        server,
        host: str = "127.0.0.1",
        port: int = 8000,
        path: str = "/mcp",
        auth_token: Optional[str] = None,
        allowed_origins: Optional[list[str]] = None,
        stateless: bool = False,
        session_timeout: float = 1800.0,
        protocol_versions: Optional[tuple[str, ...]] = None,
    ):
        self.server = server
        self.host = host
        self.port = int(port)
        self.path = path if path.startswith('/') else f'/{path}'
        self.auth_token = auth_token or None
        self.allowed_origins = set(allowed_origins or [])
        self.stateless = bool(stateless)
        self.session_timeout = float(session_timeout)
        if protocol_versions:
            self.protocol_versions: tuple[str, ...] = tuple(protocol_versions)
        else:
            from .server import SUPPORTED_PROTOCOL_VERSIONS
            self.protocol_versions = tuple(SUPPORTED_PROTOCOL_VERSIONS)
        self.sessions: dict[str, _Session] = {}
        self._default_session = _Session(session_id="stateless")
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._reaper: Optional[asyncio.Task] = None
        self._closed = asyncio.Event()

    # ------------------------------------------------------------------ lifecycle
    def build_app(self) -> web.Application:
        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.router.add_route('OPTIONS', self.path, self.handle_options)
        app.router.add_route('POST', self.path, self.handle_post)
        app.router.add_route('GET', self.path, self.handle_get)
        app.router.add_route('DELETE', self.path, self.handle_delete)
        app.router.add_route('GET', '/health', self.handle_health)
        if self.path != '/':
            app.router.add_route('OPTIONS', self.path + '/', self.handle_options)
            app.router.add_route('POST', self.path + '/', self.handle_post)
            app.router.add_route('GET', self.path + '/', self.handle_get)
            app.router.add_route('DELETE', self.path + '/', self.handle_delete)
        return app

    async def start(self):
        self._runner = web.AppRunner(self.build_app(), access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        self._reaper = asyncio.create_task(self._reap_sessions())
        actual_port = self.port
        try:
            sockets = getattr(self._site._server, 'sockets', None)
            if sockets:
                actual_port = sockets[0].getsockname()[1]
        except Exception:
            pass
        self.port = actual_port
        logger.info("MCP Streamable HTTP 已启动: http://%s:%s%s", self.host, actual_port, self.path)

    async def wait_closed(self):
        await self._closed.wait()

    async def stop(self):
        """停止 HTTP 服务、关闭所有会话与未完成请求。"""
        if self._closed.is_set():
            return
        self._closed.set()
        if self._reaper:
            self._reaper.cancel()
            await asyncio.gather(self._reaper, return_exceptions=True)
        self._default_session.closed = True
        self._default_session.closed_event.set()
        for session in list(self.sessions.values()):
            session.closed = True
            session.closed_event.set()
            for task in list(session.active.values()):
                if not task.done():
                    task.cancel()
            if session.pending:
                await asyncio.gather(*session.pending, return_exceptions=True)
        self.sessions.clear()
        if self._site:
            try:
                await self._site.stop()
            except Exception:
                pass
        if self._runner:
            await self._runner.cleanup()

    async def _reap_sessions(self):
        while not self._closed.is_set():
            await asyncio.sleep(30)
            now = time.monotonic()
            for session_id, session in list(self.sessions.items()):
                if now - session.last_seen > self.session_timeout:
                    session.closed = True
                    session.closed_event.set()
                    for task in list(session.active.values()):
                        if not task.done():
                            task.cancel()
                    self.sessions.pop(session_id, None)
                    logger.info("清理空闲 MCP 会话: %s", session_id)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _cors_headers(origin: Optional[str]) -> dict[str, str]:
        headers = {
            "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": (
                "Content-Type, Authorization, Mcp-Session-Id, MCP-Protocol-Version, Last-Event-ID"
            ),
            "Access-Control-Expose-Headers": "Mcp-Session-Id, MCP-Protocol-Version",
            "Access-Control-Max-Age": "86400",
        }
        if origin:
            headers["Access-Control-Allow-Origin"] = origin
            headers["Vary"] = "Origin"
        return headers

    def _origin_allowed(self, origin: Optional[str]) -> bool:
        if not origin:
            return True
        if origin in self.allowed_origins or '*' in self.allowed_origins:
            return True
        try:
            host = (urlsplit(origin).hostname or '').lower()
        except Exception:
            return False
        return host in _LOOPBACK_HOSTS

    def _check_origin(self, request: web.Request) -> Optional[web.Response]:
        origin = request.headers.get('Origin')
        if self._origin_allowed(origin):
            return None
        return self._json_error(-32000, 'Origin 不被允许', 403, origin=origin)

    def _check_auth(self, request: web.Request) -> Optional[web.Response]:
        if not self.auth_token:
            return None
        header = request.headers.get('Authorization', '')
        token = header[7:].strip() if header.startswith('Bearer ') else ''
        if not token:
            token = request.query.get('access_token', '')
        if token and hmac.compare_digest(token, self.auth_token):
            return None
        return self._json_error(-32001, '未授权：需要有效的 Bearer Token', 401)

    def _json_error(self, code: int, message: str, status: int,
                    origin: Optional[str] = None, extra: Optional[dict] = None) -> web.Response:
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": code, "message": message},
        }
        if extra:
            payload["error"].update(extra)
        headers = self._cors_headers(origin or '')
        return web.Response(text=json.dumps(payload, ensure_ascii=False),
                            content_type='application/json', status=status, headers=headers)

    def _json_response(self, data: Any, status: int = 200,
                       headers: Optional[dict[str, str]] = None,
                       origin: Optional[str] = None) -> web.Response:
        final_headers = self._cors_headers(origin or '')
        if headers:
            final_headers.update(headers)
        return web.Response(text=json.dumps(data, ensure_ascii=False),
                            content_type='application/json', status=status, headers=final_headers)

    def _accepted(self, origin: Optional[str], session: Optional[_Session] = None) -> web.Response:
        headers = self._cors_headers(origin or '')
        if session:
            headers.update(self._session_headers(session))
        return web.Response(status=202, headers=headers)

    def _protocol_ok(self, request: web.Request, fallback: Optional[str]) -> Optional[web.Response]:
        header = request.headers.get(PROTOCOL_HEADER) or fallback
        if header and header not in self.protocol_versions:
            return self._json_error(
                -32002, f"不支持的 MCP-Protocol-Version: {header}",
                400, origin=request.headers.get('Origin'),
                extra={"supported": list(self.protocol_versions)})
        return None

    def _get_session(self, request: web.Request) -> tuple[Optional[_Session], Optional[web.Response]]:
        session_id = request.headers.get(SESSION_HEADER)
        if self.stateless:
            if session_id and session_id != self._default_session.session_id:
                return None, self._json_error(-32001, '未知或已过期的 MCP 会话', 404,
                                              origin=request.headers.get('Origin'))
            return self._default_session, None
        if not session_id:
            return None, self._json_error(-32002, f'缺少 {SESSION_HEADER} 请求头，请先 initialize',
                                          400, origin=request.headers.get('Origin'))
        session = self.sessions.get(session_id)
        if not session or session.closed:
            return None, self._json_error(-32001, '未知或已过期的 MCP 会话', 404,
                                          origin=request.headers.get('Origin'))
        return session, None

    def _new_session(self) -> _Session:
        session = _Session(session_id=uuid.uuid4().hex)
        if not self.stateless:
            self.sessions[session.session_id] = session
        return session

    def _session_headers(self, session: _Session) -> dict[str, str]:
        headers = {}
        if not self.stateless:
            headers[SESSION_HEADER] = session.session_id
        if session.protocol_version:
            headers[PROTOCOL_HEADER] = session.protocol_version
        return headers

    # ------------------------------------------------------------------ handlers
    async def handle_options(self, request: web.Request) -> web.Response:
        origin = request.headers.get('Origin')
        if not self._origin_allowed(origin):
            return self._json_error(-32000, 'Origin 不被允许', 403, origin=origin)
        return web.Response(status=204, headers=self._cors_headers(origin))

    async def handle_health(self, request: web.Request) -> web.Response:
        auth_error = self._check_auth(request)
        if auth_error:
            return auth_error
        return self._json_response({
            "status": "ok",
            "transport": "streamable-http",
            "path": self.path,
            "sessions": len(self.sessions),
            "stateless": self.stateless,
            "onebot_ws_connected": bool(getattr(self.server.client, 'is_ws_connected', False)),
        }, origin=request.headers.get('Origin'))

    async def handle_post(self, request: web.Request) -> web.Response:
        origin = request.headers.get('Origin')
        if not self._origin_allowed(origin):
            return self._json_error(-32000, 'Origin 不被允许', 403, origin=origin)
        auth_error = self._check_auth(request)
        if auth_error:
            return auth_error

        content_type = request.headers.get('Content-Type', '')
        if 'application/json' not in content_type.lower():
            return self._json_error(-32700, 'Content-Type 必须为 application/json', 415, origin=origin)
        try:
            body = await request.json()
        except Exception:
            return self._json_error(-32700, 'JSON 解析错误', 400, origin=origin)

        if isinstance(body, list):
            return self._json_error(-32600, '不支持 JSON-RPC 批量请求', 400, origin=origin)
        if not isinstance(body, dict):
            return self._json_error(-32600, '请求必须为 JSON-RPC 对象', 400, origin=origin)

        method = body.get('method')
        requested_protocol = body.get('params', {}).get('protocolVersion') \
            if isinstance(body.get('params'), dict) else None

        if method == 'initialize':
            protocol_error = None
            header_version = request.headers.get(PROTOCOL_HEADER)
            if header_version and header_version not in self.protocol_versions:
                protocol_error = self._protocol_ok(request, None)
            if protocol_error:
                return protocol_error
            session = self._new_session()
            selected = self.server.negotiate_protocol_version(
                requested_protocol or header_version)
            session.protocol_version = selected
            session.last_seen = time.monotonic()
            response = await self.server.handle_request(body, protocol_version=selected)
            if response is None:
                return self._accepted(origin, session)
            headers = self._session_headers(session)
            headers[PROTOCOL_HEADER] = selected
            return self._json_response(response, headers=headers, origin=origin)

        active_session, session_error = self._get_session(request)
        if session_error:
            return session_error
        assert active_session is not None
        active_session.last_seen = time.monotonic()

        protocol_error = self._protocol_ok(request, active_session.protocol_version)
        if protocol_error:
            return protocol_error
        if active_session.protocol_version is None:
            active_session.protocol_version = request.headers.get(PROTOCOL_HEADER)

        # 客户端发来的 JSON-RPC 响应（如 sampling/roots 回调结果）
        if method is None and ('result' in body or 'error' in body):
            return self._accepted(origin, active_session)

        if method == 'notifications/initialized':
            await self.server.handle_request(body, protocol_version=active_session.protocol_version)
            return self._accepted(origin, active_session)

        if method == 'notifications/cancelled':
            self._handle_cancellation(active_session, body)
            return self._accepted(origin, active_session)

        # 其它通知不需要响应
        if body.get('id') is None:
            asyncio.create_task(self.server.handle_request(
                body, protocol_version=active_session.protocol_version))
            return self._accepted(origin, active_session)

        response = await self._dispatch(active_session, body)
        if response is None:
            return self._accepted(origin, active_session)
        return self._json_response(response,
                                   headers=self._session_headers(active_session), origin=origin)

    async def handle_get(self, request: web.Request) -> web.StreamResponse:
        origin = request.headers.get('Origin')
        if not self._origin_allowed(origin):
            return self._json_error(-32000, 'Origin 不被允许', 403, origin=origin)
        auth_error = self._check_auth(request)
        if auth_error:
            return auth_error

        session, session_error = self._get_session(request)
        if session_error:
            return session_error
        assert session is not None
        session.last_seen = time.monotonic()

        protocol_error = self._protocol_ok(request, session.protocol_version)
        if protocol_error:
            return protocol_error

        headers = self._cors_headers(origin)
        headers.update(self._session_headers(session))
        headers.update({
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        })
        response = web.StreamResponse(status=200, headers=headers)
        await response.prepare(request)
        try:
            await response.write(b": connected to omniqq-mcp streamable http\n\n")
            while not self._closed.is_set() and not session.closed:
                try:
                    await asyncio.wait_for(session.closed_event.wait(), timeout=15)
                    break
                except asyncio.TimeoutError:
                    pass
                session.last_seen = time.monotonic()
                await response.write(b": keepalive\n\n")
            await response.write_eof()
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        return response

    async def handle_delete(self, request: web.Request) -> web.Response:
        origin = request.headers.get('Origin')
        if not self._origin_allowed(origin):
            return self._json_error(-32000, 'Origin 不被允许', 403, origin=origin)
        auth_error = self._check_auth(request)
        if auth_error:
            return auth_error

        session_id = request.headers.get(SESSION_HEADER)
        if self.stateless:
            return web.Response(status=204, headers=self._cors_headers(origin))
        if not session_id:
            return self._json_error(-32002, f'缺少 {SESSION_HEADER} 请求头', 400, origin=origin)
        session = self.sessions.pop(session_id, None)
        if not session:
            return self._json_error(-32001, '未知或已过期的 MCP 会话', 404, origin=origin)
        session.closed = True
        session.closed_event.set()
        for task in list(session.active.values()):
            if not task.done():
                task.cancel()
        return web.Response(status=204, headers=self._cors_headers(origin))

    # ------------------------------------------------------------------ dispatch
    def _handle_cancellation(self, session: _Session, body: dict[str, Any]):
        params = body.get('params')
        if not isinstance(params, dict):
            params = {}
        request_id = params.get('requestId')
        if type(request_id) not in (int, str):
            return
        task = session.active.get(request_id)
        if task and not task.done() and getattr(task, 'cancellable', False):
            task.cancel()

    async def _dispatch(self, session: _Session, request: dict[str, Any]) -> Optional[dict[str, Any]]:
        request_id = request.get('id')
        method = request.get('method')
        if type(request_id) not in (int, str):
            return self.server._error_response(None, -32600, '无效的 JSON-RPC 请求 ID')
        if request_id in session.active:
            return self.server._error_response(request_id, -32600, '重复请求 ID')
        if len(session.pending) >= 64:
            return self.server._error_response(request_id, -32000, '服务器繁忙')

        async def run():
            try:
                if self.server.is_fast_method(method):
                    return await self.server.handle_request(
                        request, protocol_version=session.protocol_version)
                async with session.slots:
                    return await self.server.handle_request(
                        request, protocol_version=session.protocol_version)
            except asyncio.CancelledError:
                return self.server._error_response(request_id, -32800, '请求已取消')
            except Exception:
                logger.exception('MCP Streamable HTTP 请求调度失败')
                return self.server._error_response(request_id, -32603, '服务器内部错误')
            finally:
                session.active.pop(request_id, None)

        task: Any = asyncio.create_task(run())
        task.cancellable = self.server.is_cancellable_request(
            method, request.get('params'))
        session.active[request_id] = task
        session.pending.add(task)
        task.add_done_callback(session.pending.discard)
        return await task
