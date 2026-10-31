# 参考

[chronocat.nix](https://github.com/Anillc/chronocat.nix) 若有侵权，请联系我删除


# 使用方法

## docker

### docker compose

```yaml
version: "3"
services:
    llonebot:
        environment:
            - AUTH_TOKEN= # PMHQ auth-token
        volumes:
            - ./QQ:/root/.config/QQ # 挂载 QQ 配置目录
            - ./llonebot:/root/llonebot # 挂载 llonebot 数据目录
        ports:
            - 3000:3000 # OneBot HTTP 端口
            - 3001:3001 # OneBot WebSocket 端口
            - 5600:5600 # Satori 端口
            - 3080:3080 # webui
        container_name: llonebot
        network_mode: bridge
        restart: always
        image: initialencounter/llonebot:latest
```

### docker cli

```bash
docker run -d \
  --name llonebot \
  -e AUTH_TOKEN="xxx" \
  -p 3000:3000 \
  -p 3001:3001 \
  -p 5600:5600 \
  -p 3080:3080 \
  -v ./QQ:/root/.config/QQ \
  -v ./llonebot:/root/llonebot \
  --restart unless-stopped \
  initialencounter/llonebot:latest
```

## 快速体验

```bash
# 沙盒运行
nix run github:LLOneBot/llonebot.nix#sandbox

# 直接在桌面运行（需要桌面环境）
nix run github:LLOneBot/llonebot.nix

# 仅运行 pmhq
nix run github:LLOneBot/llonebot.nix#pmhq
```

## NixOS 配置

无需桌面环境
[sandbox](./examples/sandbox.nix)

需要桌面环境版
[desktop](./examples/sandbox.nix)

## 登录

- 终端扫码
- WebUI http://<宿主机IP>:3080 扫码

## 配置自动登录QQ号

- 设置环境变量 QUICK_LOGIN_QQ

## 配置 PMHQ 授权令牌

- 设置环境变量 `AUTH_TOKEN`，服务会将其作为 `--auth-token` 参数传给 PMHQ

## 与 OmniQQ-MCP 集成

本仓库根目录的 `llonebot_mcp` 是面向本 LLOneBot 后端的 MCP Server。启动 LLBot 后，将其 OneBot
HTTP/WS 端口（默认 `3000`/`3001`）与 WebUI（`3080`）暴露给 MCP：

```bash
# 使用本仓库 Nix 沙盒
nix run github:LLOneBot/llonebot.nix#sandbox

# 启动远程 Streamable HTTP MCP（默认 http://127.0.0.1:8000/mcp）
LLONEBOT_HTTP_URL=http://127.0.0.1:3000 \
LLONEBOT_WS_URL=ws://127.0.0.1:3001 \
python llonebot_mcp/run_mcp.py --host 127.0.0.1 --port 8000
```

Docker 部署时将 `3000`、`3001`、`3080` 端口映射到宿主机，并通过 `AUTH_TOKEN` 配置 PMHQ
授权；MCP 侧使用 `LLONEBOT_HTTP_URL` / `LLONEBOT_WS_URL` 指向映射后的端口即可。
