"""Homelab dashboards: services, workloads, hardware, network, logs, apps and the overview."""

import home
from lib import (BAD, GOOD, LOKI, MUTED, SLOTS, WARN, Dashboard, barchart, bargauge, by_name, deploy_annotation,
                 fixed_colors, labels_to_fields, logs, loki, merge, organize, prom, reduce_rows, sort_by, sql, stat,
                 state_timeline, status_history, table, thresholds, timeseries, value_map)

UP_DOWN = value_map([(1, "Up", GOOD), (0, "Down", BAD)])
APPS = 'label_replace({metric}{{job=~"probe/kube-prometheus-stack/apps-.*"}}, "app", "$1", "instance", "https://([^/]+).*")'

# Envoy JSON access logs (both gateways).
ENVOY = '{namespace="envoy-gateway-system", container="envoy"} |= "authority" | json host=`[":authority"]`'
OURS = '| host=~`(.+\\.)?(fruitiex\\.org|lovegren\\.fi|xn--lvegren-90a\\.fi)`'
FOREIGN = '| host!~`(.+\\.)?(fruitiex\\.org|lovegren\\.fi|xn--lvegren-90a\\.fi)`'
ERRORS = '|~ `(?i)\\b(error|panic|fatal|exception)\\b`'


def services_dashboard():
    d = Dashboard("lab-services", "Homelab · Services", folder="Homelab", tags=["homelab"], time="now-24h",
                  annotations=[deploy_annotation()],
                  description="Is everything answering, how fast, how much traffic, and is GitOps healthy.")
    d.row("Availability")
    d.add(stat("Apps down", [prom(f'count({APPS.format(metric="probe_success")} == 0) or vector(0)', instant=True)],
               graph=False, thr=thresholds(GOOD, (1, BAD)), color_mode="background",
               desc="Blackbox HTTPS probes of every app hostname, once a minute."), 4, 4)
    d.add(stat("Firing alerts", [prom('count(ALERTS{alertstate="firing", alertname!~"Watchdog|InfoInhibitor"}) or vector(0)',
                                      instant=True)], graph=False, thr=thresholds(GOOD, (1, WARN)),
               color_mode="background"), 4, 4)
    d.add(stat("Flux not ready", [prom('count(gotk_resource_info{ready="False"}) or vector(0)', instant=True)],
               graph=False, thr=thresholds(GOOD, (1, BAD)), color_mode="background"), 4, 4)
    d.add(stat("Earliest TLS expiry", [prom('min(probe_ssl_earliest_cert_expiry - time()) / 86400', instant=True)],
               unit="d", decimals=0, graph=False, thr=thresholds(BAD, (7, WARN), (21, GOOD))), 4, 4)
    d.add(stat("LAN hosts down", [prom('count(probe_success{job="lan-hosts"} == 0) or vector(0)', instant=True)],
               graph=False, thr=thresholds(GOOD, (1, BAD)), color_mode="background",
               desc="TCP checks of the NAS, Proxmox hosts, gateway, honeydew and Zigbee2MQTT."), 4, 4)
    d.add(stat("Core services down", [prom('count(probe_success{job="probe/kube-prometheus-stack/cluster-services"} == 0) or vector(0)',
                                           instant=True)], graph=False, thr=thresholds(GOOD, (1, BAD)),
               color_mode="background", desc="MQTT, both PostgreSQL instances, InfluxDB and Loki (TCP)."), 4, 4)
    d.add(status_history("App status", [prom(f'max by (app) ({APPS.format(metric="probe_success")})', legend="{{app}}",
                                             interval="5m")],
                         mappings=UP_DOWN, color={"mode": "thresholds"}, thr=thresholds(BAD, (1, GOOD)),
                         row_height=0.8), 16, 16)
    d.add(bargauge("Response time (avg over range)", [prom(
        'sort_desc(avg by (app) (label_replace(avg_over_time(probe_duration_seconds{job=~"probe/kube-prometheus-stack/apps-.*"}[$__range]), '
        '"app", "$1", "instance", "https://([^/]+).*")))',
        legend="{{app}}", instant=True)], unit="s", decimals=2, display_name="${__field.labels.app}",
        thr=thresholds(GOOD, (0.5, WARN), (2, BAD)), mode="basic"), 8, 16)
    d.add(state_timeline("LAN hosts & core services", [
        prom('max by (instance, service) (probe_success{job="lan-hosts"})', legend="{{instance}} ({{service}})"),
        prom('max by (instance) (probe_success{job="probe/kube-prometheus-stack/cluster-services"})',
             legend="{{instance}}")], mappings=UP_DOWN, color={"mode": "thresholds"}, thr=thresholds(BAD, (1, GOOD))),
          16, 9)
    d.add(bargauge("Days until certificate expiry", [prom(
        f'sort(min by (app) ({APPS.format(metric="probe_ssl_earliest_cert_expiry")}) - time()) / 86400',
        legend="{{app}}", instant=True)], unit="d", decimals=0, display_name="${__field.labels.app}",
        thr=thresholds(BAD, (7, WARN), (21, GOOD)), minv=0, maxv=90,
        desc="cert-manager renews 30 days before expiry, so anything under ~25 days means renewal is failing."),
          8, 9)

    d.row("Traffic (Envoy access logs)")
    d.add(timeseries("Requests per app", [loki(f'sum by (host) (count_over_time({ENVOY} {OURS} [$__auto]))',
                                               legend="{{host}}")],
                     unit="short", stack=True, fill=30, gradient="none", legend="right",
                     legend_calcs=["sum"], desc="Requests through the public and private gateways per interval. "
                                                 "LAN clients that talk to MetalLB addresses directly (Jellyfin) are not counted."),
          16, 9)
    d.add(bargauge("Requests in range", [loki(f'sum by (host) (count_over_time({ENVOY} {OURS} [$__auto]))',
                                              legend="{{host}}")],
                   unit="short", decimals=0, values=True, color={"mode": "fixed", "fixedColor": SLOTS[0]},
                   transformations=[reduce_rows(["sum"]), sort_by("Total", desc=True)]), 8, 9)
    d.add(timeseries("Server errors (5xx) per app", [loki(
        f'sum by (host) (count_over_time({ENVOY}, response_code {OURS} | response_code >= 500 [$__auto]))',
        legend="{{host}}")], unit="short", bars=True, stack=True, legend="right"), 12, 8)
    d.add(timeseries("p95 latency per app", [loki(
        f'quantile_over_time(0.95, {ENVOY}, duration {OURS} | duration < 60000 | unwrap duration [$__auto]) by (host)',
        legend="{{host}}")], unit="ms", fill=0, legend="right",
        desc="Request time at the gateway, ignoring requests over 60 s (SSE, WebSockets and video streams)."), 12, 8)
    d.add(timeseries("Data served per app", [loki(
        f'sum by (host) (sum_over_time({ENVOY}, bytes_sent {OURS} | unwrap bytes_sent [$__auto]))',
        legend="{{host}}")], unit="bytes", bars=True, stack=True, legend="right"), 24, 8)

    d.row("GitOps (Flux)")
    d.add(table("Flux resources", [prom('gotk_resource_info{customresource_kind=~"Kustomization|HelmRelease"}',
                                        instant=True, fmt="table")],
                transformations=[organize(
                    exclude=["Time", "Value", "__name__", "container", "endpoint", "instance", "job", "namespace",
                             "pod", "service", "customresource_group", "customresource_version", "uid"],
                    rename={"customresource_kind": "Kind", "exported_namespace": "Namespace", "name": "Name",
                            "ready": "Ready", "revision": "Revision", "suspended": "Suspended",
                            "source_name": "Source", "chart_app_version": "App version"},
                    order=["customresource_kind", "exported_namespace", "name", "ready", "revision",
                           "chart_app_version", "source_name", "suspended"])],
                overrides=[by_name("Ready", mappings=value_map([("True", "Ready", GOOD), ("False", "Not ready", BAD)]),
                                   custom__cellOptions={"type": "color-text"})],
                sort=("Ready", False)), 14, 12)
    d.add(timeseries("Reconcile time p95 by kind", [prom(
        'histogram_quantile(0.95, sum by (le, kind) (rate(gotk_reconcile_duration_seconds_bucket[$__rate_interval])))',
        legend="{{kind}}")], unit="s", fill=0, legend="bottom"), 10, 6)
    d.add(timeseries("Reconcile errors", [prom(
        'sum by (controller) (increase(controller_runtime_reconcile_errors_total{job="flux-system/flux-system"}[$__rate_interval]))',
        legend="{{controller}}")], unit="short", bars=True, stack=True), 10, 6)
    d.add(table("Firing alerts", [prom('ALERTS{alertstate="firing", alertname!~"Watchdog|InfoInhibitor"}',
                                       instant=True, fmt="table")],
                transformations=[organize(
                    exclude=["Time", "Value", "__name__", "alertstate", "container", "endpoint", "prometheus",
                             "service", "job", "uid", "instance", "pod", "gateway", "homelab", "exported_namespace"],
                    rename={"alertname": "Alert", "severity": "Severity", "namespace": "Namespace"},
                    order=["alertname", "severity", "namespace"])],
                overrides=[by_name("Severity", custom__cellOptions={"type": "color-text"},
                                   mappings=value_map([("critical", "critical", BAD), ("warning", "warning", WARN),
                                                       ("info", "info", MUTED)]))]), 24, 7)
    return d


def workloads_dashboard():
    d = Dashboard("lab-workloads", "Homelab · Workloads", folder="Homelab", tags=["homelab"], time="now-24h",
                  annotations=[deploy_annotation(".*")],
                  description="What the apps use, what keeps restarting, and the health of the data stores.")
    d.row("Cluster")
    d.add(stat("Running pods", [prom('sum(kube_pod_status_phase{phase="Running"})', instant=True)], graph=False), 4, 4)
    d.add(stat("Restarts (24 h)", [prom('sum(increase(kube_pod_container_status_restarts_total[24h]))', instant=True)],
               decimals=0, graph=False, thr=thresholds(GOOD, (3, WARN), (10, BAD))), 4, 4)
    d.add(stat("OOM kills (24 h)", [prom(
        'count(increase(kube_pod_container_status_restarts_total[24h]) > 0 and on (namespace, pod, container) '
        'kube_pod_container_status_last_terminated_reason{reason="OOMKilled"} == 1) or vector(0)', instant=True)],
        graph=False, thr=thresholds(GOOD, (1, BAD))), 4, 4)
    d.add(stat("CPU used", [prom('1 - avg(rate(node_cpu_seconds_total{job="node-exporter", mode="idle"}[5m]))')],
               unit="percentunit", decimals=0, thr=thresholds(GOOD, (0.7, WARN), (0.9, BAD))), 4, 4)
    d.add(stat("Memory used", [prom('1 - sum(node_memory_MemAvailable_bytes{job="node-exporter"}) / '
                                    'sum(node_memory_MemTotal_bytes{job="node-exporter"})')],
               unit="percentunit", decimals=0, thr=thresholds(GOOD, (0.75, WARN), (0.9, BAD))), 4, 4)
    d.add(stat("Memory requested", [prom('sum(kube_pod_container_resource_requests{resource="memory"}) / '
                                         'sum(kube_node_status_allocatable{resource="memory"})', instant=True)],
               unit="percentunit", decimals=0, graph=False, thr=thresholds(GOOD, (0.8, WARN), (0.95, BAD)),
               desc="Share of allocatable memory reserved by requests; near 100 % new pods stop scheduling."), 4, 4)

    d.row("Apps (namespace default)")
    d.add(timeseries("Memory by container", [prom(
        'topk(12, sum by (container) (container_memory_working_set_bytes{namespace="default", container!="", container!="POD"}))',
        legend="{{container}}")], unit="bytes", fill=0, legend="right", legend_calcs=["lastNotNull", "max"]), 12, 9)
    d.add(timeseries("CPU by container", [prom(
        'topk(12, sum by (container) (rate(container_cpu_usage_seconds_total{namespace="default", container!="", container!="POD"}[$__rate_interval])))',
        legend="{{container}}")], unit="suffix: cores", decimals=2, fill=0, legend="right",
        legend_calcs=["mean", "max"]), 12, 9)
    sel = '{container!="", container!="POD"}'
    d.add(table("Containers: usage vs requests and limits", [
        prom(f'sum by (namespace, container) (container_memory_working_set_bytes{sel})', instant=True, fmt="table"),
        prom('sum by (namespace, container) (kube_pod_container_resource_requests{resource="memory"})', instant=True, fmt="table"),
        prom('sum by (namespace, container) (kube_pod_container_resource_limits{resource="memory"})', instant=True, fmt="table"),
        prom(f'sum by (namespace, container) (rate(container_cpu_usage_seconds_total{sel}[5m]))', instant=True, fmt="table"),
        prom('sum by (namespace, container) (kube_pod_container_resource_requests{resource="cpu"})', instant=True, fmt="table"),
        prom('sum by (namespace, container) (increase(kube_pod_container_status_restarts_total[24h]))', instant=True, fmt="table"),
    ], transformations=[merge(), organize(exclude=["Time"], rename={
        "namespace": "Namespace", "container": "Container", "Value #A": "Memory", "Value #B": "Mem request",
        "Value #C": "Mem limit", "Value #D": "CPU", "Value #E": "CPU request", "Value #F": "Restarts 24 h"})],
        overrides=[*(by_name(n, unit="bytes", decimals=0) for n in ("Memory", "Mem request", "Mem limit")),
                   *(by_name(n, unit="suffix: cores", decimals=3) for n in ("CPU", "CPU request")),
                   by_name("Restarts 24 h", decimals=0, thresholds=thresholds(GOOD, (1, WARN), (5, BAD)),
                           custom__cellOptions={"type": "color-text"})],
        sort=("Memory", True)), 16, 12)
    d.add(table("Last rollout", [prom(
        'max by (namespace, owner_name) (kube_replicaset_created * on (namespace, replicaset) group_left (owner_name) '
        'kube_replicaset_owner{owner_kind="Deployment"}) * 1000', instant=True, fmt="table")],
        transformations=[organize(exclude=["Time"], rename={"namespace": "Namespace", "owner_name": "Deployment",
                                                            "Value": "Rolled out"})],
        overrides=[by_name("Rolled out", unit="dateTimeFromNow")], sort=("Rolled out", True)), 8, 12)
    d.add(table("Last termination reasons", [prom(
        'kube_pod_container_status_last_terminated_reason == 1', instant=True, fmt="table")],
        transformations=[organize(exclude=["Time", "Value", "__name__", "endpoint", "instance", "job", "service", "uid"],
                                  rename={"namespace": "Namespace", "pod": "Pod", "container": "Container",
                                          "reason": "Reason"}, order=["namespace", "pod", "container", "reason"])],
        overrides=[by_name("Reason", custom__cellOptions={"type": "color-text"},
                           mappings=value_map([("OOMKilled", "OOMKilled", BAD), ("Error", "Error", WARN),
                                               ("Completed", "Completed", MUTED)]))]), 12, 8)
    d.add(table("Restarts in the last 24 h", [prom(
        'sum by (namespace, container) (round(increase(kube_pod_container_status_restarts_total[24h]))) > 0',
        instant=True, fmt="table")],
        transformations=[organize(exclude=["Time"], rename={"namespace": "Namespace", "container": "Container",
                                                            "Value": "Restarts"})],
        overrides=[by_name("Restarts", decimals=0, thresholds=thresholds(WARN, (5, BAD)),
                           custom__cellOptions={"type": "color-text"})],
        sort=("Restarts", True), nulls="No restarts"), 12, 8)

    d.row("Nodes")
    node = '* on (instance) group_left (nodename) node_uname_info{job="node-exporter"}'
    d.add(timeseries("CPU by node", [prom(
        f'(1 - avg by (instance) (rate(node_cpu_seconds_total{{job="node-exporter", mode="idle"}}[$__rate_interval]))) {node}',
        legend="{{nodename}}")], unit="percentunit", minv=0, fill=0, legend="right"), 12, 8)
    d.add(timeseries("Memory by node", [prom(
        f'(1 - node_memory_MemAvailable_bytes{{job="node-exporter"}} / node_memory_MemTotal_bytes{{job="node-exporter"}}) {node}',
        legend="{{nodename}}")], unit="percentunit", minv=0, maxv=1, fill=0, legend="right"), 12, 8)

    d.row("Data stores")
    for db, label in (("ferrotune", "shared PostgreSQL"), ("immich", "Immich PostgreSQL")):
        d.add(bargauge(f"Database size ({label})", [sql(db, '''
SELECT datname AS metric, pg_database_size(datname) AS value
FROM pg_database WHERE NOT datistemplate AND datname <> 'postgres' ORDER BY 2 DESC''')],
            unit="bytes", decimals=1, values=True, color={"mode": "fixed", "fixedColor": SLOTS[0]},
            display_name="${__data.fields.metric}"), 5, 6)
        d.add(table(f"Activity ({label})", [sql(db, '''
SELECT datname AS "Database", numbackends AS "Connections",
       round(100.0 * blks_hit / nullif(blks_hit + blks_read, 0), 2) AS "Cache hit %",
       xact_commit AS "Commits", xact_rollback AS "Rollbacks", deadlocks AS "Deadlocks",
       temp_bytes AS "Temp bytes", stats_reset AS "Since"
FROM pg_stat_database WHERE datname IS NOT NULL AND datname NOT IN ('template0', 'template1') ORDER BY 2 DESC''')],
            overrides=[by_name("Temp bytes", unit="bytes"), by_name("Since", unit="dateTimeFromNow"),
                       by_name("Cache hit %", thresholds=thresholds(BAD, (90, WARN), (99, GOOD)),
                               custom__cellOptions={"type": "color-text"})]), 7, 6)
    d.add(stat("Prometheus TSDB blocks", [prom('prometheus_tsdb_blocks_loaded')], graph=True,
               thr=thresholds(GOOD, (200, WARN), (500, BAD)),
               desc="Healthy compaction keeps this in the tens. Hundreds means compaction is failing."), 6, 5)
    d.add(stat("Prometheus storage", [prom('prometheus_tsdb_storage_blocks_bytes + prometheus_tsdb_wal_storage_size_bytes')],
               unit="bytes", decimals=1), 6, 5)
    d.add(stat("Series in memory", [prom('prometheus_tsdb_head_series')], unit="short", decimals=0), 6, 5)
    d.add(stat("Loki ingest", [prom('sum(rate(loki_distributor_bytes_received_total[5m]))')], unit="Bps", decimals=1), 6, 5)
    return d


def hardware_dashboard():
    d = Dashboard("lab-hardware", "Homelab · Hardware", folder="Homelab", tags=["homelab"], time="now-24h",
                  description="Proxmox hosts and their Talos VMs, and the Synology NAS.")
    temp = ('max by (host) (node_hwmon_temp_celsius{{job="proxmox-node"}} * on (instance, chip) group_left (chip_name) '
            'node_hwmon_chip_names{{job="proxmox-node", chip_name=~"{chip}"}})')
    hosts = fixed_colors({"mango": SLOTS[0], "mandarin": SLOTS[1], "mulberry": SLOTS[2]})
    d.row("Proxmox hosts")
    d.add(stat("CPU temperature", [prom(temp.format(chip="k10temp|coretemp"), legend="{{host}}")], unit="celsius",
               decimals=0, text_mode="value_and_name", thr=thresholds(GOOD, (75, WARN), (90, BAD))), 8, 5)
    d.add(stat("CPU used", [prom('1 - avg by (host) (rate(node_cpu_seconds_total{job="proxmox-node", mode="idle"}[5m]))',
                                 legend="{{host}}")], unit="percentunit", decimals=0, text_mode="value_and_name",
               thr=thresholds(GOOD, (0.7, WARN), (0.9, BAD))), 8, 5)
    d.add(stat("Memory used", [prom('1 - node_memory_MemAvailable_bytes{job="proxmox-node"} / '
                                    'node_memory_MemTotal_bytes{job="proxmox-node"}', legend="{{host}}")],
               unit="percentunit", decimals=0, text_mode="value_and_name",
               thr=thresholds(GOOD, (0.8, WARN), (0.92, BAD))), 8, 5)
    d.add(timeseries("CPU temperature", [prom(temp.format(chip="k10temp|coretemp"), legend="{{host}}")],
                     unit="celsius", decimals=0, fill=0, overrides=hosts), 8, 8)
    d.add(timeseries("NVMe temperature", [prom(temp.format(chip="nvme"), legend="{{host}}")], unit="celsius",
                     decimals=0, fill=0, overrides=hosts), 8, 8)
    d.add(timeseries("Host CPU", [prom('1 - avg by (host) (rate(node_cpu_seconds_total{job="proxmox-node", mode="idle"}[$__rate_interval]))',
                                       legend="{{host}}")], unit="percentunit", minv=0, fill=0, overrides=hosts), 8, 8)
    vm = '* on (id, host) group_left (name) pve_guest_info{type="qemu"}'
    d.add(timeseries("VM CPU", [prom(f'pve_cpu_usage_ratio {vm}', legend="{{name}}")], unit="percentunit", minv=0,
                     fill=0, legend="right",
                     desc="Share of each VM's vCPUs in use, from the Proxmox API (read-only PVEAuditor token)."), 12, 8)
    d.add(timeseries("VM memory", [prom(f'pve_memory_usage_bytes {vm}', legend="{{name}}")], unit="bytes", fill=0,
                     legend="right"), 12, 8)
    d.add(timeseries("Host disk I/O", [
        prom('sum by (host) (rate(node_disk_read_bytes_total{job="proxmox-node"}[$__rate_interval]))', legend="{{host}} read"),
        prom('sum by (host) (rate(node_disk_written_bytes_total{job="proxmox-node"}[$__rate_interval]))', legend="{{host}} write")],
        unit="Bps", fill=0, legend="right"), 12, 8)
    d.add(timeseries("Host network", [
        prom('sum by (host) (rate(node_network_receive_bytes_total{job="proxmox-node", device=~"vmbr.*"}[$__rate_interval])) * 8',
             legend="{{host}} in"),
        prom('sum by (host) (rate(node_network_transmit_bytes_total{job="proxmox-node", device=~"vmbr.*"}[$__rate_interval])) * 8',
             legend="{{host}} out")], unit="bps", fill=0, legend="right"), 12, 8)

    d.row("NAS (nectarine)")
    nas = '{job="nas-snmp"}'
    d.add(stat("System", [prom(f'systemStatus{nas}')], graph=False, color_mode="background",
               mappings=value_map([(1, "Normal", GOOD), (2, "Failed", BAD)]), thr=thresholds(BAD, (1, GOOD), (2, BAD))), 4, 4)
    d.add(stat("RAID", [prom(f'max(raidStatus{nas})')], graph=False, color_mode="background",
               mappings=value_map([(1, "Normal", GOOD), (2, "Repairing", WARN), (7, "Syncing", WARN),
                                   (8, "Parity check", WARN), (11, "Degraded", BAD), (12, "Crashed", BAD),
                                   (21, "Scrubbing", WARN)]),
               thr=thresholds(GOOD, (2, WARN), (11, BAD))), 4, 4)
    d.add(stat("Disks healthy", [prom(f'count(diskHealthStatus{nas} == 1)'), ], graph=False,
               text_mode="value", thr=thresholds(SLOTS[0])), 4, 4)
    d.add(stat("System temperature", [prom(f'temperature{nas}')], unit="celsius", decimals=0,
               thr=thresholds(GOOD, (50, WARN), (60, BAD))), 4, 4)
    d.add(stat("Volume 3 full in", [prom(
        'max(kubelet_volume_stats_available_bytes{persistentvolumeclaim="postgres-data"}) / '
        'clamp_min(-deriv(max(kubelet_volume_stats_available_bytes{persistentvolumeclaim="postgres-data"})[14d:1h]), 1) / 86400',
        instant=True)], unit="d", decimals=0, graph=False, thr=thresholds(BAD, (30, WARN), (180, GOOD)),
        desc="Free space divided by the last 14 days' growth rate. Very large means the volume is not growing."), 4, 4)
    d.add(stat("DSM update", [prom(f'upgradeAvailable{nas}')], graph=False,
               mappings=value_map([(1, "Available", WARN), (2, "Up to date", GOOD), (3, "Checking", MUTED),
                                   (4, "Offline", MUTED), (5, "Unknown", MUTED)]), thr=thresholds(SLOTS[0])), 4, 4)
    d.add(table("Disks", [
        prom(f'diskModel{nas}', instant=True, fmt="table"),
        prom(f'diskTemperature{nas}', instant=True, fmt="table"),
        prom(f'diskHealthStatus{nas}', instant=True, fmt="table"),
        prom(f'diskStatus{nas}', instant=True, fmt="table"),
        prom(f'diskRemainLife{nas}', instant=True, fmt="table"),
        prom(f'diskBadSector{nas}', instant=True, fmt="table"),
    ], transformations=[
        {"id": "joinByField", "options": {"byField": "diskID", "mode": "outer"}},
        {"id": "organize", "options": {"excludeByName": {}, "renameByName": {}, "indexByName": {}}},
        {"id": "filterFieldsByName", "options": {"include": {"pattern": "^(diskID|diskModel.*|Value #[B-F])$"}}},
        organize(rename={"diskID": "Disk", "diskModel 1": "Model", "diskModel": "Model", "Value #B": "Temperature",
                         "Value #C": "Health", "Value #D": "Status", "Value #E": "Life left", "Value #F": "Bad sectors"})],
        overrides=[by_name("Temperature", unit="celsius", thresholds=thresholds(GOOD, (45, WARN), (55, BAD)),
                           custom__cellOptions={"type": "color-text"}),
                   by_name("Health", custom__cellOptions={"type": "color-text"},
                           mappings=value_map([(1, "Normal", GOOD), (2, "Warning", WARN), (3, "Critical", BAD), (4, "Failing", BAD)])),
                   by_name("Status", custom__cellOptions={"type": "color-text"},
                           mappings=value_map([(1, "Normal", GOOD), (2, "Initialized", MUTED), (3, "Not initialized", WARN),
                                               (4, "Partition failed", BAD), (5, "Crashed", BAD)])),
                   by_name("Life left", unit="percent", mappings=value_map([(-1, "n/a (HDD)", MUTED)]))],
        sort=("Disk", False)), 12, 7)
    d.add(timeseries("Disk temperature", [prom(f'diskTemperature{nas}', legend="{{diskID}}")], unit="celsius",
                     decimals=0, fill=0, legend="right"), 12, 7)
    d.add(bargauge("Volumes", [prom('1 - raidFreeSize{job="nas-snmp", raidName=~"Volume.*"} / raidTotalSize{job="nas-snmp", raidName=~"Volume.*"}', legend="{{raidName}}",
                                          instant=True)], unit="percentunit", decimals=1, minv=0, maxv=1,
                   display_name="${__field.labels.raidName}", thr=thresholds(GOOD, (0.8, WARN), (0.9, BAD))), 8, 6)
    d.add(timeseries("Volume 3 used", [prom('max(kubelet_volume_stats_used_bytes{persistentvolumeclaim="postgres-data"})',
                                            legend="Used"),
                                       prom('max(kubelet_volume_stats_capacity_bytes{persistentvolumeclaim="postgres-data"})',
                                            legend="Capacity")],
                     unit="bytes", decimals=1, fill=0, time_from="90d",
                     overrides=fixed_colors({"Used": SLOTS[0], "Capacity": MUTED}),
                     desc="The homelab NFS share (all nfs-csi volumes share it)."), 8, 6)
    d.add(timeseries("NAS network", [
        prom('sum(rate(ifHCInOctets{job="nas-snmp", ifName=~"eth.*|bond.*|ovs_.*"}[10m])) * 8', legend="In"),
        prom('sum(rate(ifHCOutOctets{job="nas-snmp", ifName=~"eth.*|bond.*|ovs_.*"}[10m])) * 8', legend="Out")],
        unit="bps", fill=0, overrides=fixed_colors({"In": SLOTS[0], "Out": SLOTS[1]})), 8, 6)
    return d


def network_dashboard():
    d = Dashboard("lab-network", "Homelab · Network", folder="Homelab", tags=["homelab"], time="now-24h",
                  description="UniFi gateway, switches, access points and clients (UniFi Poller).")
    wan_rx = 'sum(rate(unpoller_device_wan_receive_bytes_total[$__rate_interval])) * 8'
    wan_tx = 'sum(rate(unpoller_device_wan_transmit_bytes_total[$__rate_interval])) * 8'
    d.row("Internet")
    d.add(stat("Download now", [prom(wan_rx.replace("$__rate_interval", "2m"))], unit="bps", decimals=1), 4, 4)
    d.add(stat("Upload now", [prom(wan_tx.replace("$__rate_interval", "2m"))], unit="bps", decimals=1), 4, 4)
    d.add(stat("Speed test ↓", [prom('max(unpoller_device_speedtest_download)')], unit="Mbits", decimals=0,
               graph=False), 4, 4)
    d.add(stat("Speed test ↑", [prom('max(unpoller_device_speedtest_upload)')], unit="Mbits", decimals=0,
               graph=False), 4, 4)
    d.add(stat("WAN latency", [prom('max(unpoller_device_wan_latency_seconds)')], unit="s", decimals=3,
               thr=thresholds(GOOD, (0.03, WARN), (0.1, BAD))), 4, 4)
    d.add(stat("Clients", [prom('sum(unpoller_site_users)')], decimals=0,
               desc="Wired, Wi-Fi and VPN clients the gateway sees."), 4, 4)
    d.add(timeseries("WAN throughput", [prom(wan_rx, legend="Download"), prom(wan_tx, legend="Upload")],
                     unit="bps", fill=15, overrides=fixed_colors({"Download": SLOTS[0], "Upload": SLOTS[1]})), 16, 8)
    d.add(timeseries("Clients by network", [prom('sum by (subsystem) (unpoller_site_users)', legend="{{subsystem}}")],
                     stack=True, fill=40, gradient="none", decimals=0,
                     overrides=fixed_colors({"lan": SLOTS[0], "wlan": SLOTS[1], "vpn": SLOTS[2]})), 8, 8)
    d.add(timeseries("Ping to public resolvers", [prom('probe_icmp_duration_seconds{phase="rtt"}',
                                                       legend="{{instance}}")],
                     unit="s", decimals=1, fill=0,
                     overrides=fixed_colors({"1.1.1.1": SLOTS[0], "8.8.8.8": SLOTS[1], "9.9.9.9": SLOTS[2]}),
                     desc="Round trip from the cluster, every 30 s. Gaps are lost pings."), 12, 8)
    d.add(status_history("Reachable from the cluster", [prom('min by (instance) (probe_success{group="internet"})',
                                                              legend="{{instance}}", interval="5m")],
                         mappings=value_map([(1, "Up", GOOD), (0, "Down", BAD)]), color={"mode": "thresholds"},
                         thr=thresholds(BAD, (0.5, WARN), (1, GOOD)), max_dp=60,
                         desc="Public services the homelab depends on, 5-minute buckets; one failed probe "
                              "colours the bucket. GitHub SSH on port 22 is checked next to its port 443 "
                              "fallback (ssh.github.com:443)."), 12, 8)
    d.add(timeseries("Personal devices at home", [prom(
        'count(unpoller_client_uptime_seconds{oui="", wired="false", network="Default"}) or vector(0)',
        legend="Devices")], decimals=0, fill=20, display_name="Devices", interval="5m",
        color={"mode": "fixed", "fixedColor": SLOTS[4]},
        desc="Wi-Fi clients on the main network with private (randomised) MAC addresses: phones, tablets "
             "and laptops. A rough stand-in for how many people are home."), 24, 6)
    d.add(bargauge("Success rate in range", [prom('avg_over_time(probe_success{group="internet"}[$__range])',
                                                  legend="{{instance}}", instant=True)],
                   unit="percentunit", decimals=1, minv=0, maxv=1, display_name="${__field.labels.instance}",
                   thr=thresholds(BAD, (0.95, WARN), (0.999, GOOD))), 24, 7)

    d.row("Clients")
    traffic = ('sum by (name) (increase(unpoller_client_receive_bytes_total[$__range]) + '
               'increase(unpoller_client_transmit_bytes_total[$__range]))')
    d.add(bargauge("Top talkers in range", [prom(f'topk(15, {traffic})', legend="{{name}}", instant=True)],
                   unit="bytes", decimals=1, display_name="${__field.labels.name}",
                   color={"mode": "fixed", "fixedColor": SLOTS[0]}), 8, 12)
    d.add(timeseries("Client throughput (top 8)", [prom(
        'topk(8, sum by (name) (rate(unpoller_client_receive_bytes_total[$__rate_interval]) + '
        'rate(unpoller_client_transmit_bytes_total[$__rate_interval]))) * 8', legend="{{name}}")],
        unit="bps", fill=0, legend="right"), 16, 12)
    d.add(bargauge("Weakest Wi-Fi signals", [prom(
        'bottomk(12, max by (name, ap_name) (unpoller_client_radio_signal_db{wired="false"}))',
        legend="{{name}} ({{ap_name}})", instant=True)], unit="dBm", decimals=0, minv=-95, maxv=-30,
        display_name="${__field.labels.name} · ${__field.labels.ap_name}",
        thr=thresholds(BAD, (-75, WARN), (-67, GOOD))), 8, 10)
    d.add(timeseries("Wi-Fi clients per access point", [prom(
        'count by (ap_name) (unpoller_client_uptime_seconds{wired="false", ap_name!=""})', legend="{{ap_name}}")],
        decimals=0, fill=0, legend="right"), 8, 10)
    d.add(bargauge("Wi-Fi clients by standard", [prom(
        'sort_desc(count by (radio_proto) (unpoller_client_uptime_seconds{wired="false", radio_proto!=""}))', legend="{{radio_proto}}",
        instant=True)], decimals=0, display_name="${__field.labels.radio_proto}",
        color={"mode": "fixed", "fixedColor": SLOTS[0]},
        mappings=[], desc="ng/g = 2.4 GHz legacy, ac = Wi-Fi 5, ax = Wi-Fi 6, be = Wi-Fi 7."), 8, 10)

    d.row("UniFi devices")
    dev = '{job="unpoller"}'
    d.add(table("Devices", [
        prom(f'max by (name, model, version) (unpoller_device_info{dev})', instant=True, fmt="table"),
        prom(f'max by (name) (unpoller_device_cpu_utilization_ratio{dev})', instant=True, fmt="table"),
        prom(f'max by (name) (unpoller_device_memory_utilization_ratio{dev})', instant=True, fmt="table"),
        prom(f'max by (name) (unpoller_device_temperature_celsius{dev})', instant=True, fmt="table"),
        prom(f'max by (name) (unpoller_device_uptime_seconds{dev})', instant=True, fmt="table"),
        prom(f'max by (name) (unpoller_device_upgradable{dev})', instant=True, fmt="table"),
    ], transformations=[{"id": "joinByField", "options": {"byField": "name", "mode": "outer"}},
                        {"id": "filterFieldsByName", "options": {"include": {"pattern": "^(name|model|version|Value #[B-F])$"}}},
                        organize(rename={"name": "Device", "model": "Model", "version": "Firmware",
                                         "Value #B": "CPU", "Value #C": "Memory", "Value #D": "Temp",
                                         "Value #E": "Uptime", "Value #F": "Update"})],
        overrides=[by_name("CPU", unit="percentunit", decimals=0), by_name("Memory", unit="percentunit", decimals=0),
                   by_name("Temp", unit="celsius", decimals=0), by_name("Uptime", unit="s", decimals=0),
                   by_name("Update", mappings=value_map([(1, "Available", WARN), (0, "", GOOD)]),
                           custom__cellOptions={"type": "color-text"})],
        sort=("Device", False)), 14, 9)
    d.add(timeseries("Access point channel utilisation", [prom(
        'max by (name, radio) (unpoller_device_radio_channel_utilization_total_ratio)', legend="{{name}} {{radio}}")],
        unit="percentunit", minv=0, fill=0, legend="right"), 10, 9)
    d.add(timeseries("PoE power by switch", [prom('sum by (name) (unpoller_device_port_poe_watts)', legend="{{name}}")],
                     unit="watt", decimals=1, stack=True, fill=40, gradient="none", legend="right"), 12, 7)
    d.add(timeseries("Wi-Fi experience by access point", [prom(
        'avg by (ap_name) (unpoller_client_satisfaction_ratio{wired="false", ap_name!=""})', legend="{{ap_name}}")],
        unit="percentunit", maxv=1, fill=0, legend="right",
        desc="UniFi's client satisfaction score, averaged over each AP's clients."), 12, 7)
    return d


def logs_dashboard():
    variables = [
        {"type": "query", "name": "app", "label": "App", "datasource": LOKI, "query": "label_values(app)",
         "refresh": 2, "includeAll": True, "multi": True, "current": {"text": "All", "value": "$__all"},
         "allValue": ".+", "sort": 1},
        {"type": "textbox", "name": "search", "label": "Search", "query": "", "current": {"text": "", "value": ""}},
    ]
    d = Dashboard("lab-logs", "Homelab · Logs & security", folder="Homelab", tags=["homelab"], time="now-6h",
                  variables=variables, refresh="",
                  description="Log volume and errors per app, a filtered log view, and who knocks on the public gateway.")
    d.row("Log volume")
    d.add(timeseries("Log lines by namespace", [loki('sum by (namespace) (count_over_time({namespace=~".+"} [$__auto]))',
                                                     legend="{{namespace}}")],
                     unit="short", bars=True, stack=True, legend="right"), 12, 8)
    d.add(timeseries("Error-looking lines by app", [loki(
        f'sum by (app) (count_over_time({{namespace=~".+", app=~"$app"}} {ERRORS} [$__auto]))', legend="{{app}}")],
        unit="short", bars=True, stack=True, legend="right",
        desc="Lines matching error, panic, fatal or exception (case-insensitive). Crude, but it shows which app got noisy."),
          12, 8)
    d.add(logs("Logs ($app, matching \"$search\")", [loki('{namespace=~".+", app=~"$app"} |~ `(?i)$search`',
                                                          max_lines=500)]), 24, 14)

    d.row("Public gateway")
    public = ENVOY.replace('container="envoy"}', 'container="envoy", pod=~".*-public-.*"}')
    d.add(timeseries("Requests for hosts that aren't ours", [loki(
        f'sum(count_over_time({public} {FOREIGN} [$__auto]))', legend="Scanners")],
        unit="short", bars=True, color={"mode": "fixed", "fixedColor": SLOTS[1]},
        desc="Requests addressed to the bare IP or to other domains: internet-wide scanners."), 12, 8)
    d.add(timeseries("Public responses by status class", [loki(
        f'sum(count_over_time({public}, response_code {OURS} | response_code >= {lo} | response_code < {lo + 100} [$__auto]))',
        legend=f"{lo // 100}xx") for lo in (200, 300, 400, 500)],
        unit="short", bars=True, stack=True,
        overrides=fixed_colors({"2xx": SLOTS[0], "3xx": SLOTS[2], "4xx": SLOTS[3], "5xx": SLOTS[7]})), 12, 8)
    d.add(table("Most probed paths (4xx on public hosts)", [loki(
        f'topk(20, sum by (host, path) (count_over_time({public}, response_code, path=`["x-envoy-origin-path"]` '
        f'{OURS} | response_code >= 400 [$__range])))', instant=True)],
        transformations=[labels_to_fields(), organize(exclude=["Time"], rename={"host": "Host", "path": "Path",
                                                                                "Value #A": "Requests"})],
        sort=("Requests", True)), 12, 10)
    d.add(table("Top user agents (public)", [loki(
        f'topk(20, sum by (ua) (count_over_time({public}, ua=`["user-agent"]` [$__range])))', instant=True)],
        transformations=[labels_to_fields(), organize(exclude=["Time"], rename={"ua": "User agent",
                                                                                "Value #A": "Requests"})],
        sort=("Requests", True)), 12, 10)
    return d


def apps_dashboard():
    d = Dashboard("lab-apps", "Homelab · Apps", folder="Homelab", tags=["homelab"], time="now-30d", refresh="5m",
                  description="What the apps are used for: music, photos, automations and video. 3D printing has its own dashboard.")
    d.row("Ferrotune")
    d.add(stat("Listening this week", [sql("ferrotune", '''
SELECT sum(duration_seconds) / 3600.0 AS hours FROM listening_sessions WHERE listened_at > now() - interval '7 days' ''')],
        unit="h", decimals=1, graph=False), 4, 4)
    d.add(stat("Plays this week", [sql("ferrotune", '''
SELECT count(*) AS plays FROM listening_sessions WHERE listened_at > now() - interval '7 days' AND NOT skipped''')],
        decimals=0, graph=False), 4, 4)
    d.add(stat("Skip rate (30 d)", [sql("ferrotune", '''
SELECT avg(skipped::int) AS skip FROM listening_sessions WHERE listened_at > now() - interval '30 days' ''')],
        unit="percentunit", decimals=0, graph=False), 4, 4)
    d.add(stat("Library", [sql("ferrotune", '''
SELECT (SELECT count(*) FROM songs) AS "Songs", (SELECT count(*) FROM albums) AS "Albums",
       (SELECT count(*) FROM artists) AS "Artists"''')], decimals=0, graph=False, text_mode="value_and_name"), 12, 4)
    d.add(timeseries("Listening hours per day", [sql("ferrotune", '''
SELECT date_trunc('day', listened_at) AS time, sum(duration_seconds) / 3600.0 AS "Hours"
FROM listening_sessions WHERE $__timeFilter(listened_at) GROUP BY 1 ORDER BY 1''', fmt="time_series")],
        unit="h", decimals=1, bars=True, color={"mode": "fixed", "fixedColor": SLOTS[0]}), 12, 8)
    d.add(bargauge("Top artists in range", [sql("ferrotune", '''
SELECT a.name AS metric, round(sum(l.duration_seconds) / 3600.0, 1) AS value
FROM listening_sessions l JOIN songs s ON s.id = l.song_id JOIN artists a ON a.id = s.artist_id
WHERE $__timeFilter(l.listened_at) GROUP BY 1 ORDER BY 2 DESC LIMIT 12''')],
        unit="h", decimals=1, values=True, display_name="${__data.fields.metric}",
        color={"mode": "fixed", "fixedColor": SLOTS[0]}), 6, 8)
    d.add(table("Top songs in range", [sql("ferrotune", '''
SELECT s.title AS "Song", a.name AS "Artist", count(*) AS "Plays"
FROM listening_sessions l JOIN songs s ON s.id = l.song_id LEFT JOIN artists a ON a.id = s.artist_id
WHERE $__timeFilter(l.listened_at) AND NOT l.skipped GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 15''')]), 6, 8)

    d.row("Immich")
    d.add(stat("Photos", [sql("immich", '''SELECT count(*) FROM asset WHERE type = 'IMAGE' AND "deletedAt" IS NULL''')],
               decimals=0, graph=False), 4, 4)
    d.add(stat("Videos", [sql("immich", '''SELECT count(*) FROM asset WHERE type = 'VIDEO' AND "deletedAt" IS NULL''')],
               decimals=0, graph=False), 4, 4)
    d.add(stat("Library size", [sql("immich", '''
SELECT sum(e."fileSizeInByte") FROM asset_exif e JOIN asset a ON a.id = e."assetId" WHERE a."deletedAt" IS NULL''')],
        unit="bytes", decimals=1, graph=False), 4, 4)
    d.add(stat("Last upload", [sql("immich", '''
SELECT extract(epoch FROM now() - max("createdAt")) AS age FROM asset WHERE "deletedAt" IS NULL''')],
        unit="s", decimals=0, graph=False, thr=thresholds(GOOD, (3 * 86400, WARN), (14 * 86400, BAD)),
        desc="Time since anything was uploaded. Phones back up daily, so weeks means backups have stopped."), 4, 4)
    d.add(stat("Favourites", [sql("immich", '''SELECT count(*) FROM asset WHERE "isFavorite" AND "deletedAt" IS NULL''')],
               decimals=0, graph=False), 4, 4)
    d.add(stat("People", [sql("immich", '''SELECT count(*) FROM person WHERE name <> '' ''')], decimals=0,
               graph=False, desc="Named face clusters."), 4, 4)
    d.add(timeseries("Photos and videos taken per month", [sql("immich", '''
SELECT date_trunc('month', "fileCreatedAt") AS time, type AS metric, count(*) AS value
FROM asset WHERE "deletedAt" IS NULL AND $__timeFilter("fileCreatedAt") GROUP BY 1, 2 ORDER BY 1''', fmt="time_series")],
        bars=True, stack=True, time_from="10y", overrides=fixed_colors({"IMAGE": SLOTS[0], "VIDEO": SLOTS[1]}),
        display_name="${__field.labels.metric}"), 14, 8)
    d.add(bargauge("Cameras", [sql("immich", '''
SELECT coalesce(nullif(trim(e.make || ' ' || e.model), ''), 'Unknown') AS metric, count(*) AS value
FROM asset_exif e JOIN asset a ON a.id = e."assetId" WHERE a."deletedAt" IS NULL AND e.model IS NOT NULL
GROUP BY 1 ORDER BY 2 DESC LIMIT 10''')], decimals=0, values=True, display_name="${__data.fields.metric}",
        color={"mode": "fixed", "fixedColor": SLOTS[0]}), 5, 8)
    d.add(bargauge("Places", [sql("immich", '''
SELECT coalesce(e.city || ', ' || e.country, e.country) AS metric, count(*) AS value
FROM asset_exif e JOIN asset a ON a.id = e."assetId" WHERE a."deletedAt" IS NULL AND e.country IS NOT NULL
GROUP BY 1 ORDER BY 2 DESC LIMIT 10''')], decimals=0, values=True, display_name="${__data.fields.metric}",
        color={"mode": "fixed", "fixedColor": SLOTS[0]}), 5, 8)

    d.row("Home automation (homectl)")
    d.add(timeseries("Routine runs per hour", [sql("homectl", '''
SELECT date_trunc('hour', "timestamp"::timestamptz) AS time, entry::jsonb ->> 'routine_name' AS metric, count(*) AS value
FROM routine_history
WHERE "timestamp"::timestamptz BETWEEN $__timeFrom() AND $__timeTo() AND entry::jsonb ->> 'trigger_kind' = 'v2_run'
GROUP BY 1, 2 ORDER BY 1''', fmt="time_series")], bars=True, stack=True, legend="right",
        display_name="${__field.labels.metric}", time_from="7d"), 12, 8)
    d.add(table("Routines in range", [sql("homectl", '''
SELECT entry::jsonb ->> 'routine_name' AS "Routine",
       count(*) FILTER (WHERE entry::jsonb ->> 'trigger_kind' = 'v2_run') AS "Ran",
       count(*) FILTER (WHERE entry::jsonb ->> 'trigger_kind' = 'v2_blocked') AS "Blocked",
       max("timestamp"::timestamptz) AS "Last evaluated"
FROM routine_history WHERE "timestamp"::timestamptz BETWEEN $__timeFrom() AND $__timeTo()
GROUP BY 1 ORDER BY 2 DESC, 3 DESC''')], overrides=[by_name("Last evaluated", unit="dateTimeFromNow")],
        time_from="7d", desc="Blocked means a trigger fired but the routine's conditions were false."), 12, 8)

    d.row("Media (through the gateway)")
    d.add(timeseries("Jellyfin & Immich data served per day", [loki(
        f'sum by (host) (sum_over_time({ENVOY}, bytes_sent | host=~`(jellyfin|immich|photos)\\..*` | unwrap bytes_sent [1d]))',
        legend="{{host}}")], unit="bytes", bars=True, stack=True, interval="1d",
        desc="Only remote and gateway traffic; LAN clients streaming from Jellyfin's MetalLB address are not counted."),
          24, 7)
    return d


def overview_dashboard():
    d = Dashboard("overview", "Overview", folder="", tags=["overview"], time="now-24h",
                  annotations=[deploy_annotation() | {"enable": False}],
                  description="The house and the homelab at a glance. Click a tile or use the links above for detail.")
    d.row("House")
    home.overview_house(d)
    d.add(barchart("Spot price today and tomorrow", [home.flux(home.TZ + 'import "date"\n' + home.prices(
        start="today()", stop="date.add(d: 2d, to: today())"))], unit="c/kWh", decimals=1, thr=home.PRICE_THR,
        color={"mode": "thresholds"}, x_field="_time", label_spacing=100, display_name="Price"), 12, 7)
    d.add(timeseries("Power by plug", [home.flux(home.power())], unit="watt", stack=True, fill=40, gradient="none",
                     display_name=home.BY_DEVICE, overrides=home.PLUG_OVERRIDES, legend="right", decimals=0), 12, 7)
    d.row("Homelab")
    d.add(stat("Apps down", [prom(f'count({APPS.format(metric="probe_success")} == 0) or vector(0)', instant=True)],
               graph=False, thr=thresholds(GOOD, (1, BAD)), color_mode="background",
               links=[{"title": "Services", "url": "/d/lab-services"}]), 3, 4)
    d.add(stat("Firing alerts", [prom('count(ALERTS{alertstate="firing", alertname!~"Watchdog|InfoInhibitor"}) or vector(0)',
                                      instant=True)], graph=False, thr=thresholds(GOOD, (1, WARN)), color_mode="background"),
          3, 4)
    d.add(stat("Flux not ready", [prom('count(gotk_resource_info{ready="False"}) or vector(0)', instant=True)],
               graph=False, thr=thresholds(GOOD, (1, BAD)), color_mode="background"), 3, 4)
    d.add(stat("Restarts (24 h)", [prom('sum(increase(kube_pod_container_status_restarts_total[24h]))', instant=True)],
               decimals=0, graph=False, thr=thresholds(GOOD, (3, WARN), (10, BAD)),
               links=[{"title": "Workloads", "url": "/d/lab-workloads"}]), 3, 4)
    d.add(stat("Cluster CPU", [prom('1 - avg(rate(node_cpu_seconds_total{job="node-exporter", mode="idle"}[5m]))')],
               unit="percentunit", decimals=0, thr=thresholds(GOOD, (0.7, WARN), (0.9, BAD))), 3, 4)
    d.add(stat("Cluster memory", [prom('1 - sum(node_memory_MemAvailable_bytes{job="node-exporter"}) / '
                                       'sum(node_memory_MemTotal_bytes{job="node-exporter"})')],
               unit="percentunit", decimals=0, thr=thresholds(GOOD, (0.75, WARN), (0.9, BAD))), 3, 4)
    d.add(stat("Hottest host CPU", [prom(
        'max(node_hwmon_temp_celsius{job="proxmox-node"} * on (instance, chip) group_left (chip_name) '
        'node_hwmon_chip_names{job="proxmox-node", chip_name=~"k10temp|coretemp"})')], unit="celsius", decimals=0,
        thr=thresholds(GOOD, (75, WARN), (90, BAD)), links=[{"title": "Hardware", "url": "/d/lab-hardware"}]), 3, 4)
    d.add(stat("Internet now", [prom('sum(rate(unpoller_device_wan_receive_bytes_total[2m])) * 8')], unit="bps",
               decimals=1, links=[{"title": "Network", "url": "/d/lab-network"}]), 3, 4)
    d.add(status_history("App status", [prom(f'max by (app) ({APPS.format(metric="probe_success")})', legend="{{app}}",
                                             interval="10m")],
                         mappings=UP_DOWN, color={"mode": "thresholds"}, thr=thresholds(BAD, (1, GOOD)),
                         row_height=0.8), 12, 14)
    d.add(timeseries("Requests per app", [loki(f'sum by (host) (count_over_time({ENVOY} {OURS} [$__auto]))',
                                               legend="{{host}}")],
                     unit="short", stack=True, fill=30, gradient="none", legend="hidden"), 12, 7)
    d.add(timeseries("Memory by container", [prom(
        'topk(8, sum by (container) (container_memory_working_set_bytes{namespace="default", container!="", container!="POD"}))',
        legend="{{container}}")], unit="bytes", fill=0, legend="hidden"), 12, 7)
    return d
