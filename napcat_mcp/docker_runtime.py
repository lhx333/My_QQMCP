"""Docker 版 NapCat 运行时管理（默认部署方式）。

通过官方镜像 ``mlikiowa/napcat-docker`` 管理 NapCat 容器：镜像拉取、容器创建、
启停、日志与版本查询。NapCat 数据目录挂载到宿主机，二维码与 OneBot 配置可直接
由 MCP Server 读取。
"""
import logging
import os
import shutil
import subprocess
from typing import Any, Dict, Optional

from .config import DEFAULT_HTTP_PORT, DEFAULT_WS_PORT

logger = logging.getLogger("NapCatMCP.Docker")

DOCKER_BIN = os.environ.get("NAPCAT_DOCKER_BIN", "docker")
CONTAINER_NAME = os.environ.get("NAPCAT_DOCKER_CONTAINER", "napcat-mcp")
IMAGE = os.environ.get("NAPCAT_DOCKER_IMAGE", "mlikiowa/napcat-docker:latest")
BIND_ADDRESS = os.environ.get("NAPCAT_DOCKER_BIND", "127.0.0.1")
WEBUI_PORT = int(os.environ.get("NAPCAT_WEBUI_PORT", "6099"))
WEBUI_TOKEN = os.environ.get("NAPCAT_WEBUI_TOKEN", "")
DOCKER_TIMEOUT = float(os.environ.get("NAPCAT_DOCKER_TIMEOUT", "120"))
PULL_TIMEOUT = float(os.environ.get("NAPCAT_DOCKER_PULL_TIMEOUT", "600"))


def _run(args: list[str], timeout: Optional[float] = None) -> subprocess.CompletedProcess:
    """Run a Docker CLI command without raising so callers can inspect failures."""
    command = [DOCKER_BIN, *args]
    options: Dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "timeout": timeout or DOCKER_TIMEOUT,
        "check": False,
    }
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        return subprocess.run(command, **options)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(command, 1, "", str(exc))


def docker_available(timeout: float = 10.0) -> bool:
    """Return True only when the Docker CLI and daemon are both reachable."""
    if not shutil.which(DOCKER_BIN):
        return False
    result = _run(["version", "--format", "{{.Server.Version}}"], timeout=timeout)
    return result.returncode == 0 and bool(result.stdout.strip())


def docker_version(timeout: float = 10.0) -> str:
    result = _run(["version", "--format", "{{.Server.Version}}"], timeout=timeout)
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def image_exists(image: Optional[str] = None) -> bool:
    reference = image or IMAGE
    result = _run(["image", "inspect", "--format", "{{.Id}}", reference], timeout=15)
    return result.returncode == 0 and bool(result.stdout.strip())


def pull_image(image: Optional[str] = None) -> Dict[str, Any]:
    reference = image or IMAGE
    result = _run(["pull", reference], timeout=PULL_TIMEOUT)
    if result.returncode != 0:
        return {
            "status": "failed",
            "image": reference,
            "error": (result.stderr or result.stdout).strip() or "docker pull 失败",
        }
    return {"status": "pulled", "image": reference}


def container_exists() -> bool:
    result = _run(["inspect", "--format", "{{.Id}}", CONTAINER_NAME], timeout=10)
    return result.returncode == 0 and bool(result.stdout.strip())


def container_running() -> bool:
    result = _run(["inspect", "--format", "{{.State.Running}}", CONTAINER_NAME], timeout=10)
    return result.returncode == 0 and result.stdout.strip().lower() == "true"


def container_image() -> str:
    result = _run(["inspect", "--format", "{{.Config.Image}}", CONTAINER_NAME], timeout=10)
    return result.stdout.strip() if result.returncode == 0 else ""


def container_status() -> Dict[str, Any]:
    status = {
        "name": CONTAINER_NAME,
        "exists": container_exists(),
        "running": False,
        "image": "",
    }
    if status["exists"]:
        status["running"] = container_running()
        status["image"] = container_image()
    return status


def _host_path(path: str) -> str:
    """Docker volume syntax uses forward slashes, including Windows drive paths."""
    return os.path.abspath(path).replace("\\", "/")


def _volume_args(data_dir: str, qq_dir: str) -> list[str]:
    return ["-v", f"{_host_path(data_dir)}:/app/napcat",
            "-v", f"{_host_path(qq_dir)}:/app/.config/QQ"]


def _identity_args() -> list[str]:
    uid = getattr(os, "getuid", None)
    gid = getattr(os, "getgid", None)
    args: list[str] = []
    if callable(uid) and callable(gid):
        args.extend(["-e", f"NAPCAT_UID={uid()}", "-e", f"NAPCAT_GID={gid()}"])
    return args


def create_container(
    data_dir: str,
    qq_dir: str,
    http_port: int = DEFAULT_HTTP_PORT,
    ws_port: int = DEFAULT_WS_PORT,
    webui_port: int = WEBUI_PORT,
    image: Optional[str] = None,
) -> Dict[str, Any]:
    """Create (but do not start) a container so config can be written first."""
    reference = image or IMAGE
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(qq_dir, exist_ok=True)
    if container_exists():
        return {
            "status": "exists",
            "container": CONTAINER_NAME,
            "image": reference,
        }
    args = [
        "create",
        "--name", CONTAINER_NAME,
        "--restart", "unless-stopped",
        "-p", f"{BIND_ADDRESS}:{http_port}:{http_port}",
        "-p", f"{BIND_ADDRESS}:{ws_port}:{ws_port}",
        "-p", f"{BIND_ADDRESS}:{webui_port}:{webui_port}",
        *_volume_args(data_dir, qq_dir),
        "-e", "MODE=ws",
        *_identity_args(),
    ]
    if WEBUI_TOKEN:
        args.extend(["-e", f"WEBUI_TOKEN={WEBUI_TOKEN}"])
    args.append(reference)
    result = _run(args, timeout=60)
    if result.returncode != 0:
        return {
            "status": "failed",
            "container": CONTAINER_NAME,
            "image": reference,
            "error": (result.stderr or result.stdout).strip() or "docker create 失败",
        }
    return {"status": "created", "container": CONTAINER_NAME, "image": reference}


def start_container() -> Dict[str, Any]:
    """Start an existing container, or create one from the local image if needed."""
    if container_running():
        return {"status": "already_running", "container": CONTAINER_NAME}
    if container_exists():
        result = _run(["start", CONTAINER_NAME], timeout=60)
        if result.returncode != 0:
            return {
                "status": "failed",
                "container": CONTAINER_NAME,
                "error": (result.stderr or result.stdout).strip() or "docker start 失败",
            }
        return {"status": "started", "container": CONTAINER_NAME}
    if not image_exists():
        return {
            "status": "not_installed",
            "image": IMAGE,
            "error": f"本地缺少 NapCat Docker 镜像 {IMAGE}，请先调用 deploy_or_update_napcat 完成部署。",
        }
    from .config import DOCKER_DATA_DIR, DOCKER_QQ_DIR
    created = create_container(DOCKER_DATA_DIR, DOCKER_QQ_DIR)
    if created["status"] == "failed":
        return created
    result = _run(["start", CONTAINER_NAME], timeout=60)
    if result.returncode != 0:
        return {
            "status": "failed",
            "container": CONTAINER_NAME,
            "error": (result.stderr or result.stdout).strip() or "docker start 失败",
        }
    return {"status": "started", "container": CONTAINER_NAME, "image": IMAGE}


def restart_container() -> Dict[str, Any]:
    if not container_exists():
        return start_container()
    result = _run(["restart", CONTAINER_NAME], timeout=90)
    if result.returncode != 0:
        return {
            "status": "failed",
            "container": CONTAINER_NAME,
            "error": (result.stderr or result.stdout).strip() or "docker restart 失败",
        }
    return {"status": "restarted", "container": CONTAINER_NAME}


def stop_container() -> bool:
    if not container_running():
        return False
    result = _run(["stop", CONTAINER_NAME], timeout=60)
    return result.returncode == 0


def remove_container() -> bool:
    """Stop and remove the managed container; mounted data volumes are preserved."""
    if not container_exists():
        return False
    if container_running():
        _run(["stop", CONTAINER_NAME], timeout=60)
    result = _run(["rm", CONTAINER_NAME], timeout=60)
    return result.returncode == 0


def deploy_container(
    data_dir: str,
    qq_dir: str,
    http_port: int = DEFAULT_HTTP_PORT,
    ws_port: int = DEFAULT_WS_PORT,
    webui_port: int = WEBUI_PORT,
    image: Optional[str] = None,
    recreate: bool = True,
) -> Dict[str, Any]:
    """Pull the image and (re)create + start the managed NapCat container."""
    reference = image or IMAGE
    pulled = pull_image(reference)
    if pulled["status"] == "failed":
        return pulled
    if recreate:
        remove_container()
    created = create_container(data_dir, qq_dir, http_port, ws_port, webui_port, reference)
    if created["status"] not in ("created", "exists"):
        return created
    started = start_container()
    if started["status"] not in ("started", "already_running"):
        return started
    return {
        "status": "success",
        "container": CONTAINER_NAME,
        "image": reference,
        "data_directory": _host_path(data_dir),
        "qq_directory": _host_path(qq_dir),
        "ports": {
            "http": http_port,
            "ws": ws_port,
            "webui": webui_port,
        },
    }


def local_version() -> Dict[str, Any]:
    """Best-effort version description of the running Docker runtime."""
    reference = container_image() or IMAGE
    tag = reference.rsplit(":", 1)[-1] if ":" in reference else "latest"
    return {
        "deployment_mode": "docker",
        "image": reference,
        "image_tag": tag,
        "container": container_status(),
    }
