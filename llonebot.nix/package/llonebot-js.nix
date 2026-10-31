{ pkgs, lib, ... }:
let
  sources = import ./sources.nix { };
  src = pkgs.fetchurl {
    url = sources.llonebot_url;
    hash = sources.llonebot_hash;
  };
in
pkgs.stdenv.mkDerivation rec {
  pname = "llonebot-js";
  version = "${sources.llonebot_version}";
  buildInputs = [
    pkgs.unzip
  ];

  inherit src;
  unpackPhase = ''
    unzip $src -d js
  '';

  installPhase = ''
    mkdir -p $out
    mv js $out/js
    sed -i 's|"host": "127.0.0.1"|"host": ""|' $out/js/default_config.json
  '';

  meta = with lib; {
    description = "Pure memory hook QQNT";
    homepage = "https://github.com/linyuchen/PMHQ";
    platforms = [
      "x86_64-linux"
      "aarch64-linux"
    ];
  };
}
