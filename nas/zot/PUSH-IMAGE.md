# Push a private Radiator image (instructions for a build agent)

The registry is `nectarine.internal.fruitiex.org:5050`. Push Radiator images
under `private/radiator-server`, using a descriptive feature name and source
commit for the tag. This hostname requires the home LAN or a VPN route to it.

## Credentials supplied by the owner

Use username `builder`. The owner supplies the **password field** of the
1Password Agents item **homelab - zot registry** as the secret environment
variable `ZOT_PASSWORD` in the build agent's runner. The build agent does not
need 1Password access. Do not use the NAS account, node-reader credentials,
or retention credentials. Do not echo the password, dump environment variables,
or include credentials in image layers, Git, logs or chat.

The owner can copy that one password from 1Password into the runner's secret
settings, mapped to `ZOT_PASSWORD`. If the runner has no secret settings, inject
it through the agent process's environment from an interactive password prompt;
do not save a plaintext `.env` file or pass it as a command argument. For Bash:

```bash
read -r -s -p 'Zot builder password: ' ZOT_PASSWORD
printf '\n'
export ZOT_PASSWORD
# Start the build agent from this shell so it inherits the variable.
```

## Trust the public CA

Get `ca.crt` from this repository's `nas/zot/ca.crt`. It is public certificate
material, not a credential:

<https://raw.githubusercontent.com/FruitieX/homelab/main/nas/zot/ca.crt>

For Docker Engine on Linux, the Docker **daemon host** must trust it at:

```text
/etc/docker/certs.d/nectarine.internal.fruitiex.org:5050/ca.crt
```

For example, once the public certificate is available locally:

```sh
sudo install -D -m 0644 ca.crt \
  /etc/docker/certs.d/nectarine.internal.fruitiex.org:5050/ca.crt
```

If the agent uses a remote Docker daemon or a BuildKit container, configure
trust on that daemon/builder too. Adding a certificate only to the agent's
filesystem does not configure a remote builder. Keep TLS verification enabled.

## Build, push, and report the digest

Run the following in one Bash session from the Radiator source checkout.
Adapt the Dockerfile/build arguments to that repository's instructions.
Supply `RADIATOR_FEATURE` as a short tag-safe name such as `new-auth-flow`.
The temporary Docker configuration prevents leaving the registry password in
the agent's normal Docker configuration after the push.

```bash
set +x
set -euo pipefail
: "${ZOT_PASSWORD:?Supply the builder password through the runner secret environment}"
: "${RADIATOR_FEATURE:?Supply a short tag-safe feature name}"

registry='nectarine.internal.fruitiex.org:5050'
revision=$(git rev-parse HEAD)
image="$registry/private/radiator-server:feature-${RADIATOR_FEATURE}-${revision:0:12}"

# This session owns this temporary directory; preserve any caller's Docker config.
build_docker_config=$(mktemp -d)
trap 'find "$build_docker_config" -depth -delete' EXIT
export DOCKER_CONFIG="$build_docker_config"
printf '%s' "$ZOT_PASSWORD" |
  docker login "$registry" --username builder --password-stdin
unset ZOT_PASSWORD

# Homelab nodes use linux/amd64. Load into Docker, then push with the login above.
docker build --platform linux/amd64 -t "$image" .
docker push "$image"
docker pull --platform linux/amd64 "$image"
docker image inspect "$image" --format '{{range .RepoDigests}}{{println .}}{{end}}'
```

Return the pushed tag, full source commit, and the resulting reference:

```text
nectarine.internal.fruitiex.org:5050/private/radiator-server@sha256:<digest>
```

Use that digest reference in `apps/radiator/radiator.yaml` in the homelab
repository. The Talos nodes already trust the CA and have read-only registry
credentials. Keep `imagePullPolicy: IfNotPresent`.

## Release and retention conventions

Use `feature-`, `dev-`, `test-` or `scratch-` prefixes for disposable builds.
These tags have shorter retention; running deployments and available rollback
revisions are protected separately. Use a release tag such as `v1.2.3` or
`release-<commit>` when the image must be retained indefinitely. Tags beginning
with `retain-` belong to the automated protection job; do not create or edit them.

The exact retention policy and protection mechanism are documented in
[README.md](README.md).
