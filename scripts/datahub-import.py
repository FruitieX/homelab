#!/usr/bin/env python3
"""Import a Fingrid Datahub consumption CSV (oma.datahub.fi export) into InfluxDB.

    python3 scripts/datahub-import.py consumption.csv

Writes measurement `electricity`, field `kwh` (energy in the period starting at the timestamp), tags
`source=datahub` and `resolution` (PT1H or PT15M) to bucket `home`. Re-importing an overlapping export is safe:
identical points overwrite each other. The metering point id is not stored.
"""
import base64, csv, datetime as dt, subprocess, sys

ORG, BUCKET = "influxdata", "home"


def lines(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            if row["Unit Type"] != "kWh" or row["Quality"] not in ("OK", "Estimated", "Revised"):
                continue
            ts = int(dt.datetime.fromisoformat(row["Start Time"].replace("Z", "+00:00")).timestamp())
            kwh = float(row["Quantity"].replace(",", "."))
            yield f'electricity,source=datahub,resolution={row["Resolution"]} kwh={kwh} {ts}\n'


def write(data):
    """Write line protocol (second precision) to the home bucket."""
    token = base64.b64decode(subprocess.run(
        ["kubectl", "-n", "influxdb", "get", "secret", "influxdb-auth", "-o", "jsonpath={.data.admin-token}"],
        check=True, capture_output=True, text=True).stdout).decode()
    # The token goes in on the first stdin line so it never shows up in a process listing.
    subprocess.run(["kubectl", "-n", "influxdb", "exec", "-i", "influxdb-influxdb2-0", "--", "sh", "-c",
                    f'read -r INFLUX_TOKEN; export INFLUX_TOKEN; influx write --org {ORG} --bucket {BUCKET} '
                    '--precision s'], input=token + "\n" + data, check=True, text=True)


def main():
    data = "".join(lines(sys.argv[1]))
    write(data)
    print(f"wrote {data.count(chr(10))} points")


if __name__ == "__main__":
    main()
