#!/usr/bin/env python3
"""Backfill Finnish day-ahead spot prices into InfluxDB (bucket nordpool) in nordpool-mqtt's format.

    python3 scripts/spot-price-backfill.py 2020-01-01 2025-04-05T22:00:00Z

Prices come from Elering's public Nord Pool price API (area fi, EUR/MWh) and are stored like nordpool-mqtt
stores them: measurement `price`, tag `id=price`, field `price` in c/kWh including the VAT in force on that
day, timestamped at the start of each period.
"""
import datetime as dt, importlib.util, json, os, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("datahub_import", os.path.join(HERE, "datahub-import.py"))
influx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(influx)

# Finnish VAT on electricity: 10 % from 2022-12-01 to 2023-04-30, 24 % otherwise until 2024-09-01, then 25.5 %.
VAT = [(dt.date(2024, 9, 1), 25.5), (dt.date(2023, 5, 1), 24.0), (dt.date(2022, 12, 1), 10.0), (dt.date(1900, 1, 1), 24.0)]
HELSINKI = dt.timezone(dt.timedelta(hours=2))  # VAT changes took effect at local midnight; DST is irrelevant here


def vat(ts):
    day = dt.datetime.fromtimestamp(ts, HELSINKI).date()
    return next(v for since, v in VAT if day >= since)


def parse(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=dt.timezone.utc) \
        if "T" in s else dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc)


def main():
    start, end = parse(sys.argv[1]), parse(sys.argv[2])
    lines, cur = [], start
    while cur < end:
        nxt = min(cur + dt.timedelta(days=90), end)
        url = (f"https://dashboard.elering.ee/api/nps/price?start={cur:%Y-%m-%dT%H:%M:%S.000Z}"
               f"&end={nxt:%Y-%m-%dT%H:%M:%S.000Z}")
        for p in json.load(urllib.request.urlopen(url, timeout=120))["data"]["fi"]:
            ts = p["timestamp"]
            if start.timestamp() <= ts < end.timestamp():
                price = round(p["price"] / 10 * (1 + vat(ts) / 100), 3)
                lines.append(f"price,id=price price={price} {ts}\n")
        cur = nxt
        time.sleep(1)
    influx.write("".join(lines), bucket="nordpool")
    print(f"wrote {len(lines)} prices")


if __name__ == "__main__":
    main()
