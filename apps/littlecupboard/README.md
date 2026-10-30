# LittleCupboard

Family shopping lists, recipes and lunch planning ([FruitieX/littlecupboard](https://github.com/FruitieX/littlecupboard)). The HTTPS route attaches only to the private Envoy gateway, so the app is reachable from the LAN or the VPN. Accounts are required for all household data and images.

## Deployment

- One replica with the `Recreate` strategy: the SQLite database must only ever have one server. The pod runs as UID/GID 10001 with a read-only root filesystem.
- The image is pinned by digest in `deployment.yaml`. Every validated `main` build of the app repository deploys automatically through this repository's `image-update` workflow (see [docs/configuration.md](/docs/configuration.md)); the app repository needs the `HOMELAB_DISPATCH_TOKEN` Actions secret.
- Flux Kustomization `littlecupboard` (`clusters/homelab/apps/littlecupboard.yaml`).

## Storage

- PVC `littlecupboard-data-nfs` (5 GiB) mounted at `/data`, bound to a static PV of the same name. It points at the NAS directory `forkbasket-data-nfs`, which keeps the app's old name so the data never had to move. Retain reclaim policy.
- The database is `/data/forkbasket.sqlite3`: accounts, shopping and meal state, recipes, images and encrypted AI provider credentials.
- `LITTLECUPBOARD_SQLITE_JOURNAL_MODE=DELETE`: rollback journaling instead of WAL, which is unsafe on NFS.

## Secrets and sign-in

`secret.sops.yaml` (`littlecupboard-server-env`) holds the setup token, the key that encrypts provider credentials in the database, the Pocket ID client secret and the OIDC account map. The non-secret `LITTLECUPBOARD_OIDC_*` settings are in `deployment.yaml` (client `littlecupboard`, group `family`). Email/password sign-in also works. Back up the encryption key together with any database backup: provider credentials can't be decrypted without it.

## Operations

```sh
export KUBECONFIG="$PWD/kubeconfig"
flux reconcile kustomization littlecupboard
kubectl get pods -n default -l app=littlecupboard
curl --fail https://littlecupboard.fruitiex.org/api/ready
```

Take a consistent backup with a new filename each time (the command refuses to overwrite), then copy it off the volume:

```sh
kubectl exec -n default deploy/littlecupboard -- littlecupboard-api --backup /data/backup-$(date +%F).sqlite3
```

Settings in the app also offers household-only backup and restore. Restores, schema upgrades and rollbacks are covered in the app repository's [deployment guide](https://github.com/FruitieX/littlecupboard/blob/main/docs/DEPLOYMENT.md); before rolling back to an older release, restore the backup taken before its schema upgrade.

The optional AI assistant can use an OpenAI-compatible endpoint configured in Settings, or a personal companion worker running outside the cluster that polls the app over HTTPS (no inbound port). See the app repository's companion guide.
