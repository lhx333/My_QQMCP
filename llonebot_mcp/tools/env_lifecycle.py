"""
环境诊断、自动部署更新、多账号管理、扫码登录与服务启停控制工具集 (LLOneBot 后端)。
"""
import os
import asyncio
from typing import Any, Optional
from . import registry
from ..client import LLOneBotClient
from ..deployer import (
    check_llonebot_environment as _check_env,
    deploy_or_update_llonebot as _deploy_llonebot,
)
from ..config import (
    DEFAULT_HTTP_PORT,
    find_qrcode_image,
    is_port_listening,
    set_current_account,
    get_available_accounts,
    get_current_account,
    get_webui_url,
    kill_llonebot_processes,
    start_qrcode_login_session,
    export_qrcode_to_desktop,
    resolve_install_root,
)


@registry.register()
async def check_llonebot_environment(client: LLOneBotClient) -> dict[str, Any]:
    """
    检查本地 LLOneBot 运行时安装状态、OneBot 端口 (3000/3001) 监听情况、LLBot WebUI、已配置账号列表，
    并请求 GitHub Release API 对比 LLOneBot 官方最新版本。
    适用场景：首次连接、排查接口调用失败或判断是否有新版本可升级时优先调用。不适用与替代：若仅需读取当前在线 QQ 的 user_id 与昵称，请改用轻量级的 `get_login_info`（无需请求 GitHub，速度更快）。
    行为与返回：纯只读无副作用操作（包含一次约 8 秒超时的外部 GitHub 版本探测）；返回 `runtime`、`service`、`accounts`、`version` 诊断结论，失败时在相应字段返回原因。兼容旧工具名 `check_napcat_environment`。
    """
    if not client.uses_local_runtime():
        probe = await client.call_api('get_version_info', {}, timeout=5.0)
        return {
            'status': 'ready' if probe.get('status') == 'ok' and probe.get('retcode', 0) == 0 else 'failed',
            'mode': 'external',
            'backend': 'llonebot',
            'http_url': client.http_url,
            'probe': probe
        }
    live_ver = None
    if is_port_listening(DEFAULT_HTTP_PORT):
        res = await client.call_api("get_version_info", {}, timeout=5.0)
        if res.get("status") == "ok":
            live_ver = res.get("data")
    return await _check_env(live_version_info=live_ver)


@registry.register()
async def deploy_or_update_llonebot(
    client: LLOneBotClient,
    action: str = "deploy",
    confirmed: bool = False,
    use_local_zip_if_available: bool = True,
    target_uin: Optional[str] = None
) -> dict[str, Any]:
    """
    自动下载、解压并部署或升级本地 LLOneBot (LLBot CLI/Desktop/LiteLoader 插件) 运行时，
    同时自动写入 OneBot 11 (3000/3001) 端口配置。
    适用场景：当 `check_llonebot_environment` 显示未安装（选 `action='deploy'`）或存在新版本需升级（选 `action='update'`）时使用。不适用与替代：若运行时已安装且仅需启动、重启或切换账号，请改用 `switch_qq_account` 或 `start_llonebot_service`，切勿重复重装。
    参数约束与两阶段门禁：强制采用两阶段确认锁——首次调用必须传 `confirmed=False` 获取只读预览计划；向用户展示计划并获得明确许可后，再传 `confirmed=True` 真正执行文件覆写。`target_uin` 若指定须为 5~11 位纯数字 QQ 号字符串。
    行为与副作用：`confirmed=True` 时会终止现有本地 LLOneBot/PMHQ 进程并覆写运行时目录（升级失败或健康检查不通过时自动回滚至旧版本备份）；仅支持本地模式，外部远程 OneBot URL 调用将直接返回 `status='failed'`。兼容旧工具名 `deploy_or_update_napcat`。
    :param action: 执行模式，枚举值 'deploy'（全新部署安装）或 'update'（备份旧版并升级，失败自动回滚），默认 'deploy'
    :param confirmed: 两阶段安全门禁开关；False 时仅返回只读部署预览计划，True 时执行真实下载解压与进程重启
    :param use_local_zip_if_available: 为 True 时优先解压本地已缓存的 LLBot 离线 Release 压缩包以避免重复网络下载，默认 True
    :param target_uin: 部署后自动绑定 OneBot 端口的 5~11 位纯数字 QQ 号字符串；留空则沿用当前默认账号
    """
    if not client.uses_local_runtime():
        return {'status': 'failed', 'error': '外部 OneBot URL 不能部署本地 LLOneBot'}
    return await _deploy_llonebot(
        action=action,
        confirmed=confirmed,
        use_local_zip_if_available=use_local_zip_if_available,
        target_uin=target_uin
    )


@registry.register()
async def list_qq_accounts(client: LLOneBotClient) -> dict[str, Any]:
    """
    读取本地 LLOneBot 中所有已配置或保存过登录会话的 QQ 账号列表、当前默认激活账号及当前在线账号详情。
    适用场景：在切换账号前查询有哪些历史已登录 QQ 号可供免扫码切换。不适用与替代：若仅需确认当前在线账号的 user_id 和昵称，请改用 `get_login_info`；若需同时检查运行时安装与 LLOneBot 版本更新，请改用 `check_llonebot_environment`。
    行为与返回：纯只读无副作用操作；返回 `service_running`、`online_account`、`default_account`、`cached_accounts`、`webui_url`。列表中的账号可直接传给 `switch_qq_account` 热切换。
    """
    if not client.uses_local_runtime():
        probe = await client.call_api('get_login_info', {}, timeout=5.0)
        return {
            'status': 'ok' if probe.get('status') == 'ok' and probe.get('retcode', 0) == 0 else 'failed',
            'mode': 'external',
            'online_account': probe.get('data'),
            'default_account': None,
            'cached_accounts': [],
            'total_cached': 0,
            'webui_url': None
        }
    current_default = get_current_account()
    available = get_available_accounts()
    online_account = None

    if is_port_listening(DEFAULT_HTTP_PORT):
        res = await client.call_api("get_login_info", {}, timeout=5.0)
        if res.get("status") == "ok":
            online_account = res.get("data")

    return {
        "status": "ok",
        "service_running": is_port_listening(DEFAULT_HTTP_PORT),
        "online_account": online_account,
        "default_account": current_default,
        "cached_accounts": available,
        "total_cached": len(available),
        "install_root": resolve_install_root(),
        "webui_url": get_webui_url(),
    }


@registry.register()
async def switch_qq_account(
    client: LLOneBotClient,
    uin: str,
    restart_immediately: bool = True,
    wait_seconds: int = 25
) -> dict[str, Any]:
    """
    在本地已保存登录会话的多个 QQ 账号之间免扫码热切换，自动更新默认账号、LLOneBot OneBot 端口配置及按账号隔离的消息缓存池。
    适用场景：目标 QQ 号此前已登录过（可通过 `check_llonebot_environment` 的 `available_accounts` 查看），需快速切换上线时调用。不适用与替代：若目标是一个从未登录过的新 QQ 号或登录凭证已失效，请勿使用本工具，应改用 `login_new_qq_by_qrcode` 扫码登录。
    参数约束与交互：`uin` 必须为 5~11 位纯数字字符串；当 `restart_immediately=True` 时会立即终止旧 LLBot 进程并等待最多 `wait_seconds` 秒直至新账号端口就绪，为 `False` 时仅修改配置待下次启动生效。
    行为与返回：幂等配置变更与进程重启操作（仅限本地运行时，外部 URL 返回失败）；返回 `status`、`switched_to`、`online_account` 与 `available_accounts`。
    :param uin: 要切换上线的目标 QQ 号（5~11 位纯数字字符串，须已存在于本地会话/账号列表中）
    :param restart_immediately: 为 True 时立即终止旧进程并以新账号重启上线；为 False 时仅更新默认配置不中断当前进程，默认 True
    :param wait_seconds: 当 restart_immediately=True 时等待新账号 OneBot 端口监听就绪的超时秒数（建议 15~60），默认 25
    """
    if not client.uses_local_runtime():
        return {'status': 'failed', 'error': '外部 OneBot URL 不支持管理本地 QQ 账号'}
    uin_clean = str(uin).strip()
    ok = set_current_account(uin_clean)
    if not ok:
        return {
            "status": "failed",
            "error": f"无效的 QQ 号格式或写入配置失败: {uin}"
        }
    if not restart_immediately:
        return {
            "status": "ok",
            "switched_to": uin_clean,
            "restarted": False,
            "available_accounts": get_available_accounts(),
            "message": f"已将默认配置切换为 {uin_clean}（尚未重启 LLOneBot 服务，下次启动生效）。"
        }

    client.buffer.select_account(uin_clean)
    killed = await asyncio.to_thread(kill_llonebot_processes)
    start_res = await client.start_llonebot_process(uin=uin_clean, wait_seconds=wait_seconds)
    client.start_background_ws()

    online_info = None
    if is_port_listening(DEFAULT_HTTP_PORT):
        login_res = await client.call_api("get_login_info", {}, timeout=5.0)
        if login_res.get("status") == "ok":
            online_info = login_res.get("data")

    return {
        "status": "ok" if start_res.get("status") in ("started", "already_running") else start_res.get("status"),
        "switched_to": uin_clean,
        "stopped_old_processes": killed,
        "start_result": start_res,
        "login_required": bool(start_res.get("login_required")),
        "desktop_qrcode_path": start_res.get("desktop_qrcode_path"),
        "webui_url": start_res.get("webui_url") or get_webui_url(),
        "online_account": online_info,
        "available_accounts": get_available_accounts(),
        "message": (
            start_res.get("message")
            if start_res.get("login_required")
            else f"账号 {uin_clean} 的 LLOneBot 服务启动状态：{start_res.get('status')}。"
        )
    }


@registry.register()
async def login_new_qq_by_qrcode(
    client: LLOneBotClient,
    open_image: bool = True,
    wait_scan_seconds: int = 45
) -> dict[str, Any]:
    """
    启动全新 QQ 账号的二维码扫码登录流程，生成/捕获二维码图片等待手机 QQ 扫码，成功后自动写入 LLOneBot OneBot 11 端口配置并转为常驻运行。
    适用场景：首次绑定新 QQ 号，或已有账号登录态过期需要重新手机扫码授权时调用。不适用与替代：若目标 QQ 号已在本地列表且会话有效，请勿重新扫码，应改用 `switch_qq_account` 免扫码秒级切换。
    参数约束与交互：`open_image=True` 会在本机 Windows 桌面自动弹出二维码查看器；`wait_scan_seconds` 控制阻塞等待用户手机确认的最大秒数（超时未扫返回 `status='scan_timed_out'`）。若 LLBot 未生成二维码图片，可访问返回的 `webui_url` 在 WebUI 中扫码。
    行为与副作用：会主动停止当前正在运行的本地 LLBot 进程并清除默认账号；前置要求本地 LLOneBot 已部署（未部署请先调用 `deploy_or_update_llonebot`），成功时返回 `status='logged_in_and_configured'`、`new_uin` 及 `online_account`。
    :param open_image: 生成二维码后是否立即调用系统默认图片查看器在桌面弹出显示，默认 True
    :param wait_scan_seconds: 阻塞等待用户使用手机 QQ 扫描并确认登录的最大超时秒数（建议 30~120），默认 45
    """
    if not client.uses_local_runtime():
        return {'status': 'failed', 'error': '外部 OneBot URL 不支持本地扫码登录'}
    scan_res = await asyncio.to_thread(
        start_qrcode_login_session,
        open_image=open_image,
        wait_scan_seconds=wait_scan_seconds
    )

    if scan_res.get("status") == "logged_in_and_configured":
        new_uin = scan_res.get("new_uin")
        client.buffer.select_account(new_uin)
        start_res = await client.start_llonebot_process(uin=new_uin, wait_seconds=25)
        client.start_background_ws()
        scan_res["service_start_result"] = start_res
        if is_port_listening(DEFAULT_HTTP_PORT):
            info = await client.call_api("get_login_info", {}, timeout=5.0)
            if info.get("status") == "ok":
                scan_res["online_account"] = info.get("data")

    return scan_res


@registry.register()
async def get_login_qrcode_image(
    client: LLOneBotClient,
    open_image: bool = True
) -> dict[str, Any]:
    """
    读取 LLOneBot 最新登录二维码图片路径（data/qrcode.png 或 data/temp 缓存），并可选在桌面弹出展示该图片。
    适用场景：当 `login_new_qq_by_qrcode` 已经生成二维码但用户关闭了图片窗口需要重新查看时调用。不适用与替代：本工具不会启动扫码进程或刷新过期二维码；如需发起新的扫码会话，请改用 `login_new_qq_by_qrcode`，或访问 LLBot WebUI。
    行为与返回：只读查询本地文件状态（`open_image=True` 时触发本地图片查看器）；文件存在返回 `status='ok'` 与 `qrcode_path`，不存在返回 `status='not_found'` 及 `webui_url`。
    :param open_image: 若本地二维码图片文件存在，是否在桌面立即打开图片查看器展示，默认 True
    """
    if not client.uses_local_runtime():
        return {'status': 'not_found', 'error': '外部 OneBot URL 没有本地二维码缓存'}
    qr_path = await asyncio.to_thread(find_qrcode_image)
    exists = bool(qr_path)
    desktop_qr = await asyncio.to_thread(export_qrcode_to_desktop, qr_path) if exists else None
    target_qr = desktop_qr or qr_path
    opened = False
    if exists and open_image and os.name == "nt":
        try:
            os.startfile(target_qr)
            opened = True
        except Exception:
            pass

    return {
        "status": "ok" if exists else "not_found",
        "qrcode_exists": exists,
        "qrcode_path": target_qr,
        "desktop_qrcode_path": desktop_qr,
        "opened_on_screen": opened,
        "webui_url": get_webui_url(),
        "message": (
            f"登录二维码已保存到桌面：{target_qr}，请打开图片扫描登录" + ("（已在屏幕弹出显示）。" if opened else "。")
            if exists
            else f"当前没有缓存的二维码图片，请调用 `login_new_qq_by_qrcode` 生成新二维码，"
                 f"或访问 LLBot WebUI ({get_webui_url()}) 扫码登录。"
        )
    }


@registry.register()
async def start_llonebot_service(
    client: LLOneBotClient,
    uin: Optional[str] = None,
    wait_seconds: int = 25
) -> dict[str, Any]:
    """
    启动本地 LLOneBot 服务并等待 OneBot (3000/3001) 端口就绪，同时挂载后台 WebSocket 事件监听。
    适用场景：当本地服务处于停止状态、需要以默认或指定账号拉起 LLBot CLI/Desktop 时调用（启动前会写入带 Token 的 LLOneBot 账号配置；若检测到需要扫码登录，会自动把二维码放到桌面并提示用户扫描，或返回 WebUI 地址）。不适用与替代：若服务已在运行且需切换其他已缓存账号，请改用 `switch_qq_account`。
    行为与返回：幂等操作——若端口已在监听则返回 `status='already_running'`；新启动成功返回 `status='started'`；若需扫码登录则返回 `login_required=True`、`desktop_qrcode_path` 与 `webui_url`。兼容旧工具名 `start_napcat_service`。
    :param uin: 指定要自动登录的 5~11 位纯数字 QQ 号字符串；留空则使用本地配置中的当前默认账号
    :param wait_seconds: 启动进程后轮询等待 OneBot HTTP 端口监听就绪的最大秒数，默认 25
    """
    res = await client.start_llonebot_process(uin=uin, wait_seconds=wait_seconds)
    client.start_background_ws()
    return res


@registry.register()
async def stop_llonebot_service(client: LLOneBotClient) -> dict[str, Any]:
    """
    安全终止本地后台运行的 LLOneBot / PMHQ 进程（严格校验进程路径，绝不误杀系统其他 Node.js 或 QQ 进程）。
    适用场景：需要彻底下线当前 QQ 机器人、释放本地 3000/3001 端口或维护运行时目录时调用。不适用与替代：若希望重启服务或切换其他已缓存 QQ 号上线，请直接改用 `switch_qq_account`。
    行为与副作用：破坏性但幂等的进程终止操作——调用后当前 QQ 账号立即离线且实时消息监听中断，直至重新调用 `switch_qq_account`、`login_new_qq_by_qrcode` 或任意触发自愈的核心工具；返回被终止的进程数量与端口状态。兼容旧工具名 `stop_napcat_service`。
    """
    if not client.uses_local_runtime():
        return {'status': 'failed', 'error': '外部 OneBot URL 不能停止本地 LLOneBot'}
    killed = await asyncio.to_thread(kill_llonebot_processes)
    return {
        "status": "ok",
        "stopped_processes": killed,
        "port_listening": is_port_listening(DEFAULT_HTTP_PORT),
        "message": "LLOneBot 后台服务已停止。"
    }


@registry.register()
async def restart_llonebot_service(
    client: LLOneBotClient,
    uin: Optional[str] = None,
    wait_seconds: int = 25
) -> dict[str, Any]:
    """
    强制终止现有本地 LLOneBot 进程并重新拉起服务，支持在重启时指定目标登录 QQ 号。
    适用场景：当 LLOneBot 服务出现连接僵死、配置变更需重启生效时调用。不适用与替代：若是为了在多个已登录账号间正常切换，请优先使用语义更清晰的 `switch_qq_account`。
    行为与副作用：会短暂中断现有连接并终止旧进程，随后等待最多 `wait_seconds` 秒恢复端口监听与 WebSocket 实时事件订阅；返回 `stopped_old_processes` 及重启结果 `start_result`。兼容旧工具名 `restart_napcat_service`。
    :param uin: 重启后指定登录的 5~11 位纯数字 QQ 号字符串；留空则沿用当前默认激活账号
    :param wait_seconds: 重启后等待 OneBot 端口监听就绪的最大超时秒数，默认 25
    """
    if not client.uses_local_runtime():
        return {'status': 'failed', 'error': '外部 OneBot URL 不能重启本地 LLOneBot'}
    target_uin = str(uin).strip() if uin else get_current_account()
    if target_uin:
        if not set_current_account(target_uin):
            return {'status': 'failed', 'error': '账号配置写入失败，未重启 LLOneBot'}
        client.buffer.select_account(target_uin)
    killed = await asyncio.to_thread(kill_llonebot_processes)
    start_res = await client.start_llonebot_process(uin=target_uin, wait_seconds=wait_seconds)
    client.start_background_ws()
    return {
        "status": start_res.get("status"),
        "uin": target_uin,
        "stopped_old_processes": killed,
        "start_result": start_res
    }


# Python API 层的旧函数名兼容别名（MCP 工具名别名由 tools/__init__.py 统一注册）
check_napcat_environment = check_llonebot_environment
deploy_or_update_napcat = deploy_or_update_llonebot
start_napcat_service = start_llonebot_service
stop_napcat_service = stop_llonebot_service
restart_napcat_service = restart_llonebot_service
