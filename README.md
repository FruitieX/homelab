This repo contains a declarative, GitOps definition of my homelab.

# Notes

I have been running Kubernetes via [Talos Linux under Proxmox VE](https://www.talos.dev/v1.3/talos-guides/install/virtualized-platforms/proxmox/) for easy reinstalls when I inevitably screw something up.

Partially due to this, there is no PXE boot installation procedure - I found it easy enough to spin up new VM:s and installing Talos manually via Proxmox' web interface.

# Prerequisites

To follow this guide, you need the following:

- A [fork of this repo](https://github.com/FruitieX/homelab/fork).
- A GitHub personal access token with repo permissions. See the GitHub documentation on [creating a personal access token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token)
- A GPG key for encrypting & decrypting secrets with Mozilla SOPS. Follow [this guide](https://fluxcd.io/flux/guides/mozilla-sops/#generate-a-gpg-key) until "Store the key fingerprint as an environment variable".

# Installation

1. Install [Proxmox VE](https://www.proxmox.com/en/downloads/category/iso-images-pve) on your host(s). Optional but recommended: [set up `pve-no-subscription` updates repo](https://www.virtualizationhowto.com/2022/08/proxmox-update-no-subscription-repository-configuration/)

2. Spin up any number of VM:s and install [Talos Linux](https://www.talos.dev/v1.3/talos-guides/install/virtualized-platforms/proxmox/) on each of them. My toy cluster (with only one physical machine mind you) runs:

- One control plane node with 4 GB RAM
- Two worker nodes with 8 GB RAM each

3. While installing, note down the following details into an .envrc file:

```
# IP address of your control plane node
export CONTROL_PLANE_IP="192.168.10.206"

# Path to your talosconfig, created in the `Generate Machine Configurations` section.
export TALOSCONFIG="_out/talosconfig"

# Path to your kubeconfig, created in the `Retreive the kubeconfig` section.
export KUBECONFIG="./kubeconfig"

# Paste your GitHub personal access token from the prerequisites here
export GITHUB_TOKEN="ghp_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"

# Paste your GPG key fingerprint from the prerequisites here
export KEY_FP="XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"

# Uncomment if using Nix
# export NIXPKGS_ALLOW_UNFREE=1

# Uncomment if using nix-direnv
# use nix
```

4. Source the .envrc file into your shell. Verify that you can now run e.g. `kubectl get nodes -o wide` to list your nodes.

5. Export the public and private keypair from your local GPG keyring and create a Kubernetes secret named sops-gpg in the flux-system namespace:

```
kubectl create namespace flux-system

gpg --export-secret-keys --armor "${KEY_FP}" |
kubectl create secret generic sops-gpg \
--namespace=flux-system \
--from-file=sops.asc=/dev/stdin
```

6. Edit the configuration to match your setup. You will probably at least want to look at:

  - [/metallb-config/ipaddresspool.yaml](/metallb-config/ipaddresspool.yaml)

    Sets the IP address pool that [MetalLB](https://metallb.universe.tf/) uses when exposing services to your home network.

    Make sure it's outside the range of your router's DHCP allocation pool (e.g. 192.168.1.0/24) so you don't get collisions.

  - 

6. Open up [/clusters/homelab/cluster-secrets.yaml](cluster-secrets.yaml) in your text editor and remove the entire `sops` section at the end.

Replace all values under `stringData` with their plain-text representations. If you're not sure what the values refer to, search for them in the repo. Remove any secrets that you don't think you will need.

7. Encrypt the `cluster-secrets.yaml` file using `sops --encrypt --in-place clusters/homelab/cluster-secrets.yaml`

8. Repeat this process for any other SOPS encrypted Kubernetes secrets. Search for `sops:` to find them all.
