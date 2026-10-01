# ForkBasket

Private family shopping, recipes and lunch planning at **https://forkbasket.fruitiex.org**. Deployed on 2026-10-01 through Flux, using the private Envoy HTTPS gateway at `192.168.11.99`. The public gateway has no ForkBasket route.

The image is pinned to the tested `a4aaf397272af9c3054f31bb55215ff040b89502` application release. The pod uses the existing `ghcr-credentials` pull secret, UID/GID 10001, a read-only root filesystem and one replica with Recreate. `forkbasket-data` is a 5 GiB ext4/iSCSI PVC using `syno-storage`; Flux pruning is disabled for the PVC and the storage class retains its PV.

Server setup and provider-encryption secrets are SOPS-encrypted in `secret.sops.yaml`. Preserve the matching encryption key with database backups. Runtime accounts, recipes, images and provider configuration are stored in SQLite on the PVC.

## First login

Open the private HTTPS site from your LAN or a VPN that can reach the private gateway. Create the owner account with the setup token, save the recovery code, and invite other family accounts from Settings. No accounts have been created automatically.

To read only the setup token in a private local terminal:

```sh
cd ~/homelab
nix-shell
sops --decrypt --extract '["stringData"]["FORKBASKET_SETUP_TOKEN"]' apps/forkbasket/secret.sops.yaml
```

Configure favorite stores/aisle order and add family recipes in the app. The AI assistant stays hidden until an OpenAI-compatible endpoint/model is enabled in Settings; provider configuration is deliberately left to the owner.

## Operations

```sh
export KUBECONFIG="$PWD/kubeconfig"
flux reconcile kustomization forkbasket
kubectl get kustomization forkbasket -n flux-system
kubectl get pods -n default -l app=forkbasket
kubectl get pvc forkbasket-data -n default
curl --fail https://forkbasket.fruitiex.org/api/ready
```

Take a consistent full database backup using `kubectl exec -n default deploy/forkbasket -- forkbasket-api --backup /data/backup-2026-10-01.sqlite3`, choosing a unique filename each time, then copy it off the PVC with `kubectl cp`. The command refuses to overwrite an existing destination. Household-only backups/restoration are also available in Settings. Full recovery and upgrades are documented in [the application deployment guide](https://github.com/FruitieX/forkbasket/blob/main/docs/DEPLOYMENT.md).

Initial verification passed: Flux Ready, healthy pod, bound PVC, accepted private HTTPRoute, trusted HTTPS, DNS pointing to the private gateway, mobile first-owner setup without browser errors, and HTTP 404 through the public gateway.
