"""3D printing dashboard: Bambuddy (printer), the printer's smart plug and filament storage."""

import home
from lib import (BAD, GOOD, MUTED, SLOTS, WARN, Dashboard, bargauge, fixed_colors, flux, prom, stat,
                 state_timeline, thresholds, timeseries, value_map)

PRINTER = '{printer_name="Bambu Lab X2D"}'
STATE = value_map([(0, "Unknown", MUTED), (1, "Idle", MUTED), (2, "Printing", SLOTS[0]), (3, "Paused", WARN),
                   (4, "Finished", GOOD), (5, "Failed", BAD), (6, "Preparing", SLOTS[2]), (7, "Slicing", SLOTS[2])])
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
               overrides=fixed_colors({"completed": GOOD, "failed": BAD, "aborted": WARN}), thr=thresholds(SLOTS[0])),
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
    d.add(timeseries("Prints per day", [prom('sum by (result) (increase(bambuddy_prints_total[1d]))',
                                             legend="{{result}}", interval="1d")],
                     bars=True, stack=True, decimals=0, time_from="30d",
                     overrides=fixed_colors({"completed": GOOD, "failed": BAD, "aborted": WARN}),
                     desc="From Bambuddy's counters, so history starts when Prometheus began scraping it."), 8, 8)
    d.add(timeseries("Filament per day", [prom('max(delta(bambuddy_filament_used_grams[1d]))', legend="Filament",
                                               interval="1d")],
                     unit="massg", bars=True, decimals=0, time_from="30d",
                     color={"mode": "fixed", "fixedColor": SLOTS[0]}), 8, 8)
    d.add(timeseries("Printer energy per day", [flux(home.TZ + home.power(every="1h", devices=["3D printer"],
                                                                          time_src="_start") + '''
  |> aggregateWindow(every: 1d, fn: sum, createEmpty: false, timeSrc: "_start")
  |> map(fn: (r) => ({r with _value: r._value / 1000.0}))''')], unit="kwatth", bars=True, decimals=2,
                     time_from="30d", display_name="Energy", color={"mode": "fixed", "fixedColor": SLOTS[4]}), 8, 8)

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
