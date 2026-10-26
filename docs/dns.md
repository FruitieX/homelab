# DNS setup

In addition to an A record pointing directly at my home IP, I've also configured a wildcard CNAME record pointing at my domain:

```
;; A Records
example.org.	    60	IN	A	  <my home IP>

;; CNAME Records
*.example.org.	    60	IN	CNAME	  example.org.
```

The idea is that:

- DNS requests to any subdomain will resolve to my home IP
- A port forward rule for ports 80 (HTTP) and 443 (HTTPS) in my router will forward any HTTP(S) traffic to the public Envoy Gateway
- Envoy Gateway forwards requests to the corresponding HTTP service based on the `Host` HTTP header

Remember to forward ports 80 and 443 in your router to the IP address of the public Envoy Gateway service.

# Cluster DNS (CoreDNS)

CoreDNS comes from Talos: `kube-system/coredns` is a Talos bootstrap
manifest (`talosctl get manifests 11-core-dns`), created at bootstrap and
re-applied by `talosctl upgrade-k8s`. Talos only lets you change its image
or disable it, so the Flux Kustomization `coredns`
([infrastructure/networking/coredns](/infrastructure/networking/coredns))
server-side applies just the fields we change on top of Talos' copy:

- 3 replicas instead of 2, spread one per Proxmox host
  (`topology.kubernetes.io/zone`, see [talos/patches/hosts](/talos/patches/hosts/README.md));
- a PodDisruptionBudget (`maxUnavailable: 1`), so drains keep two running.

`talosctl upgrade-k8s` takes `replicas` back to Talos' 2 (it shows up as a
diff in `talosctl upgrade-k8s --dry-run`); Flux restores 3 on its next
reconcile, or right away with
`flux reconcile kustomization coredns`. The spread constraint and the PDB
aren't in Talos' manifest, so upgrades leave them alone. The Kustomization
never prunes, so removing it doesn't delete CoreDNS.

Why not the alternatives:

- Disabling Talos' CoreDNS (`cluster.coreDNS.disabled`) and running our own
  from Flux would leave a freshly bootstrapped cluster without DNS, and Flux
  needs DNS to fetch this repo.
- NodeLocal DNSCache programs iptables rules, and kube-proxy here runs in
  nftables mode.

A drained or gracefully rebooted node causes no DNS errors: the replica is
evicted first, CoreDNS lame-ducks for 5 s and its endpoint goes away. A node
that dies abruptly still serves timeouts for queries sent to its replica (now
one in three instead of one in two) until the node controller marks it
NotReady (node-monitor-grace-period, 50 s by default). Use [node maintenance](/docs/maintenance.md) for
planned reboots.
