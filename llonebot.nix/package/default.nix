{
  pkgs,
  lib ? pkgs.lib,
  ...
}:
let
  # 导入配置管理模块
  configLib = pkgs.callPackage ./config.nix { };

  # 使用配置管理模块的默认配置
  defaultConfig = configLib.defaultConfig;

  # 构建 LLOneBot 的主函数
  buildLLOneBot =
    userConfig:
    let
      config = configLib.mergeConfig userConfig;
    in
    rec {
      llonebot-js = pkgs.callPackage ./llonebot-js.nix { };

      pmhq = pkgs.callPackage ./pmhq.nix {
        inherit config;
      };

      llonebot = pkgs.writeScriptBin "llonebot" ''
        #!${pkgs.runtimeShell}

        # llonebot 工作目录
        if [ ! -f "~/.config/llonebot/llbot.js" ]; then
          mkdir -p ~/.config/llonebot
          cp -rf ${llonebot-js}/js/* ~/.config/llonebot/
        fi

        ${pmhq}/bin/pmhq --auth-token=${config.pmhq_auth_token} &

        cd ~/.config/llonebot && \
          ${pkgs.nodejs_24}/bin/node llbot.js \
          --pmhq-host=${config.pmhq_host} \
          --pmhq-port=${toString config.pmhq_port}
      '';
    };

  # 构建 LLOneBot 沙盒版本的函数
  buildLLOneBotSandbox =
    userConfig:
    let
      config = configLib.mergeConfig userConfig;
    in
    pkgs.callPackage ./sandbox.nix { inherit config; };

in
{
  inherit buildLLOneBot buildLLOneBotSandbox defaultConfig;

  # 提供默认构建结果
  default = buildLLOneBot { };
}
