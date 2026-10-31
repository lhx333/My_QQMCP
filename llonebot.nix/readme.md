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
