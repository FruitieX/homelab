# Monitoring

Grafana (`grafana.fruitiex.org`) opens on the **Overview** dashboard, which shows the house and the homelab
side by side. Every other dashboard is one click away in the link bar.

| Folder | Dashboards | Data |
| --- | --- | --- |
| Home | Energy & water, Climate, Devices | InfluxDB `home` (MQTT via Telegraf) and `nordpool` |
| Homelab | Services, Workloads, Hardware, Network, Logs & security, Apps | Prometheus, Loki, app PostgreSQL databases |
| Kubernetes, Loki | Stock kube-prometheus-stack and Loki mixin dashboards | Prometheus |
| Legacy (hand-made) | The old UI-made dashboards, kept for reference | InfluxDB |

## Dashboards are code

`infrastructure/monitoring/grafana/dashboards/` holds a small Python generator:

- `lib.py` contains the panel and target builders and the colour slots. Each entity (a smart plug, a room) keeps
  one fixed colour.
- `home.py` holds the house dashboards and the BLE sensor and smart plug names.
- `lab.py` holds the homelab dashboards and the overview.

Edit those files, then run `python3 infrastructure/monitoring/grafana/dashboards/generate.py` and commit the
regenerated JSON and `kustomization.yaml`. The Grafana sidecar loads them from ConfigMaps labelled
`grafana_dashboard`, into the folder named by their `grafana_folder` annotation. The ConfigMaps opt out of Flux
variable substitution because dashboards use `${...}` themselves. Saving in the UI does not stick, so use
*Save as* to experiment.

A new BLE sensor appears on the dashboards only after you add its `device_id` to the maps in `home.py`. Unlisted
ids are ignored on purpose, because neighbours' sensors show up too.

## Collectors

`infrastructure/monitoring/targets/` (Flux Kustomization `monitoring-targets`):

- **Blackbox probes:** HTTPS for every app hostname, and TCP for core services and LAN hosts. A redirect to
  Pocket ID, 401 and 403 all count as up.
- **Flux:** controller metrics via a PodMonitor. Resource readiness comes from `gotk_resource_info`, which
  kube-state-metrics builds with `customResourceState`.
- **Bambuddy:** its `/api/v1/metrics` endpoint.
- **Proxmox:**
  - Debian `prometheus-node-exporter` on each host, on port 9100.
  - `pve-exporter`, using one read-only `prometheus@pve!exporter` token per host. The tokens are stored in
    1Password as "Proxmox <host> API token" and in `pve-exporter.sops.yaml`.
- **NAS:** `snmp-exporter` with the built-in `synology` module, using SNMP v2c from DSM. The community string is
  in 1Password ("nectarine SNMP community"). Disk IDs are hex-encoded by SNMP, so a metric relabeling maps them
  to "Disk N".
- **UniFi:** `unpoller` with a UniFi Network API key (1Password "UniFi homelab API key").
- **LAN targets:** every scrape config that names a LAN address is in `lan.sops.yaml`. Only `targets` is
  encrypted there, because this repo is public.

Grafana also queries the apps' PostgreSQL databases directly as `grafana_ro`. That is a SELECT-only role with
`pg_monitor`, set to read-only by default with a 30 s statement timeout. It exists on `postgres` (ferrotune,
homectl) and on `immich-postgres`; the password is in 1Password ("homelab PostgreSQL grafana_ro"). The role was
created by hand, so recreate it if a database is restored from scratch.

## Alerts

- Prometheus rules in `targets/rules.yaml` carry `homelab="true"`. Alertmanager sends those, plus any
  `critical` alert and a few chosen stock alerts, to the Discord channel that Flux notifications use. Everything
  else stays in the Alertmanager UI.
- Grafana-managed rules for the InfluxDB data are in `grafana/grafana-alerting.sops.yaml` and go to the same
  Discord webhook. They cover the freezer, the fridge, water flowing non-stop, and home sensors going silent.

## Pitfalls

- **In-cluster clients must not use the public InfluxDB URL.** The `influxdb.fruitiex.org` route requires
  Pocket ID sign-in. A machine client writing to it gets the login page, and Telegraf treated that as success,
  so data was silently lost. In-cluster clients use `http://influxdb-influxdb2.influxdb.svc`. homectl's InfluxDB
  URL is a setting in its database (`widget_settings`, key `influxdb`).
- **Datasource UIDs must not equal another datasource's name.** Grafana resolves a reference by name as well,
  so the uid `influxdb` resolved to the old UI datasource *named* `influxdb`. That is why the provisioned one is
  `influxdb-home`.
- **Each datasource ConfigMap needs a unique key.** The sidecar writes every key into one directory, so two
  `datasource.yaml` keys overwrite each other.
- **kube-prometheus-stack CRDs:** Flux skips CRDs on upgrade by default, which left them at their 2023
  versions, so newer `Prometheus` fields were pruned silently. The release now sets `crds: CreateReplace`.
- **`scrapeConfigSelector` must be non-empty.** The chart renders nothing unless it is set, and Helm's merge
  patch drops an empty `{}`. The always-true `matchExpressions` in the values exists for this reason.
- **Prometheus compaction:** an orphaned, empty block directory with no `meta.json` stopped compaction for four
  months. The `PrometheusTSDBCompactionsFailing` alert now goes to Discord, and "Prometheus TSDB blocks" on the
  Workloads dashboard should stay in the tens.
