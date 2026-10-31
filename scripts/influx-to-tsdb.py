#!/usr/bin/env python3
"""Copy the InfluxDB history into TimescaleDB (tsdb) and verify the copy.

    influx-to-tsdb.py backfill   # week by week, oldest first, resumable
    influx-to-tsdb.py verify     # per table, week and field: counts and sums
    influx-to-tsdb.py live HOURS # the same for the dual-write period

Runs in the cluster as the Job in scripts/influx-to-tsdb-job.yaml (see
docs/monitoring.md). Environment: INFLUX_URL, INFLUX_TOKEN (read access to
home and nordpool), PGHOST/PGUSER/PGPASSWORD/PGDATABASE (role tsdb), CUTOFF
(dual-write start, RFC 3339), PROM_URL (optional, for the memory guard).

InfluxDB stays the source of truth while this runs, so every request is
bounded to at most a week (a day for recent raw MQTT data), and the script
waits whenever InfluxDB or its node runs short of memory.

Points are pivoted into Telegraf's wide layout: one row per series and
timestamp, one column per field. A field whose InfluxDB type differs from its
column's type goes to <field>_<type> (sensor_value_string). Neighbours' BLE
devices are skipped (OWN_BLE, the same list as telegraf-home's tagpass).
Each chunk deletes its time range and inserts it again in one transaction, so
a chunk can be re-run; finished chunks are recorded in tsdb_migration.chunks.
"""
import csv
import datetime as dt
import io
import json
import math
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request

import psycopg
from psycopg import sql

UTC = dt.timezone.utc
INFLUX_URL = os.environ.get("INFLUX_URL", "http://influxdb-influxdb2.influxdb.svc")
INFLUX_TOKEN = os.environ.get("INFLUX_TOKEN", "")
ORG = os.environ.get("INFLUX_ORG", "influxdata")
PROM_URL = os.environ.get("PROM_URL", "")
START = dt.datetime.fromisoformat(os.environ.get("START", "2019-12-02T00:00:00+00:00"))
PAUSE = float(os.environ.get("PAUSE_SECONDS", "1"))
OWN_BLE = sorted(os.environ.get("OWN_BLE", "").split())
BLE_RE = re.compile(r"^ble[0-9]*$")

# bucket, measurement, table, live: written by Telegraf from CUTOFF on
# (otherwise everything up to now, including future prices, is copied).
TABLES = [
    ("home", "mqtt_consumer", "mqtt_consumer", True),
    ("home", "nibe", "nibe", True),
    ("home", "esphome", "esphome", True),
    ("home", "esphome_binary", "esphome_binary", True),
    ("home", "verisure", "verisure", True),
    ("home", "fmi_observation", "fmi_observation", True),
    ("home", "fmi_forecast", "fmi_forecast", True),
    ("home", "fmi_daily", "fmi_daily", True),
    ("home", "fingrid", "fingrid", True),
    ("home", "fingrid_hourly", "fingrid_hourly", False),
    ("home", "electricity", "electricity", False),
    ("nordpool", "price", "nordpool_price", False),
]
ONLY = set(filter(None, os.environ.get("ONLY_TABLES", "").split(",")))

PG_TYPES = {"double": "double precision", "long": "bigint", "unsignedLong": "numeric",
            "string": "text", "boolean": "boolean"}
UDT = {"float8": "double", "int8": "long", "numeric": "unsignedLong", "text": "string", "bool": "boolean"}
CAST = {"double": "float8", "long": "int8", "unsignedLong": "numeric", "string": "text", "boolean": "bool"}


def log(*a):
    print(dt.datetime.now(UTC).strftime("%H:%M:%S"), *a, flush=True)


def rfc(t):
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def flux_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ---------------------------------------------------------------- guards

def prom(query):
    url = PROM_URL + "/api/v1/query?" + urllib.parse.urlencode({"query": query})
    with urllib.request.urlopen(url, timeout=20) as r:
        res = json.load(r)["data"]["result"]
    return [float(x["value"][1]) for x in res]


def wait_for_memory():
    """Block while InfluxDB or its node is short of memory."""
    if not PROM_URL:
        return
    while True:
        try:
            rss = prom('max(container_memory_rss{namespace="influxdb",container="influxdb2"})')
            ws = prom('max(container_memory_working_set_bytes{namespace="influxdb",container="influxdb2"})')
            avail = prom('min(node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)')
        except Exception as e:  # noqa: BLE001 - the guard must not stop the run
            log("memory guard: prometheus unavailable:", e)
            time.sleep(30)
            continue
        rss, ws, avail = (rss or [0])[0], (ws or [0])[0], (avail or [1])[0]
        if rss < 2.5 * 2**30 and ws < 3.3 * 2**30 and avail > 0.12:
            return
        log(f"memory guard: influx rss {rss / 2**30:.2f} GiB, working set {ws / 2**30:.2f} GiB, "
            f"lowest node available {avail:.0%}; waiting")
        time.sleep(60)


# ---------------------------------------------------------------- InfluxDB

def flux(query, annotations=True):
    """Run a Flux query and yield (datatypes, header, row) for each data row."""
    wait_for_memory()
    body = json.dumps({"query": query, "dialect": {
        "annotations": ["datatype"] if annotations else [], "header": True}}).encode()
    req = urllib.request.Request(f"{INFLUX_URL}/api/v2/query?org={ORG}", data=body, headers={
        "Authorization": f"Token {INFLUX_TOKEN}", "Content-Type": "application/json",
        "Accept": "application/csv"})
    with urllib.request.urlopen(req, timeout=900) as r:
        reader = csv.reader(io.TextIOWrapper(r, encoding="utf-8", newline=""))
        types, header = None, None
        for row in reader:
            if not row or (len(row) == 1 and not row[0]):
                header = None
                continue
            if row[0] == "#datatype":
                types, header = row, None
                continue
            if header is None:
                header = row
                if "error" in header and "reference" in header:
                    rest = next(reader, [])
                    raise RuntimeError(f"flux error: {rest}")
                continue
            yield types, header, row


def flux_values(query):
    """Single-column results (schema.tagKeys and friends)."""
    out = []
    for _types, header, row in flux(query):
        out.append(row[header.index("_value")])
    return out


def tag_keys(bucket, meas, start, stop):
    keys = flux_values(f'import "influxdata/influxdb/schema"\n'
                       f'schema.tagKeys(bucket: {flux_str(bucket)}, start: {rfc(start)}, stop: {rfc(stop)}, '
                       f'predicate: (r) => r._measurement == {flux_str(meas)})')
    return [k for k in keys if not k.startswith("_")]


def predicates(bucket, meas, start, stop):
    """Flux filter predicates covering the measurement minus foreign BLE devices."""
    base = f"r._measurement == {flux_str(meas)}"
    if meas != "mqtt_consumer":
        return [base]
    ids = flux_values(f'import "influxdata/influxdb/schema"\n'
                      f'schema.tagValues(bucket: {flux_str(bucket)}, tag: "integration_id", start: {rfc(start)}, '
                      f'stop: {rfc(stop)}, predicate: (r) => r._measurement == {flux_str(meas)})')
    ble = sorted(i for i in ids if BLE_RE.match(i))
    if not ble:
        return [base]
    # `!=` on a tag also matches series without it, so these two predicates
    # split the measurement exactly; both are pushed down to storage.
    others = base + "".join(f" and r.integration_id != {flux_str(i)}" for i in ble)
    own = (base + " and (" + " or ".join(f"r.integration_id == {flux_str(i)}" for i in ble) + ") and ("
           + " or ".join(f"r.device_id == {flux_str(d)}" for d in OWN_BLE) + ")")
    return [others, own] if OWN_BLE else [others]


# ---------------------------------------------------------------- Postgres

def setup(pg):
    with pg.cursor() as cur:
        cur.execute("""
            CREATE SCHEMA IF NOT EXISTS tsdb_migration;
            CREATE TABLE IF NOT EXISTS tsdb_migration.chunks (
              tbl text, start timestamptz, stop timestamptz, points bigint, rows bigint,
              seconds real, done_at timestamptz DEFAULT now(), PRIMARY KEY (tbl, start, stop));
            -- Which column holds which InfluxDB tag or field (and type).
            CREATE TABLE IF NOT EXISTS tsdb_migration.columns (
              tbl text, col text, kind text, field text, dtype text, PRIMARY KEY (tbl, col));
            CREATE TABLE IF NOT EXISTS tsdb_migration.verify (
              tbl text, start timestamptz, stop timestamptz, field text,
              influx_count bigint, pg_count bigint, influx_sum double precision, pg_sum double precision,
              ok boolean, checked_at timestamptz DEFAULT now(), PRIMARY KEY (tbl, start, stop, field));
        """)
    pg.commit()
    for _b, _m, tbl, _l in TABLES:
        ensure_tags(pg, tbl, [])


def table_columns(pg, tbl):
    with pg.cursor() as cur:
        cur.execute("SELECT column_name, udt_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = %s", (tbl,))
        return {c: UDT.get(u, u) for c, u in cur.fetchall()}


def registry(pg, tbl):
    with pg.cursor() as cur:
        cur.execute("SELECT col, kind, field, dtype FROM tsdb_migration.columns WHERE tbl = %s", (tbl,))
        return {c: (k, f, d) for c, k, f, d in cur.fetchall()}


SCHEMA_TAGS = {
    "mqtt_consumer": "kind integration_id device_id topic name entity mac brand model model_id type",
    "esphome": "component device_id entity topic", "esphome_binary": "component device_id entity topic",
    "nibe": "id name unit", "verisure": "topic", "fmi_observation": "param", "fmi_forecast": "param",
    "fmi_daily": "param", "fingrid": "series", "fingrid_hourly": "series", "electricity": "source resolution",
    "nordpool_price": "id",
}


def ensure_tags(pg, tbl, tags):
    """Tag columns (text); returns the list of tag columns of the table."""
    reg = registry(pg, tbl)
    cols = table_columns(pg, tbl)
    with pg.cursor() as cur:
        for t in SCHEMA_TAGS.get(tbl, "").split() + list(tags):
            if t in reg:
                continue
            if t not in cols:
                cur.execute(sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS {} text").format(
                    sql.Identifier(tbl), sql.Identifier(t)))
            cur.execute("INSERT INTO tsdb_migration.columns VALUES (%s, %s, 'tag', NULL, 'string') "
                        "ON CONFLICT DO NOTHING", (tbl, t))
    pg.commit()
    return sorted(c for c, (k, _f, _d) in registry(pg, tbl).items() if k == "tag")


def field_column(pg, tbl, field, dtype, cache):
    """Column for an InfluxDB field of the given type, created if needed.

    Runs inside the chunk's transaction, which commits the new column."""
    key = (field, dtype)
    if key in cache:
        return cache[key]
    reg = registry(pg, tbl)
    for c, (k, f, d) in reg.items():
        if k == "field" and f == field and d == dtype:
            cache[key] = c
            return c
    cols = table_columns(pg, tbl)
    tags = {c for c, (k, _f, _d) in reg.items() if k == "tag"}
    name = field + "_field" if field in tags or field == "time" else field
    # An existing column of another type (created by Telegraf or an earlier
    # week) keeps its type; this type gets its own column.
    if name in cols and cols[name] != dtype:
        name = f"{name}_{dtype.lower()}"
    if name in reg and reg[name][2] != dtype:
        raise RuntimeError(f"{tbl}.{name}: registered as {reg[name]}, wanted {field} {dtype}")
    with pg.cursor() as cur:
        if name not in cols:
            cur.execute(sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS {} " + PG_TYPES[dtype]).format(
                sql.Identifier(tbl), sql.Identifier(name)))
        cur.execute("INSERT INTO tsdb_migration.columns VALUES (%s, %s, 'field', %s, %s) "
                    "ON CONFLICT DO NOTHING", (tbl, name, field, dtype))
    cache[key] = name
    return name


# ---------------------------------------------------------------- backfill

def chunks(cutoff, live, meas):
    now = dt.datetime.now(UTC)
    end = cutoff if live else now + dt.timedelta(days=14)
    week = START
    while week < end:
        nxt = week + dt.timedelta(days=7)
        # Raw (uncompacted) MQTT data is large: a day per request.
        step = 1 if meas == "mqtt_consumer" and week > now - dt.timedelta(days=110) else 7
        t = week
        while t < nxt and t < end:
            yield week, t, min(t + dt.timedelta(days=step), end)
            t += dt.timedelta(days=step)
        week = nxt


def load_chunk(pg, bucket, meas, tbl, start, stop):
    t0 = time.monotonic()
    tags = ensure_tags(pg, tbl, tag_keys(bucket, meas, start, stop))
    preds = predicates(bucket, meas, start, stop)
    fcache = {}
    used = {}
    points = 0
    with pg.transaction():
        with pg.cursor() as cur:
            cur.execute("SET LOCAL work_mem = '256MB'")
            cur.execute("SET LOCAL tsdb.skip_replace = 'on'")
            cur.execute("CREATE TEMP TABLE IF NOT EXISTS stage (t text, f text, d text, v text) ON COMMIT DROP")
            for t in tags:
                cur.execute(sql.SQL("ALTER TABLE stage ADD COLUMN IF NOT EXISTS {} text").format(sql.Identifier(t)))
            copy_cols = sql.SQL(", ").join(map(sql.Identifier, ["t", "f", "d", "v"] + tags))
            with cur.copy(sql.SQL("COPY stage ({}) FROM STDIN").format(copy_cols)) as cp:
                for pred in preds:
                    q = (f"from(bucket: {flux_str(bucket)}) |> range(start: {rfc(start)}, stop: {rfc(stop)})"
                         f" |> filter(fn: (r) => {pred})"
                         f' |> drop(columns: ["_start", "_stop", "_measurement"])')
                    last_header = None
                    for types, header, row in flux(q):
                        if header is not last_header:
                            last_header = header
                            it, iv, iff = header.index("_time"), header.index("_value"), header.index("_field")
                            dtype = types[iv]
                            tag_idx = [(header.index(t) if t in header else None) for t in tags]
                            extra = set(header[3:]) - {"_time", "_value", "_field"} - set(tags)
                            if extra:
                                raise RuntimeError(f"{tbl}: unexpected tags {extra}")
                        if row[iv] == "" and dtype != "string":
                            continue
                        f = row[iff]
                        used[(f, dtype)] = True
                        cp.write_row([row[it], f, dtype, row[iv]] + [
                            (row[i] or None) if i is not None else None for i in tag_idx])
                        points += 1
            if points == 0:
                cur.execute(sql.SQL("DELETE FROM {} WHERE time >= %s AND time < %s").format(sql.Identifier(tbl)),
                            (start, stop))
                rows = 0
            else:
                fields = [(f, d, field_column(pg, tbl, f, d, fcache)) for f, d in sorted(used)]
                cur.execute(sql.SQL("DELETE FROM {} WHERE time >= %s AND time < %s").format(sql.Identifier(tbl)),
                            (start, stop))
                tag_ids = [sql.Identifier(t) for t in tags]
                aggs = [sql.SQL("{}(v::{}) FILTER (WHERE f = {} AND d = {})").format(
                    sql.SQL("bool_or" if d == "boolean" else "max"), sql.SQL(CAST[d]), sql.Literal(f), sql.Literal(d))
                    for f, d, _c in fields]
                cur.execute(sql.SQL("INSERT INTO {} (time, {}) SELECT t::timestamptz, {} FROM stage GROUP BY t{}").format(
                    sql.Identifier(tbl),
                    sql.SQL(", ").join(tag_ids + [sql.Identifier(c) for _f, _d, c in fields]),
                    sql.SQL(", ").join(tag_ids + aggs),
                    sql.SQL("").join(sql.SQL(", ") + i for i in tag_ids)))
                rows = cur.rowcount
            cur.execute("INSERT INTO tsdb_migration.chunks (tbl, start, stop, points, rows, seconds) "
                        "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (tbl, start, stop) DO UPDATE SET "
                        "points = excluded.points, rows = excluded.rows, seconds = excluded.seconds, done_at = now()",
                        (tbl, start, stop, points, rows, time.monotonic() - t0))
    return points, rows, time.monotonic() - t0


def set_policies(pg, scheduled):
    with pg.cursor() as cur:
        cur.execute("SELECT alter_job(job_id, scheduled => %s) FROM timescaledb_information.jobs "
                    "WHERE hypertable_schema = 'public' AND proc_name IN ('policy_compression', 'policy_columnstore')",
                    (scheduled,))
    pg.commit()


def compress_before(pg, tbl, before):
    with pg.cursor() as cur:
        cur.execute("SELECT count(compress_chunk(c, if_not_compressed => true)) "
                    "FROM show_chunks(%s::regclass, older_than => %s) c", (tbl, before))
        n = cur.fetchone()[0]
    pg.commit()
    return n


def backfill(pg, cutoff):
    setup(pg)
    with pg.cursor() as cur:
        cur.execute("SELECT tbl, start, stop FROM tsdb_migration.chunks")
        done = {(t, s, e) for t, s, e in cur.fetchall()}
    pg.commit()
    set_policies(pg, False)
    plan = {}
    for bucket, meas, tbl, live in TABLES:
        if ONLY and tbl not in ONLY:
            continue
        for week, s, e in chunks(cutoff, live, meas):
            plan.setdefault(week, []).append((bucket, meas, tbl, s, e))
    total = 0
    for week in sorted(plan):
        for bucket, meas, tbl, s, e in plan[week]:
            if (tbl, s, e) in done:
                continue
            # The last chunk ends at the dual-write start; InfluxDB must have
            # all of it before it replaces what Telegraf wrote there.
            while e > dt.datetime.now(UTC) - dt.timedelta(minutes=5) and e <= cutoff:
                log(f"waiting until {rfc(e)} is complete")
                time.sleep(60)
            points, rows, secs = load_chunk(pg, bucket, meas, tbl, s, e)
            total += points
            if points:
                log(f"{tbl} {rfc(s)} .. {rfc(e)}: {points} points -> {rows} rows in {secs:.1f}s (total {total})")
            time.sleep(PAUSE if points else 0)
        # Compress what is complete (the columnstore policies are paused).
        limit = min(week, cutoff - dt.timedelta(days=7))
        for tbl in sorted({x[2] for x in plan[week]}):
            compress_before(pg, tbl, limit)
    for _b, _m, tbl, _l in TABLES:
        compress_before(pg, tbl, cutoff - dt.timedelta(days=7))
    set_policies(pg, True)
    log(f"backfill done: {total} points")


# ---------------------------------------------------------------- verify

def influx_counts(bucket, meas, start, stop):
    counts = {}
    for pred in predicates(bucket, meas, start, stop):
        q = (f"from(bucket: {flux_str(bucket)}) |> range(start: {rfc(start)}, stop: {rfc(stop)})"
             f" |> filter(fn: (r) => {pred}) |> count() |> group(columns: [\"_field\"]) |> sum()")
        for _t, header, row in flux(q):
            f = row[header.index("_field")]
            counts[f] = counts.get(f, 0) + int(row[header.index("_value")])
    return counts


def influx_sums(bucket, meas, start, stop, fields):
    sums = {}
    for f in fields:  # one field per query: types may differ between fields
        for pred in predicates(bucket, meas, start, stop):
            q = (f"from(bucket: {flux_str(bucket)}) |> range(start: {rfc(start)}, stop: {rfc(stop)})"
                 f" |> filter(fn: (r) => {pred} and r._field == {flux_str(f)}) |> sum()"
                 f' |> map(fn: (r) => ({{_field: r._field, _value: float(v: r._value)}}))'
                 f' |> group(columns: ["_field"]) |> sum()')
            for _t, header, row in flux(q):
                v = row[header.index("_value")]
                if v != "":
                    sums[f] = sums.get(f, 0.0) + float(v)
    return sums


def pg_stats(pg, tbl, start, stop):
    reg = registry(pg, tbl)
    cols = table_columns(pg, tbl)
    fields = {}
    for c, d in cols.items():
        if c == "time" or reg.get(c, ("",))[0] == "tag":
            continue
        k, f, dtype = reg.get(c, ("field", c, d))
        fields[c] = (f, dtype)
    if not fields:
        return {}, {}
    parts = []
    for c, (_f, d) in fields.items():
        parts.append(sql.SQL("count({})").format(sql.Identifier(c)))
        parts.append(sql.SQL("sum({}::float8)").format(sql.Identifier(c)) if d in ("double", "long", "unsignedLong")
                     else sql.SQL("NULL"))
    with pg.cursor() as cur:
        cur.execute(sql.SQL("SELECT {} FROM {} WHERE time >= %s AND time < %s").format(
            sql.SQL(", ").join(parts), sql.Identifier(tbl)), (start, stop))
        vals = cur.fetchone()
    pg.commit()
    counts, sums, numeric = {}, {}, {}
    for i, (c, (f, d)) in enumerate(fields.items()):
        n, s = vals[2 * i], vals[2 * i + 1]
        if n:
            counts[f] = counts.get(f, 0) + n
            numeric.setdefault(f, set()).add(d)
            if s is not None:
                sums[f] = sums.get(f, 0.0) + s
    # Sums only for fields that are numeric throughout the range.
    sums = {f: s for f, s in sums.items() if numeric[f] <= {"double", "long", "unsignedLong"}}
    return counts, sums


def close(a, b):
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)


def verify_range(pg, bucket, meas, tbl, start, stop, label):
    ic = influx_counts(bucket, meas, start, stop)
    pc, ps = pg_stats(pg, tbl, start, stop)
    isums = influx_sums(bucket, meas, start, stop, sorted(ps)) if ps else {}
    bad = []
    with pg.cursor() as cur:
        for f in sorted(set(ic) | set(pc)):
            ok = ic.get(f, 0) == pc.get(f, 0) and (f not in ps or close(isums.get(f), ps.get(f)))
            cur.execute("INSERT INTO tsdb_migration.verify (tbl, start, stop, field, influx_count, pg_count, "
                        "influx_sum, pg_sum, ok) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT (tbl, start, stop, field) DO UPDATE SET influx_count = excluded.influx_count, "
                        "pg_count = excluded.pg_count, influx_sum = excluded.influx_sum, pg_sum = excluded.pg_sum, "
                        "ok = excluded.ok, checked_at = now()",
                        (tbl, start, stop, f, ic.get(f, 0), pc.get(f, 0), isums.get(f), ps.get(f), ok))
            if not ok:
                bad.append((f, ic.get(f, 0), pc.get(f, 0), isums.get(f), ps.get(f)))
    pg.commit()
    n = sum(ic.values())
    if bad:
        log(f"MISMATCH {label} {tbl} {rfc(start)} .. {rfc(stop)}: {bad[:10]}")
    return n, len(bad)


def rfc_us(t):
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def spot_check(pg, bucket, meas, tbl, cutoff, samples=5):
    """Compare a few random stored values with InfluxDB."""
    reg = registry(pg, tbl)
    tags = [c for c, (k, _f, _d) in reg.items() if k == "tag"]
    fields = {c: f for c, (k, f, _d) in reg.items() if k == "field"}
    bad = 0
    with pg.cursor() as cur:
        cur.execute(sql.SQL("SELECT min(time), max(time) FROM {} WHERE time < %s").format(sql.Identifier(tbl)),
                    (cutoff,))
        lo, hi = cur.fetchone()
        for _ in range(samples if lo else 0):
            t = lo + (hi - lo) * random.random()
            cur.execute(sql.SQL("SELECT * FROM {} WHERE time >= %s AND time < %s ORDER BY time LIMIT 1").format(
                sql.Identifier(tbl)), (t, cutoff))
            rec = cur.fetchone()
            if rec is None:
                continue
            row = dict(zip([d.name for d in cur.description], rec))
            cols = [c for c in fields if row.get(c) is not None]
            if not cols:
                continue
            col = random.choice(cols)
            want, ts = row[col], row["time"]
            pred = (f"r._measurement == {flux_str(meas)} and r._field == {flux_str(fields[col])}"
                    + "".join(f" and r[{flux_str(tg)}] == {flux_str(row[tg])}" for tg in tags if row.get(tg)))
            # PostgreSQL keeps microseconds, InfluxDB nanoseconds.
            q = (f"from(bucket: {flux_str(bucket)}) |> range(start: {rfc_us(ts - dt.timedelta(microseconds=1))}, "
                 f"stop: {rfc_us(ts + dt.timedelta(microseconds=1))}) |> filter(fn: (r) => {pred})")
            found = [r[h.index("_value")] for _t, h, r in flux(q)]

            def same(v):
                if isinstance(want, bool):
                    return v == str(want).lower()
                if isinstance(want, (int, float)) or hasattr(want, "as_integer_ratio"):
                    try:
                        return close(float(v), float(want))
                    except ValueError:
                        return False
                return v == str(want)
            if not any(same(v) for v in found):
                bad += 1
                log(f"SPOT MISMATCH {tbl} {ts} {col}={want!r} influx={found[:3]}")
            else:
                log(f"spot ok {tbl} {ts} {col}={want!r}")
    pg.commit()
    return bad


def verify(pg, cutoff):
    setup(pg)
    summary = {}
    for bucket, meas, tbl, live in TABLES:
        if ONLY and tbl not in ONLY:
            continue
        with pg.cursor() as cur:
            cur.execute("SELECT min(start), max(stop) FROM tsdb_migration.chunks WHERE tbl = %s", (tbl,))
            lo, hi = cur.fetchone()
        pg.commit()
        if lo is None:
            continue
        weeks = bad = points = 0
        w = START
        while w < hi:
            s, e = max(w, lo), min(w + dt.timedelta(days=7), hi)
            if s < e:
                n, b = verify_range(pg, bucket, meas, tbl, s, e, "backfill")
                weeks += 1
                bad += b
                points += n
                time.sleep(PAUSE / 4)
            w += dt.timedelta(days=7)
        spots = spot_check(pg, bucket, meas, tbl, hi)
        summary[tbl] = (weeks, points, bad, spots)
        log(f"verified {tbl}: {weeks} weeks, {points} points, {bad} mismatching fields, {spots} spot mismatches")
    log("summary " + json.dumps(summary))


def live(pg, cutoff, hours):
    """Compare whole hours since the dual-write start (up to `hours`)."""
    setup(pg)
    end = dt.datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start = max(cutoff, end - dt.timedelta(hours=hours))
    for bucket, meas, tbl, is_live in TABLES:
        if not is_live or (ONLY and tbl not in ONLY):
            continue
        h = start
        while h < end:
            n, b = verify_range(pg, bucket, meas, tbl, h, h + dt.timedelta(hours=1), "live")
            log(f"live {tbl} {rfc(h)}: {n} points, {b} mismatching fields")
            h += dt.timedelta(hours=1)


def main():
    cutoff = dt.datetime.fromisoformat(os.environ["CUTOFF"].replace("Z", "+00:00"))
    with psycopg.connect(application_name="influx-to-tsdb", autocommit=True) as pg:
        mode = sys.argv[1] if len(sys.argv) > 1 else "backfill"
        if mode == "backfill":
            backfill(pg, cutoff)
        elif mode == "verify":
            verify(pg, cutoff)
        elif mode == "live":
            live(pg, cutoff, int(sys.argv[2]) if len(sys.argv) > 2 else 6)
        else:
            sys.exit(__doc__)


if __name__ == "__main__":
    main()
