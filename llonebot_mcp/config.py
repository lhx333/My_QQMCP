"""
LLOneBot (LuckyLillia / LLBot) 全局配置、目录布局、多账号管理、扫码登录与进程控制模块。

LLOneBot 通过 LiteLoaderQQNT 插件或 LLBot CLI/Desktop 运行，并对外暴露 OneBot 11
HTTP/WebSocket 服务。本模块只依赖 LLOneBot 的公开目录结构与 default_config.json，
不再假定旧的 node.exe/index.js 运行时。
"""
import os
import time
import json
import glob
import socket
import logging
import subprocess
import queue
import threading
import tempfile
import shutil
from pathlib import Path
from typing import Any, Optional
from .token_store import ensure_onebot_token

logger = logging.getLogger("LLOneBotMCP.Config")


def _env(*names: str, default: Optional[str] = None) -> Optional[str]:
    """读取第一个非空环境变量，便于平滑迁移旧的 NAPCAT_* 配置。"""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


# 路径常量定义
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(PACKAGE_DIR)
_DEFAULT_DATA_BASE = os.environ.get('LOCALAPPDATA') or os.path.join(Path.home(), '.local', 'share')
USER_DATA_DIR: str = _env('OMNIQQ_USER_DATA_DIR',
                          default=os.path.join(_DEFAULT_DATA_BASE, 'OmniQQ-MCP')) or \
    os.path.join(_DEFAULT_DATA_BASE, 'OmniQQ-MCP')
LLBOT_HOME: str = _env('LLONEBOT_HOME', 'LLBOT_HOME', 'OMNIQQ_RUNTIME_DIR',
                       default=os.path.join(USER_DATA_DIR, 'llbot')) or \
    os.path.join(USER_DATA_DIR, 'llbot')
RUNTIME_DIR = LLBOT_HOME

CURRENT_ACCOUNT_FILE = _env('OMNIQQ_CURRENT_ACCOUNT_FILE',
                            default=os.path.join(USER_DATA_DIR, 'current_account.txt'))
_legacy_account = os.path.join(PROJECT_ROOT, 'current_account.txt')


def _config_root_for(root: str) -> str:
    """定位 default_config.json 所在目录，兼容 JS 插件与 CLI 两种发行包布局。"""
    candidates = (root, os.path.join(root, 'bin', 'llbot'), os.path.join(root, 'js'))
    for candidate in candidates:
        if os.path.isfile(os.path.join(candidate, 'default_config.json')):
            return candidate
    return root


def _data_dir_for(root: str) -> str:
    explicit = _env('LLONEBOT_DATA_DIR', 'LLBOT_DATA_DIR')
    if explicit:
        return explicit
    return os.path.join(_config_root_for(root), 'data')


def _entry_js_for(root: str) -> str:
    for candidate in (os.path.join(root, 'llbot.js'), os.path.join(_config_root_for(root), 'llbot.js')):
        if os.path.isfile(candidate):
            return candidate
    return os.path.join(root, 'llbot.js')


def _executable_for(root: str) -> Optional[str]:
    suffix = '.exe' if os.name == 'nt' else ''
    candidates = (
        os.path.join(root, f'llbot{suffix}'),
        os.path.join(root, 'bin', 'llbot', f'llbot{suffix}'),
        os.path.join(root, 'bin', f'llbot{suffix}'),
        os.path.join(root, f'LLBot{suffix}'),
    )
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


def _launcher_for(root: str) -> Optional[str]:
    for candidate in (os.path.join(root, 'start.sh'), os.path.join(root, 'bin', 'start.sh')):
        if os.path.isfile(candidate):
            return candidate
    return None


LLBOT_CONFIG_DIR = _env('LLONEBOT_CONFIG_DIR', default=_config_root_for(LLBOT_HOME))
LLBOT_DATA_DIR = _data_dir_for(LLBOT_HOME)
LLBOT_DEFAULT_CONFIG = os.path.join(LLBOT_CONFIG_DIR, 'default_config.json')
LLBOT_ENTRY_JS = _entry_js_for(LLBOT_HOME)
LLBOT_EXECUTABLE = _executable_for(LLBOT_HOME)
LLBOT_LAUNCHER = _launcher_for(LLBOT_HOME)
LLBOT_VERSION_FILE = os.path.join(LLBOT_HOME, 'llbot_version.json')
LLBOT_OFFLINE_ZIP = _env('LLONEBOT_OFFLINE_ZIP',
                         default=os.path.join(LLBOT_HOME, 'LLBot-offline.zip'))
QR_CACHE_IMAGE = os.path.join(LLBOT_DATA_DIR, 'qrcode.png')
DESKTOP_QR_FILENAME = "qq_login_qrcode.png"

# 默认通信端点 (支持通过环境变量覆盖；保留 NAPCAT_* 作为迁移兼容)
HTTP_PORT_RAW = _env('LLONEBOT_HTTP_PORT', 'LLBOT_HTTP_PORT', 'NAPCAT_HTTP_PORT', default='3000')
WS_PORT_RAW = _env('LLONEBOT_WS_PORT', 'LLBOT_WS_PORT', 'NAPCAT_WS_PORT', default='3001')
DEFAULT_HTTP_PORT = int(HTTP_PORT_RAW or 3000)
DEFAULT_WS_PORT = int(WS_PORT_RAW or 3001)
DEFAULT_WEBUI_PORT = int(_env('LLONEBOT_WEBUI_PORT', default='3080') or 3080)
DEFAULT_PMHQ_PORT = int(_env('LLONEBOT_PMHQ_PORT', default='13000') or 13000)
DEFAULT_HTTP_URL = _env('LLONEBOT_HTTP_URL', 'LLBOT_HTTP_URL', 'NAPCAT_HTTP_URL',
                        default=f"http://127.0.0.1:{DEFAULT_HTTP_PORT}") or f"http://127.0.0.1:{DEFAULT_HTTP_PORT}"
DEFAULT_WS_URL = _env('LLONEBOT_WS_URL', 'LLBOT_WS_URL', 'NAPCAT_WS_URL',
                      default=f"ws://127.0.0.1:{DEFAULT_WS_PORT}") or f"ws://127.0.0.1:{DEFAULT_WS_PORT}"


def get_webui_url() -> str:
    return _env('LLONEBOT_WEBUI_URL', default=f"http://127.0.0.1:{DEFAULT_WEBUI_PORT}") or \
        f"http://127.0.0.1:{DEFAULT_WEBUI_PORT}"


def get_desktop_dir() -> str:
    """获取当前系统的桌面目录路径（优先使用环境变量或 Windows Shell Desktop）。"""
    env_desktop = os.environ.get("OMNIQQ_DESKTOP_DIR")
    if env_desktop:
        return os.path.abspath(env_desktop)
    if os.name == "nt":
        if os.path.isdir(r"D:\Desktop"):
            return r"D:\Desktop"
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            ) as key:
                val, _ = winreg.QueryValueEx(key, "Desktop")
                expanded = os.path.expandvars(str(val))
                if os.path.isdir(expanded):
                    return expanded
        except Exception:
            pass
    return os.path.join(str(Path.home()), "Desktop")


def export_qrcode_to_desktop(source_path: Optional[str] = None) -> Optional[str]:
    """将 LLOneBot 生成的登录二维码图片复制到桌面并返回桌面文件绝对路径。"""
    src = source_path or find_qrcode_image()
    if not (src and os.path.exists(src)):
        return None
    try:
        if os.path.getsize(src) <= 0:
            return None
        desktop_dir = get_desktop_dir()
        os.makedirs(desktop_dir, exist_ok=True)
        dest_path = os.path.join(desktop_dir, DESKTOP_QR_FILENAME)
        if os.path.normcase(os.path.abspath(src)) != os.path.normcase(os.path.abspath(dest_path)):
            shutil.copy2(src, dest_path)
        return dest_path
    except Exception as e:
        logger.warning(f"复制二维码图片到桌面失败: {e}")
        return src


def atomic_json(path, data, private=False):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        if private:
            from .token_store import restrict_credential_acl
            restrict_credential_acl(Path(temporary))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def is_within(path, root):
    if not path:
        return False
    try:
        return os.path.commonpath([os.path.normcase(os.path.abspath(path)),
                                   os.path.normcase(os.path.abspath(root))]) == \
            os.path.normcase(os.path.abspath(root))
    except ValueError:
        return False


def is_port_listening(port: int, host: str = "127.0.0.1", timeout: float = 0.8) -> bool:
    """检测本地指定端口是否处于监听状态。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (ConnectionRefusedError, OSError, TimeoutError):
        return False


def find_installations() -> list[str]:
    """发现已有的 LLOneBot 安装目录，供环境诊断与进程停止使用。"""
    candidates = [
        LLBOT_HOME,
        _env('LLONEBOT_HOME', 'LLBOT_HOME') or '',
        os.path.join(USER_DATA_DIR, 'llbot'),
        os.path.join(str(Path.home()), 'LLBot'),
        os.path.join(str(Path.home()), 'llbot'),
        '/opt/LLBot',
        '/opt/llbot',
    ]
    if os.name == 'nt':
        local = os.environ.get('LOCALAPPDATA') or ''
        appdata = os.environ.get('APPDATA') or ''
        candidates.extend([
            os.path.join(local, 'LLBot'),
            os.path.join(local, 'Programs', 'LLBot'),
            os.path.join(appdata, 'LLBot'),
            os.path.join(appdata, 'LiteLoaderQQNT', 'plugins', 'LLOneBot'),
        ])
    found: list[str] = []
    for candidate in candidates:
        if not candidate or not os.path.isdir(candidate):
            continue
        normalized = os.path.normcase(os.path.abspath(candidate))
        if normalized in {os.path.normcase(os.path.abspath(item)) for item in found}:
            continue
        if any(os.path.isfile(os.path.join(candidate, name)) for name in (
                'llbot.js', 'default_config.json', 'llbot', 'llbot.exe', 'start.sh')) or \
                os.path.isdir(os.path.join(candidate, 'data')) or \
                os.path.isdir(os.path.join(candidate, 'bin')):
            found.append(candidate)
    return found


def resolve_install_root() -> Optional[str]:
    """优先返回托管目录，其次返回第一个可用的 LLOneBot 安装目录。"""
    if is_llbot_installed(LLBOT_HOME):
        return LLBOT_HOME
    installations = find_installations()
    return installations[0] if installations else None


def is_llbot_installed(root: Optional[str] = None) -> bool:
    root = root or LLBOT_HOME
    if not root or not os.path.isdir(root):
        return False
    return bool(
        os.path.isfile(_entry_js_for(root))
        or _executable_for(root)
        or _launcher_for(root)
        or os.path.isfile(os.path.join(root, 'bin', 'llbot', 'llbot.js'))
        or os.path.isfile(os.path.join(root, 'bin', 'llbot', 'llbot.exe'))
    )


def has_runtime_files(root: Optional[str] = None) -> bool:
    return is_llbot_installed(root)


def find_qrcode_image(root: Optional[str] = None) -> Optional[str]:
    """
    在 LLOneBot 数据目录中寻找最新的登录二维码。

    LLBot CLI 在终端打印二维码，WebUI 会在 data/temp 下缓存二维码图片；旧版插件
    也可能写入 data/qrcode.png。这里扫描常见位置并按修改时间选择最新文件。
    """
    root = root or resolve_install_root() or LLBOT_HOME
    data_dir = _data_dir_for(root)
    candidates = [QR_CACHE_IMAGE, os.path.join(data_dir, 'temp', 'qrcode.png')]
    patterns = [
        os.path.join(data_dir, 'qrcode*.png'),
        os.path.join(data_dir, 'temp', 'qrcode*.png'),
        os.path.join(data_dir, 'qrcode*.jpg'),
    ]
    for pattern in patterns:
        candidates.extend(glob.glob(pattern))
    existing = [path for path in candidates if os.path.isfile(path)]
    if not existing:
        return None
    try:
        newest = max(existing, key=os.path.getmtime)
        if os.path.getsize(newest) > 100:
            return newest
    except OSError:
        return None
    return None


def get_current_account() -> str:
    """读取当前默认激活的 QQ 账号 UIN。"""
    explicit = _env('LLONEBOT_QUICK_LOGIN_QQ', 'QUICK_LOGIN_QQ')
    if explicit and str(explicit).isdigit():
        return str(explicit)
    account_file = CURRENT_ACCOUNT_FILE if os.path.exists(CURRENT_ACCOUNT_FILE) else _legacy_account
    if os.path.exists(account_file):
        try:
            with open(account_file, "r", encoding="utf-8") as f:
                uin = f.read().strip()
                if uin:
                    return uin
        except Exception as e:
            logger.warning(f"读取 current_account.txt 失败: {e}")

    accounts = get_available_accounts()
    return accounts[0] if accounts else ""


def _account_from_filename(name: str) -> Optional[str]:
    """从 config_<uin>.json / qq-session-<uin>[-protocol].json 提取 QQ 号。"""
    for prefix in ('config_', 'qq-session-'):
        if not name.startswith(prefix):
            continue
        rest = name[len(prefix):]
        stem = rest.rsplit('.', 1)[0]
        digits = ''.join(ch for ch in stem.split('-')[0] if ch.isdigit())
        if digits and len(digits) <= 12:
            return digits
    return None


def get_available_accounts() -> list[str]:
    """获取 LLOneBot 已配置或保存过登录会话的所有 QQ 账号。"""
    accounts = set()
    roots = find_installations()
    if LLBOT_HOME not in roots:
        roots.append(LLBOT_HOME)
    for root in roots:
        data_dir = _data_dir_for(root)
        if not os.path.isdir(data_dir):
            continue
        for pattern in ("config_*.json", "qq-session-*.json", "qq-session-*-*.json"):
            for fp in glob.glob(os.path.join(data_dir, pattern)):
                uin = _account_from_filename(os.path.basename(fp))
                if uin:
                    accounts.add(uin)

    account_file = CURRENT_ACCOUNT_FILE if os.path.exists(CURRENT_ACCOUNT_FILE) else _legacy_account
    if os.path.exists(account_file):
        try:
            with open(account_file, "r", encoding="utf-8") as f:
                cur = f.read().strip()
                if cur.isdigit():
                    accounts.add(cur)
        except Exception:
            pass

    return sorted(accounts)


def _default_config_template(token: str, http_port: int, ws_port: int) -> dict[str, Any]:
    """与 LLOneBot v8.x 的 default_config.json 保持同构。"""
    common_token = token
    return {
        "webui": {"enable": True, "host": "127.0.0.1", "port": DEFAULT_WEBUI_PORT},
        "milky": {
            "enable": False,
            "reportSelfMessage": False,
            "http": {"host": "127.0.0.1", "port": 3010, "prefix": "", "accessToken": ""},
            "webhook": {"urls": [], "accessToken": ""},
        },
        "satori": {"enable": False, "host": "127.0.0.1", "port": 5600, "token": ""},
        "ob11": {
            "enable": True,
            "connect": [
                {
                    "type": "ws", "enable": True, "host": "127.0.0.1", "port": int(ws_port),
                    "heartInterval": 60000, "token": common_token, "reportSelfMessage": True,
                    "reportOfflineMessage": False, "messageFormat": "array", "debug": False,
                },
                {
                    "type": "ws-reverse", "enable": False, "url": "", "heartInterval": 60000,
                    "token": common_token, "reportSelfMessage": False, "reportOfflineMessage": False,
                    "messageFormat": "array", "debug": False,
                },
                {
                    "type": "http", "enable": True, "host": "127.0.0.1", "port": int(http_port),
                    "token": common_token, "reportSelfMessage": True, "reportOfflineMessage": False,
                    "messageFormat": "array", "debug": False,
                },
                {
                    "type": "http-post", "enable": False, "url": "", "enableHeart": False,
                    "heartInterval": 60000, "token": common_token, "reportSelfMessage": False,
                    "reportOfflineMessage": False, "messageFormat": "array", "debug": False,
                },
            ],
        },
        "enableLocalFile2Url": False,
        "log": True,
        "logLevel": "info",
        "autoDeleteFile": False,
        "autoDeleteFileSecond": 60,
        "musicSignUrl": "https://llob.linyuchen.net/sign/music",
        "msgCacheExpire": 120,
        "ffmpeg": "",
    }


def _merge_ob11_config(existing: dict[str, Any], token: str, http_port: int, ws_port: int) -> dict[str, Any]:
    """更新 HTTP/WS 服务，保留用户已有的反向 WS、HTTP 上报与其它插件配置。"""
    defaults = _default_config_template(token, http_port, ws_port)
    if not isinstance(existing, dict):
        return defaults
    webui = existing.setdefault('webui', {})
    if isinstance(webui, dict):
        webui.setdefault('enable', True)
        webui.setdefault('host', '127.0.0.1')
        webui.setdefault('port', DEFAULT_WEBUI_PORT)
    ob11 = existing.setdefault('ob11', {})
    if not isinstance(ob11, dict):
        existing['ob11'] = defaults['ob11']
        return existing
    ob11['enable'] = True
    connect = ob11.setdefault('connect', [])
    if not isinstance(connect, list):
        connect = ob11['connect'] = []
    managed = {
        'ws': {'type': 'ws', 'enable': True, 'host': '127.0.0.1', 'port': int(ws_port),
               'heartInterval': 60000, 'token': token, 'reportSelfMessage': True,
               'reportOfflineMessage': False, 'messageFormat': 'array', 'debug': False},
        'http': {'type': 'http', 'enable': True, 'host': '127.0.0.1', 'port': int(http_port),
                 'token': token, 'reportSelfMessage': True, 'reportOfflineMessage': False,
                 'messageFormat': 'array', 'debug': False},
    }
    for entry_type, expected in managed.items():
        current = next((item for item in connect
                        if isinstance(item, dict) and item.get('type') == entry_type), None)
        if current is None:
            connect.append(expected)
        else:
            # 保留用户自定义的 host/report 设置，只强制启用并同步端口与 Token。
            current['enable'] = True
            current['port'] = expected['port']
            current['token'] = expected['token']
            current.setdefault('host', expected['host'])
            current.setdefault('messageFormat', 'array')
    for key, value in defaults.items():
        if key in ('webui', 'ob11'):
            continue
        existing.setdefault(key, value)
    return existing


def ensure_onebot_config(
    uin: str,
    http_port: int = DEFAULT_HTTP_PORT,
    ws_port: int = DEFAULT_WS_PORT,
    token: Optional[str] = None
) -> bool:
    """写入 LLOneBot 的账号配置与默认配置，启用 HTTP/WebSocket 服务。"""
    if not str(uin).isdigit():
        return False
    root = resolve_install_root() or LLBOT_HOME
    config_dir = _config_root_for(root)
    data_dir = _data_dir_for(root)
    cfg_path = os.path.join(data_dir, f"config_{uin}.json")
    default_path = os.path.join(config_dir, "default_config.json")

    if token is None:
        token = _read_existing_token(cfg_path) or _read_existing_token(default_path) or ensure_onebot_token()
    if not token:
        raise ValueError('OneBot Token 不能为空')

    try:
        os.makedirs(data_dir, exist_ok=True)
        existing: dict[str, Any] = {}
        if os.path.exists(cfg_path):
            with open(cfg_path, encoding='utf-8') as stream:
                loaded = json.load(stream)
            if isinstance(loaded, dict):
                existing = loaded
        merged = _merge_ob11_config(existing, token, http_port, ws_port)
        atomic_json(cfg_path, merged, private=True)

        default_data: dict[str, Any] = {}
        if os.path.exists(default_path):
            with open(default_path, encoding='utf-8') as stream:
                loaded = json.load(stream)
            if isinstance(loaded, dict):
                default_data = loaded
        if default_data:
            atomic_json(default_path, _merge_ob11_config(default_data, token, http_port, ws_port))
        return True
    except Exception as e:
        logger.error(f"写入 LLOneBot OneBot11 配置失败: {e}")
        return False


def _read_existing_token(path: str) -> Optional[str]:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as stream:
            data = json.load(stream)
        ob11 = data.get('ob11') if isinstance(data, dict) else None
        connect = ob11.get('connect') if isinstance(ob11, dict) else None
        if isinstance(connect, list):
            for item in connect:
                if isinstance(item, dict) and item.get('type') in ('http', 'ws') and item.get('token'):
                    return str(item['token'])
    except Exception:
        return None
    return None


def set_current_account(uin: str) -> bool:
    """设置当前默认登录 QQ 账号，并确保其 LLOneBot 配置已存在。"""
    uin = str(uin).strip()
    if not uin.isdigit():
        return False
    try:
        if not ensure_onebot_config(uin):
            return False
        account_path = os.path.abspath(CURRENT_ACCOUNT_FILE)
        os.makedirs(os.path.dirname(account_path), exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=os.path.dirname(account_path))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(uin + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, account_path)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
        return True
    except Exception as e:
        logger.error(f"设置当前账号失败: {e}")
        return False


def clear_current_account() -> bool:
    """清除默认账号，使下次启动时进入扫码登录流程。"""
    try:
        for path in (CURRENT_ACCOUNT_FILE, _legacy_account):
            if os.path.exists(path):
                os.remove(path)
        return True
    except Exception as e:
        logger.warning(f"清除当前账号失败: {e}")
        return False


# 兼容旧命名
clear_auto_login_account = clear_current_account


def _iter_processes():
    import psutil
    return psutil.process_iter(["pid", "name", "exe", "cwd", "cmdline"])


def kill_qq_processes() -> int:
    """
    结束独立 QQ 客户端进程（仅在显式需要时调用）。

    LLOneBot Desktop / CLI 会自行拉起并托管 QQ，因此默认启动流程不会调用本函数。
    """
    killed_count = 0
    try:
        for proc in _iter_processes():
            try:
                p_name = (proc.info.get("name") or "").lower()
                exe_base = os.path.basename(proc.info.get("exe") or "").lower()
                if p_name in ("qq.exe", "qq", "qqkit.exe") or exe_base in ("qq.exe", "qqkit.exe"):
                    proc.kill()
                    killed_count += 1
            except Exception:
                continue
    except ImportError:
        raise RuntimeError("缺少 psutil，无法安全检查或终止 QQ 进程")
    if killed_count > 0:
        time.sleep(1.0)
    return killed_count


def kill_llonebot_processes(kill_qq: bool = False) -> int:
    """精准停止 LLOneBot / PMHQ 进程，避免误杀其它 Node.js 或 QQ 进程。"""
    killed_count = 0
    if kill_qq:
        killed_count += kill_qq_processes()
    roots = find_installations()
    if LLBOT_HOME not in roots:
        roots.append(LLBOT_HOME)
    normalized_roots = {os.path.normcase(os.path.abspath(root)) for root in roots}
    target_names = {'llbot', 'llbot.exe', 'pmhq', 'pmhq.exe', 'llbot-service', 'llonebot-service'}
    try:
        for proc in _iter_processes():
            try:
                name = (proc.info.get("name") or "").lower()
                exe_path = os.path.normcase(os.path.abspath(proc.info.get("exe") or ""))
                cmdline = proc.info.get("cmdline") or []
                if not isinstance(cmdline, list):
                    cmdline = []
                cwd = proc.info.get("cwd") or ''
                joined = ' '.join(str(part) for part in cmdline).lower()
                within_root = any(is_within(exe_path, root) for root in normalized_roots)
                script_in_root = False
                for arg in cmdline[1:]:
                    if not isinstance(arg, str) or arg.startswith('-'):
                        continue
                    resolved = os.path.abspath(os.path.join(cwd, arg))
                    base = os.path.basename(arg).lower()
                    if base in ('llbot.js', 'pmhq', 'pmhq.exe', 'llbot', 'llbot.exe') and \
                            any(is_within(resolved, root) for root in normalized_roots):
                        script_in_root = True
                        break
                if script_in_root or (within_root and (name in target_names or any(
                        token in joined for token in ('llbot.js', 'pmhq', 'llonebot-service')))):
                    proc.kill()
                    killed_count += 1
            except Exception:
                continue
    except ImportError:
        raise RuntimeError('缺少 psutil，无法安全确认进程所属路径')
    if killed_count:
        time.sleep(1.0)
    return killed_count


# 兼容旧命名
kill_napcat_processes = kill_llonebot_processes
kill_existing_napcat = kill_napcat_processes


def launch_llonebot_process(uin: Optional[str] = None,
                            extra_env: Optional[dict[str, str]] = None,
                            capture_output: bool = False) -> tuple[bool, str, Optional[subprocess.Popen]]:
    """
    以当前平台可用的方式启动 LLOneBot。

    返回 (是否成功拉起, 说明, 进程对象)。调用方负责轮询端口或二维码。
    """
    root = resolve_install_root()
    if not root:
        return False, '未找到 LLOneBot 安装目录，请先部署或设置 LLONEBOT_HOME。', None

    env = os.environ.copy()
    if uin:
        env['QUICK_LOGIN_QQ'] = str(uin)
    if extra_env:
        env.update({str(k): str(v) for k, v in extra_env.items()})

    command = _env('LLONEBOT_LAUNCH_COMMAND', 'LLBOT_LAUNCH_COMMAND')
    try:
        if command:
            import shlex
            cmd = shlex.split(command)
        else:
            executable = _executable_for(root)
            launcher = _launcher_for(root)
            entry_js = _entry_js_for(root)
            if os.name == 'nt' and executable:
                cmd = [executable]
            elif launcher:
                cmd = ['bash', launcher]
            elif executable:
                cmd = [executable]
            elif os.path.isfile(entry_js):
                node = shutil.which('node') or shutil.which('node.exe')
                if not node:
                    return False, '未找到 Node.js，无法运行 LLOneBot 的 llbot.js。', None
                cmd = [node, entry_js]
                pmhq_host = _env('LLONEBOT_PMHQ_HOST')
                pmhq_port = _env('LLONEBOT_PMHQ_PORT')
                if pmhq_host or pmhq_port:
                    cmd.extend([f'--pmhq-host={pmhq_host or "127.0.0.1"}',
                                f'--pmhq-port={pmhq_port or DEFAULT_PMHQ_PORT}'])
                if not is_port_listening(DEFAULT_PMHQ_PORT) and not _env('LLONEBOT_PMHQ_HOST'):
                    return False, (
                        'LLOneBot 的 llbot.js 需要 PMHQ/QQ 宿主进程。请先启动 LLBot CLI/Desktop '
                        f'或 PMHQ (默认端口 {DEFAULT_PMHQ_PORT})，或设置 LLONEBOT_LAUNCH_COMMAND。'
                    ), None
            else:
                return False, '未找到 LLOneBot 可执行文件（llbot、llbot.exe、start.sh 或 llbot.js）。', None

        options: dict[str, Any] = {'cwd': root, 'env': env}
        if capture_output:
            options.update({
                'stdout': subprocess.PIPE,
                'stderr': subprocess.STDOUT,
                'text': True,
                'encoding': 'utf-8',
                'errors': 'replace',
            })
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 7  # SW_SHOWMINNOACTIVE
            options['startupinfo'] = startupinfo
            options['creationflags'] = 0x00000010  # CREATE_NEW_CONSOLE
        process = subprocess.Popen(cmd, **options)
        return True, f'已启动 LLOneBot: {" ".join(cmd)}', process
    except Exception as e:
        return False, f'拉起 LLOneBot 进程失败: {e}', None


def _login_success_account(before: set[str], after: set[str]) -> Optional[str]:
    new_accounts = sorted(after - before)
    return new_accounts[0] if len(new_accounts) == 1 else None


def start_qrcode_login_session(
    open_image: bool = True,
    wait_scan_seconds: int = 45
) -> dict[str, Any]:
    """
    启动 LLOneBot 扫码登录流程：
    1. 停止现有 LLBot 进程并清除默认账号；
    2. 启动 LLBot（清空 QUICK_LOGIN_QQ），轮询终端输出与二维码缓存；
    3. 扫码成功后写入账号配置并返回新账号；超时则返回二维码路径与 WebUI 地址。
    """
    root = resolve_install_root()
    if not root:
        return {
            "status": "not_installed",
            "error": "未检测到 LLOneBot 运行时，请先调用 `deploy_or_update_llonebot` 部署，"
                     "或设置 LLONEBOT_HOME 指向已有安装目录。"
        }

    before_accounts = set(get_available_accounts())
    kill_llonebot_processes(kill_qq=False)
    clear_current_account()

    old_qr = find_qrcode_image(root)
    if old_qr and os.path.isfile(old_qr):
        try:
            os.remove(old_qr)
        except Exception:
            pass

    ok, message, proc = launch_llonebot_process(
        uin='', extra_env={'QUICK_LOGIN_QQ': ''}, capture_output=True
    )
    if not ok or proc is None:
        return {
            "status": "failed",
            "error": message,
            "webui_url": get_webui_url(),
        }

    qr_opened = False
    desktop_qr_path: Optional[str] = None
    logged_in_uin: Optional[str] = None
    start_ts = time.time()
    output_lines: queue.Queue = queue.Queue(maxsize=1000)

    def drain_output():
        for line in iter(proc.stdout.readline, ''):
            try:
                output_lines.put_nowait(line)
            except queue.Full:
                pass

    reader = threading.Thread(target=drain_output, daemon=True)
    reader.start()
    login_confirmed = False

    try:
        while time.time() - start_ts < max(10, int(wait_scan_seconds)):
            if proc.poll() is not None:
                break

            current_qr = find_qrcode_image(root)
            if not qr_opened and current_qr and os.path.getsize(current_qr) > 100:
                qr_opened = True
                desktop_qr_path = export_qrcode_to_desktop(current_qr)
                if open_image and os.name == "nt":
                    try:
                        os.startfile(desktop_qr_path or current_qr)
                    except Exception:
                        pass

            try:
                line = output_lines.get(timeout=0.2)
            except queue.Empty:
                line = ''
            if line:
                if '二维码' in line and not qr_opened:
                    current_qr = find_qrcode_image(root)
                    if current_qr:
                        qr_opened = True
                        desktop_qr_path = export_qrcode_to_desktop(current_qr)
                        if open_image and os.name == "nt":
                            try:
                                os.startfile(desktop_qr_path or current_qr)
                            except Exception:
                                pass
                if '登录成功' in line or '已登录' in line or 'login success' in line.lower():
                    login_confirmed = True
                    for token in line.replace("[", " ").replace("]", " ").split():
                        if token.isdigit() and 5 <= len(token) <= 12:
                            logged_in_uin = token
                if ('OneBot' in line and ('启动' in line or '监听' in line)) or 'WebSocket' in line:
                    time.sleep(0.5)
                    break
            else:
                time.sleep(0.2)

        after_accounts = set(get_available_accounts())
        target_uin = logged_in_uin or _login_success_account(before_accounts, after_accounts)

        if target_uin and (login_confirmed or target_uin in after_accounts):
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
            if not set_current_account(target_uin):
                return {'status': 'failed', 'error': '账号配置写入失败'}
            return {
                "status": "logged_in_and_configured",
                "new_uin": target_uin,
                "qrcode_path": desktop_qr_path or find_qrcode_image(root),
                "desktop_qrcode_path": desktop_qr_path,
                "webui_url": get_webui_url(),
                "available_accounts": get_available_accounts(),
                "message": f"扫码登录成功！已绑定新 QQ 账号 {target_uin} 并启用 LLOneBot OneBot 11 服务。"
            }

        try:
            proc.kill()
            proc.wait(timeout=3)
        except Exception:
            pass

        current_qr = find_qrcode_image(root)
        if not desktop_qr_path and current_qr:
            desktop_qr_path = export_qrcode_to_desktop(current_qr)
        display_qr = desktop_qr_path or current_qr
        return {
            "status": "scan_timed_out",
            "qrcode_generated": bool(current_qr),
            "qrcode_path": display_qr,
            "desktop_qrcode_path": desktop_qr_path,
            "qrcode_opened_on_screen": qr_opened,
            "webui_url": get_webui_url(),
            "message": (
                f"未在 {wait_scan_seconds} 秒内检测到扫码完成。"
                + (f"二维码已保存到：{display_qr}，请打开桌面二维码扫描。" if display_qr else "")
                + f"也可访问 LLBot WebUI ({get_webui_url()}) 扫码登录，登录后调用 `switch_qq_account` 绑定账号。"
            ),
        }
    except Exception as e:
        try:
            proc.kill()
        except Exception:
            pass
        return {"status": "failed", "error": f"扫码登录过程发生异常: {e}"}
    finally:
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=5)
        finally:
            reader.join(timeout=2)
            if proc.stdout:
                proc.stdout.close()
