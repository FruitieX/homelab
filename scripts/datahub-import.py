#!/usr/bin/env python3
"""Import a Fingrid Datahub consumption CSV (oma.datahub.fi export) into InfluxDB and TimescaleDB.

    python3 scripts/datahub-import.py consumption.csv

Writes measurement `electricity`, field `kwh` (energy in the period starting at the timestamp), tags
`source=datahub` and `resolution` (PT1H or PT15M) to bucket `home`. Re-importing an overlapping export is safe:
identical points overwrite each other. The metering point id is not stored.

`write()` is shared with spot-price-backfill.py and fmi-backfill.py. It sends the line protocol to InfluxDB and
to tsdb-relay, which stores it in TimescaleDB (docs/monitoring.md); there the tables' replace_point trigger makes
re-sent points replace the stored ones, so re-running is safe in both stores. Environment:

    WRITE_TO=influxdb,tsdb   targets, in this order (default both; WRITE_TO=tsdb to fill in TimescaleDB only)
    DRY_RUN=1                print what would be written instead of writing

The relay answers as soon as it has buffered the points; a point it can't store (wrong type, unknown table) only
shows up in its log: kubectl logs deploy/tsdb-relay.
"""
import base64, csv, datetime as dt, os, subprocess, sys

ORG, BUCKET = "influxdata", "home"
RELAY = "http://tsdb-relay.default.svc:8086"
TARGETS = {
    # target: (namespace, secret, key, extra influx write arguments)
    "influxdb": ("influxdb", "influxdb-auth", "admin-token", ""),
    "tsdb": ("default", "tsdb-relay", "RELAY_TOKEN", f"--host {RELAY} "),
}


def lines(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            if row["Unit Type"] != "kWh" or row["Quality"] not in ("OK", "Estimated", "Revised"):
                continue
            ts = int(dt.datetime.fromisoformat(row["Start Time"].replace("Z", "+00:00")).timestamp())
            kwh = float(row["Quantity"].replace(",", "."))
            yield f'electricity,source=datahub,resolution={row["Resolution"]} kwh={kwh} {ts}\n'


def targets():
    names = [t.strip() for t in os.environ.get("WRITE_TO", "influxdb,tsdb").split(",") if t.strip()]
    unknown = [t for t in names if t not in TARGETS]
    if unknown or not names:
        sys.exit(f"WRITE_TO: unknown or no targets {unknown} (choose from {', '.join(TARGETS)})")
    return names


def write(data, bucket=BUCKET):
    """Write line protocol (second precision) to bucket `bucket` (default home) of each WRITE_TO target."""
    names = targets()
    if os.environ.get("DRY_RUN") == "1":
        rows = data.splitlines()
        print(f"dry run: {len(rows)} points for bucket {bucket} -> {', '.join(names)}", *rows[:3], sep="\n  ")
        return
    for name in names:
        namespace, secret, key, args = TARGETS[name]
        token = base64.b64decode(subprocess.run(
            ["kubectl", "-n", namespace, "get", "secret", secret, "-o", f"jsonpath={{.data.{key}}}"],
            check=True, capture_output=True, text=True).stdout).decode()
        # The token goes in on the first stdin line so it never shows up in a process listing. The relay is
        # reached from the InfluxDB pod, which has the influx CLI.
        subprocess.run(["kubectl", "-n", "influxdb", "exec", "-i", "influxdb-influxdb2-0", "--", "sh", "-c",
                        f'read -r INFLUX_TOKEN; export INFLUX_TOKEN; influx write {args}--org {ORG} '
                        f'--bucket {bucket} --precision s'], input=token + "\n" + data, check=True, text=True)


def main():
    data = "".join(lines(sys.argv[1]))
    write(data)
    print(f"wrote {data.count(chr(10))} points")


if __name__ == "__main__":
    main()
