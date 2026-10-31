"""Home dashboards: energy & water, climate, devices (TimescaleDB tsdb)."""

from lib import (BAD, GOOD, HEL, MONTH, MUTED, RANGE, SLOTS, TODAY, WARN, Dashboard, agg, barchart, bargauge,
                 bucket, by_name, fixed_colors, in_list, latest, lit, organize, reduce_rows, since, stat,
                 state_timeline, status_history, table, thresholds, timeseries, tsdb, value_map, values, where)

# SwitchBot BLE sensors (device_id -> place). Unlisted ids are neighbours'.
INDOOR = {
    "D7353530665A": "Living room", "D63534385106": "Office", "D83535301C43": "Upstairs office",
    "C76A05062842": "Bedroom", "D7353530520F": "Second bedroom", "D83431306571": "Upstairs bathroom",
    "CE2A82463674": "Downstairs bathroom",
}
OUTDOOR = {
    "D83534387029": "Front yard", "D9353438450D": "Backyard", "D4343037362D": "Patio",
    "C76A03460A73": "Storage", "C76A0246647E": "Car", "A4C138FD13EF": "Patio display",
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



def ble(field, names, every=None, rng=RANGE, fn="mean"):
    """One series per place; both BLE gateways report each sensor, so the mean merges them."""
    return f'''
SELECT {bucket(every)}, place, {agg(fn, field)} AS "Value"
FROM mqtt_consumer JOIN {values(names, "device_id", "place")} USING (device_id)
WHERE {where(rng)} AND device_id IN {in_list(names)} AND {field} IS NOT NULL
GROUP BY 1, 2 ORDER BY 1'''


def ble_now(field, names, interval="3 hours"):
    """The latest window of ble() per place."""
    return latest(ble(field, names, rng=since(interval)), "place")


def power(every=None, rng=RANGE, devices=None):
    """Mean W per Tuya plug (raw_19 is tenths of a watt), labelled `device`."""
    dev = f" AND name IN {in_list(devices)}" if devices else ""
    return f'''
SELECT {bucket(every)}, name AS device, avg(raw_19) / 10.0 AS "Value"
FROM mqtt_consumer
WHERE integration_id = 'tuya' AND raw_19 IS NOT NULL AND {where(rng)}{dev}
GROUP BY 1, 2 ORDER BY 1'''


def daily_energy(devices=None):
    """kWh per plug and Helsinki day: hourly mean W (= Wh) summed."""
    return f'''
SELECT time_bucket('1 day', time, {HEL}) AS time, device, sum("Value") / 1000 AS "Value"
FROM ({power("1 hour", devices=devices)}) h
GROUP BY 1, 2 ORDER BY 1'''


PLUGS_NOW = '''
SELECT sum(w) AS "Value" FROM (
  SELECT DISTINCT ON (name) raw_19 / 10.0 AS w FROM mqtt_consumer
  WHERE integration_id = 'tuya' AND raw_19 IS NOT NULL AND time > now() - interval '10 minutes'
  ORDER BY name, time DESC) l'''

PLUG_ENERGY_TODAY = f'''
SELECT sum("Value") / 1000 AS "Value" FROM ({power("1 hour", rng=TODAY)}) h'''


def prices(rng=RANGE):
    """Nord Pool spot price (c/kWh incl. VAT), hourly mean."""
    return f'''
SELECT time_bucket('1 hour', time) AS time, avg(price) AS "Value"
FROM nordpool_price WHERE {where(rng)}
GROUP BY 1 ORDER BY 1'''


def cost(rng=RANGE):
    """Hourly € per plug: mean W over an hour is Wh; price is c/kWh."""
    return f'''
WITH energy AS ({power("1 hour", rng=rng)}),
price AS ({prices(rng)})
SELECT time, device, energy."Value" / 1000 * price."Value" / 100 AS "Value"
FROM energy JOIN price USING (time) ORDER BY 1'''


def total(query):
    """Sum of every row of a query (Flux group() |> sum())."""
    return f'SELECT sum("Value") AS "Value" FROM ({query}) q'


def daily_cost():
    return f'''
SELECT time_bucket('1 day', time, {HEL}) AS time, device, sum("Value") AS "Value"
FROM ({cost()}) c GROUP BY 1, 2 ORDER BY 1'''


PRICE_TODAY_TOMORROW = prices(TODAY + " AND {t} < date_trunc('day', now(), 'Europe/Helsinki') + interval '2 days'")
PRICE_NOW = f'''
SELECT * FROM ({prices("{t} > now() - interval '2 hours' AND {t} <= now()")}) p ORDER BY time DESC LIMIT 1'''
PRICE_BY_HOUR = '''
SELECT time_bucket('1 day', time, 'Europe/Helsinki') AS time,
       to_char(time_bucket('1 hour', time) AT TIME ZONE 'Europe/Helsinki', 'HH24') AS hour, avg(price) AS "Value"
FROM nordpool_price WHERE time > now() - interval '30 days' AND time <= now()
GROUP BY 1, 2 ORDER BY 1'''


def water(rng=RANGE, every="1 hour", extra=""):
    """Litres per period, from the meter's `rate` field (litres since the previous reading)."""
    return f'''
SELECT {bucket(every)}, sum(rate) AS "Value"
FROM mqtt_consumer
WHERE integration_id IS NULL AND device_id IS NULL AND name = 'watermeter' AND {where(rng)}
  AND rate > -20 AND rate < 20{extra}
GROUP BY 1 ORDER BY 1'''


WATER_TODAY = f'SELECT sum("Value") AS "Value" FROM ({water(TODAY)}) w'
NIGHT = " AND extract(hour FROM time AT TIME ZONE 'Europe/Helsinki') BETWEEN 2 AND 4"

# Weather model of daily electricity use, fitted on Datahub 2021–2024 against FMI daily mean temperature (R² 0.8).
ELEC_BASE, ELEC_SLOPE, ELEC_BALANCE = 19.9, 1.86, 10.5


def electricity(every=None, rng=RANGE):
    """Metered whole-house kWh (Datahub, hourly), optionally summed per period as series "Used"."""
    if not every:
        return f'''
SELECT time, kwh AS "Value" FROM electricity WHERE {where(rng)} ORDER BY 1'''
    return f'''
SELECT {bucket(every)}, 'Used' AS series, sum(kwh) AS "Value"
FROM electricity WHERE {where(rng)}
GROUP BY 1 ORDER BY 1'''


def expected_electricity(rng=RANGE):
    """Daily kWh the weather model expects, stamped at local midnight like electricity("1 day")."""
    return f'''
SELECT time_bucket('1 day', time, {HEL}) AS time, 'Expected from weather' AS series,
       {ELEC_BASE} + {ELEC_SLOPE} * greatest({ELEC_BALANCE} - value, 0) AS "Value"
FROM fmi_daily WHERE param = 'tday' AND {where(rng)} ORDER BY 1'''


def fingrid(series, every=None, rng=RANGE, fn="mean"):
    """Fingrid 3-minute data, one series per name, relabelled."""
    return f'''
SELECT {bucket(every)}, n.label AS series, {agg(fn, "value")} AS "Value"
FROM fingrid JOIN {values(series, "code", "label")} ON fingrid.series = n.code
WHERE {where(rng)} AND fingrid.series IN {in_list(series)}
GROUP BY 1, 2 ORDER BY 1'''


def fingrid_now(series):
    """The latest value of each series, within half an hour."""
    return f'''
SELECT * FROM (
  SELECT DISTINCT ON (fingrid.series) time, n.label AS series, value AS "Value"
  FROM fingrid JOIN {values(series, "code", "label")} ON fingrid.series = n.code
  WHERE time > now() - interval '30 minutes' AND fingrid.series IN {in_list(series)}
  ORDER BY fingrid.series, time DESC) l ORDER BY time'''


FINGRID_OTHER = '''
SELECT * FROM (
  SELECT $__timeGroupAlias(time, $__interval), 'Other' AS series,
         avg(value) FILTER (WHERE series = 'production') - avg(value) FILTER (WHERE series = 'nuclear')
         - avg(value) FILTER (WHERE series = 'hydro') - avg(value) FILTER (WHERE series = 'wind') AS "Value"
  FROM fingrid
  WHERE $__timeFilter(time) AND series IN ('production', 'nuclear', 'hydro', 'wind')
  GROUP BY 1) o
WHERE "Value" IS NOT NULL ORDER BY 1'''


def household_co2(rng=RANGE):
    """kg CO₂ per hour: metered kWh × the hour's consumption CO₂ intensity (backfilled hourly + live 3-min)."""
    return f'''
WITH co2 AS (
  SELECT hour, avg(c) AS c FROM (
    SELECT time_bucket('1 hour', time) AS hour, value AS c
    FROM fingrid_hourly WHERE series = 'co2_consumption' AND {where(rng)}
    UNION ALL
    SELECT time_bucket('1 hour', time), avg(value)
    FROM fingrid WHERE series = 'co2_consumption' AND {where(rng)} GROUP BY 1) u
  GROUP BY 1)
SELECT e.time, e.kwh * co2.c / 1000 AS "Value"
FROM electricity e JOIN co2 ON co2.hour = e.time
WHERE {where(rng, "e.time")} ORDER BY 1'''


SPOT_NOW = tsdb(PRICE_NOW)
SPOT_TODAY_TOMORROW = tsdb(PRICE_TODAY_TOMORROW)
COST_TODAY = tsdb(total(cost(TODAY)), fmt="table")
COST_MONTH = tsdb(total(cost(MONTH)), fmt="table")


# Verisure alarm (verisure-bridge -> MQTT -> telegraf-cloud): table verisure, tag topic, text field.
ALARM = value_map([("armed_away", "Armed away", SLOTS[1]), ("armed_home", "Armed stay", SLOTS[3]),
                   ("armed", "Armed", SLOTS[1]), ("disarmed", "Disarmed", MUTED)])
ALARM_MODE = "topic = 'home/verisure/mode' AND time > now() - interval '365 days'"


def alarm(last=False):
    """The alarm mode over the range, carrying the state from before the range in (points only exist on
    change). Text values, so table format."""
    if last:
        return tsdb(f'''
SELECT time, value AS "Value" FROM verisure WHERE {ALARM_MODE} ORDER BY time DESC LIMIT 1''', fmt="table")
    return tsdb(f'''
(SELECT $__timeFrom()::timestamptz AS time, value AS "Value" FROM verisure
 WHERE {ALARM_MODE} AND time < $__timeFrom() ORDER BY time DESC LIMIT 1)
UNION ALL
(SELECT time, value FROM verisure WHERE {ALARM_MODE} AND $__timeFilter(time))
ORDER BY 1''', fmt="table")


def overview_house_extra(d, showers_left, co2_now):
    """Second line of house tiles on the overview."""
    d.add(stat("Alarm", [alarm(last=True)], graph=False, mappings=ALARM, color_mode="background",
               fields="/^Value$/",
               thr=thresholds(MUTED), desc="Verisure, from the app's push notifications (verisure-bridge).",
               links=[{"title": "Devices", "url": "/d/home-devices"}]), 3, 4)
    d.add(state_timeline("Alarm, last 24 h", [alarm()], mappings=ALARM, color={"mode": "thresholds"},
                         display_name="Alarm",
                         thr=thresholds(MUTED), time_from="24h", show_values="auto"), 9, 4)
    d.add(showers_left, 3, 4)
    d.add(co2_now, 3, 4)


PLUG_OVERRIDES = fixed_colors(PLUG_COLORS)
BY_DEVICE = "${__field.labels.device}"
BY_PLACE = "${__field.labels.place}"
PRICE_THR = thresholds(GOOD, (6, WARN), (12, BAD))


def energy_dashboard():
    d = Dashboard("home-energy", "Home · Energy & water", folder="Home", tags=["home"], time="now-24h",
                  description="Smart-plug power, what it costs at the Nord Pool spot price, and water use.")

    d.row("Right now")
    d.add(stat("Spot price now", [SPOT_NOW],
               unit="c/kWh", decimals=2, thr=PRICE_THR, graph=False,
               desc="Nord Pool hourly spot price for Finland (excl. transfer and margin)."), 4, 4)
    d.add(stat("Metered plugs now", [tsdb(PLUGS_NOW, fmt="table")], unit="watt", decimals=0, graph=False,
               desc="Sum of the Tuya smart plugs only, not the whole house."), 4, 4)
    d.add(stat("Plug energy today", [tsdb(PLUG_ENERGY_TODAY, fmt="table")],
               unit="kwatth", decimals=2, graph=False), 4, 4)
    d.add(stat("Plug cost today", [COST_TODAY],
               unit="currencyEUR", decimals=2, graph=False), 4, 4)
    d.add(stat("Plug cost this month", [COST_MONTH],
               unit="currencyEUR", decimals=2, graph=False), 4, 4)
    d.add(stat("Water today", [tsdb(WATER_TODAY, fmt="table")],
               unit="litre", decimals=0, graph=False), 4, 4)

    d.row("Electricity price")
    d.add(barchart("Spot price today and tomorrow", [SPOT_TODAY_TOMORROW],
        unit="c/kWh", decimals=1, thr=PRICE_THR, color={"mode": "thresholds"}, x_field="_time",
        label_spacing=100, display_name="Price",
        desc="Tomorrow's prices appear after Nord Pool publishes them, around 14:00."), 14, 8)
    d.add(status_history("Price by hour of day (last 30 days)", [tsdb(PRICE_BY_HOUR)], unit="c/kWh", decimals=1, color={"mode": "continuous-blues"},
        display_name="${__field.labels.hour}:00", row_height=1, interval="1d", time_from="30d",
        desc="Each row is an hour of the day, each column a day; darker is more expensive."), 10, 8)

    d.row("Power")
    d.add(timeseries("Power by plug", [tsdb(power())], unit="watt", stack=True, fill=40, gradient="none",
                     display_name=BY_DEVICE, overrides=PLUG_OVERRIDES, legend="right", legend_calcs=["mean", "max"],
                     decimals=0), 16, 9)
    d.add(bargauge("Cost by plug this month", [tsdb(f'''
SELECT max(time) AS time, device, sum("Value") AS "Value" FROM ({cost(MONTH)}) c GROUP BY 2 ORDER BY 1''')],
        unit="currencyEUR", decimals=2, display_name=BY_DEVICE, color={"mode": "fixed", "fixedColor": SLOTS[0]},
        reduce="sum", desc="Spot-price cost of each plug's metered energy since the 1st."), 8, 9)
    d.add(timeseries("Daily energy by plug", [tsdb(daily_energy())], unit="kwatth", bars=True, stack=True,
                     display_name=BY_DEVICE, overrides=PLUG_OVERRIDES, time_from="30d", decimals=2), 12, 8)
    d.add(timeseries("Daily cost by plug", [tsdb(daily_cost())],
                     unit="currencyEUR", bars=True, stack=True, display_name=BY_DEVICE, overrides=PLUG_OVERRIDES,
                     time_from="30d", decimals=2), 12, 8)

    d.row("Fridge & freezer health")
    d.add(timeseries("Compressor duty cycle", [tsdb(f'''
SELECT time_bucket('1 day', time, {HEL}) AS time, name AS device,
       avg(CASE WHEN raw_19 > 200 THEN 100.0 ELSE 0 END) AS "Value"
FROM mqtt_consumer
WHERE integration_id = 'tuya' AND raw_19 IS NOT NULL AND name IN ('Fridge', 'Freezer') AND $__timeFilter(time)
GROUP BY 1, 2 ORDER BY 1''')],
                     unit="percent", minv=0, maxv=100, display_name=BY_DEVICE, overrides=PLUG_OVERRIDES,
                     time_from="60d", points="always", fill=0,
                     desc="Share of each day the compressor drew over 20 W. A steady climb means a door seal "
                          "or condenser problem; a one-day spike means a door was left open."), 12, 8)
    d.add(timeseries("Fridge & freezer temperature", [tsdb(ble("tempc", COLD))], unit="celsius", decimals=1,
                     display_name=BY_PLACE, overrides=fixed_colors({"Fridge": SLOTS[0], "Freezer": SLOTS[1]}),
                     time_from="7d", fill=0,
                     desc="Fridge should sit at 2–6 °C, the freezer at −16 °C or colder."), 12, 8)

    d.row("Whole house electricity (Datahub)")
    d.add(stat("Last 12 months", [tsdb(total(electricity(rng=since("365 days"))), fmt="table")], unit="kwatth",
               decimals=1, graph=False, desc="From Fingrid Datahub exports, imported with scripts/datahub-import.py. "
                                             "Datahub data arrives 1–2 days late and only when imported."), 4, 6)
    d.add(stat("Expected from weather", [tsdb(total(expected_electricity(since("365 days"))), fmt="table")],
               unit="kwatth", decimals=1, graph=False, thr=thresholds(MUTED),
               desc=f"What the 2021–2024 house would have used in the same weather: {ELEC_BASE} kWh/day plus "
                    f"{ELEC_SLOPE} kWh for each degree the daily mean is below {ELEC_BALANCE} °C."), 4, 6)
    d.add(timeseries("Electricity per day", [tsdb(electricity("1 day")), tsdb(expected_electricity())],
                     unit="kwatth", decimals=0, time_from="1y", display_name="${__field.labels.series}",
                     overrides=fixed_colors({"Used": SLOTS[0], "Expected from weather": MUTED})
                     + [by_name("Used", custom__drawStyle="bars", custom__fillOpacity=80, custom__lineWidth=1)],
                     desc="Bars: the house's metered use. Line: what the weather model expects."), 16, 6)
    # 30-day means of daily use and of the expectation (each over its own days), stamped at the window's end
    # like Flux's timedMovingAverage.
    d.add(timeseries("Use vs weather-expected (30-day)", [tsdb(f'''
WITH used AS ({electricity("1 day")}),
exp AS ({expected_electricity()}),
days AS (SELECT time FROM used UNION SELECT time FROM exp),
win AS (
  SELECT d.time + interval '1 day' AS time,
         avg(used."Value") OVER w AS u, avg(exp."Value") OVER w AS e
  FROM days d LEFT JOIN used USING (time) LEFT JOIN exp USING (time)
  WINDOW w AS (ORDER BY d.time RANGE BETWEEN interval '29 days' PRECEDING AND CURRENT ROW))
SELECT time, u / e AS "Value" FROM win WHERE u IS NOT NULL AND e IS NOT NULL ORDER BY 1''')],
                     unit="percentunit", decimals=0, time_from="6y", display_name="Used ÷ expected",
                     color={"mode": "fixed", "fixedColor": SLOTS[3]}, thresholds_style="dashed",
                     thr=thresholds(MUTED, (1, MUTED)),
                     desc="Above 100 % means more electricity than the 2021–2024 house used in the same "
                          "weather: a new always-on load, the electric heater, or a less efficient heat pump."), 12, 8)
    d.add(timeseries("Night base load", [tsdb(f'''
SELECT time_bucket('1 week', time, {HEL}) AS time, percentile_cont(0.5) WITHIN GROUP (ORDER BY kwh) AS "Value"
FROM electricity
WHERE $__timeFilter(time) AND extract(hour FROM time AT TIME ZONE 'Europe/Helsinki') BETWEEN 2 AND 4
GROUP BY 1 ORDER BY 1''')],
                     unit="kwatt", decimals=2, time_from="6y", display_name="02–05 median",
                     color={"mode": "fixed", "fixedColor": SLOTS[4]},
                     desc="Median hourly use between 02 and 05, per week. In summer this is the always-on load "
                          "(fridge, freezer, network, homelab, ventilation)."), 12, 8)

    d.row("Finnish grid (Fingrid)")
    d.add(stat("CO₂ now", [tsdb(fingrid_now({"co2_consumption": "Consumed"}))], unit="suffix: g/kWh", decimals=0,
               graph=False, thr=thresholds(GOOD, (50, WARN), (100, BAD)),
               desc="Grams of CO₂ per kWh of electricity consumed in Finland, including imports (Fingrid, 3 min)."),
          4, 5)
    d.add(stat("Grid state", [tsdb(fingrid_now({"system_state": "System", "shortage_status": "Shortage"}))],
               graph=False, text_mode="value_and_name", color_mode="background", display_name="${__field.labels.series}",
               mappings=value_map([(0, "Normal", GOOD), (1, "Normal", GOOD), (2, "Endangered", WARN),
                                   (3, "Disturbed", BAD), (4, "Serious", BAD), (5, "Restoring", WARN)]),
               overrides=[by_name("Shortage", mappings=value_map([(0, "No shortage", GOOD), (1, "Possible", WARN),
                                                                  (2, "High risk", BAD), (3, "Shortage", BAD)]))],
               thr=thresholds(MUTED), desc="Fingrid's power system state and electricity shortage status."), 8, 5)
    d.add(stat("Your CO₂, last 12 months", [tsdb(total(household_co2(since("365 days"))), fmt="table")],
               unit="masskg", decimals=0, graph=False, thr=thresholds(SLOTS[4]),
               desc="Metered use each hour times that hour's CO₂ intensity of consumption."), 6, 5)
    d.add(stat("Import / export", [tsdb(fingrid_now({"net_import": "Net import"}))], unit="megwatt", decimals=0,
               graph=False, thr=thresholds(SLOTS[0]), desc="Positive: Finland imports electricity; negative: exports."),
          6, 5)
    d.add(timeseries("Production by source", [tsdb(fingrid({"nuclear": "Nuclear", "hydro": "Hydro", "wind": "Wind"})),
                                               tsdb(FINGRID_OTHER)],
                     unit="megwatt", stack=True, fill=60, gradient="none", decimals=0, legend="right",
                     display_name="${__field.labels.series}",
                     overrides=fixed_colors({"Nuclear": SLOTS[6], "Hydro": SLOTS[0], "Wind": SLOTS[2],
                                             "Other": MUTED}), time_from="7d"), 16, 8)
    d.add(timeseries("CO₂ intensity", [tsdb(fingrid({"co2_consumption": "Consumed", "co2_production": "Produced"}))],
                     decimals=0, fill=0, display_name="${__field.labels.series}", time_from="7d",
                     overrides=fixed_colors({"Consumed": SLOTS[4], "Produced": SLOTS[3]}),
                     desc="gCO₂/kWh. Consumed includes imports."), 8, 8)
    d.add(timeseries("Your electricity CO₂ per month", [tsdb(f'''
SELECT time_bucket('1 month', time, {HEL}) AS time, sum("Value") AS "Value"
FROM ({household_co2()}) c GROUP BY 1 ORDER BY 1''')],
                     unit="masskg", bars=True, decimals=0, display_name="CO₂", time_from="6y",
                     color={"mode": "fixed", "fixedColor": SLOTS[4]},
                     desc="The Finnish grid got much cleaner after Olkiluoto 3 and the wind build-out: "
                          "similar use, a third of the emissions."), 24, 7)

    d.row("Water")
    d.add(timeseries("Water per hour", [tsdb(water())],
                     unit="litre", bars=True, display_name="Water", decimals=0,
                     color={"mode": "fixed", "fixedColor": SLOTS[0]}), 12, 8)
    d.add(timeseries("Water per day", [tsdb(water(every="1 day"))],
                     unit="litre", bars=True, display_name="Water", decimals=0, time_from="60d",
                     color={"mode": "fixed", "fixedColor": SLOTS[0]}), 6, 8)
    d.add(timeseries("Night-time water (02–05)", [tsdb(water(every="1 day", extra=NIGHT))],
                     unit="litre", bars=True, display_name="Night use", decimals=1, time_from="60d",
                     thr=thresholds(GOOD, (5, WARN), (20, BAD)), color={"mode": "thresholds"},
                     desc="Water used between 02:00 and 05:00. A few litres every night points at a "
                          "running toilet or a dripping tap."), 6, 8)
    return d


def climate_dashboard():
    d = Dashboard("home-climate", "Home · Climate", folder="Home", tags=["home"], time="now-48h",
                  description="SwitchBot temperature/humidity sensors indoors, outdoors and around the 3D printer.")

    d.row("Now")
    d.add(stat("Indoors", [tsdb(ble_now("tempc", INDOOR))],
               unit="celsius", decimals=1, display_name=BY_PLACE, graph=False, text_mode="value_and_name",
               thr=thresholds(SLOTS[0], (19, GOOD), (24, WARN), (26, BAD)), color_mode="value"), 16, 4)
    d.add(stat("Outdoors", [tsdb(ble_now("tempc", {OUTSIDE_ID: "Outside"}))],
               unit="celsius", decimals=1, graph=False, display_name="Outside",
               thr=thresholds(SLOTS[0])), 4, 4)
    d.add(stat("Indoor humidity", [tsdb(f'SELECT avg("Value") AS "Value" FROM ({ble_now("hum", INDOOR)}) l',
                                        fmt="table")], unit="humidity", decimals=0, graph=False,
               thr=thresholds(WARN, (30, GOOD), (60, WARN)), desc="Average of the indoor sensors; 30–60 % is comfortable."), 4, 4)

    d.row("Indoors")
    d.add(timeseries("Temperature", [tsdb(ble("tempc", INDOOR))], unit="celsius", decimals=1, fill=0,
                     display_name=BY_PLACE, overrides=fixed_colors(dict(zip(INDOOR.values(), SLOTS))),
                     legend="right", legend_calcs=["min", "max", "lastNotNull"]), 16, 9)
    d.add(table("Today", [tsdb(ble("tempc", INDOOR, rng=TODAY, every="10 minutes"))],
                unit="celsius", decimals=1, display_name=BY_PLACE,
                transformations=[reduce_rows(["min", "max"]),
                                 organize(rename={"Field": "Room", "Min": "Low", "Max": "High"})],
                sort=("Room", False)), 8, 9)
    d.add(timeseries("Relative humidity", [tsdb(ble("hum", INDOOR))], unit="humidity", decimals=0, fill=0,
                     display_name=BY_PLACE, overrides=fixed_colors(dict(zip(INDOOR.values(), SLOTS))),
                     legend="right"), 16, 8)
    d.add(timeseries("Outdoor air heated to 22 °C", [tsdb(f'''
SELECT * FROM (
  SELECT $__timeGroupAlias(time, $__interval), avg(hum) * exp(0.0624 * (avg(tempc) - 22)) AS "Value"
  FROM mqtt_consumer
  WHERE $__timeFilter(time) AND device_id = {lit(OUTSIDE_ID)} AND (tempc IS NOT NULL OR hum IS NOT NULL)
  GROUP BY 1) o
WHERE "Value" IS NOT NULL ORDER BY 1''')],
                     unit="humidity", decimals=0, display_name="RH at 22 °C", fill=10,
                     thr=thresholds(BAD, (25, WARN), (35, GOOD)), thresholds_style="line+area",
                     color={"mode": "fixed", "fixedColor": SLOTS[0]},
                     desc="What outdoor air becomes once warmed indoors: when this is low, ventilation "
                          "dries the house out and a humidifier helps."), 8, 8)

    d.row("Outdoors")
    d.add(timeseries("Temperature", [tsdb(ble("tempc", OUTDOOR))], unit="celsius", decimals=1, fill=0,
                     display_name=BY_PLACE, overrides=fixed_colors(dict(zip(OUTDOOR.values(), SLOTS))),
                     legend="right", legend_calcs=["min", "max", "lastNotNull"]), 16, 8)
    d.add(timeseries("Daily range outside", [tsdb(ble("tempc", {OUTSIDE_ID: "Low"}, every="1 day", fn="min")),
                                             tsdb(ble("tempc", {OUTSIDE_ID: "High"}, every="1 day", fn="max"))],
                     unit="celsius", decimals=1, display_name=BY_PLACE, time_from="60d", fill=0, points="always",
                     overrides=fixed_colors({"Low": SLOTS[0], "High": SLOTS[1]})), 8, 8)

    d.row("Weather (FMI)")
    d.add(barchart("Forecast, next 48 hours", [tsdb(fmi_forecast("Temperature"))], unit="celsius", decimals=0,
                   thr=thresholds(SLOTS[0], (0, SLOTS[2]), (15, SLOTS[1])), color={"mode": "thresholds"},
                   label_spacing=100, desc="The Finnish Meteorological Institute's forecast for home, updated hourly."),
          16, 7)
    d.add(barchart("Rain, next 48 hours", [tsdb(fmi_forecast("Precipitation1h"))], unit="lengthmm",
                   decimals=1, color={"mode": "fixed", "fixedColor": SLOTS[0]}, label_spacing=100), 8, 7)
    d.add(timeseries("Outside: own sensor vs FMI", [
        tsdb(ble("tempc", {OUTSIDE_ID: "Front yard sensor"})),
        tsdb(fmi_observed({"t2m": "FMI Espoo Tapiola"}))],
        unit="celsius", decimals=1, fill=0, display_name=BY_PLACE,
        overrides=fixed_colors({"Front yard sensor": SLOTS[1], "FMI Espoo Tapiola": SLOTS[0]}),
        desc="The front-yard SwitchBot next to the nearest official station, about 4 km away. Sun on the "
             "sensor shows up as afternoon peaks."), 12, 8)
    d.add(timeseries("Wind", [tsdb(fmi_observed({"ws_10min": "Wind", "wg_10min": "Gusts"}))], unit="velocityms",
                     decimals=1, fill=0, display_name=BY_PLACE,
                     overrides=fixed_colors({"Wind": SLOTS[0], "Gusts": SLOTS[3]})), 6, 8)
    d.add(timeseries("Rain", [tsdb(fmi_observed({"r_1h": "Rain"}, every="1 hour", fn="max"))], unit="lengthmm",
                     decimals=1, bars=True, display_name="Rain", color={"mode": "fixed", "fixedColor": SLOTS[0]},
                     desc="Hourly precipitation at the station."), 6, 8)

    return d


def fmi_observed(params, every=None, fn="mean"):
    """FMI station observations, one series per param, labelled `place`."""
    return f'''
SELECT {bucket(every)}, place, {agg(fn, "value")} AS "Value"
FROM fmi_observation JOIN {values(params, "param", "place")} USING (param)
WHERE $__timeFilter(time) AND param IN {in_list(params)}
GROUP BY 1, 2 ORDER BY 1'''


def fmi_forecast(param):
    """The latest FMI forecast for home, from the current hour on."""
    return f'''
SELECT time, value AS "Value" FROM fmi_forecast
WHERE param = {lit(param)} AND time >= date_trunc('hour', now()) AND time < now() + interval '48 hours'
ORDER BY 1'''


def batteries():
    """Last battery report per device in the last 14 days: our BLE sensors and every Zigbee device."""
    return f'''
WITH ble AS (
  SELECT DISTINCT ON (device_id) place AS device, 'SwitchBot (BLE)' AS kind, batt AS battery, time AS seen
  FROM mqtt_consumer JOIN {values(ALL_BLE, "device_id", "place")} USING (device_id)
  WHERE time > now() - interval '14 days' AND device_id IN {in_list(ALL_BLE)} AND batt IS NOT NULL
  ORDER BY device_id, time DESC),
zigbee AS (
  SELECT DISTINCT ON (device_id) device_id AS device, 'Zigbee' AS kind, battery, time AS seen
  FROM mqtt_consumer
  WHERE time > now() - interval '14 days' AND integration_id = 'zigbee2mqtt' AND battery IS NOT NULL
  ORDER BY device_id, time DESC)
SELECT device, kind, battery, seen, extract(epoch FROM now() - seen) / 3600 AS hours
FROM (SELECT * FROM ble UNION ALL SELECT * FROM zigbee) b'''


def devices_dashboard():
    d = Dashboard("home-devices", "Home · Devices", folder="Home", tags=["home"], time="now-7d", refresh="5m",
                  description="Batteries, radio links and silent sensors.")

    d.row("Alarm")
    d.add(state_timeline("Verisure alarm", [alarm()], mappings=ALARM, color={"mode": "thresholds"},
                         display_name="Alarm",
                         thr=thresholds(MUTED), show_values="auto",
                         desc="Mode from Verisure's API, looked up on each arm/disarm push. 'Armed' means the push "
                              "arrived but the mode lookup did not answer."), 24, 4)

    d.row("Batteries")
    d.add(table("Batteries (lowest first)", [tsdb(batteries() + "\nORDER BY battery", fmt="table")], cell_height="sm",
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
    d.add(stat("Below 20 %", [tsdb(f"SELECT count(*) AS battery FROM ({batteries()}) b WHERE battery < 20",
                                   fmt="table")],
               graph=False, thr=thresholds(GOOD, (1, WARN)), nulls="0", desc="Devices to swap batteries in soon."), 5, 4)
    d.add(stat("Silent for 24 h+", [tsdb(f"SELECT count(*) AS hours FROM ({batteries()}) b WHERE hours > 24",
                                         fmt="table")],
               graph=False, thr=thresholds(GOOD, (1, BAD)), nulls="0",
               desc="Battery devices that reported nothing for a day: dead battery or out of range."), 5, 4)
    d.add(bargauge("Weakest Zigbee links", [tsdb('''
SELECT device_id AS device, avg(linkquality) AS "Value"
FROM mqtt_consumer
WHERE time > now() - interval '1 day' AND integration_id = 'zigbee2mqtt' AND linkquality IS NOT NULL
GROUP BY 1 ORDER BY 2 LIMIT 12''', fmt="table")], unit="none", decimals=0, minv=0, maxv=255,
                   display_name="${__field.labels.device}", values=True,
                   thr=thresholds(BAD, (40, WARN), (80, GOOD)),
                   desc="Average link quality over the last day (0–255). Under ~40 the device needs a closer router."),
          10, 12)
    d.row("BLE sensors")
    d.add(timeseries("Sensor batteries", [tsdb(ble("batt", ALL_BLE, every="12 hours"))], unit="percent", minv=0,
                     maxv=100, display_name=BY_PLACE, time_from="30d", fill=0, legend="right"), 24, 9)
    return d


def overview_house(d):
    """House tiles for the top-level overview dashboard."""
    d.add(stat("Spot price", [SPOT_NOW], unit="c/kWh",
               decimals=2, thr=PRICE_THR, graph=False,
               links=[{"title": "Energy", "url": "/d/home-energy"}]), 3, 4)
    d.add(stat("Plugs now", [tsdb(PLUGS_NOW, fmt="table")], unit="watt", decimals=0, graph=False), 3, 4)
    d.add(stat("Plug cost today", [COST_TODAY],
               unit="currencyEUR", decimals=2, graph=False), 3, 4)
    d.add(stat("Water today", [tsdb(WATER_TODAY, fmt="table")], unit="litre",
               decimals=0, graph=False), 3, 4)
    d.add(stat("Outside", [tsdb(ble_now("tempc", {OUTSIDE_ID: "Outside"}))],
               unit="celsius", decimals=1, graph=False, display_name="Outside",
               links=[{"title": "Climate", "url": "/d/home-climate"}]), 3, 4)
    d.add(stat("Indoors (avg)", [tsdb(f'SELECT avg("Value") AS "Value" FROM ({ble_now("tempc", INDOOR)}) l',
                                      fmt="table")],
               unit="celsius", decimals=1, graph=False, thr=thresholds(SLOTS[0], (19, GOOD), (25, WARN))), 3, 4)
    d.add(stat("Freezer", [tsdb(ble_now("tempc", {"D43535302778": "Freezer"}))],
               unit="celsius", decimals=1, graph=False, thr=thresholds(GOOD, (-15, WARN), (-10, BAD))), 3, 4)
    d.add(stat("Low batteries", [tsdb(f'''
SELECT count(*) AS "Value" FROM (
  SELECT DISTINCT ON (device_id) coalesce(batt, battery) AS level
  FROM mqtt_consumer
  WHERE time > now() - interval '3 days'
    AND ((batt IS NOT NULL AND device_id IN {in_list(ALL_BLE)})
         OR (battery IS NOT NULL AND integration_id = 'zigbee2mqtt'))
  ORDER BY device_id, time DESC) l
WHERE level < 20''', fmt="table")], graph=False,
               thr=thresholds(GOOD, (1, WARN)), nulls="0", links=[{"title": "Devices", "url": "/d/home-devices"}]), 3, 4)
