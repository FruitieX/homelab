"""3D printing dashboard: Bambuddy (printer), the printer's smart plug and filament storage."""

import home
from lib import (BAD, GOOD, MUTED, SLOTS, WARN, Dashboard, bargauge, by_name, fixed_colors, flux, prom, sql,
                 stat, state_timeline, table, thresholds, timeseries, value_map)

PRINTER = '{printer_name="Bambu Lab X2D"}'
STATE = value_map([(0, "Unknown", MUTED), (1, "Idle", MUTED), (2, "Printing", SLOTS[0]), (3, "Paused", WARN),
                   (4, "Finished", GOOD), (5, "Failed", BAD), (6, "Preparing", SLOTS[2]), (7, "Slicing", SLOTS[2])])
OUTCOME_COLORS = fixed_colors({"completed": GOOD, "failed": BAD, "aborted": WARN, "cancelled": WARN})
STORAGE_ROOM = {"C76A03460A73": "Storage room"}
TEMPS = {"Nozzle L": SLOTS[1], "Nozzle R": SLOTS[3], "Bed": SLOTS[0], "Chamber": SLOTS[2]}


def printing_dashboard():
    d = Dashboard("home-printing", "Home · 3D printing", folder="Home", tags=["home"], time="now-24h",
                  refresh="30s",
                  description="The Bambu Lab X2D through Bambuddy, what printing costs, and how dry the filament is.")

    d.row("Printer now")
    d.add(stat("State", [prom(f'max(bambuddy_printer_state{PRINTER})')], graph=False, mappings=STATE,
               color_mode="background", thr=thresholds(MUTED)), 4, 5)
    d.add(stat("Progress", [prom(f'max(bambuddy_print_progress{PRINTER})')], unit="percent", decimals=0,
               graph=False, thr=thresholds(SLOTS[0])), 4, 5)
    d.add(stat("Layer", [prom(f'max(bambuddy_print_layer_current{PRINTER})', legend="Layer"),
                         prom(f'max(bambuddy_print_layer_total{PRINTER})', legend="of")],
               decimals=0, graph=False, text_mode="value_and_name"), 4, 5)
    d.add(stat("Time left", [prom(f'max(bambuddy_print_remaining_seconds{PRINTER})')], unit="s", graph=False), 4, 5)
    d.add(stat("Done at", [prom(f'(time() + max(bambuddy_print_remaining_seconds{PRINTER}) > time()) * 1000')],
               unit="dateTimeAsLocalNoDateIfToday", graph=False, nulls="Not printing",
               desc="Wall-clock time the current print should finish."), 4, 5)
    d.add(stat("Queue", [prom('max(bambuddy_queue_pending)')], decimals=0, graph=False,
               desc="Jobs waiting in Bambuddy's print queue."), 4, 5)
    d.add(stat("Temperatures", [
        prom(f'max(bambuddy_nozzle_temp_celsius{{printer_name="Bambu Lab X2D", nozzle="0"}})', legend="Nozzle L"),
        prom(f'max(bambuddy_nozzle_temp_celsius{{printer_name="Bambu Lab X2D", nozzle="1"}})', legend="Nozzle R"),
        prom(f'max(bambuddy_bed_temp_celsius{PRINTER})', legend="Bed"),
        prom(f'max(bambuddy_chamber_temp_celsius{PRINTER})', legend="Chamber")],
        unit="celsius", decimals=0, graph=False, text_mode="value_and_name", thr=thresholds(SLOTS[0])), 16, 5)
    d.add(stat("Plug power", [flux('''from(bucket: "home")
  |> range(start: -10m)
  |> filter(fn: (r) => r._measurement == "mqtt_consumer" and r.integration_id == "tuya" and r._field == "raw_19" and r.name == "3D printer")
  |> last()
  |> map(fn: (r) => ({_time: r._time, _value: float(v: r._value) / 10.0}))''')], unit="watt", decimals=0,
               graph=False), 4, 5)
    d.add(stat("Wi-Fi", [prom(f'max(bambuddy_wifi_signal_dbm{PRINTER})')], unit="dBm", decimals=0, graph=False,
               thr=thresholds(BAD, (-75, WARN), (-65, GOOD))), 4, 5)

    d.row("Activity")
    d.add(state_timeline("Printer state", [prom(f'max(bambuddy_printer_state{PRINTER})', legend="X2D")],
                         mappings=STATE, color={"mode": "thresholds"}, thr=thresholds(MUTED), time_from="7d"), 24, 4)
    d.add(timeseries("Temperatures", [
        prom(f'max(bambuddy_nozzle_temp_celsius{{printer_name="Bambu Lab X2D", nozzle="0"}})', legend="Nozzle L"),
        prom(f'max(bambuddy_nozzle_temp_celsius{{printer_name="Bambu Lab X2D", nozzle="1"}})', legend="Nozzle R"),
        prom(f'max(bambuddy_bed_temp_celsius{PRINTER})', legend="Bed"),
        prom(f'max(bambuddy_chamber_temp_celsius{PRINTER})', legend="Chamber")],
        unit="celsius", decimals=0, fill=0, overrides=fixed_colors(TEMPS), legend="right"), 14, 8)
    d.add(timeseries("Progress", [prom(f'max(bambuddy_print_progress{PRINTER})', legend="Progress")],
                     unit="percent", minv=0, maxv=100, color={"mode": "fixed", "fixedColor": SLOTS[0]}), 10, 8)

    d.row("Totals")
    d.add(stat("Prints", [prom('sum by (result) (bambuddy_prints_total)', legend="{{result}}")], decimals=0,
               graph=False, text_mode="value_and_name",
               overrides=OUTCOME_COLORS, thr=thresholds(SLOTS[0])),
          8, 4)
    d.add(stat("Success rate", [prom('sum(bambuddy_prints_total{result="completed"}) / sum(bambuddy_prints_total)')],
               unit="percentunit", decimals=0, graph=False, thr=thresholds(BAD, (0.8, WARN), (0.9, GOOD))), 4, 4)
    d.add(stat("Filament used", [prom('max(bambuddy_filament_used_grams)')], unit="massg", decimals=1,
               graph=False), 4, 4)
    d.add(stat("Print time", [prom('max(bambuddy_print_time_seconds)')], unit="s", decimals=0, graph=False), 4, 4)
    d.add(stat("Plug cost this month", [flux(home.TZ + 'import "date"\n' + home.cost(
        start='date.truncate(t: now(), unit: 1mo)', stop="now()") + '''
  |> filter(fn: (r) => r.device == "3D printer")
  |> group() |> sum()''')], unit="currencyEUR", decimals=2, graph=False,
               desc="The printer's smart-plug energy at the hourly spot price."), 4, 4)
    d.add(timeseries("Prints per week", [sql("bambuddy", '''
SELECT date_trunc('week', coalesce(started_at, created_at)) AS time, status AS metric, count(*) AS value
FROM print_archives WHERE deleted_at IS NULL AND $__timeFilter(coalesce(started_at, created_at))
GROUP BY 1, 2 ORDER BY 1''', fmt="time_series")], bars=True, stack=True, decimals=0, time_from="1y",
                     display_name="${__field.labels.metric}", overrides=OUTCOME_COLORS,
                     desc="Bambuddy's print archive, back to the first print."), 8, 8)
    d.add(timeseries("Filament per week by material", [sql("bambuddy", '''
SELECT date_trunc('week', coalesce(started_at, created_at)) AS time, coalesce(filament_type, 'Unknown') AS metric,
       sum(filament_used_grams) AS value
FROM print_archives WHERE deleted_at IS NULL AND $__timeFilter(coalesce(started_at, created_at))
GROUP BY 1, 2 ORDER BY 1''', fmt="time_series")], unit="massg", bars=True, stack=True, decimals=0, time_from="1y",
                     display_name="${__field.labels.metric}"), 8, 8)
    d.add(timeseries("Printer energy per day", [flux(home.TZ + home.power(every="1h", devices=["3D printer"],
                                                                          time_src="_start") + '''
  |> aggregateWindow(every: 1d, fn: sum, createEmpty: false, timeSrc: "_start")
  |> map(fn: (r) => ({r with _value: r._value / 1000.0}))''')], unit="kwatth", bars=True, decimals=2,
                     time_from="30d", display_name="Energy", color={"mode": "fixed", "fixedColor": SLOTS[4]}), 8, 8)

    d.row("Print history (Bambuddy archive)")
    d.add(table("Recent prints", [sql("bambuddy", '''
SELECT coalesce(started_at, created_at) AS "Started", print_name AS "Print", status AS "Result",
       print_time_seconds AS "Duration", filament_used_grams AS "Filament", filament_type AS "Material",
       cost AS "Cost", failure_reason AS "Why it failed"
FROM print_archives WHERE deleted_at IS NULL ORDER BY coalesce(started_at, created_at) DESC LIMIT 25''')],
        overrides=[by_name("Started", unit="dateTimeAsLocal"), by_name("Duration", unit="s", decimals=0),
                   by_name("Filament", unit="massg", decimals=0), by_name("Cost", unit="currencyEUR", decimals=2),
                   by_name("Result", custom__cellOptions={"type": "color-text"},
                           mappings=value_map([("completed", "completed", GOOD), ("failed", "failed", BAD),
                                               ("aborted", "aborted", WARN), ("cancelled", "cancelled", WARN)]))]),
          16, 10)
    d.add(bargauge("Most printed", [sql("bambuddy", '''
SELECT print_name AS metric, count(*) AS value FROM print_archives
WHERE deleted_at IS NULL AND status = 'completed' GROUP BY 1 ORDER BY 2 DESC LIMIT 10''')],
        decimals=0, values=True, display_name="${__data.fields.metric}",
        color={"mode": "fixed", "fixedColor": SLOTS[0]}), 8, 10)
    d.add(stat("Filament cost", [sql("bambuddy", '''
SELECT sum(cost) AS "Total", coalesce(sum(cost) FILTER (WHERE started_at > date_trunc('month', now())), 0) AS "This month",
       avg(cost) FILTER (WHERE status = 'completed') AS "Per print"
FROM print_archives WHERE deleted_at IS NULL''')], unit="currencyEUR", decimals=2, graph=False,
        text_mode="value_and_name", desc="Bambuddy's filament cost per print, from the spool prices."), 8, 5)
    d.add(stat("Typical print", [sql("bambuddy", '''
SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY print_time_seconds) AS "Duration",
       percentile_cont(0.5) WITHIN GROUP (ORDER BY filament_used_grams) AS "Filament"
FROM print_archives WHERE deleted_at IS NULL AND status = 'completed'
''')], graph=False,
        text_mode="value_and_name", overrides=[by_name("Duration", unit="s", decimals=0),
                                               by_name("Filament", unit="massg", decimals=0)],
        desc="Median of completed prints."), 8, 5)
    d.add(stat("Printer runtime", [sql("bambuddy", '''
SELECT max(print_hours_offset + runtime_seconds / 3600.0) * 3600 AS runtime FROM printers''')], unit="s",
        decimals=0, graph=False, desc="Total print-head hours as Bambuddy counts them for maintenance."), 8, 5)
    d.add(table("Maintenance", [sql("bambuddy", '''
WITH p AS (SELECT id, print_hours_offset + runtime_seconds / 3600.0 AS hours FROM printers)
SELECT t.name AS "Task", coalesce(pm.custom_interval_hours, t.default_interval_hours) AS "Every (h)",
       round((p.hours - pm.last_performed_hours)::numeric, 1) AS "Since (h)",
       round((coalesce(pm.custom_interval_hours, t.default_interval_hours) - (p.hours - pm.last_performed_hours))::numeric, 1) AS "Due in (h)",
       pm.last_performed_at AS "Last done"
FROM printer_maintenance pm JOIN maintenance_types t ON t.id = pm.maintenance_type_id JOIN p ON p.id = pm.printer_id
WHERE pm.enabled AND NOT t.is_deleted AND coalesce(pm.custom_interval_type, t.interval_type) = 'hours'
ORDER BY 4''')], overrides=[by_name("Due in (h)", thresholds=thresholds(BAD, (0, WARN), (10, GOOD)),
                                  custom__cellOptions={"type": "color-text"}),
                          by_name("Last done", unit="dateTimeFromNow")],
        desc="Hour-based maintenance from Bambuddy. Negative means overdue; mark it done in Bambuddy."), 12, 8)
    d.add(table("Spools", [sql("bambuddy", '''
SELECT material AS "Material", coalesce(nullif(color_name, ''), '#' || left(rgba, 6)) AS "Colour", brand AS "Brand",
       greatest(label_weight - coalesce(weight_used, 0), 0) AS "Left", label_weight AS "Of", last_used AS "Last used"
FROM spool WHERE archived_at IS NULL ORDER BY 4''')],
        overrides=[by_name("Left", unit="massg", decimals=0, thresholds=thresholds(BAD, (150, WARN), (300, GOOD)),
                           custom__cellOptions={"type": "color-text"}),
                   by_name("Of", unit="massg", decimals=0), by_name("Last used", unit="dateTimeFromNow")],
        desc="Spool inventory as Bambuddy tracks it; weights are only as good as its usage tracking."), 12, 8)

    d.row("Filament storage")
    d.add(bargauge("Humidity now", [flux(home.ble("hum", home.PRINTING, start="-6h", stop="now()") + "\n  |> last()")],
                   unit="humidity", decimals=0, minv=0, maxv=60, display_name=home.BY_PLACE,
                   thr=thresholds(GOOD, (20, WARN), (35, BAD)),
                   desc="Keep filament below about 20 % RH; above 35 % it starts absorbing moisture."), 8, 9)
    d.add(timeseries("Humidity", [flux(home.ble("hum", home.PRINTING))], unit="humidity", decimals=0, fill=0,
                     display_name=home.BY_PLACE, overrides=fixed_colors(dict(zip(home.PRINTING.values(), SLOTS))),
                     time_from="7d", legend="right"), 16, 9)
    d.add(timeseries("Dryer and box temperatures", [flux(home.ble("tempc", home.PRINTING))], unit="celsius",
                     decimals=1, fill=0, display_name=home.BY_PLACE,
                     overrides=fixed_colors(dict(zip(home.PRINTING.values(), SLOTS))), time_from="7d",
                     legend="right"), 12, 8)
    d.add(timeseries("Storage room temperature", [flux(home.ble("tempc", {k: "Storage room" for k in STORAGE_ROOM}))],
                     unit="celsius", decimals=1, fill=0, display_name=home.BY_PLACE, time_from="7d",
                     color={"mode": "fixed", "fixedColor": SLOTS[1]}, desc="Where the printer and spare spools live."),
          6, 8)
    d.add(timeseries("Storage room humidity", [flux(home.ble("hum", {k: "Storage room" for k in STORAGE_ROOM}))],
                     unit="humidity", decimals=0, fill=0, display_name=home.BY_PLACE, time_from="7d",
                     color={"mode": "fixed", "fixedColor": SLOTS[0]}), 6, 8)
    return d
