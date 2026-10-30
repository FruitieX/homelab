#!/usr/bin/env bash
# Take a Talos node, or every node on one Proxmox host, out of service:
# cordon, drain, optionally reboot/upgrade or wait for the operator, wait for
# the nodes to come back Ready, uncordon. See docs/maintenance.md.
#
#   scripts/node-maintenance.sh [options] <node-name|host>
#
# A host is a `topology.kubernetes.io/zone` value (mango, mandarin, mulberry;
# see talos/patches/hosts). Run from the repo root inside `nix-shell`.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/node-maintenance.sh [options] <node-name|host>

Actions (default --wait):
  --wait              after draining, wait for the operator to press Enter
                      (do the Proxmox/hardware work meanwhile)
  --reboot            talosctl reboot each node, one at a time
  --upgrade [IMAGE]   talosctl upgrade each node (default: the installer
                      image pinned in regen-configs.sh)
  --drain-only        cordon and drain, then stop (uncordon later with
                      --uncordon-only)
  --uncordon-only     wait for the nodes to be Ready and uncordon them; after
                      --drain-only it also waits for a new boot (see below)

Options:
  -n, --dry-run       show what would happen; changes nothing
  --timeout DURATION  drain timeout per node (default 10m)
  --disable-eviction  delete pods even if a PodDisruptionBudget forbids it
                      (needed for single-replica workloads whose PDB
                      allows no disruptions, e.g. influxdb)
  --force             continue although other nodes are not Ready
  --no-reboot         with --wait/--uncordon-only: the nodes were not
                      restarted, so don't wait for a new boot ID
  -y, --yes           don't ask for confirmation
  -h, --help          show this help

Environment: KUBECONFIG (default ./kubeconfig), TALOSCONFIG (default
_out/talosconfig).
EOF
}

ACTION="wait"
NO_REBOOT=0
IMAGE=""
DRY_RUN=0
TIMEOUT=10m
DISABLE_EVICTION=0
FORCE=0
YES=0
TARGET=""

while [ $# -gt 0 ]; do
  case "$1" in
    --wait) ACTION="wait" ;;
    --reboot) ACTION=reboot ;;
    --upgrade)
      ACTION=upgrade
      if [ $# -gt 1 ] && [[ "$2" != -* ]] && [[ "$2" == */* ]]; then IMAGE="$2"; shift; fi
      ;;
    --drain-only) ACTION=drain-only ;;
    --uncordon-only) ACTION=uncordon-only ;;
    -n | --dry-run) DRY_RUN=1 ;;
    --timeout) TIMEOUT="$2"; shift ;;
    --disable-eviction) DISABLE_EVICTION=1 ;;
    --force) FORCE=1 ;;
    --no-reboot) NO_REBOOT=1 ;;
    -y | --yes) YES=1 ;;
    -h | --help) usage; exit 0 ;;
    -*) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    *)
      [ -z "$TARGET" ] || { echo "only one target allowed" >&2; exit 2; }
      TARGET="$1"
      ;;
  esac
  shift
done
[ -n "$TARGET" ] || { usage >&2; exit 2; }

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export KUBECONFIG="${KUBECONFIG:-$REPO_ROOT/kubeconfig}"
export TALOSCONFIG="${TALOSCONFIG:-$REPO_ROOT/_out/talosconfig}"
ZONE_LABEL=topology.kubernetes.io/zone

for tool in kubectl talosctl; do
  command -v "$tool" >/dev/null || { echo "$tool not found; run inside nix-shell" >&2; exit 1; }
done

log() { printf '\n==> %s\n' "$*"; }
# Run a command that changes something; only print it in dry-run mode.
run() {
  if [ "$DRY_RUN" = 1 ]; then
    printf '[dry-run] %s\n' "$*"
  else
    printf '+ %s\n' "$*"
    "$@"
  fi
}

if [ "$ACTION" = upgrade ] && [ -z "$IMAGE" ]; then
  # shellcheck disable=SC1090
  eval "$(grep -E '^(TALOS_VERSION|TALOS_SCHEMATIC)=' "$REPO_ROOT/regen-configs.sh")"
  IMAGE="factory.talos.dev/installer/${TALOS_SCHEMATIC}:${TALOS_VERSION}"
fi

# --- resolve the target to nodes -------------------------------------------
if kubectl get node "$TARGET" >/dev/null 2>&1; then
  NODES=("$TARGET")
  HOST="$(kubectl get node "$TARGET" -o jsonpath="{.metadata.labels.${ZONE_LABEL//./\\.}}")"
else
  mapfile -t NODES < <(kubectl get nodes -l "$ZONE_LABEL=$TARGET" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')
  HOST="$TARGET"
  [ "${#NODES[@]}" -gt 0 ] || { echo "no node or host named '$TARGET'" >&2; kubectl get nodes -L "$ZONE_LABEL" >&2; exit 1; }
fi

node_ip() { kubectl get node "$1" -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}'; }
is_cp() { kubectl get node "$1" -o jsonpath='{.metadata.labels}' | grep -q 'node-role.kubernetes.io/control-plane'; }

# Workers first, so a host's control plane is the last to go and first back.
mapfile -t NODES < <(for n in "${NODES[@]}"; do if is_cp "$n"; then echo "1 $n"; else echo "0 $n"; fi; done | sort | cut -d' ' -f2)

declare -A IPS
CP_TARGETS=0
for n in "${NODES[@]}"; do
  IPS[$n]="$(node_ip "$n")"
  if is_cp "$n"; then CP_TARGETS=$((CP_TARGETS + 1)); fi
done
if [ "$CP_TARGETS" -gt 1 ]; then
  echo "refusing: target contains $CP_TARGETS control planes; etcd would lose quorum" >&2
  exit 1
fi

# talosctl goes through a control plane that stays up.
TALOS_ENDPOINT=""
for n in $(kubectl get nodes -l node-role.kubernetes.io/control-plane -o jsonpath='{.items[*].metadata.name}'); do
  if [[ " ${NODES[*]} " != *" $n "* ]]; then TALOS_ENDPOINT="$(node_ip "$n")"; break; fi
done
[ -n "$TALOS_ENDPOINT" ] || { echo "no control plane outside the target" >&2; exit 1; }
talos() { talosctl -e "$TALOS_ENDPOINT" "$@"; }

echo "Target: ${HOST:-?} -> ${NODES[*]}"
echo "Action: $ACTION${IMAGE:+ ($IMAGE)}"
[ "$CP_TARGETS" = 0 ] || echo "Includes a control plane: the API VIP moves to another control plane by itself; kubectl may pause for a few seconds."

# --- pre-flight -------------------------------------------------------------
log "Nodes"
kubectl get nodes -L "$ZONE_LABEL"
NOT_READY="$(kubectl get nodes --no-headers | awk -v t=" ${NODES[*]} " '$2 !~ /^Ready/ && index(t, " "$1" ") == 0 {print $1}')"
if [ -n "$NOT_READY" ] && [ "$ACTION" != uncordon-only ]; then
  echo "Other nodes are not Ready: $NOT_READY" >&2
  [ "$FORCE" = 1 ] || { echo "refusing without --force" >&2; exit 1; }
fi

if [ "$ACTION" != uncordon-only ]; then
  log "Disruption budgets that allow no disruptions (block draining)"
  kubectl get pdb -A --no-headers | awk '$5 == 0 {print "  " $1 "/" $2}' || true
fi

if [ "$DRY_RUN" = 0 ] && [ "$YES" = 0 ]; then
  read -r -p "Proceed? [y/N] " ans
  [[ "$ans" =~ ^[yY] ]] || exit 1
fi

DRAIN_ARGS=(--ignore-daemonsets --delete-emptydir-data --timeout="$TIMEOUT" --grace-period=-1)
[ "$DISABLE_EVICTION" = 1 ] && DRAIN_ARGS+=(--disable-eviction)

# Boot IDs from before the maintenance. A node that was shut down keeps
# showing Ready until its status goes stale, so "Ready" alone can pass before
# the node has even gone down; a changed boot ID proves it really restarted.
# --drain-only saves them so a later --uncordon-only can check too.
STATE_DIR="${XDG_RUNTIME_DIR:-/tmp}/node-maintenance"
declare -A BOOT_ID=()
boot_id() { kubectl get node "$1" -o jsonpath='{.status.nodeInfo.bootID}' 2>/dev/null || true; }
save_boot_ids() {
  mkdir -p "$STATE_DIR"
  for n in "${NODES[@]}"; do
    BOOT_ID[$n]="$(boot_id "$n")"
    printf '%s\n' "${BOOT_ID[$n]}" >"$STATE_DIR/$n.bootid"
  done
}
load_boot_ids() {
  for n in "${NODES[@]}"; do
    [ -f "$STATE_DIR/$n.bootid" ] && BOOT_ID[$n]="$(cat "$STATE_DIR/$n.bootid")"
  done
  return 0
}

wait_ready() {
  local n="$1"
  if [ "$DRY_RUN" = 1 ]; then
    echo "[dry-run] kubectl wait --for=condition=Ready node/$n --timeout=20m"
    return
  fi
  # Poll instead of `kubectl wait`: the API VIP may move while a control
  # plane reboots, and a failed request must not abort the script.
  local deadline=$((SECONDS + 1200)) status before="${BOOT_ID[$n]:-}"
  [ "$NO_REBOOT" = 1 ] && before=""
  if [ -n "$before" ]; then
    echo "Waiting for $n to come back (new boot) and be Ready..."
  else
    echo "Waiting for $n to be Ready..."
  fi
  while [ "$SECONDS" -lt "$deadline" ]; do
    status="$(kubectl get node "$n" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || true)"
    if [ "$status" = True ] && { [ -z "$before" ] || [ "$(boot_id "$n")" != "$before" ]; }; then
      echo "$n is Ready"
      rm -f "$STATE_DIR/$n.bootid"
      return
    fi
    sleep 5
  done
  echo "$n not Ready after 20 minutes; it stays cordoned. Rerun with --uncordon-only once it is." >&2
  exit 1
}

uncordon_all() {
  log "Uncordoning"
  for n in "${NODES[@]}"; do wait_ready "$n"; run kubectl uncordon "$n"; done
}

if [ "$ACTION" = uncordon-only ]; then
  load_boot_ids
  uncordon_all
  exit 0
fi

[ "$DRY_RUN" = 1 ] || save_boot_ids

# --- cordon all first, so pods don't hop between the target's nodes ----------
log "Cordoning"
for n in "${NODES[@]}"; do run kubectl cordon "$n"; done

log "Draining"
for n in "${NODES[@]}"; do
  if [ "$DRY_RUN" = 1 ]; then
    echo "[dry-run] kubectl drain $n ${DRAIN_ARGS[*]}; would evict:"
    # Short timeout: a PDB-blocked eviction would otherwise retry until
    # --timeout runs out.
    kubectl drain "$n" "${DRAIN_ARGS[@]}" --timeout=30s --dry-run=server 2>&1 |
      grep -E 'evicting|error' | sed -E 's/ \(will retry after 5s\)//' | sort -u | sed 's/^/  /' || true
  elif ! run kubectl drain "$n" "${DRAIN_ARGS[@]}"; then
    echo "Drain of $n failed or timed out. Pods still on it:" >&2
    kubectl get pods -A --field-selector "spec.nodeName=$n" -o wide | grep -v -E 'DaemonSet|kube-proxy|flannel' >&2 || true
    echo "Nodes stay cordoned. Fix the blocker (or rerun with --disable-eviction), or undo with --uncordon-only." >&2
    exit 1
  fi
done

case "$ACTION" in
  drain-only)
    log "Drained. Rerun with --uncordon-only when done."
    exit 0
    ;;
  wait)
    if [ "$DRY_RUN" = 1 ]; then
      echo "[dry-run] would wait for the operator"
    else
      log "Nodes drained. Do the maintenance now (e.g. shut down / reboot the VMs or host)."
      read -r -p "Press Enter when the nodes are back up... " _
    fi
    ;;
  reboot | upgrade)
    for n in "${NODES[@]}"; do
      log "$ACTION $n (${IPS[$n]})"
      if [ "$ACTION" = reboot ]; then
        run talos -n "${IPS[$n]}" reboot --drain=false --wait --timeout 20m --progress plain
      else
        run talos -n "${IPS[$n]}" upgrade --image "$IMAGE" --drain=false --wait --timeout 30m --progress plain
      fi
      wait_ready "$n"
    done
    ;;
esac

uncordon_all

log "Done"
kubectl get nodes -L "$ZONE_LABEL"
NOT_READY_PODS="$(kubectl get pods -A --no-headers | awk '{split($3, r, "/")} r[1] != r[2] && $4 != "Completed"')"
[ -z "$NOT_READY_PODS" ] || { echo "Pods not ready yet:"; echo "$NOT_READY_PODS"; }
