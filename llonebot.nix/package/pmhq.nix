{
  pkgs,
  lib,
  config,
  ...
}:
let
  qq = pkgs.callPackage ./qq/package.nix {
    libgbm = pkgs.mesa; # 显式传递 libgbm
    inherit (pkgs)
      alsa-lib
      libuuid
      cups
      dpkg
      fetchurl
      glib
      libssh2
      gtk3
      libayatana-appindicator
      libdrm
      libgcrypt
      libkrb5
      libnotify
      libpulseaudio
      libGL
      nss
      libxdamage
      systemd
      at-spi2-core
      autoPatchelfHook
      makeShellWrapper
      wrapGAppsHook4
      ;
    commandLineArgs = ""; # 可选参数
  };
  sources = import ./sources.nix { };

  boolToString = b: if b then "true" else "false";

  srcs = {
    x86_64-linux = pkgs.fetchurl {
      url = sources.pmhq_amd64_url;
      hash = sources.pmhq_amd64_hash;
    };
    aarch64-linux = pkgs.fetchurl {
      url = sources.pmhq_arm64_url;
      hash = sources.pmhq_arm64_hash;
    };
  };

  currentSystem = pkgs.stdenv.hostPlatform.system;
  src = srcs.${currentSystem} or (throw "Unsupported system: ${currentSystem}");
in

pkgs.stdenv.mkDerivation rec {
  pname = "pmhq";
  version = "${sources.pmhq_version}";

  nativeBuildInputs = [
    pkgs.autoPatchelfHook
  ];

  buildInputs = with pkgs; [
    unzip
    zlib
    libgcc
    libssh2
    curl
  ];

  inherit src;
  unpackPhase = ''
    unzip $src
  '';

  installPhase = ''
        mkdir -p $out/bin
        mv pmhq-linux-* $out/bin/source-pmhq
        mv libpmhq.so $out/bin/libpmhq.so
        cp ${pkgs.stdenv.cc.bintools.dynamicLinker} $out/bin/pmhq-loader
        chmod +x $out/bin/source-pmhq
        
        # Create wrapper script with proper library paths
        cat > $out/bin/pmhq << 'WRAPPER_EOF'
    #!/bin/sh
    export LD_PRELOAD="${pkgs.libssh2}/lib/libssh2.so.1''${LD_PRELOAD:+:$LD_PRELOAD}"
    export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath [ pkgs.libGL pkgs.libuuid pkgs.libssh2 pkgs.curl ]}''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    WRAPPER_EOF
        
        # Append QQ wrapper content (skip last line)
        head -n -1 ${qq}/opt/QQ/qq-wrapper >> $out/bin/pmhq
        
        # PMHQ 8.x resolves libpmhq.so relative to /proc/self/exe but its
        # self-decompressing ELF cannot be patched. Keep a loader beside the
        # shared object so the lookup remains inside $out/bin.
        echo "exec $out/bin/pmhq-loader $out/bin/source-pmhq \"\$@\"" >> $out/bin/pmhq
        chmod +x $out/bin/pmhq
        cat <<EOF > $out/bin/config.json
    {
      "qq_path": "${qq}/opt/QQ/qq",
      "auth-token": "${config.pmhq_auth_token}"
    }
    EOF
  '';

  # 通过 passthru 导出 qq
  passthru = {
    inherit qq;
  };

  meta = with lib; {
    description = "Pure memory hook QQNT";
    homepage = "https://github.com/linyuchen/PMHQ";
    platforms = [
      "x86_64-linux"
      "aarch64-linux"
    ];
  };
}
