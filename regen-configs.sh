#!/usr/bin/env sh
set -eu

TALOS_VERSION=v1.13.9
TALOS_SCHEMATIC=c9078f9419961640c712a8bf2bb9174933dfcf1da383fd8ea2b7dc21493f8bac
KUBERNETES_VERSION=1.36.3
CONTROL_PLANE_IP="${CONTROL_PLANE_IP:-192.168.10.97}"

talosctl gen config homelab-cluster "https://${CONTROL_PLANE_IP}:6443" \
  --with-secrets secrets.yaml \
  --install-image "factory.talos.dev/installer/${TALOS_SCHEMATIC}:${TALOS_VERSION}" \
  --kubernetes-version "${KUBERNETES_VERSION}" \
  --output-dir _out \
  --force

talosctl --talosconfig _out/talosconfig config endpoint "${CONTROL_PLANE_IP}"
talosctl --talosconfig _out/talosconfig config node "${CONTROL_PLANE_IP}"
