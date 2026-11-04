"""Machines dashboard: the NixOS hosts outside the cluster (job nixos-node, see docs/monitoring.md)."""

from lib import (BAD, GOOD, MUTED, PROM, WARN, Dashboard, organize, prom, stat, table, thresholds,
                 timeseries, value_map)

SEL = 'job="nixos-node", host=~"$host"'
NX = "{" + SEL + "}"
# Physical and Wi-Fi interfaces; skips loopback, Docker bridges and veths.
NIC = "{" + SEL + ', device=~"en.*|eth.*|wl.*"}'


def temp(chip):
    return (f'max by (host) (node_hwmon_temp_celsius{NX} * on (instance, chip) group_left (chip_name) '
            f'node_hwmon_chip_names{{{SEL}, chip_name=~"{chip}"}})')


def rate(metric, extra=""):
    return f'rate({metric}{{{SEL}{extra}}}[$__rate_interval])'


CPU_BUSY = rate("node_cpu_seconds_total", ', mode="idle"')


def machines_dashboard():
    variables = [
        {"type": "query", "name": "host", "label": "Host", "datasource": PROM,
         "query": {"query": 'label_values(up{job="nixos-node"}, host)', "refId": "host"},
         "definition": 'label_values(up{job="nixos-node"}, host)',
         "refresh": 2, "includeAll": True, "multi": True, "current": {"text": "All", "value": "$__all"},
         "allValue": ".+", "sort": 1},
    ]
    d = Dashboard("machines", "Machines", folder="Machines", tags=["machines"], time="now-24h", variables=variables,
                  description="The NixOS machines outside the cluster. Some only run on demand; "
                              "gaps in the graphs are times they were off.")

    d.row("Now")
    d.add(stat("State", [prom(f'up{NX}', legend="{{host}}")], graph=False, color_mode="background",
               text_mode="value_and_name", mappings=value_map([(1, "On", GOOD), (0, "Off", MUTED)]),
               thr=thresholds(MUTED, (1, GOOD))), 4, 5)
    d.add(stat("Uptime", [prom(f'time() - node_boot_time_seconds{NX}', legend="{{host}}")], unit="s", decimals=0,
               graph=False, text_mode="value_and_name"), 4, 5)
    d.add(stat("CPU used", [prom(f'1 - avg by (host) ({CPU_BUSY})',
                                 legend="{{host}}")], unit="percentunit", decimals=0, text_mode="value_and_name",
               thr=thresholds(GOOD, (0.7, WARN), (0.9, BAD))), 4, 5)
    d.add(stat("Memory used", [prom(f'1 - node_memory_MemAvailable_bytes{NX} / node_memory_MemTotal_bytes{NX}',
                                    legend="{{host}}")], unit="percentunit", decimals=0, text_mode="value_and_name",
               thr=thresholds(GOOD, (0.8, WARN), (0.92, BAD))), 4, 5)
    d.add(stat("Disk free (/)", [prom(f'node_filesystem_avail_bytes{{{SEL}, mountpoint="/"}} / '
                                      f'node_filesystem_size_bytes{{{SEL}, mountpoint="/"}}', legend="{{host}}")],
               unit="percentunit", decimals=0, text_mode="value_and_name",
               thr=thresholds(BAD, (0.1, WARN), (0.2, GOOD))), 4, 5)
    d.add(stat("Failed units", [prom(f'count by (host) (node_systemd_unit_state{{{SEL}, state="failed"}} == 1 '
                                     f'or node_systemd_user_unit_failed{NX} == 1) or sum by (host) (up{NX} == 1) * 0',
                                     legend="{{host}}")], decimals=0, graph=False, text_mode="value_and_name",
               thr=thresholds(GOOD, (1, BAD)),
               desc="System and user units (T3 Code and the remote-control bridges run as user units)."), 4, 5)

    d.row("CPU")
    d.add(timeseries("CPU used", [prom(f'1 - avg by (host) ({CPU_BUSY})',
                                       legend="{{host}}")], unit="percentunit", minv=0, maxv=1, fill=0), 8, 8)
    d.add(timeseries("Load per core", [prom(f'node_load1{NX} / on (host) count by (host) '
                                            f'(node_cpu_seconds_total{{{SEL}, mode="idle"}})', legend="{{host}}")],
                     unit="short", decimals=2, minv=0, fill=0,
                     desc="1-minute load average divided by the number of cores. Above 1 means work is queueing."), 8, 8)
    d.add(timeseries("Temperature", [
        prom(temp("k10temp|coretemp"), legend="{{host}} CPU"),
        prom(temp("amdgpu"), legend="{{host}} GPU"),
        prom(temp("nvme"), legend="{{host}} NVMe")], unit="celsius", decimals=0, fill=0,
        desc="VMs have no temperature sensors. NixosHostHot fires above 90°C for 30 minutes."), 8, 8)

    d.row("Memory")
    d.add(timeseries("Memory used", [prom(f'node_memory_MemTotal_bytes{NX} - node_memory_MemAvailable_bytes{NX}',
                                          legend="{{host}}")], unit="bytes", minv=0, fill=0), 8, 8)
    d.add(timeseries("Swap used", [prom(f'node_memory_SwapTotal_bytes{NX} - node_memory_SwapFree_bytes{NX}',
                                        legend="{{host}}")], unit="bytes", minv=0, fill=0,
                     desc="zram, where the host has it."), 8, 8)
    d.add(timeseries("Pressure stalls", [
        prom(f'avg by (host) ({rate("node_pressure_memory_waiting_seconds_total")})', legend="{{host}} memory"),
        prom(f'avg by (host) ({rate("node_pressure_io_waiting_seconds_total")})', legend="{{host}} I/O"),
        prom(f'avg by (host) ({rate("node_pressure_cpu_waiting_seconds_total")})', legend="{{host}} CPU")],
        unit="percentunit", minv=0, fill=0,
        desc="Share of time some task waited for memory, I/O or CPU (Linux PSI). Sustained memory stalls come before OOM kills."),
          8, 8)

    d.row("Disk and network")
    d.add(timeseries("Free space", [prom(f'node_filesystem_avail_bytes{{{SEL}, fstype!~"tmpfs|ramfs|fuse.*", '
                                         f'mountpoint!="/nix/store"}}', legend="{{host}}:{{mountpoint}}")],
                     unit="bytes", minv=0, fill=0, desc="NixosHostDiskFillingUp fires under 10% free."), 8, 8)
    d.add(timeseries("Disk I/O", [
        prom(f'sum by (host) ({rate("node_disk_read_bytes_total")})', legend="{{host}} read"),
        prom(f'sum by (host) ({rate("node_disk_written_bytes_total")})', legend="{{host}} write")],
        unit="Bps", minv=0, fill=0), 8, 8)
    d.add(timeseries("Network", [
        prom(f'sum by (host) (rate(node_network_receive_bytes_total{NIC}[$__rate_interval])) * 8', legend="{{host}} in"),
        prom(f'sum by (host) (rate(node_network_transmit_bytes_total{NIC}[$__rate_interval])) * 8', legend="{{host}} out")],
        unit="bps", minv=0, fill=0), 8, 8)

    d.row("Problems")
    d.add(table("Failed units", [
        prom(f'node_systemd_unit_state{{{SEL}, state="failed"}} == 1', instant=True, fmt="table"),
        prom(f'node_systemd_user_unit_failed{NX} == 1', instant=True, fmt="table")],
        transformations=[{"id": "merge", "options": {}},
                         organize(exclude=["Time", "Value", "__name__", "instance", "job", "state", "type",
                                           "Value #A", "Value #B"],
                                  rename={"host": "Host", "name": "Unit", "user": "User"})],
        desc="Empty is good. User units have a User; check them with journalctl --user -u <unit>."), 12, 8)
    d.add(timeseries("OOM kills", [prom(f'increase(node_vmstat_oom_kill{NX}[$__interval])', legend="{{host}}")],
                     unit="short", decimals=0, bars=True, minv=0,
                     desc="Processes the kernel killed for lack of memory, usually builds running too many jobs."), 12, 8)
    return d
