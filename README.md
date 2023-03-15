This repo contains the gitops definition of my homelab.

# Installation

Install [Talos Linux](https://www.talos.dev/v1.3/talos-guides/install/) on your nodes.

I have been [running Talos under Proxmox VE](https://www.talos.dev/v1.3/talos-guides/install/virtualized-platforms/proxmox/) for easy reinstalls when I inevitably screw something up.

Export the public and private keypair from your local GPG keyring and create a Kubernetes secret named sops-gpg in the flux-system namespace:

gpg --export-secret-keys --armor "${KEY_FP}" |
kubectl create secret generic sops-gpg \
--namespace=flux-system \
--from-file=sops.asc=/dev/stdin
