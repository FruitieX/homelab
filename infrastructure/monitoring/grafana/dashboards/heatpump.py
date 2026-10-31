"""Heat pump dashboard: the NIBE F750 via myUplink (TimescaleDB tsdb, table nibe)."""

import home
from lib import (BAD, HEL, MUTED, RANGE, SLOTS, WARN, Dashboard, agg, bucket, by_name, fixed_colors, in_list, stat,
                 state_timeline, table, thresholds, timeseries, tsdb, value_map, values, where)

BY_POINT = "${__field.labels.point}"

PRIORITY = value_map([(10, "Off", MUTED), (20, "Hot water", SLOTS[1]), (30, "Heating", SLOTS[0]),
                      (60, "Cooling", SLOTS[2])])
COMPRESSOR = value_map([(20, "Off", MUTED), (40, "Starting", SLOTS[2]), (60, "Running", SLOTS[0]),
                        (100, "Stopping", SLOTS[2])])
ADD_HEAT = value_map([(0, "Alarm", BAD), (1, "Alarm", BAD), (2, "On", WARN), (3, "Off", MUTED),
                      (4, "Blocked", MUTED), (5, "Off", MUTED), (6, "On", WARN)])
PRICE_MODE = value_map([(0, "Unknown", MUTED), (1, "Low price", SLOTS[2]), (2, "Normal", MUTED),
                        (3, "High price", SLOTS[3])])

# Lifetime counters (the pump's own), all monotonic.
HEAT = {"44308": "Heating, compressor", "44306": "Hot water, compressor"}
HEAT_TOTAL = {"44300": "44308", "44298": "44306"}  # incl. electric heater -> compressor only
COMPRESSOR_KWH = "43144"  # labelled "time factor:" in myUplink; compressor energy in kWh
SETTINGS = {"47007": "Heating curve", "47011": "Curve offset", "47041": "Hot water demand (0 eco, 1 normal, 2 lux)",
            "47137": "Operating mode (0 auto)", "47212": "Max electric heater (×10 W)",
            "47206": "Compressor starts at (DM)", "48072": "Heater allowed from (DM below start)",
            "47375": "Stop heating above (°C)", "47376": "Stop electric heater above (°C)",
            "48442": "Smart Price Adaption on", "48444": "Price effect on room temperature",
            "48566": "Price effect on hot water", "47050": "Periodic hot-water boost on",
            "47051": "Boost every (days)"}


# Hot water, from the F750 technical data and this household's water meter history (13 months to
# 2026-10): evening showers run a steady ~4.8 L/min for 13-15 minutes, about 65 L of mixed water.
TANK_L = 180
TANK_EFFICIENCY = 0.86  # EN 16147: 227 L at 40 °C from a 54 °C tank, vs 264 L if fully usable
MIX_C = 40.0
SHOWER_LPM = 4.8
SHOWER_L = 65


def hot_water_left(per):
    """Mixed 40 °C water the tank can give at its current top temperature, as shower minutes or showers.
    Cold water is estimated by season: about 5 °C in February, 13 °C in August (HSY tap water)."""
    div = SHOWER_LPM if per == "minute" else SHOWER_L
    name = "Our estimate" if per == "minute" else "Showers"
    return tsdb(f'''
WITH c AS (SELECT 9.0 + 4.0 * cos(2 * pi() * (extract(month FROM now()) - 8.5) / 12) AS cold)
SELECT time, CASE WHEN value <= {MIX_C} THEN 0 ELSE
       {TANK_L} * {TANK_EFFICIENCY} * (value - cold) / ({MIX_C} - cold) / {float(div)} END AS "{name}"
FROM nibe, c
WHERE id = '40013' AND time > now() - interval '15 minutes'
ORDER BY time DESC LIMIT 1''', fmt="table")


def nibe(points, every=None, fn="mean", rng=RANGE):
    """One series per data point, labelled `point`."""
    return f'''
SELECT {bucket(every)}, point, {agg(fn, "value")} AS "Value"
FROM nibe JOIN {values(points, "id", "point")} USING (id)
WHERE {where(rng)} AND id IN {in_list(points)}
GROUP BY 1, 2 ORDER BY 1'''


def now(*points):
    """Latest value of each (id, label) point, within 15 minutes."""
    pts = dict(p if isinstance(p, tuple) else (p, p) for p in points)
    return tsdb(f'''
SELECT * FROM (
  SELECT DISTINCT ON (id) time, point, value AS "Value"
  FROM nibe JOIN {values(pts, "id", "point")} USING (id)
  WHERE time > now() - interval '15 minutes' AND id IN {in_list(pts)}
  ORDER BY id, time DESC) l ORDER BY time''')


def per_period(points, every="1 day"):
    """Increase of lifetime counters per period (kWh)."""
    return tsdb(nibe(points, every=every, fn="spread"))


def daily_spread(ids):
    """Per Helsinki day, the increase of each counter in `ids`, summed."""
    return f'''
SELECT time, sum(s) AS v FROM (
  SELECT time_bucket('1 day', time, {HEL}) AS time, id, max(value) - min(value) AS s
  FROM nibe WHERE $__timeFilter(time) AND id IN {in_list(ids)} GROUP BY 1, 2) d
GROUP BY 1'''


def heatpump_dashboard():
    d = Dashboard("home-heatpump", "Home · Heat pump", folder="Home", tags=["home"], time="now-24h",
                  description="The NIBE F750 exhaust-air heat pump through the myUplink API, polled once a minute. "
                              "Read-only: the poller's token cannot change settings.")

    d.row("Now")
    d.add(stat("Doing", [now("49994")], graph=False, mappings=PRIORITY, color_mode="background",
               thr=thresholds(MUTED), desc="What the compressor is working on."), 4, 4)
    d.add(stat("Hot water", [now("40013")], unit="celsius", decimals=1, graph=False,
               thr=thresholds(SLOTS[0], (40, WARN), (45, SLOTS[1])),
               desc="Top of the tank (BT7), where hot water is drawn from."), 4, 4)
    d.add(stat("Shower time left", [hot_water_left(per="minute"), now(("50345", "NIBE"))], unit="m", decimals=0,
               graph=False, text_mode="value_and_name", overrides=[by_name("Our estimate", decimals=0)],
               desc=f"Our estimate uses this household's showers: {SHOWER_LPM} L/min of mixed water, measured "
                    "from the water meter. NIBE's own figure assumes a higher flow."), 4, 4)
    d.add(stat("Compressor", [now("41778")], unit="hertz", decimals=0, graph=False,
               thr=thresholds(SLOTS[0])), 4, 4)
    d.add(stat("Supply / target", [now(("40008", "Supply"), ("43009", "Target"))], unit="celsius", decimals=1,
               graph=False, text_mode="value_and_name",
               desc="Water going out to the radiators/floor (BT63) and the temperature the heating curve asks for."),
          4, 4)
    d.add(stat("Outdoor", [now("40004")], unit="celsius", decimals=1, graph=False,
               desc="The pump's own outdoor sensor (BT1)."), 4, 4)
    d.add(stat("Degree minutes", [now("40941")], decimals=0, graph=False,
               thr=thresholds(BAD, (-700, WARN), (-60, SLOTS[0])),
               desc="Heat deficit the pump is working off. The compressor starts at −60; the electric heater "
                    "is allowed in from −700."), 4, 4)
    d.add(stat("Electric heater", [now("49993")], graph=False, mappings=ADD_HEAT, color_mode="background",
               thr=thresholds(MUTED), desc="The built-in immersion heater (up to 6.5 kW)."), 4, 4)
    d.add(stat("Heater power", [now("43084")], unit="kwatt", decimals=1, graph=False,
               thr=thresholds(SLOTS[0], (0.1, WARN))), 4, 4)
    d.add(stat("Smart Price Adaption", [now("41929")], graph=False, mappings=PRICE_MODE, color_mode="background",
               thr=thresholds(MUTED), desc="How the pump rates the current Nord Pool price."), 4, 4)
    d.add(stat("Exhaust fan", [now("50221")], unit="percent", decimals=0, graph=False), 4, 4)
    d.add(stat("Showers left", [hot_water_left(per="shower")], decimals=1, graph=False,
               thr=thresholds(BAD, (1, WARN), (2, SLOTS[0])),
               desc=f"Typical showers ({SHOWER_L} L at {SHOWER_LPM} L/min) the tank can give right now."), 4, 4)

    d.row("Activity")
    d.add(state_timeline("What it was doing", [
        tsdb(nibe({"49994": "Priority"}, fn="last")),
        tsdb(nibe({"43427": "Compressor"}, fn="last")),
        tsdb(nibe({"49993": "Electric heater"}, fn="last")),
        tsdb(nibe({"41929": "Spot price"}, fn="last"))],
        mappings=PRIORITY,
        overrides=[by_name("Compressor", mappings=COMPRESSOR), by_name("Electric heater", mappings=ADD_HEAT),
                   by_name("Spot price", mappings=PRICE_MODE)],
        display_name=BY_POINT, color={"mode": "thresholds"}, thr=thresholds(MUTED)), 24, 6)
    d.add(timeseries("Hot water tank", [tsdb(nibe({"40013": "Top (BT7)", "40014": "Charging (BT6)"}))],
                     unit="celsius", decimals=1, display_name=BY_POINT, fill=0,
                     overrides=fixed_colors({"Top (BT7)": SLOTS[1], "Charging (BT6)": SLOTS[3]}),
                     desc="The tank reheats when the charging sensor drops; a shower shows as a fall at the top."),
          12, 8)
    d.add(timeseries("Water use", [tsdb(home.water(every="10 minutes"))], unit="litre", bars=True, decimals=1,
                     display_name="Water", color={"mode": "fixed", "fixedColor": SLOTS[0]},
                     desc="From the water meter, per 10 minutes. Hot-water reheats follow big draws."), 12, 8)
    d.add(timeseries("Heating circuit", [tsdb(nibe({"40008": "Supply (BT63)", "40012": "Return (BT3)",
                                                    "43009": "Target supply"}))],
                     unit="celsius", decimals=1, display_name=BY_POINT, fill=0,
                     overrides=fixed_colors({"Supply (BT63)": SLOTS[1], "Return (BT3)": SLOTS[0],
                                             "Target supply": SLOTS[6]})
                     + [by_name("Target supply", custom__lineStyle={"fill": "dash", "dash": [6, 4]})]),
          12, 8)
    d.add(timeseries("Air", [tsdb(nibe({"40025": "From rooms (BT20)", "40026": "Out of the house (BT21)",
                                        "40004": "Outdoor (BT1)"}))],
                     unit="celsius", decimals=1, display_name=BY_POINT, fill=0,
                     overrides=fixed_colors({"From rooms (BT20)": SLOTS[1], "Out of the house (BT21)": SLOTS[0],
                                             "Outdoor (BT1)": SLOTS[2]}),
                     desc="Exhaust air from the rooms and what is left after the pump has taken its heat."), 12, 8)
    d.add(timeseries("Compressor frequency", [tsdb(nibe({"41778": "Compressor"}))], unit="hertz", decimals=0,
                     display_name="Compressor", minv=0, color={"mode": "fixed", "fixedColor": SLOTS[0]}), 12, 7)
    d.add(timeseries("Degree minutes", [tsdb(nibe({"40941": "Degree minutes"}))], decimals=0,
                     display_name="Degree minutes", color={"mode": "fixed", "fixedColor": SLOTS[3]},
                     thresholds_style="dashed", thr=thresholds(MUTED, (-60, MUTED)),
                     desc="Below −60 the compressor starts; near 0 the house is warm enough."), 12, 7)

    d.row("Energy")
    d.add(timeseries("Heat produced per day", [per_period(HEAT), tsdb(f'''
WITH total AS ({daily_spread(HEAT_TOTAL)}), comp AS ({daily_spread(HEAT_TOTAL.values())})
SELECT time, 'Electric heater' AS point, total.v - comp.v AS "Value"
FROM total JOIN comp USING (time) ORDER BY 1''')],
        unit="kwatth", bars=True, stack=True, decimals=1, display_name=BY_POINT, time_from="30d",
        overrides=fixed_colors({"Heating, compressor": SLOTS[0], "Hot water, compressor": SLOTS[1],
                                "Electric heater": SLOTS[3]}),
        desc="From the pump's heat meters. History starts when the poller was added (2026-10-25)."), 12, 8)
    d.add(timeseries("Compressor energy per day", [per_period({COMPRESSOR_KWH: "Compressor"})],
                     unit="kwatth", bars=True, decimals=1, display_name="Compressor", time_from="30d",
                     color={"mode": "fixed", "fixedColor": SLOTS[4]},
                     desc="The pump's own compressor energy counter (myUplink calls it \"time factor\")."), 6, 8)
    d.add(timeseries("Heat per compressor kWh", [tsdb(f'''
WITH heat AS ({daily_spread(HEAT)}), elec AS ({daily_spread([COMPRESSOR_KWH])})
SELECT time, heat.v / elec.v AS "Value"
FROM heat JOIN elec USING (time) WHERE elec.v > 0.5 ORDER BY 1''')], decimals=1, bars=True, display_name="Ratio", time_from="30d",
                     color={"mode": "fixed", "fixedColor": SLOTS[2]},
                     desc="Compressor heat divided by the compressor energy counter: an efficiency estimate "
                          "from the pump's own counters, not a measured COP (fans and pumps are not included)."),
          6, 8)
    d.add(stat("Lifetime", [now(("43420", "Running hours"), ("43424", "Hot water hours"),
                            ("43416", "Compressor starts"), ("43081", "Heater hours (weighted)"))],
               decimals=0, graph=False, text_mode="value_and_name", display_name=BY_POINT,
               desc="Since installation. Heater hours are weighted by heater power."), 12, 5)
    d.add(stat("Lifetime heat", [now(("44300", "Heating"), ("44298", "Hot water"))],
               unit="kwatth", decimals=0, graph=False, text_mode="value_and_name", display_name=BY_POINT,
               desc="All heat delivered, including the electric heater."), 12, 5)

    d.row("Settings")
    d.add(table("Settings", [tsdb(f'''
SELECT DISTINCT ON (id) setting AS "Setting", value AS "Value"
FROM nibe JOIN {values(SETTINGS, "id", "setting")} USING (id)
WHERE time > now() - interval '1 hour' AND id IN {in_list(SETTINGS)}
ORDER BY id, time DESC''', fmt="table")],
        desc="Current values of settings that matter for comfort and cost. Change them on the pump or in myUplink."),
          12, 10)
    d.add(timeseries("Setting changes", [tsdb(nibe({"47007": "Heating curve", "47011": "Curve offset",
                                                    "47041": "Hot water demand"}, fn="last"))],
                     decimals=0, display_name=BY_POINT, fill=0, legend="right",
                     desc="Step changes show when someone adjusted the pump."), 12, 10)
    return d
