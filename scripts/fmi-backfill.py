#!/usr/bin/env python3
"""Backfill hourly FMI weather observations into InfluxDB (bucket home, measurement fmi_observation).

    python3 scripts/fmi-backfill.py <fmisid> 2020-01-01 2026-10-25

Uses the same layout as telegraf-cloud (tag `param`, field `value`) at hourly resolution; live data is 10-minute.
FMI's open data allows a week of observations per request, so this walks the range week by week.
"""
import datetime as dt, importlib.util, os, re, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("datahub_import", os.path.join(HERE, "datahub-import.py"))
influx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(influx)

PARAMS = "t2m,td,rh,ws_10min,wg_10min,wd_10min,r_1h,snow_aws,p_sea,n_man,vis"
ELEMENT = re.compile(r"<BsWfs:Time>([^<]+)</BsWfs:Time>\s*<BsWfs:ParameterName>([^<]+)</BsWfs:ParameterName>\s*"
                     r"<BsWfs:ParameterValue>([^<]+)</BsWfs:ParameterValue>")


def main():
    station = sys.argv[1]
    cur = dt.datetime.fromisoformat(sys.argv[2]).replace(tzinfo=dt.timezone.utc)
    end = dt.datetime.fromisoformat(sys.argv[3]).replace(tzinfo=dt.timezone.utc)
    total = 0
    while cur < end:
        nxt = min(cur + dt.timedelta(days=7), end)
        url = ("https://opendata.fmi.fi/wfs?service=WFS&version=2.0.0&request=getFeature"
               f"&storedquery_id=fmi::observations::weather::simple&fmisid={station}&timestep=60"
               f"&parameters={PARAMS}&starttime={cur:%Y-%m-%dT%H:%M:%SZ}&endtime={nxt:%Y-%m-%dT%H:%M:%SZ}")
        for attempt in range(5):
            try:
                xml = urllib.request.urlopen(url, timeout=120).read().decode()
                break
            except Exception:
                time.sleep(10 * (attempt + 1))
        lines = []
        for t, param, value in ELEMENT.findall(xml):
            if value != "NaN":
                ts = int(dt.datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp())
                lines.append(f"fmi_observation,param={param} value={float(value)} {ts}\n")
        if lines:
            influx.write("".join(lines))
        total += len(lines)
        print(cur.date(), len(lines), flush=True)
        cur = nxt
        time.sleep(0.5)
    print(f"wrote {total} values")


if __name__ == "__main__":
    main()
