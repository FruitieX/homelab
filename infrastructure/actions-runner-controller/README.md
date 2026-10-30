# GitHub Actions runners

This deploys GitHub's Actions Runner Controller (ARC) with a repository-scoped
scale set for `FruitieX/homelab`, which keeps one idle runner available. Personal GitHub accounts do not have an account-wide
runner scope, so each additional repository needs its own scale set.

## Create the GitHub credential

Create a GitHub App installation token or a suitable personal access token and
put it in `github-actions-runner-secret.yaml`. For this repository-level setup,
a fine-grained token should be restricted to the repositories using these
scale sets (now only `FruitieX/homelab`) and have
`Administration: Read and write` permission for each repository.

Do not commit the plaintext file. After replacing
`REPLACE_WITH_GITHUB_PAT`, encrypt it from the repository root:

```sh
nix-shell --run \
  'sops --encrypt --in-place infrastructure/actions-runner-controller/github-actions-runner-secret.yaml'
```

The file should then contain `ENC[...]` values and a `sops:` section. Verify it
can be decrypted locally before committing:

```sh
nix-shell --run \
  'sops --decrypt infrastructure/actions-runner-controller/github-actions-runner-secret.yaml >/dev/null'
```

For a GitHub App, replace `github_token` with the three keys
`github_app_id`, `github_app_installation_id`, and `github_app_private_key`.
GitHub recommends a GitHub App for production runner authentication.

After the secret exists, commit/push this directory and check:

```sh
flux reconcile source git flux-system
flux reconcile kustomization actions-runner-controller --with-source
kubectl get pods -n arc-systems
kubectl get pods -n arc-runners
```

Use the runner in workflows with:

```yaml
jobs:
  build:
    runs-on: homelab
```

## Public repositories get no in-cluster runners

A self-hosted runner runs whatever a workflow tells it to, inside the cluster
and on the home network. In a public repository anyone can open a pull request
that adds a workflow with `runs-on: <our label>`, so a runner registered there
hands strangers a foothold in the homelab. Rules:

- Never register a runner scale set for a public repository. Public
  repositories get unlimited GitHub-hosted minutes anyway; in-cluster runners
  are for private repositories only.
- `FruitieX/homelab` is the one exception, because Flux reconciles must be
  triggered from inside the cluster (the webhook receiver is gone, see
  docs/configuration.md). Its runner is kept harmless instead: no
  Docker-in-Docker, unprivileged and non-root in a namespace that enforces the
  restricted Pod Security level, and a service account that may only patch the
  flux-system GitRepository. The repository requires approval before workflows
  from any outside contributor's pull request run (Settings → Actions →
  General → "Require approval for all external contributors"); never approve
  one that touches `.github/workflows`.
- A private repository that needs in-cluster builds gets its own scale set
  (Docker-in-Docker only if its builds need it, in a separate privileged
  namespace). Remove it before that repository is made public.

## Upgrading

Helm cannot upgrade ARC's CRDs, so a version bump is not a plain chart
update. Following [GitHub's upgrade steps](https://docs.github.com/en/actions/how-tos/manage-runners/use-actions-runner-controller/deploy-runner-scale-sets#upgrading-arc),
with all three chart versions bumped in one commit:

```sh
flux suspend kustomization actions-runner-controller
kubectl -n arc-runners delete helmrelease homelab-runners
# wait until arc-runners has no pods and arc-systems has no listener pods
kubectl -n arc-systems delete helmrelease actions-runner-controller
# only if the chart's crds/ changed between the versions:
kubectl delete crd autoscalinglisteners.actions.github.com \
  autoscalingrunnersets.actions.github.com \
  ephemeralrunnersets.actions.github.com ephemeralrunners.actions.github.com
git push   # the version bump
flux reconcile source git flux-system   # before resuming, see below
flux resume kustomization actions-runner-controller
```

Fetch the new revision before resuming: resuming first reinstalls the old
chart, whose CRDs then stay in place (Helm skips CRDs on upgrade) and the
new scale sets fail with `field not declared in schema`. If that happens,
apply the new chart's `crds/` with `kubectl apply --server-side
--force-conflicts` and reconcile the scale set HelmReleases with `--force`.
