This repo contains a declarative, GitOps definition of my homelab.

# Notes

I have been running Kubernetes via [Talos Linux under Proxmox VE](https://www.talos.dev/v1.3/talos-guides/install/virtualized-platforms/proxmox/) for easy reinstalls when I inevitably screw something up.

Partially due to this, there is no PXE boot installation procedure - I found it easy enough to spin up new VM:s and installing Talos manually via Proxmox' web interface.

# Prerequisites

To follow this guide, you need the following:

- One or more x86_64 hosts to run this on.
- The [nix package manager](https://nixos.org/download.html).
- A [fork of this repo](https://github.com/FruitieX/homelab/fork).
- A GitHub personal access token with repo permissions. See the GitHub documentation on [creating a personal access token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token).
- A GPG key for encrypting & decrypting secrets with Mozilla SOPS. Follow [this guide](https://fluxcd.io/flux/guides/mozilla-sops/#generate-a-gpg-key) until `Store the key fingerprint as an environment variable`.
- NFS mounts on a NAS if any of your workloads need persistent storage.
- A domain name if you want to access your cluster's services from outside your home network.

  In addition to an A record pointing directly at my home IP, I've also configured a wildcard CNAME record pointing at my domain:

  ```
  ;; A Records
  example.org.	    60	IN	A	      <my home IP>

  ;; CNAME Records
  *.example.org.	  60	IN	CNAME	  example.org.
  ```

  The idea is that any HTTP requests to subdomains are forwarded to the corresponding service by `ingress-nginx` running in the cluster.

# Installation

1.  Install [Proxmox VE](https://www.proxmox.com/en/downloads/category/iso-images-pve) on your host(s). Optional but recommended: [set up `pve-no-subscription` updates repo](https://www.virtualizationhowto.com/2022/08/proxmox-update-no-subscription-repository-configuration/)

2.  Spin up any number of VM:s and [install Talos Linux](https://www.talos.dev/v1.3/talos-guides/install/virtualized-platforms/proxmox/) on each of them. My toy cluster (with only one physical machine mind you) runs:

    - One control plane node with 4 GB RAM
    - Two worker nodes with 8 GB RAM each

3.  While installing, note down the following details into an .envrc file:

    ```
    # IP address of your control plane node, printed to machine TTY in the `Start Control Plane Node` section.
    export CONTROL_PLANE_IP="192.168.10.206"

    # Path to your talosconfig, created in the `Generate Machine Configurations` section.
    export TALOSCONFIG="_out/talosconfig"

    # Path to your kubeconfig, created in the `Retreive the kubeconfig` section.
    export KUBECONFIG="./kubeconfig"

    # Paste your GitHub personal access token from the prerequisites here
    export GITHUB_TOKEN="ghp_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"

    # Paste your GPG key fingerprint from the prerequisites here
    export SOPS_PGP_FP="XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"

    export NIXPKGS_ALLOW_UNFREE=1

    # Uncomment if using nix-direnv
    # use nix
    ```

4.  If using nix-direnv:

    - Approve the .envrc with `direnv allow`

    If using nix-shell manually:

    - Enter the nix shell using `nix-shell`.
    - Source the .envrc file into your shell.

    Verify that you can now run e.g. `kubectl get nodes -o wide` to list your nodes.

5.  Edit the configuration to match your setup. You will probably at least want to look at:

    - [/metallb-config/ipaddresspool.yaml](/metallb-config/ipaddresspool.yaml)

      Sets the IP address pool that [MetalLB](https://metallb.universe.tf/) uses when exposing services to your home network.

      Make sure it's outside the range of your router's DHCP allocation pool (e.g. 192.168.1.0/24) so you don't get collisions.

    - [/apps/kustomization.yaml](/apps/kustomization.yaml)

      Comment out all apps and start adding back only the ones you need. I recommend starting out with only `podinfo` as it requires minimal configuration.

    - [/clusters/homelab/cluster-config.yaml](/clusters/homelab/cluster-config.yaml)

      Contains configuration values that can be interpolated with `${CONFIG_KEY}` syntax into most other files:

      - `LETSENCRYPT_CLUSTER_ISSUER`: You will probably want to set this to `letsencrypt-staging` until you have your domain name / DNS / port forwards etc set up correctly. This is to avoid hitting Let's Encrypt's very strict rate limits.

      Feel free to add more configuration values here as needed.

6.  Replace SOPS encrypted secrets in the configuration.

    The repository contains a number of secrets that have been encrypted using my GPG key with Mozilla SOPS. You (and your cluster) won't be able to read these encrypted values, and attempting to deploy them as such will probably fail.

    Thankfully you can still open these yaml files containing the secrets, and see what keys (as in key/value pair) they contain.
    Search for `sops:` to find remaining encrypted secrets. See below for a list of files you may want to modify.

    The procedure for each secrets file is as follows:

    - Open up a secret file in your text editor and remove the entire `sops:` section at the end.

    - Replace all values under `stringData` with their plain-text representations. If you're not sure what the values refer to, search for them in the repo. Remove any secrets that you don't think you will need.

    - Re-encrypt the file using your GPG key by running: `sops --encrypt --in-place path/to/secrets-file.yaml`

    You will probably at least want to look at:

    - [/clusters/homelab/cluster-secrets.yaml](/clusters/homelab/cluster-secrets.yaml)

      Contains secret config values that can be interpolated with `${SECRET_CONFIG_KEY}` syntax into most other files:

      - `PIHOLE_PASSWORD`: Password used to access the Pi-hole web interface.
      - `LETSENCRYPT_EMAIL`: Your e-mail address so letsencrypt can contact you if needed (certificate expiration warnings).
      - `FLUX_NOTIFICATION_DOMAIN_NAME`: Domain name for your flux webhook notification endpoint. For example `flux-notification.example.org`
      - `PODINFO_DOMAIN_NAME`: Domain name where the `podinfo` example application will be exposed. For example `podinfo.example.org`

      Other values can be removed for now. Feel free to add more secret values here as needed.

      Remember to re-encrypt the file before you commit.

    - [/infrastructure/controllers/github-webhook-token.yaml](/infrastructure/controllers/github-webhook-token.yaml)

      Note: If you don't want to use flux webhook receivers, remove the `infrastructure/*/github-webhook-*.yaml` files from the `kustomization.yaml` file next to them.

      If you do want to use [flux webhook receivers](https://fluxcd.io/flux/guides/webhook-receivers/), then this file should contain a randomly generated `token` that flux will use to verify that it's GitHub sending the HTTP request and not somebody else.

      Replace the `token` value with the output of `head -c 12 /dev/urandom | shasum | cut -d ' ' -f1`. Take note of the value, as you will need it later.

      Remember to re-encrypt the file before you commit.

7.  Bootstrap your cluster with flux

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
    flux bootstrap github --owner=MyGitHubUser --repository=homelab --personal --path=clusters/homelab
    ```

8.  Optional: Set up GitHub push event webhook notifications

    Normally, flux will poll your repository for changes. This means there will be a delay between when you push until flux picks up on the changes. You can get around this by manually running `flux reconcile kustomization flux-system`, or by setting up webhook receivers so that GitHub will notify your cluster of the push event.

    Assuming you have saved an encrypted token into `github-webhook-token.yaml` from an earlier step, you should be able to run `kubectl -n flux-system get receiver`. Note down the receiver URL (i.e. `/hook/...`)

    The domain name address is configured in [/clusters/homelab/github-webhook-receiver.yaml](/clusters/homelab/github-webhook-receiver.yaml)

    On GitHub, navigate to your repository and click on the “Add webhook” button under “Settings/Webhooks”. Fill the form with:

    - Payload URL: compose the address using the receiver LB and the generated URL http://<LoadBalancerAddress>/<ReceiverURL>
    - Secret: use the token string
