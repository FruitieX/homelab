# Per-host Talos patches

Each Talos VM is labelled with the Proxmox host it runs on as
`topology.kubernetes.io/zone`. Workloads with several replicas spread across
this label (`topologySpreadConstraints`), so losing one host leaves a replica
running, and `scripts/node-maintenance.sh` finds a host's nodes by it.

| host     | nodes                                          |
|----------|------------------------------------------------|
| mango    | talos-85f-atb (control plane), talos-tio-mgv, talos-7us-vka |
| mandarin | talos-fwz-qfi (control plane), talos-zza-kh3, talos-ttd-bge |
| mulberry | talos-jyc-zhh (control plane)                  |

The label differs per host, so it isn't part of `regen-configs.sh`'s shared
patches. Apply the host's patch to a running node:

```
talosctl patch mc -n <node> --patch @talos/patches/hosts/<host>.yaml
```

and include it when installing or re-applying a node's config:

```
talosctl apply-config -n <node> --file _out/worker.yaml \
  --config-patch @talos/patches/hosts/<host>.yaml
```

Moving a VM to another host means re-applying the new host's patch.
Check with `kubectl get nodes -L topology.kubernetes.io/zone`.
