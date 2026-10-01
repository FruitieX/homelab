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

ForkBasket's application CI is configured to deploy every successfully validated and published main build through this repository's `image-update` dispatch workflow, following add-bot's existing pattern. The payload includes the immutable registry digest and tested source SHA. The receiver updates only the image in `deployment.yaml`, commits it, and Flux deploys it; historical migration records below remain unchanged. Image-update jobs are serialized to avoid competing Git pushes. PRs and release tags do not deploy, and the sender skips older main revisions.

The application repository must have a `HOMELAB_DISPATCH_TOKEN` Actions secret containing a fine-grained PAT restricted to this homelab repository with Contents read/write. The receiver needs no additional secret: it commits with its built-in `GITHUB_TOKEN`. Until the sender secret is configured, ForkBasket CI warns and skips deployment while still publishing the image. Token activation and direct transfer from 1Password are documented in [the application deployment guide](https://github.com/FruitieX/forkbasket/blob/main/docs/DEPLOYMENT.md). Confirm the application CI deployment job, this repository's image-update run, and Flux Ready before considering automatic deployment active.

Automatic deployment is active and was verified on 2026-10-01. The dispatch secret was installed directly from the user's scoped 1Password service account. ForkBasket's deployment job for tested main release `cfba1c4` dispatched successfully, receiver run `36860915497` committed its source tag and digest in `dcfe140`, and Flux automatically reached Ready/Healthy at that commit. The running pod used digest `sha256:b524336ceca98ad39d9b37413187c3cecef0ec4f90f508d80ac8941a7120cf74`, with one ready replica and the existing NFS claim. This release includes the desktop settings layout fix. Future successful main builds follow the same path.

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

## Personal Hermes companion — 2026-10-01

The application now supports a personal companion on Pear that polls its existing private HTTPS origin. No additional ingress, inbound agent port or OAuth secret was added to Kubernetes. The scoped worker token is stored only as a hash in SQLite and as a mode-0600 pairing file in the dedicated personal Hermes profile. The household uses `gpt-6-luna` with `xhigh` reasoning and `gpt-image-2-medium` for native images. ChatGPT credentials remain on Pear.

Source `bfafba0` passed full CI/container checks and automatically deployed digest `sha256:2b5017604a326d627385b4f48ec761653d91f401b7770533065e284304c80cc9` in revision `a74b8ce`. Flux reported Ready/Healthy with one ready replica. The real HTTPS queue check confirmed native model discovery, chat and a private generated image; unauthenticated image access is denied. A consistent pre-upgrade database backup is `/data/before-hermes-companion-20261001.sqlite3`. This release migrates the database to schema version 2; restore that backup before reverting to a version that only understands schema 1.

The user service is defined in the private NixOS repository and activated via Home Manager. Switching Pear's NixOS generation makes the new unit part of system boot activation. See the private application's [companion guide](https://github.com/FruitieX/forkbasket/blob/main/docs/HERMES-COMPANION.md) for pairing, revocation, worker operation and the native-auth support boundary. AI suggestions retain the app's existing review step; these connection checks did not alter shopping items, recipes or meal plans.

## Media/settings and connection recovery — 2026-10-01

Media/settings source `cc4ba39` passed complete CI and automatically deployed image digest `sha256:3e6ead33156563ad2c68eed7f5d6d2093981f6fe748be04164f2fd619713de03` through homelab revision `bb166ec`. The private app has one ready replica on NFS. This release adds recipe transcription from upload/camera/clipboard, safe Markdown descriptions, reference-guided transparent item icons stored at 256 px, optional generated recipe-draft photos, shop/family/group editing, less UI clutter, and collapsed owner-only AI setup and advanced prompts. The personal worker was restarted with native image transparency and vision support. Its consistent pre-schema-3 backup is `/data/before-media-settings-20261001.sqlite3`.

At the user's request, accidental AI configuration deletion was repaired by administratively pairing the existing personal Hermes profile again with `gpt-6-luna` / `xhigh` and `gpt-image-2-medium`. The scoped worker token was transferred in memory into the profile's private pairing file; ChatGPT credentials, company CLI auth, household content and the default Hermes account were unchanged. The actual private HTTPS queue passed model discovery and chat afterward. Backup `/data/before-ai-repair-20261001.sqlite3` preserves the state before repair, and `/data/before-settings-management-20261001.sqlite3` is a full consistent backup after reconnection and before the next schema upgrade.

Settings/pantry source `2651e3c` passed application CI `36899757079` and receiver `36901998867`, deploying digest `sha256:66617271a3f08cfcaea0b4bc502dcbe250a1845378c5010bbf5289eae3e73902` through homelab revision `13dd8e6`. Flux reached Ready/Healthy with one ready replica. It adds confirmed AI disconnection/revocation/token replacement inside collapsed setup, searchable item/recipe management, reversible catalog archives and household product overrides, and confirmed shop/group/recipe removal. Custom products may be permanently deleted after recipe references are removed; built-in products retain stable identities and are archived/restored instead. The pantry conflict fix compares equivalent JSON numbers (`2` / `2.0`) without weakening real concurrent-edit checks. Schema 4 guards catalog customization/archive JSON; restore the pre-upgrade database backup before rolling back to a schema-3 binary.

Follow-up source `d317ad3` clears the restored item's entire pantry entry when moving it out of Recently checked, including surplus released by a changed meal plan, and reverses that purchase's meal credits. Shopping quantity/notes, unrelated pantry entries and prior purchase history are preserved. Server and immediate offline client behavior agree. Failed outbox entries now have their own Try syncing action, retaining the original concurrent-edit guards; pantry changes show a descriptive label instead of `patch`. Existing unwanted pantry records can be removed with X after updating the app; this release does not purge household records automatically. Local verification passed 46 Rust tests, 25 frontend tests, TypeScript, formatting, Clippy, production two-device/offline browser regressions (including one injected failed pantry removal followed by retry), and reproducible container restart/backup/restore checks. A real mobile retry-dialog screenshot was reviewed. The follow-up keeps schema 4.

The follow-up passed complete application CI/deployment run `36902783617` and homelab receiver `36905089102`. Revision `da1109f` pins `d317ad38a99322a5783562ec35745be0d0bfebc8@sha256:6dc36b5a5a4ead1a3386e993e21896e928fe968c071c9776c7ed0fbe5650ed7a`. Flux reached Ready/Healthy at that revision at 18:14:20 UTC, with one ready replica. Private HTTPS readiness returns 200, account setup remains complete, anonymous state access returns 401, and the served versioned frontend contains the retry and protected Settings flows. The post-rollout private companion check passed model discovery and chat with `gpt-6-luna` / `xhigh`; its user service remains active. No shopping, recipe or meal-plan data was changed by deployment checks. Refresh/reopen all app tabs to pick up the new service-worker version before retrying old queued pantry changes.

Production PWA verification confirms a standalone manifest, 192/512 PNG icons, accessible precached assets, an activated service worker and no Chromium installability errors with a disposable persistent profile. Android users can install through Chrome → Add to home screen → Install while connected to the LAN/VPN; a physical Android installation was not tested.

## Automatic and bulk item icons — 2026-10-01

Source `dca6092` adds owner-only automatic item icons (default off) and a counted, confirmed **Generate missing icons** batch under Settings → AI assistant → Configure AI assistant → Compatibility & image generation. Generic cart/empty placeholders with no attached image are eligible, including catalog entries; distinctive emoji artwork, existing uploads/generated images and archived items are preserved. Enabling automatic generation applies to newly saved shopping products, recipe ingredients and accepted assistant proposal products. Existing items use the explicit bulk action.

The server processes one icon at a time through the saved API provider or existing personal companion, using the current flat item prompt/reference style, transparent backgrounds and 256 px storage. Persistent jobs deduplicate household/product requests, survive closed browsers and restart, expose progress/stop/retry controls, and retain a rolling allowance of 20 background attempts per household per hour. Remaining jobs wait for the next allowance; failures need explicit retry. Results recheck concurrent artwork, name/group edits, archival/deletion and cancellation before an atomic upload/state/history commit and family SSE update. A stopped in-flight provider request may still consume quota, and a request interrupted by a server crash may repeat. Manual batches adopt queued automatic jobs and survive switching automatic generation off.

Schema 5 adds the persistent queue/attempt tables and protects the new strict provider setting. A consistent pre-upgrade schema-4 backup is `/data/before-item-icons-20261001.sqlite3`; restore it with its matching encryption key before reverting to a schema-4 binary. OAuth/pairing, NFS and private ingress are unchanged. Local verification passed 50 Rust tests, 25 frontend tests, TypeScript, formatting, Clippy, complete mobile/tablet/desktop production browser regressions, real screenshot review, reproducible Nix packaging and non-root/read-only container persistence/restart/private-backup/restore checks. The preference is left off and no production bulk batch has been requested.

Complete application CI/deployment run `36911868943` succeeded. The receiver committed image `dca60922c541972be16b8d968dbf052502b55013@sha256:d5477ca67135216b3f1675a96c6e0aed9e1b6b71bc64162cbf036699fe72c43a` in homelab revision `f7016f8`. Flux reached Ready/Healthy at that revision at 19:25:46 UTC, with one ready replica. Private readiness returned 200, setup remained complete, anonymous state and icon-job access returned 401, and the versioned frontend serves the automatic/bulk/progress/stop/retry controls. The post-rollout personal companion model discovery/chat check passed with `gpt-6-luna` / `xhigh`; no worker update or credential rotation was needed. Finish syncing, then refresh/reopen all app tabs to pick up the updated service worker. Deployment checks did not change shopping, recipes, meal plans or request production icons.

## NFS migration — 2026-10-01

The old claim could not have its storage class changed in place, so migration used a new claim. The one-time helper files are available in Git history at `3ba43a7` and were retired when the old storage was removed. The script copied a stopped source volume into temporary local storage, recovered any remaining WAL records there, made a SQLite online backup to the empty NFS destination, enabled DELETE journaling, and compared schema/content hashes and row counts for every table. It also copied ancillary application files; ext4 `lost+found` and obsolete WAL/SHM files were excluded. Both source and destination passed full integrity checks. Destination files are owned by UID/GID 10001, and the database is mode 0600. The source mounted read-only and was never altered by the copier.

Historical procedure for a fresh migration, with an empty destination claim and the helper files from `3ba43a7`:

1. Take a consistent backup, suspend the `forkbasket` Flux Kustomization, and scale the Deployment to zero. Wait until every app pod is deleted before copying; do not force-delete an app pod that may still be writing.
2. Create the helper ConfigMap: `kubectl create configmap forkbasket-nfs-migration-script -n default --from-file=migrate-to-nfs.py=apps/forkbasket/migrate-to-nfs.py --dry-run=client -o yaml | kubectl apply -f -`.
3. Apply `migration-job.yaml.example` and wait for successful completion. Prefer scheduling it on the previous app node to reuse the iSCSI attachment. Inspect `kubectl logs -n default job/forkbasket-nfs-migration`; it must report `status: verified`, identical full content hashes and `ok` integrity checks.
4. Switch the Deployment to the NFS claim, DELETE journaling and a tested image, and restore one replica through Git. Remove the maintenance Job/ConfigMap before starting the app, resume Flux, and reconcile the pushed revision. Verify readiness, the NFS claim/mount, private ingress and a full database backup.

The script refuses to overwrite a nonempty destination, including a previous migration. Never rerun it against the active NFS database. The old claim has since been deleted. For recovery, stop the app and restore a consistent database backup with its matching SOPS encryption key.

The cutover copier verified identical content hash `babef21cd8e3a29fbb6671aa5f6c7492a855cdc253e0f8cb89dcecf9f58a577e`: one household, two accounts, two sessions, 14 events and 14 idempotent operations. Both full integrity checks passed. A separate private `pre-nfs-2026-10-01.sqlite3` backup was copied too. The cutover release was `296304a7cd3d177531cbe6d661b98d10ff5646e2`, digest `sha256:62bd4dee085a306ad7a847106143ead873a4f6cdc8f86bd6449b954411716f5b`; its complete CI validation and registry publication passed before cutover. See `deployment.yaml` for the current release.

Post-cutover verification passed: Flux resumed and Ready on `3ba43a7`, one healthy pod using the exact image digest, a bound NFS CSI volume, private HTTPS readiness, preserved account setup and a mobile browser without runtime errors. A consistent backup from the running app passed integrity checks, confirmed DELETE journaling, and still matched the complete cutover hash above. `/data` contains the private database and pre-migration backup, with no WAL/SHM files. The public gateway continues to have no ForkBasket route.

At the user's subsequent request, the old storage was retired after confirming the deployment uses NFS and no pod/VolumeAttachment references the old volume. Only PV `pvc-7f4a4516-00ac-4e89-b937-a2783dd4f885` was changed from Retain to Delete; the NFS PV and cluster storage classes were unchanged. Deleting the old claim caused Synology CSI to delete LUN `6d7aceac-164e-4e4c-a00a-bb0718c22eb8`, with the provisioner recording `volume deleted` and `persistentvolume deleted succeeded` at 2026-10-01 10:40:08 UTC. Both old Kubernetes resources are absent; the NFS claim is Bound, the app is ready over HTTPS, and Flux is Ready on retirement revision `e6a6c89`.
