# FruitieX' homelab

This repo contains a declarative, GitOps definition of my homelab.

![Photo of my homelab](/docs/homelab.jpg)

## Components

In summary, the homelab is built up using:

- [A mini PC](https://store.minisforum.de/collections/alle-produkte-1/products/minisforum-venus-series-um560?variant=41392983572663), [NAS](https://www.synology.com/en-us/support/download/DS420+?version=7.1#system) and some [UniFi](https://eu.store.ui.com/products/unifi-dream-machine) network infrastructure
- [Kubernetes](https://kubernetes.io/), running on virtual [Talos Linux](https://www.talos.dev/) nodes in [Proxmox VE](https://www.proxmox.com/en/proxmox-ve)
- [Flux CD](https://fluxcd.io/) reconciling cluster configuration from this git repo
- [MetalLB](https://metallb.universe.tf/) & [external-dns](https://github.com/kubernetes-sigs/external-dns) expose apps on separate IP addresses with associated DNS records
- [ingress-nginx](https://github.com/kubernetes/ingress-nginx) & [cert-manager](https://cert-manager.io/) reverse proxy subdomain HTTP requests with auto-renewing [Let's Encrypt](https://letsencrypt.org/) certificates
- [nfs-subdir-external-provisioner](https://github.com/kubernetes-sigs/nfs-subdir-external-provisioner) & [synology-csi](https://github.com/SynologyOpenSource/synology-csi) provide persistent storage on NAS
- [kube-prometheus-stack](https://github.com/prometheus-operator/kube-prometheus) provides detailed metrics

[More detailed writeup](/docs/components.md)

# Prerequisites

To follow this guide, you need the following:

- One or more x86_64 hosts to run this on.
- The [nix package manager](https://nixos.org/download.html).
- [A fork of this repo](https://github.com/FruitieX/homelab/fork).
- A GitHub personal access token with repo permissions. This is needed by Flux to read your (possibly private) repo and push some initial manifests. See the GitHub documentation on [creating a personal access token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token).
- A GPG key for encrypting & decrypting secrets with Mozilla SOPS. Follow [this guide](https://fluxcd.io/flux/guides/mozilla-sops/#generate-a-gpg-key) until `Store the key fingerprint as an environment variable`.
- NFS or iSCSI mounts on a NAS if any of your workloads need persistent storage.
- A domain name if you want to provide access to certain cluster services from outside your home network.

  [Click here for more info on my DNS provider and router setup](/docs/dns.md)

# Installation

## Setting up the cluster

- Install [Proxmox VE](https://www.proxmox.com/en/downloads/category/iso-images-pve) on your host(s). Optional but recommended: [set up `pve-no-subscription` updates repo](https://www.virtualizationhowto.com/2022/08/proxmox-update-no-subscription-repository-configuration/)

- Spin up any number of VM:s and [install Talos Linux](https://www.talos.dev/v1.3/talos-guides/install/virtualized-platforms/proxmox/) on each of them. My toy cluster (running on one physical machine only mind you) runs:

  - One control plane node with 4 GB RAM
  - Two worker nodes with 8 GB RAM each

  NOTE: If you want iSCSI support, you need to apply the included `talos-machine-patch.yaml` like so:

  ```
  talosctl gen config homelab-cluster https://$CONTROL_PLANE_IP:6443 --output-dir _out --config-patch @talos-machine-patch.yaml
  ```

  NOTE: While installing, write down the following details into an .envrc file:

  ```
  # IP address of your control plane node, printed to machine TTY in the `Start Control Plane Node` section.
  export CONTROL_PLANE_IP="192.168.10.206"

  # Path to your talosconfig, created in the `Generate Machine Configurations` section.
  export TALOSCONFIG="_out/talosconfig"

  # Path to your kubeconfig, created in the `Retreive the kubeconfig` section.
  export KUBECONFIG="./kubeconfig"

  # Paste your GitHub personal access token from the prerequisites here
  export GITHUB_TOKEN="ghp_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"

  # Write down your GitHub username here
  export GITHUB_USER="XXXXXXXXXX"

  # Paste your GPG key fingerprint from the prerequisites here
  export SOPS_PGP_FP="XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"

  export NIXPKGS_ALLOW_UNFREE=1

  # Uncomment if using nix-direnv
  # use nix
  ```

- After the installation:

  - Enter the nix shell using `nix-shell`.
  - Source the .envrc file into your shell: `source .envrc`

  Verify that you can now run e.g. `kubectl get nodes -o wide` to list your nodes.

  Note that all following steps will assume your .envrc is sourced and necessary tools are installed (via entering the nix-shell or otherwise).

## Preparing your configuration for bootstrapping Flux CD

- If you are installing this for the first time, I suggest starting out with a
  clean slate and commenting out all apps and infrastructure manifests before
  proceeding. There's plenty of configuration and secrets that are specific to my setup, and it's maybe a bit much to take it all in at once.
  
  You can add manifests back later one at a time after you've bootstrapped
  a minimal cluster setup with Flux CD.

  Open up and edit the following files:

  - [/clusters/homelab/infrastructure/kustomization.yaml](/clusters/homelab/infrastructure/kustomization.yaml)

    Comment out all resources except for `sources.yaml`

  - [/clusters/homelab/apps/kustomization.yaml](/clusters/homelab/apps/kustomization.yaml)

    Comment out all resources except for `podinfo.yaml`

  - [/clusters/homelab/cluster-config.yaml](/clusters/homelab/cluster-config.yaml)

    Contains configuration values that can be interpolated with `${CONFIG_KEY}` syntax into most other files.
  
    Change the following values:

    - `LETSENCRYPT_CLUSTER_ISSUER`: Set this to `letsencrypt-staging` until you have your domain name / DNS / port forwards etc set up correctly. This is to avoid hitting Let's Encrypt's very strict rate limits.

    Feel free to eventually add more configuration values here as needed.

  - [/clusters/homelab/cluster-secrets.yaml](/clusters/homelab/cluster-secrets.yaml)

    Contains secret configuration values that can be interpolated with `${CONFIG_KEY}` syntax into most other files.

    Replace the entire file contents with:

    ```
    apiVersion: v1                                                   
    kind: Secret                                                     
    metadata:                                                        
        namespace: flux-system                                       
        name: cluster-secrets                                        
    type: Opaque                                                     
    stringData:                                                      
        # Domain name of the `podinfo` app ingress
        PODINFO_DOMAIN_NAME: podinfo.example.org                    
    ```

    Set `PODINFO_DOMAIN_NAME` to some valid domain name of your choice.

    Now it's time to encrypt the file using Mozilla SOPS:

    `sops --encrypt --in-place clusters/homelab/cluster-secrets.yaml`

    If you want to make further changes to the file, run:
    
    `sops clusters/homelab/cluster-secrets.yaml`

## Bootstrap Flux CD

```
# Create flux-system namespace or below command will fail
kubectl create namespace flux-system

# Export the public and private keypair from your local GPG keyring and
# create a Kubernetes secret named sops-gpg in the flux-system namespace
gpg --export-secret-keys --armor "${SOPS_PGP_FP}" |
kubectl create secret generic sops-gpg \
--namespace=flux-system \
--from-file=sops.asc=/dev/stdin

# Inject cluster secrets to circumvent dependency issue
sops --decrypt clusters/homelab/cluster-secrets.yaml | kubectl apply -f -

# Bootstrap flux
flux bootstrap github --owner=${GITHUB_USER} --repository=homelab --personal --path=clusters/homelab
```

# Status checks & troubleshooting

- Commands to check the status of your cluster/reconciliation:

  ```
  # Show status of your nodes
  kubectl get nodes -o wide

  # Show status of all your pods
  kubectl get pods -A

  # Show status of all flux resources
  flux get all -A

  # Follow logs from flux components
  flux logs --tail 20 -f

  # Get Kubernetes events with type=Warning
  kubectl get events -A --field-selector type=Warning
  ```

- Flux CD troubleshooting guide: https://fluxcd.io/flux/cheatsheets/troubleshooting/

## Further configuration

[Click here](/docs/configuration.md) for more information on adding back various apps and infrastructure to your configuration.
