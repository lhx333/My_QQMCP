"""
LLOneBot 环境自检、版本比对与自动化部署/更新管理器。

支持检测 LLBot CLI / Desktop / LiteLoaderQQNT 插件目录，远程查询
LLOneBot/LuckyLilliaBot GitHub Release 最新版本，并在用户明确授权
(confirmed=True) 后执行一键部署或升级。
"""
import os
import sys
import json
import zipfile
import logging
import shutil
import subprocess
import tempfile
import time
import asyncio
import platform
from typing import Any, Optional

import aiohttp

from .config import (
    RUNTIME_DIR,
    LLBOT_HOME,
    LLBOT_CONFIG_DIR,
    LLBOT_DATA_DIR,
    LLBOT_DEFAULT_CONFIG,
    LLBOT_ENTRY_JS,
    LLBOT_EXECUTABLE,
    LLBOT_LAUNCHER,
    LLBOT_VERSION_FILE,
    LLBOT_OFFLINE_ZIP,
    DEFAULT_HTTP_PORT,
    DEFAULT_WS_PORT,
    DEFAULT_WEBUI_PORT,
    find_qrcode_image,
    find_installations,
    is_llbot_installed,
    is_port_listening,
    get_current_account,
    get_available_accounts,
    ensure_onebot_config,
    kill_llonebot_processes,
    export_qrcode_to_desktop,
    get_webui_url,
    resolve_install_root,
    _entry_js_for,
    _executable_for,
    _launcher_for,
    _config_root_for,
    _data_dir_for,
)

logger = logging.getLogger("LLOneBotMCP.Deployer")

GITHUB_RELEASE_API = "https://api.github.com/repos/LLOneBot/LuckyLilliaBot/releases/latest"
GITHUB_RELEASES_URL = "https://github.com/LLOneBot/LuckyLilliaBot/releases"
LOCAL_RELEASE_ZIP = LLBOT_OFFLINE_ZIP
_deployment_lock = asyncio.Lock()


def _preferred_asset_keywords() -> tuple[str, ...]:
    machine = platform.machine().lower()
    is_arm = ('arm' in machine) or ('aarch64' in machine)
    if sys.platform.startswith('win'):
        return ('LLBot-Desktop-win-arm64.zip', 'LLBot-Desktop-win-x64.zip') if is_arm else \
            ('LLBot-Desktop-win-x64.zip', 'LLBot-Desktop-win-arm64.zip')
    if sys.platform == 'darwin':
        return ('LLBot-Desktop-mac-arm64.zip', 'LLBot-Desktop-mac-x64.zip') if is_arm else \
            ('LLBot-Desktop-mac-x64.zip', 'LLBot-Desktop-mac-arm64.zip')
    return ('LLBot-CLI-linux-arm64.zip', 'LLBot-CLI-linux-x64.zip') if is_arm else \
        ('LLBot-CLI-linux-x64.zip', 'LLBot-CLI-linux-arm64.zip')


def _select_release_asset(assets: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    names = {str(asset.get('name', '')): asset for asset in assets if isinstance(asset, dict)}
    for keyword in _preferred_asset_keywords():
        if keyword in names:
            asset = names[keyword]
            return {
                "name": keyword,
                "size_mb": round(asset.get("size", 0) / (1024 * 1024), 2),
                "download_url": asset.get("browser_download_url", ""),
            }
    for name, asset in names.items():
        if name.endswith('.zip') and ('LuckyLillia' in name or 'LLBot' in name):
            return {
                "name": name,
                "size_mb": round(asset.get("size", 0) / (1024 * 1024), 2),
                "download_url": asset.get("browser_download_url", ""),
            }
    return None


def check_installed_runtime(root: Optional[str] = None) -> dict[str, str]:
    """检查已安装的 LLOneBot 入口文件是否完整且可解析。"""
    root = root or resolve_install_root() or LLBOT_HOME
    entry_js = LLBOT_ENTRY_JS if root == LLBOT_HOME else _entry_js_for(root)
    executable = LLBOT_EXECUTABLE if root == LLBOT_HOME else _executable_for(root)
    launcher = LLBOT_LAUNCHER if root == LLBOT_HOME else _launcher_for(root)

    if os.path.isfile(entry_js):
        node = shutil.which('node') or shutil.which('node.exe')
        if not node:
            return {'entry': 'ok', 'node_version': 'not-checked'}
        options: dict[str, Any] = {'cwd': root, 'capture_output': True, 'text': True, 'timeout': 10}
        if os.name == 'nt':
            options['creationflags'] = subprocess.CREATE_NO_WINDOW
        version = subprocess.run([node, '--version'], **options)
        if version.returncode != 0 or not version.stdout.strip().startswith('v'):
            raise RuntimeError('Node.js 运行时版本检查失败')
        syntax = subprocess.run([node, '--check', entry_js], **options)
        if syntax.returncode != 0:
            raise RuntimeError('LLOneBot 入口脚本语法检查失败')
        return {'entry': 'llbot.js', 'node_version': version.stdout.strip(), 'entry_syntax': 'ok'}

    if executable or launcher:
        return {'entry': os.path.basename(executable or launcher or 'llbot'), 'status': 'ok'}
    raise RuntimeError('未找到 LLOneBot 入口文件 (llbot.js / llbot / llbot.exe / start.sh)')


def get_local_llonebot_version() -> dict[str, str]:
    """读取本地安装的 LLOneBot 版本标记与入口信息。"""
    marker = LLBOT_VERSION_FILE
    version = 'unknown'
    released_at = ''
    if os.path.exists(marker):
        try:
            with open(marker, 'r', encoding='utf-8') as f:
                data = json.load(f)
            version = str(data.get('version') or 'unknown')
            released_at = str(data.get('installed_at') or '')
        except Exception:
            pass
    root = resolve_install_root() or LLBOT_HOME
    config_root = _config_root_for(root)
    return {
        'llonebot_version': version,
        'installed_at': released_at,
        'install_root': root,
        'config_dir': config_root,
        'data_dir': _data_dir_for(root),
        'entry_js_exists': os.path.isfile(_entry_js_for(root)),
        'executable_exists': bool(_executable_for(root)),
        'launcher_exists': bool(_launcher_for(root)),
    }


async def fetch_github_latest_release() -> dict[str, Any]:
    """查询 LLOneBot/LuckyLilliaBot 官方 GitHub 最新 Release 信息。"""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "OmniQQ-MCP-Server/2.0"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(GITHUB_RELEASE_API, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    assets = data.get("assets", [])
                    return {
                        "reachable": True,
                        "latest_tag": data.get("tag_name", ""),
                        "published_at": data.get("published_at", ""),
                        "release_url": data.get("html_url", GITHUB_RELEASES_URL),
                        "recommended_asset": _select_release_asset(assets),
                    }
                return {
                    "reachable": False,
                    "error": f"GitHub API 返回状态码 {resp.status}",
                    "release_url": GITHUB_RELEASES_URL
                }
    except Exception as e:
        return {
            "reachable": False,
            "error": f"无法连接 GitHub Release API: {e}",
            "release_url": GITHUB_RELEASES_URL
        }


async def check_llonebot_environment(live_version_info: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """全方位诊断当前 LLOneBot 环境与 OneBot 服务状态。"""
    root = resolve_install_root() or LLBOT_HOME
    installed = is_llbot_installed(root)
    local_zip_exists = os.path.exists(LLBOT_OFFLINE_ZIP)
    entry_path = LLBOT_ENTRY_JS if root == LLBOT_HOME else _entry_js_for(root)
    entry_exists = os.path.isfile(entry_path) or bool(_executable_for(root))
    launcher_exists = bool(_launcher_for(root))
    config_dir = LLBOT_CONFIG_DIR if root == LLBOT_HOME else _config_root_for(root)
    data_dir = LLBOT_DATA_DIR if root == LLBOT_HOME else _data_dir_for(root)
    default_config_exists = os.path.isfile(
        LLBOT_DEFAULT_CONFIG if root == LLBOT_HOME else os.path.join(config_dir, 'default_config.json'))

    http_open = is_port_listening(DEFAULT_HTTP_PORT)
    ws_open = is_port_listening(DEFAULT_WS_PORT)
    webui_open = is_port_listening(DEFAULT_WEBUI_PORT)
    service_running = http_open and ws_open

    local_ver = get_local_llonebot_version()
    if live_version_info and isinstance(live_version_info, dict):
        local_ver["running_app_name"] = live_version_info.get("app_name", "LLOneBot")
        local_ver["running_app_version"] = live_version_info.get("app_version", "unknown")
        local_ver["protocol_version"] = live_version_info.get("protocol_version", "v11")

    github_info = await fetch_github_latest_release()

    if not installed:
        status = "not_installed"
        advice = (
            "未检测到完整的 LLOneBot 本地运行环境。"
            "可询问用户是否允许调用 `deploy_or_update_llonebot(action='deploy', confirmed=True)` "
            "自动下载部署 LLBot，或手动安装 LLBot CLI/Desktop 后设置 LLONEBOT_HOME。"
        )
    elif not service_running:
        status = "installed_not_running"
        current_qr = find_qrcode_image(root)
        desktop_qr = export_qrcode_to_desktop(current_qr) if current_qr else None
        if desktop_qr:
            advice = (
                f"LLOneBot 已安装，检测到需要扫码登录！已将登录二维码放到桌面：{desktop_qr}，"
                "请扫描登录，或调用 `start_llonebot_service` / `login_new_qq_by_qrcode`。"
            )
        else:
            advice = (
                f"LLOneBot 已安装，但 OneBot 服务尚未启动（端口 {DEFAULT_HTTP_PORT}/{DEFAULT_WS_PORT} 未监听）。"
                "可调用 `start_llonebot_service`、`switch_qq_account` 或 `login_new_qq_by_qrcode` 拉起服务；"
                f"也可以访问 WebUI ({get_webui_url()}) 查看登录与配置。"
            )
    else:
        status = "ready"
        advice = "LLOneBot 已就绪且 OneBot 11 服务正在正常运行。"

    return {
        "status": status,
        "advice": advice,
        "runtime": {
            "installed": installed,
            "runtime_dir": root,
            "config_dir": config_dir,
            "data_dir": data_dir,
            "entry_path": entry_path,
            "entry_exists": entry_exists,
            "launcher_exists": launcher_exists,
            "default_config_exists": default_config_exists,
            "local_offline_zip_exists": local_zip_exists,
            "local_offline_zip_path": LLBOT_OFFLINE_ZIP if local_zip_exists else None,
            "discovered_installations": find_installations(),
        },
        "service": {
            "running": service_running,
            "http_port": DEFAULT_HTTP_PORT,
            "http_listening": http_open,
            "ws_port": DEFAULT_WS_PORT,
            "ws_listening": ws_open,
            "webui_port": DEFAULT_WEBUI_PORT,
            "webui_url": get_webui_url(),
            "webui_listening": webui_open,
        },
        "accounts": {
            "current_account": get_current_account(),
            "available_accounts": get_available_accounts(),
        },
        "version": {
            "local": local_ver,
            "github_latest": github_info,
        }
    }


async def deploy_or_update_llonebot(
    action: str = "deploy",
    confirmed: bool = False,
    use_local_zip_if_available: bool = True,
    target_uin: Optional[str] = None
) -> dict[str, Any]:
    """Serialize confirmed deployments so backups and rollbacks cannot overlap."""
    if confirmed is not True:
        return await _deploy_or_update_llonebot(action, confirmed, use_local_zip_if_available, target_uin)
    async with _deployment_lock:
        return await _deploy_or_update_llonebot(action, confirmed, use_local_zip_if_available, target_uin)


def _decode_zip_name(info: zipfile.ZipInfo) -> str:
    """ZIP 无 UTF-8 标记时按 UTF-8/GBK 兼容解码中文文件名。"""
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    try:
        decoded = name.encode('cp437').decode('utf-8')
        return decoded
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def _locate_payload(staging: str) -> str:
    """兼容发行包根目录直接存放或嵌套一层目录的布局。"""
    candidates = [staging]
    try:
        for name in os.listdir(staging):
            nested = os.path.join(staging, name)
            if os.path.isdir(nested):
                candidates.append(nested)
    except OSError:
        return staging
    for candidate in candidates:
        if os.path.isfile(os.path.join(candidate, 'llbot.js')) or \
                os.path.isfile(os.path.join(candidate, 'default_config.json')) or \
                os.path.isfile(os.path.join(candidate, 'llbot')) or \
                os.path.isfile(os.path.join(candidate, 'llbot.exe')) or \
                os.path.isfile(os.path.join(candidate, 'start.sh')) or \
                os.path.isdir(os.path.join(candidate, 'bin', 'llbot')):
            return candidate
    return staging


def _is_persistent_data_path(relative: str) -> bool:
    """安装升级时保留 LLOneBot 的 data 目录（账号、Token、日志、缓存）。"""
    normalized = relative.replace('\\', '/').strip('/')
    return normalized == 'data' or normalized.startswith(('data/', 'bin/llbot/data/', 'js/data/'))


def _work_root() -> str:
    """备份、下载与解压暂存目录放在安装目录之外，避免回滚时删除备份本身。"""
    runtime = os.path.abspath(RUNTIME_DIR)
    home = os.path.abspath(LLBOT_HOME)
    if runtime == home:
        return os.path.dirname(home) or runtime
    return runtime


def _ensure_executable(root: str) -> None:
    """ZIP 解压通常不保留 Unix 可执行位，安装后补齐 LLBot 入口权限。"""
    if os.name == 'nt':
        return
    for relative in ('llbot', 'start.sh', 'bin/llbot/llbot', 'pmhq'):
        path = os.path.join(root, relative)
        if os.path.isfile(path):
            try:
                os.chmod(path, 0o755)
            except OSError:
                logger.warning('无法为 %s 设置可执行权限', path)


async def _deploy_or_update_llonebot(
    action: str = "deploy",
    confirmed: bool = False,
    use_local_zip_if_available: bool = True,
    target_uin: Optional[str] = None
) -> dict[str, Any]:
    """
    自动部署或更新 LLOneBot 运行时环境。
    安全门禁：confirmed=False 时仅生成部署预览计划，绝不执行文件写入或下载。
    """
    if action not in ('deploy', 'update'):
        return {'status': 'failed', 'error': 'action 必须为 deploy 或 update'}
    if target_uin is not None and not str(target_uin).isdigit():
        return {'status': 'failed', 'error': 'target_uin 必须为数字 QQ 号'}

    github_info = await fetch_github_latest_release()
    local_zip_exists = os.path.exists(LLBOT_OFFLINE_ZIP)
    uin = target_uin or get_current_account()
    asset = github_info.get("recommended_asset") or {}
    download_url = asset.get("download_url", "")

    if confirmed is not True:
        source_desc = (
            f"本地离线包 ({LLBOT_OFFLINE_ZIP})"
            if (use_local_zip_if_available and local_zip_exists)
            else (download_url or f"GitHub LLOneBot 官方 Release 最新包 ({GITHUB_RELEASES_URL})")
        )
        return {
            "status": "confirmation_required",
            "requires_user_permission": True,
            "planned_action": action,
            "target_directory": LLBOT_HOME,
            "package_source": source_desc,
            "github_latest_tag": github_info.get("latest_tag", "unknown"),
            "account_to_configure": uin or "(部署后首次扫码登录)",
            "message_for_ai": (
                "⚠️ 安全拦截：自动部署或更新将修改本地 LLOneBot 安装目录。"
                "请先向用户展示上述【部署目标路径、安装包来源、版本号】，"
                "在获得用户明确许可后，再次调用本工具并设置 `confirmed=True` 执行。"
            )
        }

    os.makedirs(RUNTIME_DIR, exist_ok=True)
    os.makedirs(LLBOT_HOME, exist_ok=True)
    work_root = _work_root()
    os.makedirs(work_root, exist_ok=True)

    zip_to_extract: Optional[str] = None
    if use_local_zip_if_available and local_zip_exists and action == "deploy":
        zip_to_extract = LLBOT_OFFLINE_ZIP
    elif download_url:
        downloaded_zip = os.path.join(work_root, f"LLBot_{github_info.get('latest_tag', 'latest')}.zip")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(download_url, timeout=aiohttp.ClientTimeout(total=600)) as resp:
                    if resp.status != 200:
                        if local_zip_exists:
                            zip_to_extract = LLBOT_OFFLINE_ZIP
                        else:
                            return {
                                "status": "failed",
                                "error": f"下载 LLOneBot 发布包失败 (HTTP {resp.status})，且本地无离线包。",
                                "download_url": download_url
                            }
                    else:
                        with open(downloaded_zip, "wb") as f:
                            async for chunk in resp.content.iter_chunked(1024 * 64):
                                f.write(chunk)
                        zip_to_extract = downloaded_zip
        except Exception as e:
            if local_zip_exists:
                zip_to_extract = LLBOT_OFFLINE_ZIP
            else:
                return {
                    "status": "failed",
                    "error": f"网络下载异常: {e}",
                    "download_url": download_url
                }
    elif local_zip_exists:
        zip_to_extract = LLBOT_OFFLINE_ZIP
    else:
        return {
            "status": "failed",
            "error": "既无法从 GitHub 获取 LLOneBot 下载链接，本地也不存在 LLBot 离线包。",
            "manual_url": GITHUB_RELEASES_URL
        }

    def install_runtime():
        backup_directory = None
        changed_installation = False

        def restore_backup():
            if not backup_directory or not os.path.isdir(backup_directory):
                return False
            if not os.path.commonpath([os.path.abspath(RUNTIME_DIR),
                                      os.path.abspath(LLBOT_HOME)]) == os.path.abspath(RUNTIME_DIR):
                raise RuntimeError('安装目录不在运行时目录内，拒绝回滚')
            shutil.rmtree(LLBOT_HOME)
            shutil.copytree(backup_directory, LLBOT_HOME)
            return True

        try:
            with tempfile.TemporaryDirectory(prefix='llbot-stage-', dir=work_root) as staging:
                with zipfile.ZipFile(zip_to_extract, "r") as zf:
                    for info in zf.infolist():
                        target = os.path.abspath(os.path.join(staging, _decode_zip_name(info)))
                        if os.path.commonpath([os.path.abspath(staging), target]) != os.path.abspath(staging):
                            raise ValueError('安装包包含越界路径')
                    zf.extractall(staging)
                payload = _locate_payload(staging)
                if not (
                    os.path.isfile(os.path.join(payload, 'llbot.js'))
                    or os.path.isfile(os.path.join(payload, 'llbot'))
                    or os.path.isfile(os.path.join(payload, 'llbot.exe'))
                    or os.path.isfile(os.path.join(payload, 'start.sh'))
                    or os.path.isdir(os.path.join(payload, 'bin', 'llbot'))
                ):
                    raise ValueError('安装包缺少 LLOneBot 入口文件，未修改已安装运行时')

                if os.path.isdir(LLBOT_HOME):
                    backup_directory = os.path.join(work_root, f'backup-{time.time_ns()}')
                    shutil.copytree(LLBOT_HOME, backup_directory)
                    kill_llonebot_processes()

                try:
                    changed_installation = True
                    for folder, _directories, files in os.walk(payload):
                        relative = os.path.relpath(folder, payload)
                        target_folder = LLBOT_HOME if relative == '.' else os.path.join(LLBOT_HOME, relative)
                        os.makedirs(target_folder, exist_ok=True)
                        for name in files:
                            target = os.path.join(target_folder, name)
                            if os.path.exists(target) and _is_persistent_data_path(relative):
                                continue
                            shutil.copy2(os.path.join(folder, name), target)
                    _ensure_executable(LLBOT_HOME)
                    if not (os.path.isfile(LLBOT_ENTRY_JS) or LLBOT_EXECUTABLE or LLBOT_LAUNCHER
                            or os.path.isdir(os.path.join(LLBOT_HOME, 'bin', 'llbot'))):
                        raise RuntimeError('升级后 LLOneBot 运行时文件校验失败')
                except Exception:
                    if backup_directory:
                        restore_backup()
                    raise
        except Exception as e:
            if changed_installation and not backup_directory:
                shutil.rmtree(LLBOT_HOME, ignore_errors=True)
            return {
                "status": "failed",
                "error": f"解压安装包 ({zip_to_extract}) 到 {LLBOT_HOME} 失败: {e}"
            }

        config_ok = False
        if uin:
            config_ok = ensure_onebot_config(uin)
            if not config_ok:
                try:
                    rolled_back = restore_backup()
                    if not backup_directory and changed_installation:
                        if os.path.commonpath([os.path.abspath(RUNTIME_DIR),
                                              os.path.abspath(LLBOT_HOME)]) != os.path.abspath(RUNTIME_DIR):
                            raise RuntimeError('安装目录不在运行时目录内，拒绝回滚')
                        shutil.rmtree(LLBOT_HOME)
                        rolled_back = True
                except Exception as exc:
                    return {'status': 'failed', 'error': f'LLOneBot 配置失败且回滚失败: {exc}',
                            'backup_directory': backup_directory}
                return {'status': 'failed', 'error': 'LLOneBot OneBot 配置写入失败',
                        'rolled_back': rolled_back, 'backup_directory': backup_directory}

        try:
            health = check_installed_runtime(LLBOT_HOME)
        except Exception as exc:
            try:
                rolled_back = restore_backup()
                if not backup_directory and changed_installation:
                    if os.path.commonpath([os.path.abspath(RUNTIME_DIR),
                                          os.path.abspath(LLBOT_HOME)]) != os.path.abspath(RUNTIME_DIR):
                        raise RuntimeError('安装目录不在运行时目录内，拒绝回滚')
                    shutil.rmtree(LLBOT_HOME)
                    rolled_back = True
            except Exception as rollback_error:
                return {'status': 'failed', 'error': f'运行时校验失败且回滚失败: {rollback_error}',
                        'backup_directory': backup_directory}
            return {'status': 'failed', 'error': f'运行时校验失败: {exc}',
                    'rolled_back': rolled_back, 'backup_directory': backup_directory}

        try:
            with open(LLBOT_VERSION_FILE, 'w', encoding='utf-8') as f:
                json.dump({
                    'version': github_info.get('latest_tag') or 'unknown',
                    'installed_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                    'source': os.path.basename(zip_to_extract),
                    'action': action,
                }, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

        return {
            "status": "success",
            "action": action,
            "extracted_from": zip_to_extract,
            "target_directory": LLBOT_HOME,
            "entry_ready": bool(LLBOT_ENTRY_JS or LLBOT_EXECUTABLE or LLBOT_LAUNCHER),
            "onebot_configured_for_uin": uin if config_ok else None,
            "backup_directory": backup_directory,
            "health_check": health,
            "webui_url": get_webui_url(),
            "message": "LLOneBot 运行时已成功部署/更新！现在可以调用 `switch_qq_account` 或 "
                       "`login_new_qq_by_qrcode` 登录并启动服务。"
        }

    task = asyncio.create_task(asyncio.to_thread(install_runtime))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


# 兼容旧函数名
check_napcat_environment = check_llonebot_environment
deploy_or_update_napcat = deploy_or_update_llonebot
