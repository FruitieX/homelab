{ pkgs ? import (fetchTarball "https://github.com/NixOS/nixpkgs/archive/356294f35290bf10442bfc60408cbe5ccde5a202.tar.gz") {}
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
