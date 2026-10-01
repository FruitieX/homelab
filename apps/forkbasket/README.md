# ForkBasket

Private family shopping, recipes and lunch planning at **https://forkbasket.fruitiex.org**. Deployed on 2026-10-01 through Flux, using the private Envoy HTTPS gateway at `192.168.11.99`. The public gateway has no ForkBasket route.

The pod uses the existing `ghcr-credentials` pull secret, UID/GID 10001, a read-only root filesystem and one replica with Recreate. The image is pinned to a tested release digest in `deployment.yaml`.

Active storage is the 5 GiB `forkbasket-data-nfs` claim using `nfs-csi`, mounted at `/data`. On **nectarine.internal.fruitiex.org**, the directory is **`/volume3/homelab/forkbasket-data-nfs`**. The app database is `forkbasket.sqlite3`; accounts, shopping/meal state, recipes, images and encrypted AI credentials are inside it. NFSv4.1 uses hard mounts with locking enabled. `FORKBASKET_SQLITE_JOURNAL_MODE=DELETE` selects rollback journaling and synchronous EXTRA; WAL is unsuitable for NFS. The NAS must honor file locks and durable sync operations.

The previous `forkbasket-data` iSCSI claim and its Synology LUN were removed at the user's request after the migration was verified. Only the active NFS claim remains, with Flux pruning disabled and a Retain reclaim policy. A private pre-migration database backup remains on NFS. Keep regular backups of the active NFS database and its matching SOPS secret.

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

The shopping list defaults to one tile grid in store aisle order. Settings → Shopping list → Group by category enables headings and separate grids; this account/device preference does not change another family member’s view.

## Operations

```sh
export KUBECONFIG="$PWD/kubeconfig"
flux reconcile kustomization forkbasket
kubectl get kustomization forkbasket -n flux-system
kubectl get pods -n default -l app=forkbasket
kubectl get pvc forkbasket-data-nfs -n default
curl --fail https://forkbasket.fruitiex.org/api/ready
```

Take a consistent full database backup using `kubectl exec -n default deploy/forkbasket -- forkbasket-api --backup /data/backup-2026-10-01.sqlite3`, choosing a unique filename each time, then copy it off the PVC with `kubectl cp`. The command refuses to overwrite an existing destination. Household-only backups/restoration are also available in Settings. Full recovery and upgrades are documented in [the application deployment guide](https://github.com/FruitieX/forkbasket/blob/main/docs/DEPLOYMENT.md).

Initial verification passed: Flux Ready, healthy pod, bound PVC, accepted private HTTPRoute, trusted HTTPS, DNS pointing to the private gateway, mobile first-owner setup without browser errors, and HTTP 404 through the public gateway.

## NFS migration — 2026-10-01

The old claim could not have its storage class changed in place, so migration used a new claim. The one-time helper files are available in Git history at `3ba43a7` and were retired when the old storage was removed. The script copied a stopped source volume into temporary local storage, recovered any remaining WAL records there, made a SQLite online backup to the empty NFS destination, enabled DELETE journaling, and compared schema/content hashes and row counts for every table. It also copied ancillary application files; ext4 `lost+found` and obsolete WAL/SHM files were excluded. Both source and destination passed full integrity checks. Destination files are owned by UID/GID 10001, and the database is mode 0600. The source mounted read-only and was never altered by the copier.

Historical procedure for a fresh migration, with an empty destination claim and the helper files from `3ba43a7`:

1. Take a consistent backup, suspend the `forkbasket` Flux Kustomization, and scale the Deployment to zero. Wait until every app pod is deleted before copying; do not force-delete an app pod that may still be writing.
2. Create the helper ConfigMap: `kubectl create configmap forkbasket-nfs-migration-script -n default --from-file=migrate-to-nfs.py=apps/forkbasket/migrate-to-nfs.py --dry-run=client -o yaml | kubectl apply -f -`.
3. Apply `migration-job.yaml.example` and wait for successful completion. Prefer scheduling it on the previous app node to reuse the iSCSI attachment. Inspect `kubectl logs -n default job/forkbasket-nfs-migration`; it must report `status: verified`, identical full content hashes and `ok` integrity checks.
4. Switch the Deployment to the NFS claim, DELETE journaling and a tested image, and restore one replica through Git. Remove the maintenance Job/ConfigMap before starting the app, resume Flux, and reconcile the pushed revision. Verify readiness, the NFS claim/mount, private ingress and a full database backup.

The script refuses to overwrite a nonempty destination, including a previous migration. Never rerun it against the active NFS database. The old claim has since been deleted. For recovery, stop the app and restore a consistent database backup with its matching SOPS encryption key.

The cutover copier verified identical content hash `babef21cd8e3a29fbb6671aa5f6c7492a855cdc253e0f8cb89dcecf9f58a577e`: one household, two accounts, two sessions, 14 events and 14 idempotent operations. Both full integrity checks passed. A separate private `pre-nfs-2026-10-01.sqlite3` backup was copied too. The active release is `296304a7cd3d177531cbe6d661b98d10ff5646e2`, digest `sha256:62bd4dee085a306ad7a847106143ead873a4f6cdc8f86bd6449b954411716f5b`; its complete CI validation and registry publication passed before cutover.

Post-cutover verification passed: Flux resumed and Ready on `3ba43a7`, one healthy pod using the exact image digest, a bound NFS CSI volume, private HTTPS readiness, preserved account setup and a mobile browser without runtime errors. A consistent backup from the running app passed integrity checks, confirmed DELETE journaling, and still matched the complete cutover hash above. `/data` contains the private database and pre-migration backup, with no WAL/SHM files. The public gateway continues to have no ForkBasket route.

At the user's subsequent request, the old storage was retired after confirming the deployment uses NFS and no pod/VolumeAttachment references the old volume. Only PV `pvc-7f4a4516-00ac-4e89-b937-a2783dd4f885` was changed from Retain to Delete; the NFS PV and cluster storage classes were unchanged. Deleting the old claim caused Synology CSI to delete LUN `6d7aceac-164e-4e4c-a00a-bb0718c22eb8`, with the provisioner recording `volume deleted` and `persistentvolume deleted succeeded` at 2026-10-01 10:40:08 UTC. Both old Kubernetes resources are absent; the NFS claim is Bound, the app is ready over HTTPS, and Flux is Ready on retirement revision `e6a6c89`.
