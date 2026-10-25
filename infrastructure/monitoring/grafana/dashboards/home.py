"""Home dashboards: energy & water, climate, devices (InfluxDB home/nordpool)."""

from lib import (BAD, GOOD, MUTED, SLOTS, WARN, Dashboard, barchart, bargauge, by_name, fixed_colors, flux,
                 organize, reduce_rows, stat, status_history, table, text, thresholds, timeseries, value_map)

TZ = 'import "timezone"\noption location = timezone.location(name: "Europe/Helsinki")\n'

# SwitchBot BLE sensors (device_id -> place). Unlisted ids are neighbours'.
INDOOR = {
    "D7353530665A": "Living room", "D63534385106": "Office", "D83535301C43": "Upstairs office",
    "C76A05062842": "Bedroom", "D7353530520F": "Ada's room", "D83431306571": "Upstairs bathroom",
    "CE2A82463674": "Downstairs bathroom",
}
OUTDOOR = {
    "D83534387029": "Front yard", "D9353438450D": "Backyard", "D4343037362D": "Patio",
    "C76A03460A73": "Storage", "C76A0246647E": "Car",
}
COLD = {"C76A0306280B": "Fridge", "D43535302778": "Freezer"}
PRINTING = {
    "CE2A83062A34": "Filament dryer", "C76A00462055": "AMS 1", "C76A05065C4E": "AMS 2",
    "CE2A82463295": "Drybox 1", "D9343130222A": "Drybox 2", "CE2A8486490B": "Drybox 3",
}
ALL_BLE = {**INDOOR, **OUTDOOR, **COLD, **PRINTING}
OUTSIDE_ID = "D83534387029"

# Tuya smart plugs, each pinned to a categorical slot.
PLUGS = ["Fridge", "Freezer", "PC", "Sim racing rig", "3D printer", "Outdoor heater 1", "Outdoor heater 2"]
PLUG_COLORS = {name: SLOTS[i] for i, name in enumerate(PLUGS)}


def _dict(names):
    return "[" + ", ".join(f'"{k}": "{v}"' for k, v in names.items()) + "]"


def _set(names):
    return "[" + ", ".join(f'"{k}"' for k in names) + "]"


def _ids(names):
    """device_id filter InfluxDB can push down to storage (contains() cannot)."""
    return " or ".join(f'r.device_id == "{k}"' for k in names)


def ble(field, names, every="v.windowPeriod", start="v.timeRangeStart", stop="v.timeRangeStop", fn="mean"):
    """One series per place; both BLE gateways report each sensor, mean merges them."""
    return f'''
import "dict"
names = {_dict(names)}
from(bucket: "home")
  |> range(start: {start}, stop: {stop})
  |> filter(fn: (r) => r._measurement == "mqtt_consumer" and r._field == "{field}")
  |> filter(fn: (r) => {_ids(names)})
  |> map(fn: (r) => ({{_time: r._time, _value: float(v: r._value), place: dict.get(dict: names, key: r.device_id, default: r.device_id)}}))
  |> group(columns: ["place"])
  |> aggregateWindow(every: {every}, fn: {fn}, createEmpty: false)
'''


def power(every="v.windowPeriod", start="v.timeRangeStart", stop="v.timeRangeStop", devices=None, time_src="_stop"):
    dev = ""
    if devices:
        dev = "\n  |> filter(fn: (r) => " + " or ".join(f'r.name == "{d}"' for d in devices) + ")"
    return f'''from(bucket: "home")
  |> range(start: {start}, stop: {stop})
  |> filter(fn: (r) => r._measurement == "mqtt_consumer" and r.integration_id == "tuya" and r._field == "raw_19"){dev}
  |> map(fn: (r) => ({{_time: r._time, _value: float(v: r._value) / 10.0, device: r.name}}))
  |> group(columns: ["device"])
  |> aggregateWindow(every: {every}, fn: mean, createEmpty: false, timeSrc: "{time_src}")'''


def prices(start="v.timeRangeStart", stop="v.timeRangeStop"):
    return f'''from(bucket: "nordpool")
  |> range(start: {start}, stop: {stop})
  |> filter(fn: (r) => r._measurement == "price" and r._field == "price")
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false, timeSrc: "_start")
  |> keep(columns: ["_time", "_value"])'''


def cost(start="v.timeRangeStart", stop="v.timeRangeStop"):
    """Hourly € per plug: mean W over an hour is Wh; price is c/kWh."""
    return f'''energy = {power(every="1h", start=start, stop=stop, time_src="_start")}
price = {prices(start=start, stop=stop)}
join(tables: {{e: energy, p: price}}, on: ["_time"])
  |> map(fn: (r) => ({{_time: r._time, device: r.device, _value: r._value_e / 1000.0 * r._value_p / 100.0}}))
  |> group(columns: ["device"])'''


def water(start="v.timeRangeStart", stop="v.timeRangeStop"):
    """Litres since the previous reading (the meter's `rate` field)."""
    return f'''from(bucket: "home")
  |> range(start: {start}, stop: {stop})
  |> filter(fn: (r) => r._measurement == "mqtt_consumer" and r.name == "watermeter" and r._field == "rate")
  |> filter(fn: (r) => r._value > -20.0 and r._value < 20.0)
  |> keep(columns: ["_time", "_value"])'''


# Weather model of daily electricity use, fitted on Datahub 2021–2024 against FMI daily mean temperature (R² 0.8).
ELEC_BASE, ELEC_SLOPE, ELEC_BALANCE = 19.9, 1.86, 10.5


def electricity(every=None, start="v.timeRangeStart", stop="v.timeRangeStop"):
    """Metered whole-house kWh (Datahub, hourly), optionally summed per period."""
    q = f'''from(bucket: "home")
  |> range(start: {start}, stop: {stop})
  |> filter(fn: (r) => r._measurement == "electricity" and r._field == "kwh")
  |> group()
  |> keep(columns: ["_time", "_value"])'''
    if every:
        q += f'''
  |> aggregateWindow(every: {every}, fn: sum, createEmpty: false, timeSrc: "_start")
  |> set(key: "series", value: "Used")'''
    return q


def expected_electricity(start="v.timeRangeStart", stop="v.timeRangeStop"):
    """Daily kWh the weather model expects, stamped at local midnight like electricity(every="1d")."""
    return f'''import "date"
from(bucket: "home")
  |> range(start: {start}, stop: {stop})
  |> filter(fn: (r) => r._measurement == "fmi_daily" and r.param == "tday")
  |> map(fn: (r) => ({{_time: date.truncate(t: r._time, unit: 1d), _value: {ELEC_BASE} + {ELEC_SLOPE} * (if r._value < {ELEC_BALANCE} then {ELEC_BALANCE} - r._value else 0.0), series: "Expected from weather"}}))
  |> group(columns: ["series"])'''


def fingrid(series, fn="mean", start="v.timeRangeStart", stop="v.timeRangeStop"):
    """Fingrid 3-minute data (measurement fingrid), one series per name, relabelled."""
    return f'''
import "dict"
from(bucket: "home")
  |> range(start: {start}, stop: {stop})
  |> filter(fn: (r) => r._measurement == "fingrid" and r._field == "value")
  |> filter(fn: (r) => {" or ".join(f'r.series == "{k}"' for k in series)})
  |> map(fn: (r) => ({{_time: r._time, _value: r._value, series: dict.get(dict: {_dict(series)}, key: r.series, default: r.series)}}))
  |> group(columns: ["series"])
  |> aggregateWindow(every: v.windowPeriod, fn: {fn}, createEmpty: false)
'''


def fingrid_other():
    return '''from(bucket: "home")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "fingrid" and r._field == "value")
  |> filter(fn: (r) => r.series == "production" or r.series == "nuclear" or r.series == "hydro" or r.series == "wind")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["series"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.production and exists r.nuclear and exists r.hydro and exists r.wind)
  |> map(fn: (r) => ({_time: r._time, _value: r.production - r.nuclear - r.hydro - r.wind, series: "Other"}))
  |> group(columns: ["series"])'''


def household_co2(start="v.timeRangeStart", stop="v.timeRangeStop"):
    """kg CO₂ per hour: metered kWh × the hour's consumption CO₂ intensity (backfilled hourly + live 3-min)."""
    return f'''
hourly = from(bucket: "home")
  |> range(start: {start}, stop: {stop})
  |> filter(fn: (r) => r._measurement == "fingrid_hourly" and r.series == "co2_consumption")
live = from(bucket: "home")
  |> range(start: {start}, stop: {stop})
  |> filter(fn: (r) => r._measurement == "fingrid" and r.series == "co2_consumption")
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false, timeSrc: "_start")
co2 = union(tables: [hourly, live])
  |> group()
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false, timeSrc: "_start")
  |> set(key: "_field", value: "c")
use = from(bucket: "home")
  |> range(start: {start}, stop: {stop})
  |> filter(fn: (r) => r._measurement == "electricity" and r._field == "kwh")
  |> group()
  |> keep(columns: ["_time", "_value"])
  |> set(key: "_field", value: "u")
union(tables: [use, co2])
  |> keep(columns: ["_time", "_field", "_value"])
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.u and exists r.c)
  |> map(fn: (r) => ({{_time: r._time, _value: r.u * r.c / 1000.0}}))'''


PLUG_OVERRIDES = fixed_colors(PLUG_COLORS)
BY_DEVICE = "${__field.labels.device}"
BY_PLACE = "${__field.labels.place}"
PRICE_THR = thresholds(GOOD, (6, WARN), (12, BAD))


def energy_dashboard():
    d = Dashboard("home-energy", "Home · Energy & water", folder="Home", tags=["home"], time="now-24h",
                  description="Smart-plug power, what it costs at the Nord Pool spot price, and water use.")

    d.row("Right now")
    d.add(stat("Spot price now", [flux(TZ + prices(start="-2h", stop="now()") + "\n  |> last()")],
               unit="c/kWh", decimals=2, thr=PRICE_THR, graph=False,
               desc="Nord Pool hourly spot price for Finland (excl. transfer and margin)."), 4, 4)
    d.add(stat("Metered plugs now", [flux('''from(bucket: "home")
  |> range(start: -10m)
  |> filter(fn: (r) => r._measurement == "mqtt_consumer" and r.integration_id == "tuya" and r._field == "raw_19")
  |> group(columns: ["name"]) |> last()
  |> map(fn: (r) => ({r with _value: float(v: r._value) / 10.0}))
  |> group() |> sum()''')], unit="watt", decimals=0, graph=False,
               desc="Sum of the Tuya smart plugs only, not the whole house."), 4, 4)
    d.add(stat("Plug energy today", [flux(TZ + power(every="1h", start="today()", stop="now()", time_src="_start")
                                          + '\n  |> group() |> sum() |> map(fn: (r) => ({r with _value: r._value / 1000.0}))')],
               unit="kwatth", decimals=2, graph=False), 4, 4)
    d.add(stat("Plug cost today", [flux(TZ + cost(start="today()", stop="now()") + "\n  |> group() |> sum()")],
               unit="currencyEUR", decimals=2, graph=False), 4, 4)
    d.add(stat("Plug cost this month", [flux(TZ + 'import "date"\n'
                                             + cost(start='date.truncate(t: now(), unit: 1mo)', stop="now()")
                                             + "\n  |> group() |> sum()")],
               unit="currencyEUR", decimals=2, graph=False), 4, 4)
    d.add(stat("Water today", [flux(TZ + water(start="today()", stop="now()") + "\n  |> sum()")],
               unit="litre", decimals=0, graph=False), 4, 4)

    d.row("Electricity price")
    d.add(barchart("Spot price today and tomorrow", [flux(TZ + 'import "date"\n' + prices(
        start="today()", stop="date.add(d: 2d, to: today())"))],
        unit="c/kWh", decimals=1, thr=PRICE_THR, color={"mode": "thresholds"}, x_field="_time",
        label_spacing=100, display_name="Price",
        desc="Tomorrow's prices appear after Nord Pool publishes them, around 14:00."), 14, 8)
    d.add(status_history("Price by hour of day (last 30 days)", [flux(TZ + 'import "date"\n' + prices(
        start="-30d", stop="now()") + '''
  |> map(fn: (r) => ({_time: date.truncate(t: r._time, unit: 1d), _value: r._value,
      hour: if date.hour(t: r._time) < 10 then "0" + string(v: date.hour(t: r._time)) else string(v: date.hour(t: r._time))}))
  |> group(columns: ["hour"])
  |> sort(columns: ["_time"])''')], unit="c/kWh", decimals=1, color={"mode": "continuous-blues"},
        display_name="${__field.labels.hour}:00", row_height=1, interval="1d", time_from="30d",
        desc="Each row is an hour of the day, each column a day; darker is more expensive."), 10, 8)

    d.row("Power")
    d.add(timeseries("Power by plug", [flux(power())], unit="watt", stack=True, fill=40, gradient="none",
                     display_name=BY_DEVICE, overrides=PLUG_OVERRIDES, legend="right", legend_calcs=["mean", "max"],
                     decimals=0), 16, 9)
    d.add(bargauge("Cost by plug this month", [flux(TZ + 'import "date"\n' + cost(
        start='date.truncate(t: now(), unit: 1mo)', stop="now()") + "\n  |> sum()")],
        unit="currencyEUR", decimals=2, display_name=BY_DEVICE, color={"mode": "fixed", "fixedColor": SLOTS[0]},
        reduce="sum", desc="Spot-price cost of each plug's metered energy since the 1st."), 8, 9)
    d.add(timeseries("Daily energy by plug", [flux(TZ + power(every="1h", time_src="_start") + '''
  |> aggregateWindow(every: 1d, fn: sum, createEmpty: false, timeSrc: "_start")
  |> map(fn: (r) => ({r with _value: r._value / 1000.0}))''')], unit="kwatth", bars=True, stack=True,
                     display_name=BY_DEVICE, overrides=PLUG_OVERRIDES, time_from="30d", decimals=2), 12, 8)
    d.add(timeseries("Daily cost by plug", [flux(TZ + cost() + '''
  |> aggregateWindow(every: 1d, fn: sum, createEmpty: false, timeSrc: "_start")''')],
                     unit="currencyEUR", bars=True, stack=True, display_name=BY_DEVICE, overrides=PLUG_OVERRIDES,
                     time_from="30d", decimals=2), 12, 8)

    d.row("Fridge & freezer health")
    d.add(timeseries("Compressor duty cycle", [flux(TZ + '''from(bucket: "home")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "mqtt_consumer" and r.integration_id == "tuya" and r._field == "raw_19")
  |> filter(fn: (r) => r.name == "Fridge" or r.name == "Freezer")
  |> map(fn: (r) => ({_time: r._time, device: r.name, _value: if float(v: r._value) > 200.0 then 100.0 else 0.0}))
  |> group(columns: ["device"])
  |> aggregateWindow(every: 1d, fn: mean, createEmpty: false, timeSrc: "_start")''')],
                     unit="percent", minv=0, maxv=100, display_name=BY_DEVICE, overrides=PLUG_OVERRIDES,
                     time_from="60d", points="always", fill=0,
                     desc="Share of each day the compressor drew over 20 W. A steady climb means a door seal "
                          "or condenser problem; a one-day spike means a door was left open."), 12, 8)
    d.add(timeseries("Fridge & freezer temperature", [flux(ble("tempc", COLD))], unit="celsius", decimals=1,
                     display_name=BY_PLACE, overrides=fixed_colors({"Fridge": SLOTS[0], "Freezer": SLOTS[1]}),
                     time_from="7d", fill=0,
                     desc="Fridge should sit at 2–6 °C, the freezer at −16 °C or colder."), 12, 8)

    d.row("Whole house electricity (Datahub)")
    d.add(stat("Last 12 months", [flux(electricity(start="-365d", stop="now()") + "\n  |> sum()")], unit="kwatth",
               decimals=0, graph=False, desc="From Fingrid Datahub exports, imported with scripts/datahub-import.py. "
                                             "Datahub data arrives 1–2 days late and only when imported."), 4, 6)
    d.add(stat("Expected from weather", [flux(expected_electricity(start="-365d", stop="now()") + "\n  |> sum()")],
               unit="kwatth", decimals=0, graph=False, thr=thresholds(MUTED),
               desc=f"What the 2021–2024 house would have used in the same weather: {ELEC_BASE} kWh/day plus "
                    f"{ELEC_SLOPE} kWh for each degree the daily mean is below {ELEC_BALANCE} °C."), 4, 6)
    d.add(timeseries("Electricity per day", [flux(electricity(every="1d")), flux(expected_electricity())],
                     unit="kwatth", decimals=0, time_from="1y", display_name="${__field.labels.series}",
                     overrides=fixed_colors({"Used": SLOTS[0], "Expected from weather": MUTED})
                     + [by_name("Used", custom__drawStyle="bars", custom__fillOpacity=80, custom__lineWidth=1)],
                     desc="Bars: the house's metered use. Line: what the weather model expects."), 16, 6)
    d.add(timeseries("Use vs weather-expected (30-day)", [flux(TZ + f'''
used =
{electricity(every="1d")}
  |> group()
  |> timedMovingAverage(every: 1d, period: 30d)
exp =
{expected_electricity()}
  |> group()
  |> timedMovingAverage(every: 1d, period: 30d)
join(tables: {{u: used, e: exp}}, on: ["_time"])
  |> map(fn: (r) => ({{_time: r._time, _value: r._value_u / r._value_e}}))
''')],
                     unit="percentunit", decimals=0, time_from="6y", display_name="Used ÷ expected",
                     color={"mode": "fixed", "fixedColor": SLOTS[3]}, thresholds_style="dashed",
                     thr=thresholds(MUTED, (1, MUTED)),
                     desc="Above 100 % means more electricity than the 2021–2024 house used in the same "
                          "weather: a new always-on load, the electric heater, or a less efficient heat pump."), 12, 8)
    d.add(timeseries("Night base load", [flux(TZ + 'import "date"\n' + electricity() + '''
  |> filter(fn: (r) => date.hour(t: r._time) >= 2 and date.hour(t: r._time) < 5)
  |> aggregateWindow(every: 1w, fn: median, createEmpty: false, timeSrc: "_start")''')],
                     unit="kwatt", decimals=2, time_from="6y", display_name="02–05 median",
                     color={"mode": "fixed", "fixedColor": SLOTS[4]},
                     desc="Median hourly use between 02 and 05, per week. In summer this is the always-on load "
                          "(fridge, freezer, network, homelab, ventilation)."), 12, 8)

    d.row("Finnish grid (Fingrid)")
    d.add(stat("CO₂ now", [flux(fingrid({"co2_consumption": "Consumed"}, start="-30m", stop="now()", fn="last")
                                + "\n  |> last()")], decimals=0, graph=False,
               thr=thresholds(GOOD, (50, WARN), (100, BAD)),
               desc="Grams of CO₂ per kWh of electricity consumed in Finland, including imports (Fingrid, 3 min)."),
          4, 5)
    d.add(stat("Grid state", [flux(fingrid({"system_state": "System", "shortage_status": "Shortage"}, start="-30m",
                                           stop="now()", fn="last") + "\n  |> last()")],
               graph=False, text_mode="value_and_name", color_mode="background", display_name="${__field.labels.series}",
               mappings=value_map([(0, "Normal", GOOD), (1, "Normal", GOOD), (2, "Endangered", WARN),
                                   (3, "Disturbed", BAD), (4, "Serious", BAD), (5, "Restoring", WARN)]),
               overrides=[by_name("Shortage", mappings=value_map([(0, "No shortage", GOOD), (1, "Possible", WARN),
                                                                  (2, "High risk", BAD), (3, "Shortage", BAD)]))],
               thr=thresholds(MUTED), desc="Fingrid's power system state and electricity shortage status."), 8, 5)
    d.add(stat("Your CO₂, last 12 months", [flux(household_co2(start="-365d", stop="now()") + "\n  |> sum()")],
               unit="masskg", decimals=0, graph=False, thr=thresholds(SLOTS[4]),
               desc="Metered use each hour times that hour's CO₂ intensity of consumption."), 6, 5)
    d.add(stat("Import / export", [flux(fingrid({"net_import": "Net import"}, start="-30m", stop="now()", fn="last")
                                        + "\n  |> last()")], unit="megwatt", decimals=0, graph=False,
               thr=thresholds(SLOTS[0]), desc="Positive: Finland imports electricity; negative: exports."), 6, 5)
    d.add(timeseries("Production by source", [flux(fingrid({"nuclear": "Nuclear", "hydro": "Hydro", "wind": "Wind"})),
                                               flux(TZ + fingrid_other())],
                     unit="megwatt", stack=True, fill=60, gradient="none", decimals=0, legend="right",
                     display_name="${__field.labels.series}",
                     overrides=fixed_colors({"Nuclear": SLOTS[6], "Hydro": SLOTS[0], "Wind": SLOTS[2],
                                             "Other": MUTED}), time_from="7d"), 16, 8)
    d.add(timeseries("CO₂ intensity", [flux(fingrid({"co2_consumption": "Consumed", "co2_production": "Produced"}))],
                     decimals=0, fill=0, display_name="${__field.labels.series}", time_from="7d",
                     overrides=fixed_colors({"Consumed": SLOTS[4], "Produced": SLOTS[3]}),
                     desc="gCO₂/kWh. Consumed includes imports."), 8, 8)
    d.add(timeseries("Your electricity CO₂ per month", [flux(TZ + household_co2() + '''
  |> aggregateWindow(every: 1mo, fn: sum, createEmpty: false, timeSrc: "_start")''')],
                     unit="masskg", bars=True, decimals=0, display_name="CO₂", time_from="6y",
                     color={"mode": "fixed", "fixedColor": SLOTS[4]},
                     desc="The Finnish grid got much cleaner after Olkiluoto 3 and the wind build-out: "
                          "similar use, a third of the emissions."), 24, 7)

    d.row("Water")
    d.add(timeseries("Water per hour", [flux(TZ + water() + '''
  |> aggregateWindow(every: 1h, fn: sum, createEmpty: true, timeSrc: "_start")''')],
                     unit="litre", bars=True, display_name="Water", decimals=0,
                     color={"mode": "fixed", "fixedColor": SLOTS[0]}), 12, 8)
    d.add(timeseries("Water per day", [flux(TZ + water() + '''
  |> aggregateWindow(every: 1d, fn: sum, createEmpty: false, timeSrc: "_start")''')],
                     unit="litre", bars=True, display_name="Water", decimals=0, time_from="60d",
                     color={"mode": "fixed", "fixedColor": SLOTS[0]}), 6, 8)
    d.add(timeseries("Night-time water (02–05)", [flux(TZ + 'import "date"\n' + water() + '''
  |> filter(fn: (r) => date.hour(t: r._time) >= 2 and date.hour(t: r._time) < 5)
  |> aggregateWindow(every: 1d, fn: sum, createEmpty: true, timeSrc: "_start")''')],
                     unit="litre", bars=True, display_name="Night use", decimals=1, time_from="60d",
                     thr=thresholds(GOOD, (5, WARN), (20, BAD)), color={"mode": "thresholds"},
                     desc="Water used between 02:00 and 05:00. A few litres every night points at a "
                          "running toilet or a dripping tap."), 6, 8)
    return d


def climate_dashboard():
    d = Dashboard("home-climate", "Home · Climate", folder="Home", tags=["home"], time="now-48h",
                  description="SwitchBot temperature/humidity sensors indoors, outdoors and around the 3D printer.")

    d.row("Now")
    d.add(stat("Indoors", [flux(ble("tempc", INDOOR, start="-3h", stop="now()") + "\n  |> last()")],
               unit="celsius", decimals=1, display_name=BY_PLACE, graph=False, text_mode="value_and_name",
               thr=thresholds(SLOTS[0], (19, GOOD), (24, WARN), (26, BAD)), color_mode="value"), 16, 4)
    d.add(stat("Outdoors", [flux(ble("tempc", {OUTSIDE_ID: "Outside"}, start="-3h", stop="now()") + "\n  |> last()")],
               unit="celsius", decimals=1, graph=False, display_name="Outside",
               thr=thresholds(SLOTS[0])), 4, 4)
    d.add(stat("Indoor humidity", [flux(ble("hum", INDOOR, start="-3h", stop="now()") + '''
  |> last() |> group() |> mean()''')], unit="humidity", decimals=0, graph=False,
               thr=thresholds(WARN, (30, GOOD), (60, WARN)), desc="Average of the indoor sensors; 30–60 % is comfortable."), 4, 4)

    d.row("Indoors")
    d.add(timeseries("Temperature", [flux(ble("tempc", INDOOR))], unit="celsius", decimals=1, fill=0,
                     display_name=BY_PLACE, overrides=fixed_colors(dict(zip(INDOOR.values(), SLOTS))),
                     legend="right", legend_calcs=["min", "max", "lastNotNull"]), 16, 9)
    d.add(table("Today", [flux(TZ + ble("tempc", INDOOR, start="today()", stop="now()", every="10m"))],
                unit="celsius", decimals=1, display_name=BY_PLACE,
                transformations=[reduce_rows(["min", "max"]),
                                 organize(rename={"Field": "Room", "Min": "Low", "Max": "High"})],
                sort=("Room", False)), 8, 9)
    d.add(timeseries("Relative humidity", [flux(ble("hum", INDOOR))], unit="humidity", decimals=0, fill=0,
                     display_name=BY_PLACE, overrides=fixed_colors(dict(zip(INDOOR.values(), SLOTS))),
                     legend="right"), 16, 8)
    d.add(timeseries("Outdoor air heated to 22 °C", [flux('''import "math"
from(bucket: "home")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "mqtt_consumer" and r.device_id == "''' + OUTSIDE_ID + '''")
  |> filter(fn: (r) => r._field == "tempc" or r._field == "hum")
  |> map(fn: (r) => ({r with _value: float(v: r._value)}))
  |> group(columns: ["_field"])
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> group()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.tempc and exists r.hum)
  |> map(fn: (r) => ({_time: r._time, _value: r.hum * math.exp(x: 0.0624 * (r.tempc - 22.0))}))''')],
                     unit="humidity", decimals=0, display_name="RH at 22 °C", fill=10,
                     thr=thresholds(BAD, (25, WARN), (35, GOOD)), thresholds_style="line+area",
                     color={"mode": "fixed", "fixedColor": SLOTS[0]},
                     desc="What outdoor air becomes once warmed indoors: when this is low, ventilation "
                          "dries the house out and a humidifier helps."), 8, 8)

    d.row("Outdoors")
    d.add(timeseries("Temperature", [flux(ble("tempc", OUTDOOR))], unit="celsius", decimals=1, fill=0,
                     display_name=BY_PLACE, overrides=fixed_colors(dict(zip(OUTDOOR.values(), SLOTS))),
                     legend="right", legend_calcs=["min", "max", "lastNotNull"]), 16, 8)
    d.add(timeseries("Daily range outside", [flux(TZ + ble("tempc", {OUTSIDE_ID: "Low"}, every="1d", fn="min")),
                                             flux(TZ + ble("tempc", {OUTSIDE_ID: "High"}, every="1d", fn="max"))],
                     unit="celsius", decimals=1, display_name=BY_PLACE, time_from="60d", fill=0, points="always",
                     overrides=fixed_colors({"Low": SLOTS[0], "High": SLOTS[1]})), 8, 8)

    d.row("Weather (FMI)")
    d.add(barchart("Forecast, next 48 hours", [flux(TZ + fmi_forecast("Temperature"))], unit="celsius", decimals=0,
                   thr=thresholds(SLOTS[0], (0, SLOTS[2]), (15, SLOTS[1])), color={"mode": "thresholds"},
                   label_spacing=100, desc="The Finnish Meteorological Institute's forecast for home, updated hourly."),
          16, 7)
    d.add(barchart("Rain, next 48 hours", [flux(TZ + fmi_forecast("Precipitation1h"))], unit="lengthmm",
                   decimals=1, color={"mode": "fixed", "fixedColor": SLOTS[0]}, label_spacing=100), 8, 7)
    d.add(timeseries("Outside: own sensor vs FMI", [
        flux(ble("tempc", {OUTSIDE_ID: "Front yard sensor"})),
        flux(fmi_observed({"t2m": "FMI Espoo Tapiola"}))],
        unit="celsius", decimals=1, fill=0, display_name=BY_PLACE,
        overrides=fixed_colors({"Front yard sensor": SLOTS[1], "FMI Espoo Tapiola": SLOTS[0]}),
        desc="The front-yard SwitchBot next to the nearest official station, about 4 km away. Sun on the "
             "sensor shows up as afternoon peaks."), 12, 8)
    d.add(timeseries("Wind", [flux(fmi_observed({"ws_10min": "Wind", "wg_10min": "Gusts"}))], unit="velocityms",
                     decimals=1, fill=0, display_name=BY_PLACE,
                     overrides=fixed_colors({"Wind": SLOTS[0], "Gusts": SLOTS[3]})), 6, 8)
    d.add(timeseries("Rain", [flux(fmi_observed({"r_1h": "Rain"}, every="1h", fn="max"))], unit="lengthmm",
                     decimals=1, bars=True, display_name="Rain", color={"mode": "fixed", "fixedColor": SLOTS[0]},
                     desc="Hourly precipitation at the station."), 6, 8)

    return d


def fmi_observed(params, every="v.windowPeriod", fn="mean"):
    """FMI station observations (measurement fmi_observation), one series per param, labelled `place`."""
    return f'''
import "dict"
from(bucket: "home")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "fmi_observation" and r._field == "value")
  |> filter(fn: (r) => {" or ".join(f'r.param == "{k}"' for k in params)})
  |> map(fn: (r) => ({{_time: r._time, _value: r._value, place: dict.get(dict: {_dict(params)}, key: r.param, default: r.param)}}))
  |> group(columns: ["place"])
  |> aggregateWindow(every: {every}, fn: {fn}, createEmpty: false)
'''


def fmi_forecast(param):
    """The latest FMI forecast for home, from the current hour on."""
    return f'''import "date"
from(bucket: "home")
  |> range(start: date.truncate(t: now(), unit: 1h), stop: 48h)
  |> filter(fn: (r) => r._measurement == "fmi_forecast" and r._field == "value" and r.param == "{param}")
  |> keep(columns: ["_time", "_value"])'''


def devices_dashboard():
    d = Dashboard("home-devices", "Home · Devices", folder="Home", tags=["home"], time="now-7d", refresh="5m",
                  description="Batteries, radio links and silent sensors.")

    battery_q = f'''import "dict"
names = {_dict(ALL_BLE)}
ble = from(bucket: "home")
  |> range(start: -14d)
  |> filter(fn: (r) => r._measurement == "mqtt_consumer" and r._field == "batt")
  |> filter(fn: (r) => {_ids(ALL_BLE)})
  |> group(columns: ["device_id"]) |> last()
  |> map(fn: (r) => ({{device: dict.get(dict: names, key: r.device_id, default: r.device_id), kind: "SwitchBot (BLE)", battery: float(v: r._value), seen: r._time}}))
zigbee = from(bucket: "home")
  |> range(start: -14d)
  |> filter(fn: (r) => r._measurement == "mqtt_consumer" and r.integration_id == "zigbee2mqtt" and r._field == "battery")
  |> group(columns: ["device_id"]) |> last()
  |> map(fn: (r) => ({{device: r.device_id, kind: "Zigbee", battery: float(v: r._value), seen: r._time}}))
union(tables: [ble, zigbee])
  |> group()
  |> map(fn: (r) => ({{r with hours: float(v: int(v: now()) - int(v: r.seen)) / 3600000000000.0}}))
  |> sort(columns: ["battery"])'''
    d.row("Batteries")
    d.add(table("Batteries (lowest first)", [flux(battery_q)], cell_height="sm",
                overrides=[
                    by_name("battery", unit="percent", displayName="Battery", min=0, max=100,
                            thresholds=thresholds(BAD, (15, WARN), (30, GOOD)),
                            custom__cellOptions={"type": "gauge", "mode": "basic", "valueDisplayMode": "text"},
                            custom__width=260),
                    by_name("seen", unit="dateTimeFromNow", displayName="Last report"),
                    by_name("hours", displayName="Hours silent", decimals=1,
                            thresholds=thresholds(GOOD, (3, WARN), (24, BAD)),
                            custom__cellOptions={"type": "color-text"}),
                    by_name("device", displayName="Device"),
                    by_name("kind", displayName="Type"),
                ], transformations=[organize(order=["device", "kind", "battery", "seen", "hours"])]), 14, 16)
    d.add(stat("Below 20 %", [flux(battery_q + '\n  |> filter(fn: (r) => r.battery < 20.0) |> count(column: "battery")')],
               graph=False, thr=thresholds(GOOD, (1, WARN)), nulls="0", desc="Devices to swap batteries in soon."), 5, 4)
    d.add(stat("Silent for 24 h+", [flux(battery_q + '\n  |> filter(fn: (r) => r.hours > 24.0) |> count(column: "hours")')],
               graph=False, thr=thresholds(GOOD, (1, BAD)), nulls="0",
               desc="Battery devices that reported nothing for a day: dead battery or out of range."), 5, 4)
    d.add(bargauge("Weakest Zigbee links", [flux('''from(bucket: "home")
  |> range(start: -1d)
  |> filter(fn: (r) => r._measurement == "mqtt_consumer" and r.integration_id == "zigbee2mqtt" and r._field == "linkquality")
  |> group(columns: ["device_id"]) |> mean()
  |> group() |> sort(columns: ["_value"]) |> limit(n: 12)
  |> map(fn: (r) => ({_value: r._value, device: r.device_id}))''')], unit="none", decimals=0, minv=0, maxv=255,
                   display_name="${__field.labels.device}", values=True,
                   thr=thresholds(BAD, (40, WARN), (80, GOOD)),
                   desc="Average link quality over the last day (0–255). Under ~40 the device needs a closer router."),
          10, 12)
    d.row("BLE sensors")
    d.add(timeseries("Sensor batteries", [flux(ble("batt", ALL_BLE, every="12h"))], unit="percent", minv=0, maxv=100,
                     display_name=BY_PLACE, time_from="30d", fill=0, legend="right"), 24, 9)
    return d


def overview_house(d):
    """House tiles for the top-level overview dashboard."""
    d.add(stat("Spot price", [flux(TZ + prices(start="-2h", stop="now()") + "\n  |> last()")], unit="c/kWh",
               decimals=2, thr=PRICE_THR, graph=False,
               links=[{"title": "Energy", "url": "/d/home-energy"}]), 3, 4)
    d.add(stat("Plugs now", [flux('''from(bucket: "home")
  |> range(start: -10m)
  |> filter(fn: (r) => r._measurement == "mqtt_consumer" and r.integration_id == "tuya" and r._field == "raw_19")
  |> group(columns: ["name"]) |> last()
  |> map(fn: (r) => ({r with _value: float(v: r._value) / 10.0}))
  |> group() |> sum()''')], unit="watt", decimals=0, graph=False), 3, 4)
    d.add(stat("Plug cost today", [flux(TZ + cost(start="today()", stop="now()") + "\n  |> group() |> sum()")],
               unit="currencyEUR", decimals=2, graph=False), 3, 4)
    d.add(stat("Water today", [flux(TZ + water(start="today()", stop="now()") + "\n  |> sum()")], unit="litre",
               decimals=0, graph=False), 3, 4)
    d.add(stat("Outside", [flux(ble("tempc", {OUTSIDE_ID: "Outside"}, start="-3h", stop="now()") + "\n  |> last()")],
               unit="celsius", decimals=1, graph=False, display_name="Outside",
               links=[{"title": "Climate", "url": "/d/home-climate"}]), 3, 4)
    d.add(stat("Indoors (avg)", [flux(ble("tempc", INDOOR, start="-3h", stop="now()") + "\n  |> last() |> group() |> mean()")],
               unit="celsius", decimals=1, graph=False, thr=thresholds(SLOTS[0], (19, GOOD), (25, WARN))), 3, 4)
    d.add(stat("Freezer", [flux(ble("tempc", {"D43535302778": "Freezer"}, start="-3h", stop="now()") + "\n  |> last()")],
               unit="celsius", decimals=1, graph=False, thr=thresholds(GOOD, (-15, WARN), (-10, BAD))), 3, 4)
    d.add(stat("Low batteries", [flux(f'''from(bucket: "home")
  |> range(start: -3d)
  |> filter(fn: (r) => r._measurement == "mqtt_consumer" and ((r._field == "batt" and ({_ids(ALL_BLE)}))
      or (r._field == "battery" and r.integration_id == "zigbee2mqtt")))
  |> group(columns: ["device_id"]) |> last()
  |> group() |> filter(fn: (r) => float(v: r._value) < 20.0) |> count()''')], graph=False,
               thr=thresholds(GOOD, (1, WARN)), nulls="0", links=[{"title": "Devices", "url": "/d/home-devices"}]), 3, 4)
