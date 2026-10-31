"""
LLOneBot OneBot 11 异步通信客户端 (HTTP API 调用 + WebSocket 实时事件监听 + 自愈启动)
"""
import os
import json
import asyncio
import logging
import random
from urllib.parse import urlsplit
from typing import Any, Optional

import aiohttp
import websockets

from .config import (
    DEFAULT_HTTP_URL,
    DEFAULT_WS_URL,
    DEFAULT_HTTP_PORT,
    DEFAULT_WS_PORT,
    LLBOT_HOME,
    get_current_account,
    get_webui_url,
    ensure_onebot_config,
    resolve_install_root,
    is_llbot_installed,
    find_qrcode_image,
    export_qrcode_to_desktop,
    is_port_listening,
    launch_llonebot_process,
)
from .message_buffer import MessageBuffer
from .token_store import load_onebot_token, ensure_onebot_token, save_verified_onebot_token

logger = logging.getLogger("LLOneBotMCP.Client")


class LLOneBotClient:
    """LLOneBot (OneBot 11) 标准与扩展协议通信核心客户端"""

    def __init__(
        self,
        http_url: str = DEFAULT_HTTP_URL,
        ws_url: str = DEFAULT_WS_URL,
        token: Optional[str] = None,
        auto_heal: bool = True,
        buffer_maxlen: int = 200
    ):
        self.http_url = http_url.rstrip("/")
        self.ws_url = ws_url.rstrip("/")
        self._use_external_token_file = token is None and bool(
            os.environ.get('OMNIQQ_ONEBOT_TOKEN_FILE') or os.environ.get('LLONEBOT_ONEBOT_TOKEN_FILE')
        )
        self.token = (load_onebot_token() if self.uses_local_runtime() or self._use_external_token_file
                      else '') if token is None else token
        self.auto_heal = auto_heal
        self.buffer = MessageBuffer(maxlen=buffer_maxlen)

        self._session: Optional[aiohttp.ClientSession] = None
        self._ws_task: Optional[asyncio.Task] = None
        self.on_ws_connected = None
        self.on_close = None
        self._running = False
        self._ws_connected = False
        self._start_lock = asyncio.Lock()

    def _get_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if not self.token and (self.uses_local_runtime() or self._use_external_token_file):
            self.token = load_onebot_token()
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def uses_local_runtime(self) -> bool:
        endpoint = urlsplit(self.http_url)
        return endpoint.hostname in ('localhost', '127.0.0.1', '::1') and \
            (endpoint.port or 80) == DEFAULT_HTTP_PORT

    async def ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    @staticmethod
    def _config_token_candidates(account: str) -> list[str]:
        """从 LLOneBot 的 config_<uin>.json / default_config.json 读取候选 Token。"""
        candidates: list[tuple[str, str]] = []
        roots = []
        try:
            from .config import find_installations
            roots = find_installations()
        except Exception:
            roots = []
        if LLBOT_HOME not in roots:
            roots.append(LLBOT_HOME)
        for root in roots:
            for path in (
                os.path.join(root, 'data', f'config_{account}.json'),
                os.path.join(root, 'bin', 'llbot', 'data', f'config_{account}.json'),
                os.path.join(root, 'default_config.json'),
                os.path.join(root, 'bin', 'llbot', 'default_config.json'),
            ):
                candidates.append((root, path))
        result: list[str] = []
        for root, path in candidates:
            if not os.path.isabs(path) or not os.path.isfile(path):
                continue
            try:
                with open(path, encoding='utf-8') as stream:
                    data = json.load(stream)
            except Exception:
                continue
            ob11 = data.get('ob11') if isinstance(data, dict) else None
            connect = ob11.get('connect') if isinstance(ob11, dict) else None
            if isinstance(connect, list):
                for item in connect:
                    if isinstance(item, dict) and item.get('type') in ('http', 'ws') and item.get('token'):
                        token = str(item['token'])
                        if token not in result:
                            result.append(token)
                continue
            # 迁移兼容：旧 NapCat / 旧 LLOneBot 配置结构
            network = data.get('network') if isinstance(data, dict) else None
            servers = network.get('httpServers') if isinstance(network, dict) else None
            if isinstance(servers, list):
                for item in servers:
                    if isinstance(item, dict) and item.get('token'):
                        token = str(item['token'])
                        if token not in result:
                            result.append(token)
        return result

    async def _recover_local_token(self) -> bool:
        """仅用本地 LLOneBot 配置中经只读探测验证的候选 Token 恢复鉴权。"""
        if not self.uses_local_runtime():
            return False
        account = get_current_account()
        if not account:
            return False
        session = await self.ensure_session()
        for candidate in self._config_token_candidates(account):
            if not candidate or candidate == self.token:
                continue
            try:
                async with session.post(self.http_url + '/get_login_info', json={},
                                        headers={'Authorization': f'Bearer {candidate}'},
                                        timeout=aiohttp.ClientTimeout(total=5)) as response:
                    data = await response.json()
                if response.status != 200 or data.get('status') != 'ok' or data.get('retcode', 0) != 0:
                    continue
                if str((data.get('data') or {}).get('user_id')) != account:
                    continue
                save_verified_onebot_token(candidate)
                self.token = candidate
                logger.warning('检测到 LLOneBot OneBot Token 已变化，已用只读查询验证并同步本地凭据')
                return True
            except Exception as exc:
                logger.debug('LLOneBot Token 候选验证失败：%s', type(exc).__name__)
        return False

    async def start_llonebot_process(self, uin: Optional[str] = None, wait_seconds: int = 25) -> dict[str, Any]:
        if not self.uses_local_runtime():
            return {'status': 'failed', 'error': '当前使用外部 OneBot URL，不能启动本地 LLOneBot'}
        async with self._start_lock:
            return await self._start_llonebot_process(uin, wait_seconds)

    # 兼容旧方法名
    async def start_napcat_process(self, uin: Optional[str] = None, wait_seconds: int = 25) -> dict[str, Any]:
        return await self.start_llonebot_process(uin, wait_seconds)

    async def _start_llonebot_process(self, uin: Optional[str], wait_seconds: int) -> dict[str, Any]:
        """
        启动本地 LLOneBot 服务：
        1. 端口已监听则直接返回；
        2. 未安装则返回诊断信息；
        3. 写入带 Token 的 LLOneBot 账号配置后拉起进程；
        4. 若需要扫码登录，自动导出二维码到桌面并返回 WebUI 地址。
        """
        if await asyncio.to_thread(is_port_listening, DEFAULT_HTTP_PORT):
            return {
                "status": "already_running",
                "http_port": DEFAULT_HTTP_PORT,
                "ws_port": DEFAULT_WS_PORT,
                "webui_url": get_webui_url(),
                "message": "LLOneBot OneBot 服务已在运行中，无需重复启动。"
            }

        root = await asyncio.to_thread(resolve_install_root)
        if not root or not await asyncio.to_thread(is_llbot_installed, root):
            return {
                "status": "not_installed",
                "error": "未找到 LLOneBot 运行环境。请调用 `check_llonebot_environment` 诊断，"
                         "或调用 `deploy_or_update_llonebot` 部署，或设置 LLONEBOT_HOME 指向已有安装目录。"
            }

        self.token = ensure_onebot_token()
        account = str(uin or get_current_account()).strip()
        if account:
            if not await asyncio.to_thread(ensure_onebot_config, account, DEFAULT_HTTP_PORT,
                                           DEFAULT_WS_PORT, self.token):
                return {'status': 'failed', 'error': '无法写入带 Token 的 LLOneBot OneBot 配置'}

        # 清理旧二维码缓存，准确识别本次启动是否生成新二维码
        old_qr = await asyncio.to_thread(find_qrcode_image, root)
        if old_qr and os.path.exists(old_qr):
            try:
                os.remove(old_qr)
            except Exception:
                pass

        ok, message, _process = await asyncio.to_thread(
            launch_llonebot_process, account or None, None, False
        )
        if not ok:
            return {
                'status': 'failed',
                'error': message,
                'webui_url': get_webui_url(),
            }

        desktop_qr: Optional[str] = None
        for _ in range(max(1, int(wait_seconds)) * 2):
            if await asyncio.to_thread(is_port_listening, DEFAULT_HTTP_PORT):
                return {
                    "status": "started",
                    "uin": account,
                    "http_port": DEFAULT_HTTP_PORT,
                    "ws_port": DEFAULT_WS_PORT,
                    "webui_url": get_webui_url(),
                    "message": f"LLOneBot 服务已成功拉起并监听端口 {DEFAULT_HTTP_PORT}/{DEFAULT_WS_PORT}。"
                }
            current_qr = await asyncio.to_thread(find_qrcode_image, root)
            if current_qr and os.path.getsize(current_qr) > 100:
                desktop_qr = await asyncio.to_thread(export_qrcode_to_desktop, current_qr)
                break
            await asyncio.sleep(0.5)

        if not desktop_qr:
            current_qr = await asyncio.to_thread(find_qrcode_image, root)
            if current_qr:
                desktop_qr = await asyncio.to_thread(export_qrcode_to_desktop, current_qr)

        if desktop_qr:
            return {
                "status": "starting_pending_login",
                "login_required": True,
                "uin": account,
                "qrcode_path": desktop_qr,
                "desktop_qrcode_path": desktop_qr,
                "cache_qrcode_path": await asyncio.to_thread(find_qrcode_image, root),
                "webui_url": get_webui_url(),
                "message": (
                    f"检测到需要登录 QQ，已将 LLOneBot 生成的二维码图片放到桌面：{desktop_qr}，"
                    f"请打开图片扫描登录，或访问 WebUI：{get_webui_url()}。"
                )
            }

        return {
            "status": "starting_pending_login",
            "uin": account,
            "webui_url": get_webui_url(),
            "message": "LLOneBot 进程已启动，但 OneBot 端口尚未就绪（可能需要扫码登录或正在初始化）。"
                       f"可访问 WebUI ({get_webui_url()}) 查看登录状态。"
        }

    async def call_api(
        self,
        action: str,
        params: Optional[dict[str, Any]] = None,
        timeout: float = 30.0,
        _auth_retry: bool = True,
    ) -> dict[str, Any]:
        """
        调用任意 LLOneBot OneBot 11 HTTP API 接口。
        :param action: 接口名称 (如 'send_private_msg', 'get_group_list' 等)
        :param params: JSON 参数字典
        :param timeout: 超时时间（秒）
        """
        action_clean = action.strip().lstrip("/")
        payload = params or {}
        clean_payload = {k: v for k, v in payload.items() if v is not None}

        local_default = self.uses_local_runtime()
        if self.auto_heal and local_default and not await asyncio.to_thread(is_port_listening, DEFAULT_HTTP_PORT):
            heal_res = await self.start_llonebot_process(wait_seconds=15)
            if heal_res.get("status") in ("not_installed", "failed"):
                return {
                    "status": "failed",
                    "retcode": -1,
                    "action": action_clean,
                    "error": heal_res.get("error"),
                    "environment_hint": "请调用 check_llonebot_environment 查看详情，"
                                        "或调用 deploy_or_update_llonebot 部署环境。"
                }
            if heal_res.get("login_required"):
                return {
                    "status": "failed",
                    "retcode": -2,
                    "action": action_clean,
                    "login_required": True,
                    "qrcode_path": heal_res.get("qrcode_path"),
                    "desktop_qrcode_path": heal_res.get("desktop_qrcode_path"),
                    "webui_url": heal_res.get("webui_url"),
                    "error": heal_res.get("message"),
                    "advice": f"请打开桌面上的二维码图片 ({heal_res.get('desktop_qrcode_path') or heal_res.get('qrcode_path')}) "
                              f"扫描登录，或访问 LLBot WebUI ({heal_res.get('webui_url')})。"
                }

        url = f"{self.http_url}/{action_clean}"
        session = await self.ensure_session()

        try:
            async with session.post(
                url,
                json=clean_payload,
                headers=self._get_headers(),
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                text = await resp.text()
                if resp.status in (401, 403) and local_default and _auth_retry and await self._recover_local_token():
                    return await self.call_api(action, params, timeout, _auth_retry=False)
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    return {
                        "status": "failed",
                        "retcode": resp.status,
                        "action": action_clean,
                        "error": f"非 JSON 响应 (HTTP {resp.status}): {text[:300]}"
                    }
                if not isinstance(data, dict):
                    return {'status': 'failed', 'retcode': -103, 'action': action_clean,
                            'error': 'OneBot 响应必须为 JSON 对象'}
                if resp.status >= 400:
                    return {**data, 'status': 'failed', 'retcode': resp.status, 'action': action_clean}
                return data
        except (aiohttp.ClientConnectorError, ConnectionRefusedError) as e:
            current_qr = find_qrcode_image()
            if local_default and current_qr:
                desktop_qr = await asyncio.to_thread(export_qrcode_to_desktop, current_qr)
                return {
                    "status": "failed",
                    "retcode": -100,
                    "action": action_clean,
                    "login_required": True,
                    "qrcode_path": desktop_qr or current_qr,
                    "desktop_qrcode_path": desktop_qr,
                    "webui_url": get_webui_url(),
                    "error": f"无法连接到 LLOneBot HTTP 服务 ({self.http_url}): 检测到需要扫码登录。",
                    "advice": f"已将 LLOneBot 登录二维码放到桌面：{desktop_qr or current_qr}，"
                              f"请扫描登录，或访问 WebUI ({get_webui_url()})。"
                }
            return {
                "status": "failed",
                "retcode": -100,
                "action": action_clean,
                "error": f"无法连接到 LLOneBot HTTP 服务 ({self.http_url}): {e}",
                "advice": "请确认 LLOneBot 已启动并登录 QQ，或调用 start_llonebot_service / "
                          "check_llonebot_environment 诊断环境、使用 switch_qq_account / "
                          "login_new_qq_by_qrcode 启动登录。"
            }
        except asyncio.TimeoutError:
            return {
                "status": "failed",
                "retcode": -101,
                "action": action_clean,
                "error": f"调用接口 {action_clean} 超时 ({timeout}s)"
            }
        except Exception as e:
            return {
                "status": "failed",
                "retcode": -102,
                "action": action_clean,
                "error": f"调用接口 {action_clean} 发生异常: {e}"
            }

    async def _ws_loop(self):
        """后台 WebSocket 实时事件监听协程 (支持自动断线重连)"""
        retry_delay = 1.0
        loop = asyncio.get_running_loop()
        while self._running:
            connected_at = None
            try:
                headers = self._get_headers()
                async with websockets.connect(
                    self.ws_url,
                    additional_headers=headers if headers else None,
                    ping_interval=20,
                    ping_timeout=20
                ) as ws:
                    self._ws_connected = True
                    connected_at = loop.time()
                    logger.info(f"成功连接 LLOneBot WebSocket 事件流: {self.ws_url}")
                    if self.on_ws_connected:
                        await self.on_ws_connected()
                    async for raw_msg in ws:
                        if not self._running:
                            break
                        try:
                            event = json.loads(raw_msg)
                            if isinstance(event, dict) and event.get('retcode') == 1403:
                                self._ws_connected = False
                                await self._recover_local_token()
                                break
                            await self.buffer.push_event_async(event)
                        except Exception as e:
                            logger.warning(f"解析 LLOneBot WS 事件失败: {e}")
            except Exception as e:
                if getattr(getattr(e, 'response', None), 'status_code', None) in (401, 403):
                    await self._recover_local_token()
                logger.warning('LLOneBot WebSocket 连接中断 (%s)', e)
            finally:
                self._ws_connected = False
            if self._running:
                if connected_at is not None and loop.time() - connected_at >= 30:
                    retry_delay = 1.0
                delay = min(30.0, retry_delay * random.uniform(0.8, 1.2))
                logger.info('LLOneBot WebSocket %.1f 秒后重连', delay)
                await asyncio.sleep(delay)
                retry_delay = min(30.0, retry_delay * 2)

    def start_background_ws(self):
        """启动后台 WebSocket 监听任务"""
        if not self._running:
            self._running = True
            self._ws_task = asyncio.create_task(self._ws_loop())

    async def close(self):
        """优雅关闭客户端连接与后台任务"""
        self._running = False
        self._ws_connected = False
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        if self._session and not self._session.closed:
            await self._session.close()
        await self.buffer.close()
        if self.on_close:
            self.on_close()

    @property
    def is_ws_connected(self) -> bool:
        return self._ws_connected


# 兼容旧类名，方便已有 Python 集成继续使用
NapCatClient = LLOneBotClient
