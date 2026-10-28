{ pkgs ? import (fetchTarball {
    # nixos-unstable, 2026-10-28: kubectl 1.37 and talosctl 1.14 match the
    # cluster (kubectl supports one minor of skew; talosctl should match the
    # Talos minor). Bump when the cluster moves on.
    url = "https://github.com/NixOS/nixpkgs/archive/31ecbaca1bdefa45dffc409f0250bd8039ea0010.tar.gz";
    sha256 = "sha256-dhoW6VgFWlaodSY5jrBn+CF2FLmaUvDVgM7+C7T2+NM=";
  }) {}
}:
pkgs.mkShell {
  name = "homelab";
  buildInputs = with pkgs; [
    gnupg
    kubectl
    sops
    fluxcd
    talosctl
    sshpass
    pre-commit
    (python3.withPackages (ps: [ ps.pyyaml ]))
    kustomize
    kubeconform
    yq-go
  ];
}
