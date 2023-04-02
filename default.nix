{ pkgs ? import (fetchTarball "https://github.com/NixOS/nixpkgs/archive/143b829e58f25437e61d1f766fc9644fd9139ea0.tar.gz") {}
}:
pkgs.mkShell {
  name = "homelab";
  buildInputs = with pkgs; [
    _1password
    gnupg
    sops
    fluxcd
  ];
}
