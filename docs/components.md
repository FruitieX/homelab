## Components

I make use of the following pieces of software and infrastructure:

### Hardware

- Three independent Proxmox mini PCs (Ryzen 7 5800H, Ryzen 7 6800H, Intel N150) running the Talos VMs
- Synology DS923+ NAS
- UniFi Cloud Gateway Fiber + various UniFi switches and access points

### Kubernetes

- Convenient way to run a bunch of services in containers
- You can easily scale out by adding more nodes (e.g. old hardware)
- Searching for Kubernetes related error messages online often brings up useful help
- Large ecosystem of readily available packages and solutions
  - Surprisingly little in terms of configuration can accomplish complex things

### Proxmox VE

- Virtual machine management platform (among other things)
- Makes it possible to perform reinstalls and spin up various toy environments without physical access to the hardware each time.

### Talos Linux

- Linux distro specifically designed for running a Kubernetes cluster
- Minimal configuration involved in getting cluster up and running

### Flux CD

- Cluster configuration is described by a declarative configuration in a git
  repository (this one)
  - `git push` automatically reconciles cluster to match new configuration
  - Rollbacks normally amount to running `git revert`
- Discord notifications on reconciliation errors
- Auto updates with Renovate ([renovate.json](/renovate.json)): patch, minor and
  digest updates automerge after a 3-day wait once the
  [validate](/.github/workflows/validate.yaml) check passes; majors, Flux and
  auth/ingress/database control planes stay as PRs. The check renders every
  Flux Kustomization path and runs kubeconform
  (`nix-shell --run scripts/validate-manifests.sh`)
- Safely commit secrets to git thanks to Mozilla SOPS
- In case of emergency, redeployment of the entire cluster (including OS
  installations, assuming configuration is already adapted to environment) takes < 30 min

### MetalLB & external-dns

- Services get their own IP addresses allocated in my home network under a separate subnet
  - Every container can use whatever TCP/UDP ports it wants, there are no collisions
  - It's possible to run multiple duplicate services on their default ports
- external-dns publishes annotated services' addresses as Cloudflare DNS records
  - For example, the `podinfo` hostname resolves the podinfo service, reachable both inside the cluster and from my home network

### Envoy Gateway & cert-manager

- HTTP services will have their subdomains reverse proxied automatically
  - For example, accessing `podinfo.example.org` will hit the public Envoy Gateway (thanks to wildcard CNAME & router port forwarding)
  - Envoy Gateway will check the `Host` HTTP header and proxy the request to the `podinfo` service
- All subdomains get auto-renewing Let's Encrypt certificates

### csi-driver-nfs

- NFS mounts on a separate NAS provide persistent storage
  - NFS is the default StorageClass for the current workloads
  - Nothing of value resides on the compute nodes' disks and as such they can be considered "throwaways"

### CloudNativePG (shared PostgreSQL)

- The shared PostgreSQL that most apps use (`postgres` Service, `postgres.default.svc:5432`) is the CloudNativePG
  cluster `postgres-ha` ([apps/postgres/postgres-ha.yaml](/apps/postgres/postgres-ha.yaml)): PostgreSQL 18, a
  primary and one streaming replica on different physical hosts, quorum commit while the replica is connected
  - The `postgres` Service selects the current primary, so app connection strings never change; a failover takes
    about 15 s and apps reconnect on their own
  - Both instances keep their data on `nfs-csi`, so the NAS remains a single point of failure: the cluster survives
    losing a node or a host, not the NAS
  - `postgres-dump` dumps every database plus roles nightly to the `postgres-dumps` volume (14 days kept), which
    the NAS backs up. Restore: `psql -f roles-with-passwords.sql postgres`, then `pg_restore --create -d postgres <db>.dump`
  - Status and switchover: `kubectl cnpg status postgres-ha`, `kubectl cnpg promote postgres-ha <instance>`
  - Role memberships and per-role settings (`ALTER ROLE ... SET`) are not carried over by a CNPG import;
    `grafana_ro`'s `pg_monitor` membership is declared in the Cluster, its settings were set by hand
  - Secrets the operator reads (superuser, managed role passwords) must carry the label `cnpg.io/reload: "true"`;
    without it CNPG doesn't notice a changed password and the database keeps the old one
- Outside the cluster, the `postgres` LoadBalancer Service (pinned MetalLB address, external-dns hostname) accepts
  connections only from the sources in `loadBalancerSourceRanges`: the pod network and a few admin workstations.
  The addresses are variables from the SOPS-encrypted `cluster-secrets` (Flux postBuild substitution); add a
  client there. Node ports are disabled because source ranges don't apply to them
- Immich has its own PostgreSQL (`immich-postgres`, [apps/immich/immich-postgres.yaml](/apps/immich/immich-postgres.yaml)),
  with the vector extensions Immich needs; it is reachable only inside the cluster (`immich-postgres.default.svc`)

### Mosquitto (MQTT)

- The MQTT broker for home automation ([apps/mosquitto](/apps/mosquitto)): Service `mqtt` (`mqtt.default.svc:1883`
  in the cluster, a LoadBalancer with an external-dns hostname on the LAN). Retained messages persist on `nfs-csi`
- Clients authenticate with a username and password. Each client has its own user, and the ACL limits each user
  to the topics it needs (bridges to their own topic tree, Telegraf to reading, homectl to everything).
  Devices outside the cluster that can't be given credentials without reflashing still connect anonymously; the
  anonymous ACL lets them read everything and publish only their state, never commands
- The hashed password file (`passwd`, made with `mosquitto_passwd`) and the ACL (`acl`; lines before the first
  `user` apply to anonymous clients) are the SOPS Secret
  [apps/mosquitto/auth.sops.yaml](/apps/mosquitto/auth.sops.yaml). The plain passwords are in 1Password, one Login
  item per user named `MQTT broker: <user>`
- A sidecar sends mosquitto a SIGHUP when the mounted config or Secret changes, so password and ACL changes apply
  a minute or two after Flux applies them, without a restart. A denied publish is logged only at `log_type debug`
- Adding a client:
  1. Create the 1Password item (`op item create --category=login --title='MQTT broker: <user>'
     --generate-password=32,letters,digits username=<user>`)
  2. Hash it with the broker's own `mosquitto_passwd` (same version; feed `user:password` lines on stdin, e.g.
     `kubectl exec -i deploy/mosquitto -c mosquitto -- sh -c 'f=$(mktemp); cat >$f; mosquitto_passwd -U $f; cat $f; rm $f'`)
     and append the line to `passwd` with `sops apps/mosquitto/auth.sops.yaml`
  3. Add a `user <user>` block with its `topic read|write|readwrite <pattern>` lines to `acl`
  4. Give the client its credentials (the bridges take `username`/`password` in the `[mqtt]` section of their
     Settings.toml; homectl and Bambuddy keep them in their own settings)
- The Secret and the Deployment have Flux variable substitution disabled, because password hashes and the sidecar
  script contain `$`. Mosquitto 2.0 warns that the mounted files are not owned by the mosquitto user; later versions
  refuse to load them, so an upgrade needs the files copied and chowned first (e.g. by an init container)

### kube-prometheus-stack

- Metrics, alerts and dashboards; see [monitoring](monitoring.md)
