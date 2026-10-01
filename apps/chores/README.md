# Chores

A shared chores and bedtime app, deployed through Flux at **https://chores.fruitiex.org**. The route attaches only to the private Envoy gateway. LAN/VPN access is required; ExternalDNS points the hostname at the private gateway address.

First launch sets up the household, both adult accounts and the alternating bedtime starting date. There is no setup token. Save the separate recovery codes in each adult's password manager. Chores, point values, maintenance intervals and the rolling balance are configurable in the app.

Source and CI: [FruitieX/chores](https://github.com/FruitieX/chores) (private). [Operational instructions](https://github.com/FruitieX/chores/blob/main/docs/DEPLOYMENT.md) cover publishing, full database backups and restoration. The deployment pins a tested source revision and image digest.

Data is on `chores-data-nfs` with the existing `nfs-csi` storage class. SQLite uses DELETE rollback journaling and synchronous EXTRA; keep one replica with Recreate. PVC pruning is disabled to preserve household data if the deployment manifests are removed. The existing `ghcr-credentials` secret provides private image pulls.

Health endpoints are `/api/health` and `/api/ready`. `/api/auth/status` reports whether first launch has been completed. Verification should remain read-only on production and leave initial setup to the user.
