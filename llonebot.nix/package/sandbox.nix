{
  pkgs,
  lib,
  config,
  ...
}:
let
  llonebot-service =
    (pkgs.callPackage ./llonebot-service.nix {
      inherit config;
    }).service;
in
rec {
  sandbox = pkgs.writeScriptBin "sandbox" ''
    #!${pkgs.runtimeShell}

    ${pkgs.bubblewrap}/bin/bwrap \
      --unshare-all \
      --share-net \
      --as-pid-1 \
      --uid 0 --gid 0 \
      --ro-bind /nix/store /nix/store \
      --ro-bind ${pkgs.tzdata}/share/zoneinfo/Asia/Shanghai /etc/localtime \
      --bind ${config.sandbox_root_dir} /root/ \
      --proc /proc \
      --dev /dev \
      --tmpfs /tmp \
      --setenv XDG_RUNTIME_DIR /tmp/runtime \
      --setenv WLR_BACKENDS headless \
      --setenv WLR_LIBINPUT_NO_DEVICES 1 \
      ${llonebot-service}/bin/llonebot-service
  '';
}
