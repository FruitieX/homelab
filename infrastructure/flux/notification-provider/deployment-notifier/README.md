# Deployment notifications

This independent service sends rich success messages to the existing Discord
webhook (`flux-system/discord-webhook-url`, key `address`). Existing Flux error
alerts continue to use the same webhook. No new credentials or exposed endpoint
are needed. The code and configuration are mounted from generated ConfigMaps;
there is no custom image to build or publish.

Example message:

```text
✅ add-bot deployed to homelab
Version: 07c45c4 · Previous commit: 94e1475
• Remember recent form styles and add moon and Christmas icons

View changes → GitHub comparison between the two deployed source commits
Image: ghcr.io/fruitiex/add-bot-rs:main@sha256:…
```

## Delivery and readiness

Every 30 seconds the notifier checks each configured Flux Kustomization and its
Deployment. Flux must report Ready for the current generation, with no active
reconciliation or stalled condition. The Deployment must have observed its
current generation, with all requested replicas updated, ready and available,
and no old replicas left. Suspended Flux resources, paused Deployments, scaled
down workloads, and images without pinned digests are skipped.

Homectl's readiness probe uses `/health/ready`, which waits for startup warmup.
Add-bot currently has no application readiness endpoint: its success means the
new container is running and Kubernetes considers it ready, rather than a
Telegram API connectivity check.

The first ready image for each service is recorded **silently as a baseline**.
Subsequent digest changes produce notifications; config-only changes and repeated
reconciliations do not. State is saved to an NFS PVC, survives restarts, and is
updated only after Discord confirms the send (`wait=true`). Failed API calls are
retried on the next check. Returning to an earlier image also sends a message.
Keep a single replica with `Recreate`, since the state file has one writer.

Polling catches the latest ready version after a notifier outage. Versions that
were deployed and replaced entirely during an outage or between polls cannot be
reported. Discord does not offer an idempotency key for webhook sends: a crash
after a successful send but before state is saved, or an ambiguous network timeout,
can cause a duplicate. Removing the PVC resets the baseline.

## Version and change summaries

For public GHCR images, the notifier reads OCI labels from the **pinned image
digest**, resolving the configured Linux architecture inside multi-platform
indexes. `org.opencontainers.image.revision` identifies the app commit, while
`org.opencontainers.image.version` supplies a release version when available.
Moving tags such as `main` are replaced by the short commit. Other registries
and images without labels still get digest-based messages.

GitHub comparisons use the previous and current app commits, not the homelab
manifest commit. Messages contain up to five actual commit subject lines and a
comparison link. Rollbacks/diverged histories are marked and show the deployed
commit's subject instead. A GitHub outage/rate limit removes the summaries but
keeps the link; a GHCR failure delays sending until the pinned image can be read.
Mentions are disabled so commit messages cannot ping channel members.

The default uses public GitHub API access. `GITHUB_TOKEN` is supported optionally
to raise its API limit (mount it from a Secret; never put tokens in configuration).
Registry authentication for private images is not implemented.

## Adding a service

1. Add a service to `services.json`: name, Flux Kustomization, Deployment namespace
   and name, container name, and GitHub repository (`owner/repo`). Names identify
   saved notification history; keep them stable.
2. Add the workload name to the Deployment Role's `resourceNames` and the Flux
   name to the Kustomization Role's `resourceNames` in `rbac.yaml`. For a different
   workload namespace, add a Role and RoleBinding there. The service account stays
   in `flux-system`; it only needs `get` on the named resources.
3. Ensure the Flux Kustomization has `wait: true`, the image is digest-pinned,
   and the workload has an appropriate readiness probe if it provides one.
4. Adjust `architecture` if the workload images target something other than
   `amd64`. This currently assumes one architecture for all monitored workloads.

The notifier is included in the existing `notification-provider` Flux
Kustomization, so the usual homelab GitOps push deploys it. Generated ConfigMap
hashes trigger a restart when code or configuration changes. It checks add-bot
and homectl by default. Telegram is not configured in this implementation.

## Local checks

From the homelab repository root:

```sh
nix-shell --run 'python3 -B -m unittest discover -s infrastructure/flux/notification-provider/deployment-notifier -v'
```

The tests use fake external APIs, never send Discord messages, and cover
rollout readiness, persistence, duplicate suppression, rollbacks, failed delivery,
image metadata resolution, and rendered Kubernetes namespaces/RBAC/references.
The same checks run in the deployment notifier GitHub workflow.
