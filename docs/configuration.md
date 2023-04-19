# Further configuration

## Configuration approaches

For each app and infrastructure manifest, make sure you understand roughly what you are about to deploy and what configuration values the deployment will use.

There are a few different approaches to configuration in the k8s ecosystem. In this repo you will find examples of:

- ### Bare deployments

  A `kind: Deployment` manifest instructs the cluster to deploy a container directly from e.g. Docker Hub or another container registry. For example, the [hue-mqtt](/apps/hue-mqtt/hue-mqtt.yaml) app.

  - Advantages: Very simple to write yourself for quick deployments of container images.

  - Downsides: Verbose configuration when you want to expose the deployment via a service and/or ingress. You then need to write separate Service/Ingress manifests, as for example the [podinfo](/apps/podinfo/podinfo.yaml) app manifest does.

  Configuration usually happens through environment variables or volume mounts to mount config files from either `kind: ConfigMap` or `kind: Secret` manifests. These kinds of manifests both act as a key/value store, where the key is the filename and value is the file contents. The filenames will be available under some given volume mountPath.

- ### Helm

  [Helm](https://helm.sh/) is a package manager for Kubernetes.

  Applications come packaged as Helm charts. You can find readily made helm charts [e.g. here](https://artifacthub.io/packages/search)

  This repo is structured such that Helm chart "sources" - HelmRepositories - are stored in [/infrastructure/sources](/infrastructure/sources), and charts can be "released" by a HelmRelease (for example [/infrastructure/networking/ingress-nginx-public/release.yaml](/infrastructure/networking/ingress-nginx-public/release.yaml), here you can also see values being overridden)

  - Advantages: Helm charts can be an easy way to deploy very complex infrastructure into your cluster very conveniently. Helm charts usually let you override "values" to configure the deployment as you want. [Here's an example](https://artifacthub.io/packages/helm/prometheus-community/kube-prometheus-stack?modal=values) of configurable values in `kube-prometheus-stack`'s values.yaml.

  - Downsides: Possibly more work to understand what goes wrong if a deployment fails, although with popular helm charts usually someone else online has had the same problem and found a solution.

# Notes on configuring specific apps & infrastructure:

These notes explain how I make use of each of the services and how to configure them for this use case. Note that they can usually do a lot more than explained here.

## [MetalLB](https://metallb.universe.tf/)

MetalLB allows k8s services to appear under their own unique IP address in your home network.

- [/infrastructure/crds/metallb-config.yaml](/infrastructure/crds/metallb-config.yaml)

  Set the IP address pool that MetalLB should use when exposing services to your home network.

  Make sure it's outside the range of your router's DHCP allocation pool (don't use e.g. 192.168.1.0/24) so you don't get collisions.

- Search for `metallb.universe.tf/loadBalancerIPs` annotations in the repo.

  This annotation sets a static IP (from the MetalLB IP address pool) for a given service. Useful if you don't want the IP of a certain service to change (for example a DNS server or nginx-ingress, both of which you will probably configure IP addresses manually for in your router settings)

- Add `metallb.yaml` and `crds.yaml` back in [/clusters/homelab/infrastructure/kustomization.yaml](/clusters/homelab/infrastructure/kustomization.yaml). Commit and push.

- You should now start seeing external IP:s being assigned to your services in `kubectl get svc -A`

## Pi-hole

[Pi-hole](https://pi-hole.net/) acts as a DNS server that can block ads in your home network. It's supported by external-dns that we will configure below.

- Set `PIHOLE_PASSWORD` in your [/clusters/homelab/cluster-secrets.yaml](/clusters/homelab/cluster-secrets.yaml) file.

  See [/docs/sops.md](/docs/sops.md) for more information on how to store secrets.

  This will be the password that's used to access the Pi-hole web interface under `http://pihole.example.org/admin`

- Add `pihole.yaml` back in [/clusters/homelab/infrastructure/kustomization.yaml](/clusters/homelab/infrastructure/kustomization.yaml). Commit and push.

- Once you have Pi-hole running, first get the external IP address of the `pihole` service: `kubectl get svc -n pihole`

- Verify that DNS requests to Pi-hole work:
  `dig @<external ip of pihole> google.com +short`
  `dig @<external ip of pihole> podinfo +short`

- Configure your router's DHCP server to hand out the DNS address of the Pi-hole service.

## external-dns

[external-dns](https://github.com/kubernetes-sigs/external-dns) keeps the DNS records of supported DNS servers in sync with IP addresses of your services.

- Search for `external-dns.alpha.kubernetes.io/hostname` annotations in the repo.

  This annotation sets the hostname/domain name that external dns will publish to Pi-hole.
  Can be set to multiple values by comma separating them.

- Add `external-dns.yaml` back in [/clusters/homelab/infrastructure/kustomization.yaml](/clusters/homelab/infrastructure/kustomization.yaml). Commit and push.

- You should now start seeing DNS records appear in Pi-hole's admin web interface under Local DNS -> DNS Records.

## Flux CD notification receiver

Normally, flux will poll your repository for changes. This means there will be a delay between when you push until flux picks up on the changes. You can get around this by manually running `flux reconcile kustomization flux-system`, or by setting up webhook receivers so that GitHub will notify your cluster of the push event.

- [/infrastructure/flux/notification-receiver/github-webhook-token.yaml](/infrastructure/flux/notification-receiver/github-webhook-token.yaml)

  If you do want to use [flux webhook receivers](https://fluxcd.io/flux/guides/webhook-receivers/), then this file should contain a randomly generated `token` that flux will use to verify that it's GitHub sending the HTTP request and not somebody else.

  See [/docs/sops.md](/docs/sops.md) for more information on how to store secrets.

  Replace the `token` value with the output of `head -c 12 /dev/urandom | shasum | cut -d ' ' -f1`. Take note of the value, as you will need it later.

  Remember to encrypt the secrets file before you commit.

- The domain name for the receiver's ingress is set in cluster config (or secrets if you want) with the `FLUX_NOTIFICATION_DOMAIN_NAME` key, for example:

  ```
  FLUX_NOTIFICATION_DOMAIN_NAME: flux-notification.example.org
  ```

- Add `notification-receiver.yaml` back in [/clusters/homelab/infrastructure/kustomization.yaml](/clusters/homelab/infrastructure/kustomization.yaml). Commit and push.

- Wait for reconciliation. After reconciliation finishes, you should be able to run `kubectl -n flux-system get receiver`. Note down the receiver URL (i.e. `/hook/...`)

- On GitHub, navigate to your repository and click on the “Add webhook” button under “Settings/Webhooks”. Fill the form with:

  - Payload URL: combine `FLUX_NOTIFICATION_DOMAIN_NAME` and the hook URL from above:

    `http://<FLUX_NOTIFICATION_DOMAIN_NAME>/<ReceiverURL>`

  - Secret: use the `token` string from above

- Next time you commit a change, Flux should start reconciliation almost immediately. You can see this take place with `flux logs --tail 20 -f`.

## Flux CD notification provider

Flux can forward reconciliation errors to various chat apps, I'm personally using Discord for this.

- Create a Discord server for your homelab

- Go to server settings -> Integrations -> Webhooks

- Create new webhook and click `Copy Webhook URL`

- Paste the URL into [/infrastructure/flux/notification-provider/discord-webhook-url.yaml](/infrastructure/flux/notification-provider/discord-webhook-url.yaml) under the `address:` key.

  See [/docs/sops.md](/docs/sops.md) for more information on how to store secrets.

- Add `notification-provider.yaml` back in [/clusters/homelab/infrastructure/kustomization.yaml](/clusters/homelab/infrastructure/kustomization.yaml). Commit and push.

- You should now get Discord notifications on reconciliation failures.

## ingress-nginx

[ingress-nginx](https://github.com/kubernetes/ingress-nginx) acts as a reverse proxy for HTTP requests to your cluster. Based on the subdomain a HTTP request was made to, ingress-nginx can forward the request to the appropriate service.

- This repo contains two instances of ingress-nginx:

  - ingress-nginx-public: For public (Internet) facing HTTP services
  - ingress-nginx-private: For private (LAN) facing HTTP services

- It's possible to select which ingress controller you want to use by specifying either:

  - `ingressClassName: public`
  - `ingressClassName: private`

  in the `spec:` section of a `kind: Ingress` manifest. [Here's an example for the podinfo ingress](/apps/podinfo/podinfo.yaml).

- By default the public ingress is set to use a static IP `192.168.11.1`, which
  you can use in your router's port forwarding settings to forward both ports 80
  (HTTP) and 443 (HTTPS) to.

- Add `ingress-nginx-public.yaml` and/or `ingress-nginx-private.yaml` back in [/clusters/homelab/infrastructure/kustomization.yaml](/clusters/homelab/infrastructure/kustomization.yaml). Commit and push.

- If kustomizations containing ingresses fail with `x509: certificate signed by unknown authority` errors, run:

  ```
  ns=ingress-nginx-public
  CA=$(kubectl -n $ns get secret ingress-nginx-admission -ojsonpath='{.data.ca}')
  kubectl patch validatingwebhookconfigurations ingress-nginx-admission -n $ns --type='json' -p='[{"op": "add", "path": "/webhooks/0/clientConfig/caBundle", "value":"'$CA'"}]'
  ```

  NOTE: Depending on if you get this error for the public or private instance of ingress-nginx, you may need to adjust `ns` accordingly.

  Source: https://fabianlee.org/2022/01/29/nginx-ingress-nginx-controller-admission-error-x509-certificate-signed-by-unknown-authority/

## cert-manager

[cert-manager](https://cert-manager.io/) obtains and renews Let's Encrypt certificates.

- Set `LETSENCRYPT_EMAIL` in your [/clusters/homelab/cluster-secrets.yaml](/clusters/homelab/cluster-secrets.yaml) file.

  See [/docs/sops.md](/docs/sops.md) for more information on how to store secrets.

  This e-mail address will be used by Let's Encrypt to let you know of certificate expiration and other possible issues.

- Remember to set `LETSENCRYPT_CLUSTER_ISSUER` in [/clusters/homelab/cluster-config.yaml](/clusters/homelab/cluster-config.yaml) back to `letsencrypt-prod` once you have verified that your setup is working with certificates issued by `letsencrypt-staging` (which you naturally have to manually allow in your browser when testing).

- Add `cert-manager.yaml` and `crds.yaml` back in [/clusters/homelab/infrastructure/kustomization.yaml](/clusters/homelab/infrastructure/kustomization.yaml). Commit and push.

## synology-csi

[synology-csi](https://github.com/SynologyOpenSource/synology-csi) is a Container Storage Interface driver for Synology NAS. It
automatically manages iSCSI targets and LUNs so you don't need to create them by
hand.

synology-csi makes it possible have persistent storage in your containers by
creating and mounting iSCSI targets from a Synology NAS.

- Replace the contents of [/infrastructure/storage/synology-csi/client-info.yaml](/infrastructure/storage/synology-csi/client-info.yaml) with:

  ```
  apiVersion: v1
  kind: Secret
  metadata:
      name: client-info-secret
      namespace: synology-csi
  stringData:
      client-info.yaml: |
          ---
          clients:
            - host: <hostname or IP of synology NAS>
              port: 5000
              https: false
              username: <username>
              password: <password>
  ```

  See [/docs/sops.md](/docs/sops.md) for more information on how to store secrets.

  Remember to encrypt the secrets file before you commit.

- Add `synology-csi.yaml` back in [/clusters/homelab/infrastructure/kustomization.yaml](/clusters/homelab/infrastructure/kustomization.yaml). Commit and push.

- You should now be able to create PersistentVolumeClaims and mount them as volumes into containers. See [/apps/postgres/postgres.yaml](/apps/postgres/postgres.yaml) for an example.

- Use `kubectl get pvc` and `kubectl get pv` to list PersistentVolumeClaims and PersistentVolumes respectively.

- Debugging tips if containers are stuck waiting for volumes forever:
  - List all synology-csi pods: `kubectl get pod -n synology-csi`
  - Follow logs from a particular pod: `kubectl logs pod/synology-csi-controller-0 -n synology-csi --all-containers -f`
  - See if you can find relevant any error messages
  - Check in the Synology NAS' SAN Manager if the iSCSI targets & LUNs get created, if not then there may be some authentication/permission problems with your Synology user account.
  - More info: https://github.com/SynologyOpenSource/synology-csi

## nfs-subdir-external-provisioner

You can also opt to use NFS with [nfs-subdir-external-provisioner](https://github.com/kubernetes-sigs/nfs-subdir-external-provisioner) instead of iSCSI. It's a bit easier to configure,
however there may be various pitfalls involved especially if used as storage for
database servers.

- Configure your NFS server to allow connections from the IP address range that your cluster nodes use.

- Edit [/infrastructure/storage/nfs-subdir-external-provisioner/release.yaml](/infrastructure/storage/nfs-subdir-external-provisioner/release.yaml) to match your NFS server setup

  - Change `nfs.server` so it points at your NAS hostname or IP address
  - Change `nfs.path` to the path you want to mount on your NAS

- Add `nfs-subdir-external-provisioner.yaml` back in [/clusters/homelab/infrastructure/kustomization.yaml](/clusters/homelab/infrastructure/kustomization.yaml). Commit and push.

## Apps

Use the existing app manifests as examples on how to create new ones if needed.

You can install apps from Helm charts as well, remember to add the HelmRepository to [/infrastructure/sources](/infrastructure/sources) if it doesn't already exist, and include the manifest
in [/infrastructure/sources/kustomization.yaml](/infrastructure/sources/kustomization.yaml) as well. Then create a `kind: HelmRelease` manifest under apps, include it in a `kustomization.yaml` as well as in [/clusters/homelab/apps/kustomization.yaml](/clusters/homelab/apps/kustomization.yaml).

Look at example HelmRelease manifests by searching for `kind: HelmRelease` under /infrastructure.
