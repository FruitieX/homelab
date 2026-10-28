#!/usr/bin/env sh
set -eu

TALOS_VERSION=v1.13.9
TALOS_SCHEMATIC=c9078f9419961640c712a8bf2bb9174933dfcf1da383fd8ea2b7dc21493f8bac
KUBERNETES_VERSION=1.36.6
# Cluster endpoint: the control planes' shared virtual IP
# (talos/patches/controlplane.yaml), not any single node.
API_VIP="${API_VIP:-192.168.10.5}"
# The control planes' own addresses, for talosctl.
CONTROL_PLANE_IPS="${CONTROL_PLANE_IPS:-192.168.10.97 192.168.10.248 192.168.10.232}"
OUTPUT_DIR="${OUTPUT_DIR:-_out}"

# Runtime credentials stay encrypted in Git. Remove the decrypted patch even
# when configuration generation fails; do not pass credentials in argv.
umask 077
REGISTRY_PATCH=$(mktemp)
trap 'rm -f "$REGISTRY_PATCH"' EXIT HUP INT TERM
sops --decrypt talos/patches/registries.sops.yaml > "$REGISTRY_PATCH"

talosctl gen config homelab-cluster "https://${API_VIP}:6443" \
  --with-secrets secrets.yaml \
  --install-image "factory.talos.dev/installer/${TALOS_SCHEMATIC}:${TALOS_VERSION}" \
  --kubernetes-version "${KUBERNETES_VERSION}" \
  --config-patch @talos/patches/all.yaml \
  --config-patch "@$REGISTRY_PATCH" \
  --config-patch-control-plane @talos/patches/controlplane.yaml \
  --output-dir "${OUTPUT_DIR}" \
  --force

# shellcheck disable=SC2086
talosctl --talosconfig "${OUTPUT_DIR}/talosconfig" config endpoint ${CONTROL_PLANE_IPS}
talosctl --talosconfig "${OUTPUT_DIR}/talosconfig" config node "${CONTROL_PLANE_IPS%% *}"
