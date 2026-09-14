# GitHub Actions runners

This deploys GitHub's Actions Runner Controller (ARC) with repository-scoped
scale sets for `FruitieX/homelab` and `FruitieX/add-bot-rs`. Each keeps one
idle runner available. Personal GitHub accounts do not have an account-wide
runner scope, so each additional repository needs its own scale set.

## Create the GitHub credential

Create a GitHub App installation token or a suitable personal access token and
put it in `github-actions-runner-secret.yaml`. For this repository-level setup,
a fine-grained token should be restricted to the repositories using these
scale sets (`FruitieX/homelab` and `FruitieX/add-bot-rs`) and have
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

These runners use Docker-in-Docker and are privileged. Do not send untrusted
pull-request code to them, and avoid sharing this runner group with workflows
that handle sensitive credentials.
