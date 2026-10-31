"""Retention for Loki's boltdb-shipper period (data before 2026-10-31).

Loki's compactor stops its retention pass at the first old boltdb table whose
index lacks an entry for an expiring chunk ("could not find entry of chunk ...
to remove it"), so expired data piled up. This removes boltdb index tables and
chunk files a day before the compactor would expire them, so it never reaches
them. It never touches data from the TSDB period, which Loki's own retention
handles. Once every boltdb table is gone (late January 2027) it does nothing,
and the CronJob can be deleted.
"""
import base64
import os
import shutil
import time

ROOT = "/var/loki/chunks"
DAY = 86400
TSDB_FROM = 1793404800  # 2026-10-31T00:00:00Z, first day of the TSDB period
KEEP_DAYS = int(os.environ.get("KEEP_DAYS", "89"))  # Loki retention is 90d

cutoff = min(int(time.time()) - KEEP_DAYS * DAY, TSDB_FROM)
cutoff_table = cutoff // DAY  # tables before this day are entirely older
dry = os.environ.get("DRY_RUN") == "1"

tables = sorted(
    t for t in os.listdir(os.path.join(ROOT, "index"))
    if t.startswith("loki_index_") and int(t.rsplit("_", 1)[1]) < cutoff_table
)
for t in tables:
    if not dry:
        shutil.rmtree(os.path.join(ROOT, "index", t))

chunks = size = 0
for stream in os.scandir(os.path.join(ROOT, "fake")):
    if not stream.is_dir():
        continue
    for f in os.scandir(stream.path):
        try:
            through = int(base64.b64decode(f.name).decode().split(":")[1], 16)
        except (ValueError, IndexError):
            continue
        if through < cutoff * 1000:
            chunks += 1
            size += f.stat().st_size
            if not dry:
                os.remove(f.path)

print(f"{'would remove' if dry else 'removed'} {len(tables)} tables "
      f"{tables[:1]}..{tables[-1:]}, {chunks} chunks ({size / 2**20:.0f} MiB) "
      f"before {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(cutoff))}")
