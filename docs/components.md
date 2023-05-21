## Components

I make use of the following pieces of software and infrastructure:

### Hardware

- Minisforum UM580 (Ryzen 7 5800H)
- Synology DS420+ NAS
- UniFi Dream Machine router + various UniFi switches and access points

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
- Auto updates with Renovate
- Safely commit secrets to git thanks to Mozilla SOPS
- In case of emergency, redeployment of the entire cluster (including OS
  installations, assuming configuration is already adapted to environment) takes < 30 min

### MetalLB & external-dns

- Services get their own IP addresses allocated in my home network under a separate subnet
  - Every container can use whatever TCP/UDP ports it wants, there are no collisions
  - It's possible to run multiple duplicate services on their default ports
- Every service gets their IP address added to Cloudfront DNS records
  - For example, the `podinfo` hostname resolves the podinfo service, reachable both inside the cluster and from my home network

### ingress-nginx & cert-manager

- HTTP services will have their subdomains reverse proxied automatically
  - For example, accessing `podinfo.example.org` will hit the `ingress-nginx` service (thanks to wildcard CNAME & router port forwarding)
  - `ingress-nginx` will check the `Host` HTTP header and proxy the request to the `podinfo` service
- All subdomains get auto-renewing Let's Encrypt certificates

### nfs-subdir-external-provisioner, synology-csi

- iSCSI/NFS mounts on separate NAS for persistent storage
  - Nothing of value resides on the compute nodes' disks and as such they can be considered "throwaways"

### kube-prometheus-stack

- Detailed Grafana dashboards & metrics

