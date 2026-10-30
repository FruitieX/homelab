# Monitoring

Grafana (`grafana.fruitiex.org`) opens on the **Overview** dashboard, which shows the house and the homelab
side by side. Every other dashboard is one click away in the link bar.

| Folder | Dashboards | Data |
| --- | --- | --- |
| Home | Energy & water, Climate, Devices, Heat pump, 3D printing | InfluxDB `home` (MQTT via Telegraf, myUplink) and `nordpool` |
| Homelab | Services, Workloads, Hardware, Network, Logs & security, Apps | Prometheus, Loki, app PostgreSQL databases |
| Kubernetes, Loki | Stock kube-prometheus-stack and Loki mixin dashboards | Prometheus |
| Legacy | The old hand-made dashboards (Home, Indoor, Outdoor, Appliances, 3D printing, Loki logs), kept for reference | InfluxDB `home`, Loki |

Every dashboard and datasource is provisioned from this repository; nothing lives only in Grafana's database.

## Dashboards are code

`infrastructure/monitoring/grafana/dashboards/` holds a small Python generator:

- `lib.py` contains the panel and target builders and the colour slots. Each entity (a smart plug, a room) keeps
  one fixed colour.
- `home.py` holds the house dashboards and the BLE sensor and smart plug names; `heatpump.py` and `printing.py`
  hold the heat pump and 3D printing dashboards.
- `lab.py` holds the homelab dashboards and the overview.

Edit those files, then run `python3 infrastructure/monitoring/grafana/dashboards/generate.py` and commit the
regenerated JSON and `kustomization.yaml`. The Grafana sidecar loads them from ConfigMaps labelled
`grafana_dashboard`, into the folder named by their `grafana_folder` annotation. The ConfigMaps opt out of Flux
variable substitution because dashboards use `${...}` themselves. Saving in the UI does not stick, so use
*Save as* to experiment.

A new BLE sensor appears on the dashboards only after you add its `device_id` to the maps in `home.py`. Unlisted
ids are ignored on purpose, because neighbours' sensors show up too.

`legacy/` holds the old dashboards that were made in the UI, exported once as plain JSON (not generated). They
are frozen: fix them by editing the JSON, or port a panel to the generator. BLE sensors are heard by more than
one gateway (`integration_id` `ble3`, `ble4`, …), so their queries merge gateways with `group()` instead of
filtering on one.

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

- **Internet:** ICMP to public resolvers, the SSH banner of GitHub on ports 22 and 443, and TCP to the cloud APIs
  the homelab uses (`group="internet"`). ICMP works unprivileged through the pod's `ping_group_range` sysctl.

**Pod logs:** Grafana Alloy (`infrastructure/monitoring/loki/alloy-release.yaml`, a DaemonSet on every node) tails
`/var/log/pods`, decodes the CRI format and pushes to Loki. It sets the same labels Promtail did (`namespace`, `pod`,
`container`, `app`, `job`, `instance`, `component`, `node_name`, `stream`, `filename`). The Loki security rules and
the log dashboards select on these, so keep them unchanged. Read offsets are kept in `/var/lib/alloy` on each node.

Telegraf `telegraf-home` (config in `apps/influxdb/config.yaml`) stores MQTT data in bucket `home`, measurement
`mqtt_consumer`, tagged `kind`/`integration_id`/`device_id` from the topic. It keeps only our own BLE sensors (a
`tagpass` list of device ids; the gateways also hear neighbours' devices), so add a new sensor there as well as in
the dashboards. It drops repeated zigbee2mqtt device metadata and settings. ESPHome light state lands next to the
old Tuya bulb history (`kind=lights`, `integration_id=esphome`); ESPHome chip temperature and Wi-Fi signal go to
measurement `esphome`, thermal lockout (`ON`/`OFF`) to `esphome_binary`.

`apps/influxdb/telegraf-cloud.yaml` fetches cloud data into InfluxDB (bucket `home`, field `value`). Its
credentials, the NIBE device id, the FMI station and the home coordinates are in `telegraf-cloud.sops.yaml`,
and its InfluxDB token ("homelab InfluxDB telegraf-nibe token" in 1Password) can only write to `home`.

- **NIBE F750 heat pump** (measurement `nibe`, tags `id`, `name`, `unit`): every myUplink data point, once a
  minute, the same interval as the official Home Assistant integration. Query by `id` (the parameter number),
  because NIBE can rename points. The myUplink app ("myUplink homelab API" in 1Password) only requests the
  `READSYSTEM` scope, so the poller cannot change pump settings. Point `43144` is labelled "time factor" but is
  the compressor's energy counter in kWh.
- **FMI weather** (no key): `fmi_observation` holds the nearest station's 10-minute observations, tagged by
  `param` (`t2m`, `ws_10min`, `r_1h` and so on). `fmi_forecast` holds the forecast for home, rewritten hourly so
  future points are always the latest forecast. NaN values are skipped because InfluxDB cannot store them.

- **FMI daily means** (`fmi_daily`, `param=tday`): fetched every 6 hours, backfilled from 2020.
- **Fingrid open data** (measurement `fingrid`, tag `series`): CO₂ intensity of consumption and production,
  national consumption and production, nuclear, hydro and wind, net import, frequency, system state and
  shortage status, every 3 minutes. The key ("Fingrid Open Data API" in 1Password, field "primary key") allows one
  request per 2 s and 10 000 a day, so the 11 inputs are staggered 10 s apart. A manual backfill using the same
  key at the same time will cause 429 errors. `fingrid_hourly` holds hourly CO₂ intensity backfilled from 2020.

**Whole-house electricity** comes from Fingrid Datahub, which has no consumer API. Export the consumption CSV from
oma.datahub.fi and run `KUBECONFIG=kubeconfig python3 scripts/datahub-import.py consumption.csv` (measurement
`electricity`, field `kwh`, hourly until the meter is replaced). Exports can overlap. The energy dashboard's
"expected from weather" line is a model fitted on 2021–2024 (`ELEC_*` in `home.py`).

History before the live collectors started was backfilled from 2020 with scripts that can be re-run:
`scripts/spot-price-backfill.py` (Nord Pool FI prices from Elering's public API, written in nordpool-mqtt's
format with the VAT of each day) and `scripts/fmi-backfill.py` (hourly FMI observations; live data is
10-minute). The FMI daily means and Fingrid's hourly CO₂ intensity were loaded the same way.

The heat pump dashboard's shower estimate uses constants in `heatpump.py`. They come from the water meter
history: evening showers run about 4.8 L/min for 13–15 minutes. Re-measure them if the shower head changes.

Grafana also queries the apps' PostgreSQL databases directly as `grafana_ro`. That is a SELECT-only role with
`pg_monitor`, set to read-only by default with a 30 s statement timeout. It exists on `postgres` (ferrotune,
homectl, bambuddy) and on `immich-postgres`; the password is in 1Password ("homelab PostgreSQL grafana_ro"). The role was
created by hand, so recreate it if a database is restored from scratch. On the shared CloudNativePG cluster its
`pg_monitor` membership is declared in `apps/postgres/postgres-ha.yaml`; reapply the role settings by hand
(`ALTER ROLE grafana_ro SET default_transaction_read_only = on`, `... SET statement_timeout = '30s'`).

Bambuddy stores its data in the shared PostgreSQL (`bambuddy` database, `DATABASE_URL` in
`apps/bambuddy/database.sops.yaml`, connection pool capped at 15). It moved off SQLite on 2026-10-24 with
Bambuddy's own importer, and the last SQLite snapshot stays in its data volume as `bambuddy-pre-postgres.db`.
To roll back, drop `DATABASE_URL` from the deployment and restore that file as `bambuddy.db`. Prints made after
the migration would then be missing. The 3D printing dashboard reads the print archive, maintenance and spools
from that database.

## Alerts

- Discord channels: `#flux-notifications` gets only Flux's own notifications (its notification provider);
  Alertmanager's `homelab="true"`, `critical` and chosen stock alerts go to `#homelab`
  (`alertmanager-discord.sops.yaml`), as do Grafana rules outside the Home folder. Home-folder rules go to `#home`.
  The Flux webhook stays in `notification-provider/discord-webhook-url.yaml`; the other two are "Discord webhook
  home" and "Discord webhook homelab" in the Agents vault.
- Backups (`homelab.backups` in `targets/rules.yaml`): `PostgresDumpStale` fires when `postgres-dump` has not
  succeeded for 36 h, `CronJobLastRunFailed` when any CronJob's latest run failed. The stock `KubeJobFailed` is a
  plain warning and never reaches Discord. Immich's own database backups are not monitored.
- Grafana-managed rules for the InfluxDB data are in `grafana/grafana-alerting.sops.yaml`. They cover the freezer, the fridge, water flowing non-stop for 3 hours (normal use always pauses within
  about 2 hours), home sensors going silent, and the sauna being ready, from the upstairs bathroom BLE sensor (device `D83431306571`). It fires when, over the
  last 20 minutes, the temperature has risen at least 2 °C, humidity at most 5 points, and it is now at least 26 °C:
  sauna heating is a steady dry rise, while a shower sends humidity up at once and barely moves the temperature. It
  then stays firing while the bathroom is at least 2.5 °C above its lowest reading of the past 2 hours, so throwing
  water mid-session doesn't re-notify. Backtested on October 2026 data, it fired once per session (5 of 5, at
  26.2–27.0 °C, 30–40 min before the room passes 30 °C), never on other days, and resolved 2.5–3 h later. Its own
  route sets `group_wait: 0s` for an immediate message (a zero `repeat_interval` is invalid and breaks the whole file).
- More Home rules, with thresholds from July–October 2026 data:
  - Humidity stays high: any indoor sensor above 70% for 2 h (bathrooms never stayed above 70% for more than 1 h after a
    shower or sauna).
  - It's getting cold indoors: any indoor sensor below 18 °C for an hour (lowest since July was 20.3 °C). Winter data
    will show whether airing a room trips it.
  - Alarm isn't armed: Verisure mode `disarmed` between 23:30 and 05:00 (measurement `verisure`, topic
    `home/verisure/mode`). The household usually arms at 23:15–00:15, so 23:00 would nag nightly.
  - Electricity prices tomorrow: from 14:00, once tomorrow's prices are in, only if some hours are at or above
    30 c/kWh or at or below 0 c/kWh (VAT included); about 43 days a year in the past 12 months. The Flux query builds
    the message itself and passes it in the `text` label.
  The last two carry `notify=reminder` and go to the "Home reminders" contact point, which sends no resolved message.
- Device health and upkeep (thresholds from the same data):
  - A device stopped reporting: a BLE sensor silent for 1 h (normal gaps stayed under 20 min) or the fridge/freezer
    plug for 15 min (they report every ~16 s). The car sensor is skipped because it leaves with the car, and the rule
    stays quiet while every source is silent, which "No home sensor data" already covers.
  - Battery low: BLE sensors below 20% (24 h mean, smoothing cold dips) and Zigbee devices below 15%.
  - Filament storage is getting damp (12 h mean): dryboxes above 30% (they sat at 20–23%), AMS units above 38%
    (29–33%), and the filament dryer box above 45% (35–47%, open to room air). Its advice text travels in a label.
  - Fridge or freezer plug is off: Telegraf stores each Tuya plug's on/off state as field `on`, because the generic
    JSON consumer drops booleans and the freezer draws 0 W whenever its compressor rests.
  - Frost tonight (`notify=reminder`): only the first frost after a frost-free week, from `fmi_forecast` and FMI's
    observed `t2m`, checked 17:00–23:00.
  Battery and filament alerts carry `notify=maintenance`, which repeats every 72 h instead of 12 h.
- Home, Sauna and Home reminders post to Discord `#home` and to ntfy (`apps/ntfy`, topic `home`, LAN/VPN only). The
  `home.title`/`home.message` templates keep messages to the title and summary; a failed query says so instead of
  reusing the rule's alarm text. A resolved alert shows the rule's `resolved` annotation ("Freezer plug is reporting
  again."), falling back to the rule name and `place`. Use only labels in it: annotations are frozen at the last
  firing evaluation, so a value there would be the alarming one.

## Pitfalls

- **Memory-mapped files must not live on NFS.** Loki (bbolt and TSDB index) died with SIGBUS reading an index
  file through mmap on its NFS volume, which broke every log panel until it restarted. Its index caches, active TSDB
  index and compactor directory are on a node-local emptyDir (`/var/loki-local`); chunks and the WAL stay on NFS.
  The same applies to any other app that mmaps a database file (bbolt, LMDB, SQLite in WAL mode).
- **Provisioned MIP `matchers` are not a free map.** In `grafana-alerting.sops.yaml` a route's `matchers` must be an
  array of `label=value` strings or `object_matchers`; a plain object fails the reload (`cannot unmarshal object
  into Go struct field Route.matchers of type []string`) and the whole `home-alerts.json` then fails to provision,
  silently killing every rule in the file.
- **In-cluster clients must not use the public InfluxDB URL.** The `influxdb.fruitiex.org` route requires
  Pocket ID sign-in. A machine client writing to it gets the login page, and Telegraf treated that as success,
  so data was silently lost. In-cluster clients use `http://influxdb-influxdb2.influxdb.svc`. homectl's InfluxDB
  URL is a setting in its database (`widget_settings`, key `influxdb`).
- **Datasource UIDs must not equal another datasource's name.** Grafana resolves a reference by name as well,
  so the uid `influxdb` resolved to the old UI datasource *named* `influxdb` (since deleted). That is why the
  provisioned one is `influxdb-home`.
- **Each datasource ConfigMap needs a unique key.** The sidecar writes every key into one directory, so two
  `datasource.yaml` keys overwrite each other.
- **kube-prometheus-stack CRDs:** Flux skips CRDs on upgrade by default, which left them at their 2023
  versions, so newer `Prometheus` fields were pruned silently. The release now sets `crds: CreateReplace`.
- **`scrapeConfigSelector` must be non-empty.** The chart renders nothing unless it is set, and Helm's merge
  patch drops an empty `{}`. The always-true `matchExpressions` in the values exists for this reason.
- **Prometheus compaction:** an orphaned, empty block directory with no `meta.json` stopped compaction for four
  months. The `PrometheusTSDBCompactionsFailing` alert now goes to Discord, and "Prometheus TSDB blocks" on the
  Workloads dashboard should stay in the tens.
