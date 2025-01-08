{ pkgs ? import (fetchTarball "https://github.com/NixOS/nixpkgs/archive/893757b4674d2653daf6d0cab18615d2570a29cd.tar.gz") {}
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
