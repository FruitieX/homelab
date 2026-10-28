# NAS container registry

Zot runs directly on Synology Container Manager, independently of Kubernetes.
The declarative files here deploy to `/volume3/docker/zot`; `data/` holds OCI
images and `secrets/` holds the service's runtime credentials. Neither directory
belongs in Git. The endpoint is `https://nectarine.internal.fruitiex.org:5050`.

## Deploy or update

Install Container Manager and enable SSH for the agent's DSM administrator
account. From this repository:

```sh
nix-shell --run 'python3 scripts/deploy-zot.py'
```

The script retrieves the NAS login, registry credentials and TLS keys from the
Agents vault using `op-agent`, and GHCR read access from Kubernetes'
`default/ghcr-credentials`. It verifies the Zot configuration, starts the Compose
service and restarts it to load updated files. SSH host identity must already
be trusted. `restart: unless-stopped` lets Docker start the locally stored image
on NAS reboot; routine restarts do not depend on GitHub or Kubernetes access.
An operator-driven redeployment does need the vault and cluster credentials.

The full Zot image is required for sync. Keep its version and digest pinned in
`compose.yaml`. Preserve `data/` when updating or recreating the service.

Credentials and private TLS keys are backed up in the Agents items
`homelab - zot registry`, `homelab - zot node reader`, and
`homelab - zot retention`. The authoritative
public certificates are `ca.crt` and `server.crt` here. Renew the server
certificate before its expiry using the stored CA key, then redeploy; a CA-key
rotation also requires updating node/client trust. Check expiry with:

```sh
openssl x509 -in nas/zot/server.crt -noout -dates
```

## Transparent caching

`config.json` maps each upstream into its own namespace:

| Upstream | Zot prefix |
| --- | --- |
| docker.io | dockerhub |
| ghcr.io | ghcr |
| quay.io | quay |
| registry.k8s.io | k8s |
| factory.talos.dev | factory |

Talos' shared patch in `talos/patches/all.yaml` maps those registries to the
corresponding `/v2/<prefix>` endpoints. Workload image references, Renovate,
Flux and app image-update dispatches keep their original registry names.
Upstream fallback remains enabled if Zot is unavailable.

The registry requires authentication for all image access. `node-reader` can
pull; `builder` can pull and push under `private/`. Neither account can push
into the upstream cache namespaces. Private GHCR images use the existing
upstream read credentials and remain authenticated in the cache.

Zot fetches missing images on demand. Digest references preserve the upstream
manifest bytes; `http.compat` accepts Docker-format manifests too. Mutable tags
are revalidated on demand, at most once per five minutes; digest-pinned
references avoid tag freshness ambiguity. No periodic full-upstream scans are
configured. See [Retention](#retention) for protected tags and cleanup.

A cache cannot supply an image it has never fetched. Seed current workloads,
including init containers and running image digests:

```sh
nix-shell --run 'python3 scripts/seed-zot.py'
```

The seeder selects linux/amd64 child manifests and verifies image blob digests.
Additional system images can be passed as arguments. Find the live etcd,
kubelet and sandbox image versions with `talosctl images list --namespace
system` and `talosctl images list`. Include those when upgrading Kubernetes or
Talos. Images from future Jobs and operator-created pods that have never run
also need seeding if they must work offline immediately.

## Retention

A daily Kubernetes CronJob (`registry-maintenance/zot-protection`, 04:17 Helsinki
local time) protects desired workload templates, active Jobs and Pods, resolved
running image digests, and the current plus three previous available rollout
revisions. It includes suspended CronJob templates and CNPG database images.
It also protects the explicit Talos/system images in
[`policy.json`](../../infrastructure/registry/retention/policy.json); update that
list when upgrading kubelet, etcd, the sandbox image, or Zot.

The job verifies manifests and linux/amd64 blobs are cached, then creates
`retain-<sha256>` tags without changing manifest bytes. Only after all required
images are protected does it remove stale tags in that reserved namespace.
Kubernetes/API errors, missing images, incomplete blobs, or failed tag writes
abort before any stale protection is removed. Ordinary image tags and digest
references are never deleted by this job. An alert fires after 36 hours without
a successful scheduled run.

Zot performs its own cleanup during 03:00–04:00 UTC:

| Content | Retention |
| --- | --- |
| `retain-<sha256>` tags | Until the protection job no longer needs them |
| Upstream cache tags and untagged manifests | Pulled or cached within 90 days, or among the newest four per repository/category |
| Private `feature-`, `dev-`, `test-`, `scratch-` tags | Pulled/pushed within 30 days, or the newest four matching tags per repository |
| Other private tags, including releases | Indefinitely |
| Untagged private manifests | Pulled/cached within 30 days, or the newest four per repository |
| Unclassified repository namespaces | Indefinitely until explicitly given a policy |

A retained multi-platform index protects its referenced manifests and layers.
Orphan manifests/referrers and unreferenced blobs have a 24-hour grace period.
Space reclamation is delayed by these grace periods; deleting a tag does not
necessarily free its layers immediately. Deduplication shares identical blobs.
Keep the ordered tag rules: the pinned Zot version uses the **first matching**
tag rule, so reserved tags precede disposable tags and the private catch-all.

Long-running pods need not pull for months; protection is independent of last
pull time. Rollback protection is based on revisions Kubernetes still holds.
Old mutable tags cannot reconstruct an earlier build once its digest is no
longer recorded in the cluster; use digest-pinned deployments for reproducible
rollbacks. A missing historical image is reported as a failed protection run,
not silently treated as protected. Existing pins remain if discovery fails;
this favors retaining extra data over removing needed images.

This is an age/count policy, **not a hard size quota**. A dedicated DSM share
quota can bound storage separately. Do not prune the NAS directory manually.

Inspect or run the job:

```sh
nix-shell
kubectl --kubeconfig=kubeconfig -n registry-maintenance get cronjob,jobs
kubectl --kubeconfig=kubeconfig -n registry-maintenance logs job/<job-name>
KUBECONFIG="$PWD/kubeconfig" python3 infrastructure/registry/retention/protect.py \
  --local --dry-run --ca-file nas/zot/ca.crt
```

The local dry run reads the maintenance identity from the Agents vault and
resolves/caches required images, but does not write or remove protection tags.
The scheduled job has read-only Kubernetes discovery RBAC and a separate
SOPS-encrypted registry maintenance credential. Do not give that credential to
build agents. Native retention rules are in `config.json`; the protection job,
RBAC, credentials and alert are in `infrastructure/registry/retention`.

Run the safety tests with:

```sh
nix-shell --run 'python3 -m unittest discover -s infrastructure/registry/retention -v'
```

`scripts/test-zot-retention.py` exercises the native matching/count rules against
an isolated Docker registry with short time windows. It creates only disposable
test data and never mounts the real registry store. Run it on a Docker host
with the pinned Zot image already present; `--docker`, `--config`, `--image`,
and `--scratch-parent` select the local tools and paths. It checks actual expiry,
dry-run behavior, release tags, protected multi-platform images, and safe tag
deletion. Repeat it when upgrading Zot or changing retention rules.

## Apply Talos configuration

`regen-configs.sh` decrypts `talos/patches/registries.sops.yaml` into a temporary,
restricted file, combines it with the public shared patch, and removes the
temporary file on exit. Generated machine configs under `_out/` contain runtime
credentials and stay outside Git.

To patch existing nodes without replacing their per-host configuration:

```sh
nix-shell
umask 077
registry_patch=$(mktemp)
trap 'rm -f "$registry_patch"' EXIT
sops --decrypt talos/patches/registries.sops.yaml > "$registry_patch"
talosctl --talosconfig _out/talosconfig -n <node-address> patch machineconfig \
  --mode=no-reboot --patch @talos/patches/all.yaml --patch "@$registry_patch"
```

Apply to one worker and verify pulls before continuing with the other nodes.
These settings are applied live without reboot. Use the pinned Talos 1.13
client from `nix-shell`; the older 1.11 client cannot patch the current
multi-document node configuration.

## Push a custom image

Trust `ca.crt` in the build machine's container tooling. For Docker Engine on
Linux, install it as
`/etc/docker/certs.d/nectarine.internal.fruitiex.org:5050/ca.crt`.
Then authenticate without placing the password in command arguments:

```sh
op-agent read 'op://Agents/homelab - zot registry/password' |
  docker login nectarine.internal.fruitiex.org:5050 -u builder --password-stdin

docker build -t nectarine.internal.fruitiex.org:5050/private/my-app:feature-<commit> .
docker push nectarine.internal.fruitiex.org:5050/private/my-app:feature-<commit>
```

Use the resulting digest in the workload manifest. Talos' node credentials
also cover direct pulls from this registry. Reach the NAS over LAN/VPN from
outside the house.

## Recovery boundaries

Image caching addresses image availability. It does not provide database or
NAS redundancy, application offline authentication, Git/Helm source mirrors,
or replacement-node installation media. Local DNS and NAS storage must also
be available. A full WAN-disconnected rack restart is a separate validation
exercise; do not infer it solely from successful registry pulls.
