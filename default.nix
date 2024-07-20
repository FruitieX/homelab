{ pkgs ? import (fetchTarball "https://github.com/NixOS/nixpkgs/archive/63911cd477b27ee05240f565e26dc9a545270430.tar.gz") {}
}:
pkgs.mkShell {
  name = "homelab";
  buildInputs = with pkgs; [
    gnupg
    kubectl
    sops
    fluxcd
    talosctl
    pre-commit
  ];
}
