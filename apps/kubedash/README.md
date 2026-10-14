# kubedash

Mobile-first dashboard for this cluster's workloads at
**https://kubedash.fruitiex.org**. It shows health, pods, events and live logs,
and can rollout-restart workloads or delete pods. Source:
[FruitieX/kubedash](https://github.com/FruitieX/kubedash).

kubedash has no login of its own, so its HTTPRoute attaches only to the
**private** Envoy gateway (LAN/VPN). Never add a public route. The `kubedash`
ClusterRole is read-only except for `patch` on Deployments, StatefulSets and
DaemonSets (restart) and `delete` on pods.

Pushes to kubedash `main` publish a new image. The repo's
`HOMELAB_DISPATCH_TOKEN` secret then triggers `update-image.yaml` here, which
pins the new digest.

```sh
export KUBECONFIG="$PWD/kubeconfig"
flux reconcile kustomization kubedash --with-source
kubectl get pods -n default -l app=kubedash
```
