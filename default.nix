{ pkgs ? import (fetchTarball "https://github.com/NixOS/nixpkgs/archive/356294f35290bf10442bfc60408cbe5ccde5a202.tar.gz") {}
, talosPkgs ? import (fetchTarball {
    # Talos 1.13 machine configs need a matching client; the older package
    # set's 1.11 client cannot patch their multi-document configuration.
    url = "https://github.com/NixOS/nixpkgs/archive/5e2305d577ca00acbba631b05cb1094d172b29f3.tar.gz";
    sha256 = "sha256-/eLCD/X9kaWJqPR47+fDozEC87Hjmm3i8gJbYmDAD7g=";
  }) {}
}:
pkgs.mkShell {
  name = "homelab";
  buildInputs = with pkgs; [
    gnupg
    kubectl
    sops
    fluxcd
    talosPkgs.talosctl
    sshpass
    pre-commit
    (python3.withPackages (ps: [ ps.pyyaml ]))
    kustomize
    kubeconform
    yq-go
  ];
}
