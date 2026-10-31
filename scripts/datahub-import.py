#!/usr/bin/env python3
"""Import a Fingrid Datahub consumption CSV (oma.datahub.fi export) into TimescaleDB (table `electricity`).

    python3 scripts/datahub-import.py consumption.csv

Writes measurement `electricity`, field `kwh` (energy in the period starting at the timestamp), tags
`source=datahub` and `resolution` (PT1H or PT15M). Re-importing an overlapping export is safe: the table's
replace_point trigger makes re-sent points replace the stored ones. The metering point id is not stored.

`write()` is shared with spot-price-backfill.py and fmi-backfill.py. It sends InfluxDB line protocol to tsdb-relay,
which stores it in TimescaleDB (docs/monitoring.md). Set DRY_RUN=1 to print what would be written instead.

The relay answers as soon as it has buffered the points; a point it can't store (wrong type, unknown table) only
shows up in its log: kubectl logs deploy/tsdb-relay.
"""
import csv, datetime as dt, os, subprocess, sys

BUCKET = "home"


def lines(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            if row["Unit Type"] != "kWh" or row["Quality"] not in ("OK", "Estimated", "Revised"):
                continue
            ts = int(dt.datetime.fromisoformat(row["Start Time"].replace("Z", "+00:00")).timestamp())
            kwh = float(row["Quantity"].replace(",", "."))
            yield f'electricity,source=datahub,resolution={row["Resolution"]} kwh={kwh} {ts}\n'


def write(data, bucket=BUCKET):
    """Write line protocol (second precision) to tsdb-relay; the bucket (default home) becomes a tag there."""
    if os.environ.get("DRY_RUN") == "1":
        rows = data.splitlines()
        print(f"dry run: {len(rows)} points for bucket {bucket} -> tsdb-relay", *rows[:3], sep="\n  ")
        return
    # Posted from inside the relay pod, which has busybox wget and its own token in RELAY_TOKEN.
    subprocess.run(["kubectl", "-n", "default", "exec", "-i", "deploy/tsdb-relay", "--", "sh", "-c",
                    'wget -q -O /dev/null --header "Authorization: Token $RELAY_TOKEN" --post-file /dev/stdin '
                    f'"http://127.0.0.1:8086/api/v2/write?bucket={bucket}&precision=s"'],
                   input=data, check=True, text=True)


def main():
    data = "".join(lines(sys.argv[1]))
    write(data)
    print(f"wrote {data.count(chr(10))} points")


if __name__ == "__main__":
    main()
