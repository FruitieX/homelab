# Monitoring

Grafana (`grafana.fruitiex.org`) opens on the **Overview** dashboard, which shows the house and the homelab
side by side. Every other dashboard is one click away in the link bar.

| Folder | Dashboards | Data |
| --- | --- | --- |
| Home | Energy & water, Climate, Devices, Heat pump, 3D printing | TimescaleDB `tsdb` (MQTT via Telegraf, myUplink, FMI, Fingrid, Datahub, Nord Pool) |
| Homelab | Services, Workloads, Hardware, Network, Logs & security, Apps | Prometheus, Loki, app PostgreSQL databases |
| Kubernetes, Loki | Stock kube-prometheus-stack and Loki mixin dashboards | Prometheus |
| Legacy | The old hand-made dashboards (Home, Indoor, Outdoor, Appliances, 3D printing, Loki logs) | TimescaleDB `tsdb`, Loki |

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

House panels are SQL against TimescaleDB (datasource `tsdb`, see [Querying](#querying)), built with the helpers in
`lib.py`:

- `tsdb(query)` makes the target. With the default `time_series` format every text column becomes a label of the
  numeric columns, like a Flux group key: name the value column `"Value"` and the label columns `place`, `device`,
  `series` or `point`, which the panels' `${__field.labels.…}` display names use. Text values (the alarm mode) and
  rows without a time column need `fmt="table"`.
- Ranges are templates over the time column: `RANGE` (the dashboard's, `$__timeFilter`), `since("3 hours")`, `TODAY`
  and `MONTH` (Helsinki calendar). `where(rng, col)` fills one in.
- `bucket(every)` groups by Grafana's interval or a fixed width (`"10 minutes"`, `"1 day"`); days, weeks and months
  follow Helsinki's calendar and weeks start on Monday. Buckets are stamped at their start (Flux's
  `aggregateWindow` stamped the end).
- `values(mapping, key, label)` and `in_list(keys)` turn a Python dict of ids into a `VALUES` join and an `IN`
  filter (keep the `IN`: it lets compressed chunks be skipped by `device_id`); `latest(query, key)` keeps the last
  row per series; `agg(fn, col)` maps `mean`, `last`, `spread`, … to SQL.
- `home.ble()`, `power()`, `cost()`, `water()`, `electricity()`, `fingrid()` and `heatpump.nibe()` cover most panels.

`legacy/` holds the old dashboards that were made in the UI, exported once as plain JSON (not generated). They
are frozen: fix them by editing the JSON, or port a panel to the generator. Their queries are SQL on `tsdb`,
ported one to one from Flux: buckets are stamped at their end like Flux's `aggregateWindow`, and the series keep
Flux's field names and labels (`tempc`, `price {integration_id=…}`), which their overrides, transformations and
server-side expressions match on. BLE sensors are heard by more than one gateway (`integration_id` `ble3`,
`ble4`, …), so their queries merge gateways instead of filtering on one.

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

Telegraf `telegraf-home` (config in `apps/telegraf/config.yaml`) stores MQTT data in TimescaleDB table
`mqtt_consumer` ([Schema](#schema)), tagged `kind`/`integration_id`/`device_id` from the topic. It keeps only our own BLE sensors (a
`tagpass` list of device ids; the gateways also hear neighbours' devices), so add a new sensor there as well as in
the dashboards. It drops repeated zigbee2mqtt device metadata and settings. ESPHome light state lands next to the
old Tuya bulb history (`kind=lights`, `integration_id=esphome`); ESPHome chip temperature and Wi-Fi signal go to
table `esphome`, thermal lockout (`ON`/`OFF`) to `esphome_binary`.

`apps/telegraf/telegraf-cloud.yaml` fetches cloud data into TimescaleDB (one table per measurement, field
`value`). Its credentials, the NIBE device id, the FMI station and the home coordinates are in
`telegraf-cloud.sops.yaml`.

- **NIBE F750 heat pump** (measurement `nibe`, tags `id`, `name`, `unit`): every myUplink data point, once a
  minute, the same interval as the official Home Assistant integration. Query by `id` (the parameter number),
  because NIBE can rename points. The myUplink app ("myUplink homelab API" in 1Password) only requests the
  `READSYSTEM` scope, so the poller cannot change pump settings. Point `43144` is labelled "time factor" but is
  the compressor's energy counter in kWh.
- **FMI weather** (no key): `fmi_observation` holds the nearest station's 10-minute observations, tagged by
  `param` (`t2m`, `ws_10min`, `r_1h` and so on). `fmi_forecast` holds the forecast for home, rewritten hourly so
  future points are always the latest forecast. NaN values are skipped.

- **FMI daily means** (`fmi_daily`, `param=tday`): fetched every 6 hours, backfilled from 2020.
- **Fingrid open data** (measurement `fingrid`, tag `series`): CO₂ intensity of consumption and production,
  national consumption and production, nuclear, hydro and wind, net import, frequency, system state and
  shortage status, every 3 minutes. The key ("Fingrid Open Data API" in 1Password, field "primary key") allows one
  request per 2 s and 10 000 a day, so the 11 inputs are staggered 10 s apart. A manual backfill using the same
  key at the same time will cause 429 errors. `fingrid_hourly` holds hourly CO₂ intensity backfilled from 2020.

**Whole-house electricity** comes from Fingrid Datahub, which has no consumer API. Export the consumption CSV from
oma.datahub.fi and run `KUBECONFIG=kubeconfig python3 scripts/datahub-import.py consumption.csv` (table
`electricity`, column `kwh`, hourly until the meter is replaced). Exports can overlap. The script writes to `tsdb`
through [tsdb-relay](#writers); `DRY_RUN=1` only prints what it would write (this works for the backfill scripts
below too). The energy dashboard's
"expected from weather" line is a model fitted on 2021–2024 (`ELEC_*` in `home.py`).

History before the live collectors started was backfilled from 2020 with scripts that can be re-run:
`scripts/spot-price-backfill.py` (Nord Pool FI prices from Elering's public API, written in nordpool-mqtt's
format with the VAT of each day) and `scripts/fmi-backfill.py` (hourly FMI observations; live data is
10-minute). The FMI daily means and Fingrid's hourly CO₂ intensity were loaded the same way. Elering has 15-minute prices since October 2025, while
nordpool-mqtt stores hourly averages, so don't re-run the spot price backfill over recent days.

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

## TimescaleDB (tsdb)

TimescaleDB holds the house's time series. It replaced InfluxDB on 2026-10-31: the history was copied (see
[History from InfluxDB](#history-from-influxdb)), and every writer, dashboard, alert rule and homectl widget uses
`tsdb`.

- **Cluster:** CloudNativePG `tsdb` ([apps/tsdb/tsdb.yaml](/apps/tsdb/tsdb.yaml)), separate from `postgres-ha`, one
  instance on `nfs-csi` (60 Gi; the class can't grow a volume). A replica would share the NAS with the primary, so
  it would double every write without protecting the data; Telegraf buffers while the instance restarts. Connect to
  `tsdb-rw.default.svc:5432`, database `tsdb`. The calendar day is Helsinki's: the database's default `timezone` is
  `Europe/Helsinki` (storage is `timestamptz`, so this only affects how times are shown and truncated).
- **Image:** `ghcr.io/fruitiex/postgresql-timescaledb`, CloudNativePG's PostgreSQL 18 image plus TimescaleDB from
  Timescale's apt repository with the Timescale License, which compression and continuous aggregates need
  (CloudNativePG only publishes the Apache-only build). [images/postgresql-timescaledb](/images/postgresql-timescaledb)
  is built by `.github/workflows/tsdb-image.yaml`, which smoke-tests compression before pushing and then pins the
  new digest in `apps/` through the image-update dispatch. Renovate proposes base image and TimescaleDB updates for
  review. Keep the dump job's image equal to the cluster's: `pg_dump` and the extension version must match.
- **Roles** (managed role secrets in `apps/tsdb/*.sops.yaml`, passwords in the Agents vault as "homelab
  TimescaleDB …"):
  - `tsdb` owns the database and every table; Telegraf, the relay and the backfill write as `tsdb`.
  - `grafana_ro`: SELECT on all tables (including future ones, through default privileges), `pg_monitor`,
    read-only by default with a 30 s statement timeout (set in [bootstrap.sql](/apps/tsdb/bootstrap.sql)). Its
    password differs from the one on `postgres-ha`.
  - `homectl_ro`: SELECT on `mqtt_consumer` and `nordpool_price` only, otherwise like `grafana_ro`. homectl gets it
    as `TSDB_URL` in `homectl-server-env` and reads its sensor history and spot price widgets from `tsdb`. Without
    `TSDB_URL` it would fall back to its InfluxDB widget source, which no longer exists. CloudNativePG creates a managed role
    but not its settings or grants: for a new read-only role run its lines from bootstrap.sql and schema.sql by hand.
  - `postgres`, the superuser, is used only by `tsdb-dump` and for maintenance.
- **Grafana** queries it as `grafana_ro` through the provisioned datasource `tsdb` ("TimescaleDB tsdb", key
  `tsdb-datasource.yaml` in `grafana/grafana-datasources.sops.yaml`). The database's `Europe/Helsinki` timezone
  applies to its sessions; `$__timeFilter` passes UTC instants, so only truncation depends on it.
- **Backup:** the `tsdb-dump` CronJob (02:47 Helsinki, [tsdb-dump.sh](/apps/tsdb/tsdb-dump.sh)) writes the
  `tsdb-dumps` volume for the NAS's Hyper Backup to version. `latest/` is a `pg_dump -Fd` dump plus the roles,
  with each table's data in `data/<schema>.<table>.dat.zst` and `manifest.tsv` mapping pg_dump's dumpIds to those
  names. A file is only replaced when its content changes, so old compressed chunks keep their file (content and
  mtime) and only new or recompressed chunks reach the backup each night. Updates are staged as `*.new` and
  committed with renames, so a failed run leaves the previous dump intact. To restore, run
  `latest/restore-dump.sh [--roles] LATEST_DIR [DBNAME]` against a superuser connection on a server with the same
  PostgreSQL and TimescaleDB versions: it rebuilds the pg_dump directory and runs `pg_restore` between
  `timescaledb_pre_restore()` and `timescaledb_post_restore()`. The job also keeps `KEEP_DAYS` (3) dated
  `pg_dump -Fc` copies (`YYYY-MM-DD/tsdb.dump`); set it to `"0"` once Hyper Backup covers `latest/`.
  `TsdbDumpStale` fires after 36 h without a successful dump. The PodMonitor `tsdb` scrapes CloudNativePG's metrics.

### Schema

[schema.sql](/apps/tsdb/schema.sql) creates one hypertable per measurement in Telegraf's
`outputs.postgresql` layout: a `time` column, one `text` column per tag, one typed column per field (`double
precision`, `bigint`, `text`). A row is one line-protocol point with all its fields, so fields of the same message
share a row. It runs once at bootstrap and can be re-run.

| Table | Tags | Fields | Written by |
| --- | --- | --- | --- |
| `mqtt_consumer` | `kind`, `integration_id`, `device_id`, `topic`, `name`, `entity`, `mac`, `brand`, `model`, `model_id`, `type` | one column per MQTT field (`tempc`, `hum`, `batt`, `raw_19`, `"on"`, `state`, `brightness`, `rate`, …) | telegraf-home |
| `esphome`, `esphome_binary` | `component`, `device_id`, `entity`, `topic` | `value` (double; text `ON`/`OFF`) | telegraf-home |
| `nibe` | `id`, `name`, `unit` | `value` | telegraf-cloud |
| `fmi_observation`, `fmi_forecast`, `fmi_daily` | `param` | `value` | telegraf-cloud |
| `fingrid`, `fingrid_hourly` | `series` | `value` | telegraf-cloud (`fingrid_hourly`: backfill only) |
| `verisure` | `topic` | `value` (text) | telegraf-cloud |
| `electricity` | `source`, `resolution` | `kwh` | `datahub-import.py` (through tsdb-relay) |
| `nordpool_price` | `id` | `price` (c/kWh incl. VAT) | nordpool-mqtt, `spot-price-backfill.py` (through tsdb-relay) |

- Telegraf adds a column when a device reports a new field, typed by its first value. If a later value has another
  type, Telegraf drops that batch for this table, so convert such a field before it reaches the output. The
  history copied from InfluxDB keeps values whose InfluxDB type differed from the column's in `<field>_<type>`, for
  example `sensor_value_string`. `tsdb_migration.columns` records which column holds which InfluxDB tag or field.
- `on` is an SQL keyword and some Zigbee fields are mixed case: quote them (`"on"`, `"device_applicationVersion"`).
- Neighbours' BLE devices were not copied: the backfill keeps the `tagpass` list of telegraf-home.
- **Indexes:** every table has `(series tags, time DESC)` (`mqtt_consumer`: `device_id` and `integration_id`).
- **Compression:** chunks older than 7 days move to the columnstore, segmented by the series tags
  (`mqtt_consumer`: `integration_id, device_id`) and ordered by `time DESC`; `chunk_compression_stats('mqtt_consumer')`
  shows about 40× less space. Chunks are a day for `mqtt_consumer` and up to a year for the small tables. Nothing
  expires.
- **Re-sent points replace old ones.** FMI, Fingrid, Datahub and Nord Pool writers send the same point again (FMI
  returns the last hours on every fetch, the forecast is rewritten hourly, Datahub exports overlap). InfluxDB
  overwrote such points; here the `replace_point` trigger on those tables deletes the stored row with the same tags
  and time first. It is a trigger, not a unique index plus `ON CONFLICT`, because Telegraf can't send
  `ON CONFLICT` and TimescaleDB checks unique indexes on compressed chunks before triggers run. The append-only
  tables (`mqtt_consumer`, `esphome*`, `nibe`, `verisure`) have none.

### Writers

- telegraf-home and telegraf-cloud write with Telegraf's `postgresql` output. Its buffer holds 50 000 metrics
  (about two hours of MQTT data), and `startup_error_behavior = "retry"` lets Telegraf start and buffer while
  `tsdb` is down.
- **tsdb-relay** ([apps/tsdb/relay.yaml](/apps/tsdb/relay.yaml)) is a Telegraf that accepts the InfluxDB v2 write
  API on `http://tsdb-relay.default.svc:8086` (token: secret `tsdb-relay`, Agents vault "homelab TimescaleDB relay
  write token") and writes to `tsdb`. The bucket becomes a tag, and bucket `nordpool`'s measurement `price` becomes
  table `nordpool_price`. It answers once the points are buffered, so a point it can't store (a type that doesn't
  match the column) only shows up in `kubectl logs deploy/tsdb-relay`.
  - nordpool-mqtt's `[[influxdb2]]` target in its `Settings.toml` (`apps/nordpool-mqtt/config.yaml`) is the
    relay. It writes a day's prices when they are fetched (at start-up and
    when the next day's are published), so a missed write is repaired by restarting it.
  - `datahub-import.py`, `spot-price-backfill.py` and `fmi-backfill.py` post line protocol to the relay from
    inside its pod (busybox `wget` with the pod's own `RELAY_TOKEN`).

### History from InfluxDB

InfluxDB held the house data from 2019-12 until 2026-10-31. `scripts/influx-to-tsdb.py` (removed after the
migration; `git log --all -- scripts/influx-to-tsdb.py`) copied it week by week and verified every table, week and
field by point count and sum, plus random values. Its bookkeeping stays in schema `tsdb_migration`. InfluxDB had
compacted MQTT data older than 90 days to 1-minute means; those days (2024-07-29 to 2026-07-26) were then replaced
with the raw readings from a full `influx backup` taken just before the compaction, verified per day and field
(`tsdb_migration.raw_restore`, `raw_restore_verify`). `mqtt_consumer` is raw throughout. Most of its old volume is
light and device state messages that Telegraf has dropped since 2026-10-30.

InfluxDB's data directory stays on the NAS for a few months: the released volume (reclaim policy `Retain`) in
`/volume3/homelab/influxdb-influxdb2`, the cold copy `influxdb-final-backup-20261031` taken after the last write,
and `influxdb-influxdb2-backup-2.7.4-20261031` from before the upgrade to 2.9. To read one, run InfluxDB 2.9 on a
copy of the directory with the operator token from 1Password ("admin's Token"). Delete them, and the released PV,
once nothing is missing from `tsdb`.

### Querying

Grafana's PostgreSQL datasource macros work as usual (`$__timeFilter(time)`, `$__timeGroupAlias(time, $__interval)`).
Always bound `time`: chunks are only skipped by the time filter, and only for literals and `now() - interval`. A
calendar bound such as `date_trunc('day', now(), 'Europe/Helsinki')` scans every chunk (3 s instead of 5 ms on
`mqtt_consumer`), so add a redundant `time > now() - interval '1 day'`. Flux patterns and their SQL:

~~~sql
-- aggregateWindow(every: 1d, fn: sum, timeSrc: "_start") with location Europe/Helsinki
SELECT time_bucket('1 day', time, 'Europe/Helsinki') AS time, sum(kwh) AS used
FROM electricity WHERE $__timeFilter(time) GROUP BY 1 ORDER BY 1;

-- dict.get(dict: names, key: r.device_id) + group by place (names: INDOOR in home.py). Both BLE gateways
-- report each sensor, so the mean merges them.
SELECT $__timeGroupAlias(time, $__interval), n.place AS metric, avg(m.tempc) AS value
FROM mqtt_consumer m
JOIN (VALUES ('C76A05062842', 'Bedroom'), ('D83431306571', 'Upstairs bathroom')) AS n (device_id, place) USING (device_id)
WHERE $__timeFilter(m.time) AND m.tempc IS NOT NULL
GROUP BY 1, 2 ORDER BY 1;

-- join(energy, price) on the hour: plug energy (mean W over an hour = Wh) times the spot price (c/kWh), in €
WITH energy AS (
  SELECT time_bucket('1 hour', time) AS hour, name AS device, avg(raw_19) / 10.0 AS wh
  FROM mqtt_consumer WHERE integration_id = 'tuya' AND raw_19 IS NOT NULL AND $__timeFilter(time) GROUP BY 1, 2),
price AS (
  SELECT time_bucket('1 hour', time) AS hour, avg(price) AS c_kwh
  FROM nordpool_price WHERE $__timeFilter(time) GROUP BY 1)
SELECT hour AS time, device AS metric, wh / 1000 * c_kwh / 100 AS eur
FROM energy JOIN price USING (hour) ORDER BY 1;

-- last(): latest value per series, with a bounded lookback
SELECT DISTINCT ON (device_id) device_id, time, batt
FROM mqtt_consumer
WHERE batt IS NOT NULL AND integration_id IN ('ble3', 'ble4') AND time > now() - interval '1 day'
ORDER BY device_id, time DESC;
~~~

## Alerts

- Discord channels: `#flux-notifications` gets only Flux's own notifications (its notification provider);
  Alertmanager's `homelab="true"`, `critical` and chosen stock alerts go to `#homelab`
  (`alertmanager-discord.sops.yaml`), as do Grafana rules outside the Home folder. Home-folder rules go to `#home`.
  The Flux webhook stays in `notification-provider/discord-webhook-url.yaml`; the other two are "Discord webhook
  home" and "Discord webhook homelab" in the Agents vault.
- Backups (`homelab.backups` in `targets/rules.yaml`): `PostgresDumpStale` fires when `postgres-dump` has not
  succeeded for 36 h, `CronJobLastRunFailed` when any CronJob's latest run failed. The stock `KubeJobFailed` is a
  plain warning and never reaches Discord. Immich's own database backups are not monitored.
- Grafana-managed rules for the house data are in `grafana/grafana-alerting.sops.yaml` and query `tsdb`. They cover the freezer, the fridge, water flowing non-stop for 3 hours (normal use always pauses within
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
    30 c/kWh or at or below 0 c/kWh (VAT included); about 43 days a year in the past 12 months. The query builds
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
- **Datasource UIDs must not equal another datasource's name.** Grafana resolves a reference by name as well,
  so a provisioned uid `influxdb` once resolved to an old UI-made datasource *named* `influxdb`.
- **Each datasource ConfigMap needs a unique key.** The sidecar writes every key into one directory, so two
  `datasource.yaml` keys overwrite each other.
- **kube-prometheus-stack CRDs:** Flux skips CRDs on upgrade by default, which left them at their 2023
  versions, so newer `Prometheus` fields were pruned silently. The release now sets `crds: CreateReplace`.
- **`scrapeConfigSelector` must be non-empty.** The chart renders nothing unless it is set, and Helm's merge
  patch drops an empty `{}`. The always-true `matchExpressions` in the values exists for this reason.
- **Prometheus compaction:** an orphaned, empty block directory with no `meta.json` stopped compaction for four
  months. The `PrometheusTSDBCompactionsFailing` alert now goes to Discord, and "Prometheus TSDB blocks" on the
  Workloads dashboard should stay in the tens.
