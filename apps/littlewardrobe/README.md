# Little Wardrobe

Private family clothing inventory at **https://littlewardrobe.fruitiex.org**, available through the LAN/VPN. The HTTPS HTTPRoute attaches only to the private Envoy gateway (`192.168.11.99`). External-dns manages the private A record. The public gateway has no route for this application.

The deployment pins the tested application source `c4cb95e665056248e18aafd7d31cfc53726f4d57` and image digest `sha256:1bc82975fe97a170ac5ee206a1caf25a01b9ea5f9df5828f785601f154dcca54`. Application CI `36922591167` passed all checks, including the complete production browser suite against the packaged container. Image access uses the existing `ghcr-credentials` secret. Updates require changing the pinned image through Git; this app has no automatic homelab dispatch configured.

One replica with Recreate runs as UID/GID 10001 with a read-only root, runtime-default seccomp, temporary writable storage and startup/readiness/liveness probes. Data persists on the retained 5 GiB `littlewardrobe-data-nfs` claim (`nfs-csi`), backed by `/volume3/homelab/littlewardrobe-data-nfs` on `nectarine.internal.fruitiex.org`. `LITTLEWARDROBE_SQLITE_JOURNAL_MODE=DELETE` enables rollback journaling with synchronous EXTRA for the NFS volume. Keep one replica and preserve working NFS locks and durable writes.

## First login

The new family starts empty. Create the first owner, save the recovery code, then invite the second adult in Settings. Children, clothing sizes and storage locations are configured in Family. No accounts or household data are copied from another app.

Read only the setup token in your own private local terminal:

```sh
cd ~/homelab
nix-shell
sops --decrypt --extract '["stringData"]["LITTLEWARDROBE_SETUP_TOKEN"]' apps/littlewardrobe/secret.sops.yaml
```

The setup token and provider encryption key are freshly generated and SOPS-encrypted in `secret.sops.yaml`. Preserve that encryption key with database backups. Configure an API provider or pair the personal Hermes companion after first login; deployment does not copy another app's AI credentials or create a worker. [AI setup](https://github.com/FruitieX/littlewardrobe/blob/main/docs/AI.md).

## Operations and recovery

```sh
export KUBECONFIG="$PWD/kubeconfig"
flux reconcile kustomization littlewardrobe
kubectl get kustomization littlewardrobe -n flux-system
kubectl get pods -n default -l app=littlewardrobe
kubectl get pvc littlewardrobe-data-nfs -n default
curl --fail https://littlewardrobe.fruitiex.org/api/ready
```

Take a consistent full database backup using `kubectl exec -n default deploy/littlewardrobe -- littlewardrobe-api --backup /data/backup-YYYY-MM-DD.sqlite3`, choosing a new filename each time. Copy it off the PVC and retain the matching encryption secret. The command uses SQLite's backup API and refuses an existing destination. Settings also supports a household JSON backup with clothing photos and provenance. Stop the app before restoring the complete database; preserve UID/GID permissions and restore the matching encryption key. [Deployment and recovery guide](https://github.com/FruitieX/littlewardrobe/blob/main/docs/DEPLOYMENT.md).

## Verified rollout — 2026-10-02

Homelab revision `273db55` deployed successfully through Flux. `littlewardrobe` is Ready/Healthy with one ready replica running the exact pinned digest above; the NFS claim is Bound and retained. Private HTTPRoute conditions are Accepted and ResolvedRefs. External-dns created the private A record at 05:25:48 UTC, and normal DNS now resolves to `192.168.11.99`.

Trusted HTTPS frontend/readiness passed through normal DNS and the private gateway. The public gateway (`192.168.11.1`) and WAN (`91.159.199.233`) both returned HTTP 404 for this hostname. Anonymous household state, AI configuration, photos and SSE returned 401; a cross-origin write returned 403. Fresh mobile and desktop Chromium sessions loaded the first-owner setup screen without JavaScript errors or horizontal overflow, with a secure context, standalone manifest/icons and an activated service worker. These were browser checks from Pear, not a physical phone installation.

The running process and database are owned by UID/GID 10001; the database is mode 0600. A consistent initial backup at `/data/initial-deployment-2026-10-02.sqlite3` passed full SQLite integrity checks and confirmed DELETE journaling. The initial database has zero users and households. Create the first owner using the instructions above; provider configuration remains an owner setup step. No production family accounts or clothing data were created during verification.
