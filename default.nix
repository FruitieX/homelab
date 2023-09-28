{ pkgs ? import (fetchTarball "https://github.com/NixOS/nixpkgs/archive/a9f6c4e42df9296e3994fdf1f6af9ec99ec385bc.tar.gz") {}
}:
pkgs.mkShell {
  name = "homelab";
  buildInputs = with pkgs; [
    gnupg
    kubectl
    sops
    fluxcd
    pre-commit
  ];
}
