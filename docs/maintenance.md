# Node and host maintenance

The cluster runs on three Proxmox hosts. mango and mandarin each run one
control plane and two worker VMs; mulberry runs only a control plane. Every
node is labelled with its host as `topology.kubernetes.io/zone`
([talos/patches/hosts](/talos/patches/hosts/README.md)), and workloads with
several replicas (Envoy, Envoy Gateway, CoreDNS) keep one per host, so the
cluster survives losing any one host.

## scripts/node-maintenance.sh

Run from the repo root inside `nix-shell`. The target is a node name or a host
name:

```
# See what would happen; changes nothing
scripts/node-maintenance.sh --dry-run --reboot mango

# Drain one node, reboot it through Talos, uncordon when Ready
scripts/node-maintenance.sh --reboot talos-zza-kh3

# Upgrade Talos on every node of a host (image from regen-configs.sh)
scripts/node-maintenance.sh --upgrade mandarin

# Drain a whole host, then wait while you work on it in Proxmox
scripts/node-maintenance.sh mango          # same as --wait
```

What it does:

1. Resolves the target to nodes (a host's workers first, its control plane
   last), refuses targets with more than one control plane, and refuses to
   start while another node is NotReady (override with `--force`).
2. Lists PodDisruptionBudgets that currently allow no disruptions.
3. Cordons all target nodes, then drains them one by one
   (`--ignore-daemonsets --delete-emptydir-data`, `--timeout 10m` by default).
4. `--reboot` / `--upgrade` runs `talosctl reboot` / `talosctl upgrade` on
   each node in turn through a control plane outside the target and waits
   until it's Ready again. `--wait` waits for you to press Enter instead.
   `--drain-only` stops here; finish later with `--uncordon-only`.
5. Waits for every node to be Ready and uncordons it.

When a host includes a control plane, the API virtual IP moves to another
control plane by itself and kubectl may pause for a few seconds. etcd keeps
quorum with two of three members, so only one control plane may be down at a
time.

### When a drain gets stuck

The script stops, lists the pods still on the node and leaves the nodes
cordoned (undo with `--uncordon-only`). Usual causes:

- A PodDisruptionBudget allowing no disruptions on a single-replica workload,
  e.g. `influxdb/influxdb-influxdb2` (minAvailable 1 with one replica).
  Rerun with `--disable-eviction` to delete such pods anyway, accepting their
  downtime.
- A CloudNativePG primary (`postgres-ha-primary` PDB): switch over to a
  replica first (`kubectl cnpg promote` with the cnpg plugin), or accept downtime with
  `--disable-eviction`.
- Bare pods without a controller (one-off debug or job pods): delete them.

## Rebalancing afterwards

Drained pods don't move back by themselves. After maintenance, spread them
again by restarting Deployments and StatefulSets in small batches, lowest
priority first (`homelab-batch`, no class, `homelab-apps`, `homelab-home`,
then `homelab-critical`; see
[priority classes](/infrastructure/priority-classes/priority-classes.yaml)),
checking between batches that all pods are Ready, `flux get kustomizations -A`
is all Ready and no blackbox probe fails:

```
kubectl -n default rollout restart deploy/<name>
kubectl get --raw '/api/v1/namespaces/kube-prometheus-stack/services/kube-prometheus-stack-prometheus:9090/proxy/api/v1/query?query=count(probe_success==0)'
```

The scheduler places restarted pods on the node with the fewest memory
requests, so they drift to the emptier host. Delete the pod instead of
restarting for operator-managed StatefulSets (Prometheus, Alertmanager), and
leave database pods to their owners. Compare hosts with `kubectl top nodes`.

There is deliberately no descheduler: almost every app runs a single replica,
so each eviction it made would be a short outage, traded for balance that
only matters until the next maintenance.
