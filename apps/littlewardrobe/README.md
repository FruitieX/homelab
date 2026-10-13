# Little Wardrobe

Private family clothing inventory at **https://littlewardrobe.fruitiex.org**, available through the LAN/VPN. The HTTPS HTTPRoute attaches only to the private Envoy gateway (`192.168.11.99`). External-dns manages the private A record. The public gateway has no route for this application.

The deployment pins the tested application source `fe0d6cc517fd4b6f325a88114fb6c4b5f239ac8b` and image digest `sha256:cda9dc974658e673ef51589ed608118411ccf95c6bd11e0c37399c770491f647`. Application CI `38428197988` passed all checks, including the complete production browser suite against the packaged container. Image access uses the existing `ghcr-credentials` secret. Updates require changing the pinned image through Git; this app has no automatic homelab dispatch configured.

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

## Verified initial rollout — 2026-10-02

Homelab revision `273db55` deployed successfully through Flux. `littlewardrobe` is Ready/Healthy with one ready replica running the initial immutable release; the NFS claim is Bound and retained. Private HTTPRoute conditions are Accepted and ResolvedRefs. External-dns created the private A record at 05:25:48 UTC, and normal DNS now resolves to `192.168.11.99`.

Trusted HTTPS frontend/readiness passed through normal DNS and the private gateway. The public gateway (`192.168.11.1`) and WAN (`91.159.199.233`) both returned HTTP 404 for this hostname. Anonymous household state, AI configuration, photos and SSE returned 401; a cross-origin write returned 403. Fresh mobile and desktop Chromium sessions loaded the first-owner setup screen without JavaScript errors or horizontal overflow, with a secure context, standalone manifest/icons and an activated service worker. These were browser checks from Pear, not a physical phone installation.

The running process and database are owned by UID/GID 10001; the database is mode 0600. A consistent initial backup at `/data/initial-deployment-2026-10-02.sqlite3` passed full SQLite integrity checks and confirmed DELETE journaling. The initial database has zero users and households. Create the first owner using the instructions above; provider configuration remains an owner setup step. No production family accounts or clothing data were created during verification.

## Personal AI connection — 2026-10-02

The owner has created the family, and the app is paired to Honeydew's dedicated
`littlewardrobe` Hermes profile. Its independent mode-0600 pairing file and
personal login stay on Honeydew. The cluster stores only the revocable
household-scoped worker-token digest, provider settings and encrypted
provider-secret storage.
`littlewardrobe-companion.service` is declared in the private NixOS repository
and runs as a sandboxed user service; it requires LAN/VPN access to this origin.

Settings match the established family setup: `gpt-6-luna` with `xhigh` reasoning,
strict JSON schema, no token parameter, and `gpt-image-2-medium` with a 1024 × 1024
preset. Clothing-specific prompts and reviewed drafts remain in this app.
Production queue model discovery/chat and private image generation passed
after a real owned-credential refresh. An isolated tag fixture was read as
Example, size 98. Anonymous access to the generated test image returned 401.
No inventory or listing was created. A consistent pre-pairing database backup
is `/data/pre-companion-20261002.sqlite3`; retain the matching encryption key.

Check or restart the outbound worker on Honeydew:

```sh
systemctl --user status littlewardrobe-companion
systemctl --user restart littlewardrobe-companion
```

The app's photo suggestions and Finnish sale drafting are ready to use.
Enhanced listing images remain subject to comparison/review; Vinted exports
always use original photos.

## Distinct visual identity — 2026-10-02

Source `756b262` adds denim-blue and apricot styling, bold sans-serif headings,
clothing-rail artwork, explicit selected filters and a consistent hanger/shirt
PWA icon. CI `36976223456` passed all checks and the two-device production
browser suite against its packaged non-root container. A consistent pre-upgrade
backup is `/data/pre-visual-20261002.sqlite3`, including the new AI pairing.
The immutable image above is the approved release; private routing, storage,
accounts and provider settings are preserved.

Homelab revision `9fdc66e` reconciled successfully. Flux is Ready/Healthy and
the running image ID matches the new digest. The private HTTPRoute remains
Accepted/ResolvedRefs. Fresh 390px and 1440px HTTPS browsers verified the new
theme, headings, icons, manifest and active service worker without page errors
or overflow. Production model discovery/chat passed again after rollout.
Pear is running the built NixOS generation with the companion enabled for
boot; both family workers are active and no system/user units are failed.

## Hermes host migration — 2026-10-02

The existing personal profile, OAuth login and pairing file were transferred
intact to Honeydew (`192.168.1.158`) with the companion scripts and declarative
sandboxed user service. Pear's worker is stopped/disabled and its profile
archived outside the active directory. Outbound HTTPS polling uses the same
origin and token, so the application Deployment and household settings needed
no change. The production `littlewardrobe-api --check-companion --image` passed
model discovery, `gpt-6-luna` / `xhigh` chat and private native image generation
on Honeydew. No clothing inventory or listing was created.

## Photo suggestion repair — 2026-10-13

Source `fe05d38` constrains AI category, size system, season and condition to valid clothing choices and clarifies unknown values. It repairs an empty-season response that previously produced a misleading child/location/date error. Production original photos passed a native AI recheck with the corrected schema, and CI passed the complete production browser and packaged-container suites. A consistent pre-upgrade backup is `/data/pre-photo-fix-20261013.sqlite3`. The user has authorized checked updates to this existing private instance; routing remains LAN/VPN only.

## Background clothing photo intake — 2026-10-13

Source `fe0d6cc` adds Add from photo with durable server-side recognition, original photo retention, retry/cancel controls and AI review markers. Saving the reviewed item cancels unfinished work and sale approval requires review. Results preserve concurrent manual edits and cannot revive deleted or canceled entries. It also adds separate camera/gallery controls, fixed dialog headers, multiword accent-insensitive search and recovery from handler/worker/database panics. CI `38428197988` passed all checks and the expanded two-device production browser suite against both the production build and packaged non-root container. A consistent pre-schema-7 backup is `/data/pre-photo-ports-20261013.sqlite3`; its integrity and DELETE journal checks passed. Pending photo tasks resume after a process restart; household JSON restore cancels pending tasks and preserves marked entries/photos. Restore the compatible SQLite backup to downgrade.

Rollout verification: homelab `90f05e4` is Ready/Healthy with the exact pinned digest and a Bound retained NFS claim. The private HTTPRoute is Accepted/ResolvedRefs, and the public gateway returns 404. Fresh 390px/1440px trusted HTTPS browsers confirmed the new bundle/PWA, no errors/overflow, anonymous private API denial and cross-origin photo-job rejection. Native model discovery/chat passed. The post-upgrade backup `/data/post-photo-ports-20261013.sqlite3` passes schema-7, integrity and DELETE-journal checks. Inventory, family configuration, photos and accounts are retained; the encrypted AI config/secret checksum matches the pre-upgrade backup. No production clothing or listing was created during verification.
