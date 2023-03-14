This template repo is used to illustrate a [flux-managed Kubernetes cluster](http://localhost:8123/kubernetes/deployment/flux/), in Funky Penguin's Geek Cookbook

# Installation

Install [Talos Linux](https://www.talos.dev/v1.3/talos-guides/install/) on your hosts.

I have been [running Talos under Proxmox VE](https://www.talos.dev/v1.3/talos-guides/install/virtualized-platforms/proxmox/) for easy reinstalling when I inevitably screw something up.

I chose not to go the PXE boot route (at least for now) because

Export the public and private keypair from your local GPG keyring and create a Kubernetes secret named sops-gpg in the flux-system namespace:

gpg --export-secret-keys --armor "${KEY_FP}" |
kubectl create secret generic sops-gpg \
--namespace=flux-system \
--from-file=sops.asc=/dev/stdin
