set -e

echo "========== 开始本地模拟GitHub Actions CI流程 =========="
DOCKER_REPO="initialencounter/llonebot-arm"

echo "========== 1. 设置QEMU支持 =========="
# 在NixOS中安装qemu-user-static
if ! nix-shell -p qemu --run "qemu-aarch64 --version" &>/dev/null; then
  echo "安装QEMU支持..."
  # 在NixOS中临时安装QEMU
  nix-shell -p qemu --run "echo 'QEMU已安装'"
  
  # 对于永久安装，可以提示用户修改configuration.nix
  echo "提示: 要永久安装QEMU支持，请在configuration.nix中添加:"
  echo "  virtualisation.qemu.package = pkgs.qemu;"
  echo "  boot.binfmt.emulatedSystems = [ \"aarch64-linux\" ];"
  echo "然后运行 sudo nixos-rebuild switch"
else
  echo "QEMU支持已就绪"
fi

# 配置binfmt支持
echo "配置binfmt支持arm64架构..."
if [ -x "$(command -v docker)" ]; then
  sudo docker run --privileged --rm tonistiigi/binfmt --install arm64
else
  echo "警告: Docker未安装，无法配置binfmt，可能影响后续构建"
  echo "提示: 在NixOS中，可通过在configuration.nix添加以下配置安装Docker:"
  echo "  virtualisation.docker.enable = true;"
fi

echo "========== 3. 构建Docker镜像 =========="
# 从源代码获取版本号
if [ -f "package/sources.nix" ]; then
  VERSION=$(grep "llonebot_version = " package/sources.nix | cut -d'"' -f2)
  if [ -z "$VERSION" ]; then
    echo "警告: 无法提取版本号，使用默认值"
    VERSION="unknown"
  fi
  TAG="v$VERSION"
  echo "使用标签: $TAG"
else
  echo "警告: 未找到package/sources.nix文件，无法获取版本号"
  VERSION="unknown"
  TAG="vunknown"
fi

# 构建Docker镜像
echo "开始构建aarch64 Docker镜像..."
nix build --option system aarch64-linux --show-trace .#dockerImage -o result-aarch64