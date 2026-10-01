"""Run once with ForkBasket stopped, /source read-only and /target on NFS.

Copies all application files and creates a consistent SQLite backup, including
any remaining WAL records. Refuses to overwrite destination data. Source files
are never modified. Prints only integrity results, counts and content hashes.
"""

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile


def contents(connection):
    digest = hashlib.sha256()
    counts = {}
    schema = connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
    ).fetchall()
    digest.update(json.dumps(schema).encode())
    for (name,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ):
        quoted = '"' + name.replace('"', '""') + '"'
        rows = []
        for row in connection.execute(f"SELECT * FROM {quoted}"):
            values = [
                {"blob": value.hex()} if isinstance(value, bytes) else value
                for value in row
            ]
            rows.append(hashlib.sha256(json.dumps(values).encode()).digest())
        counts[name] = len(rows)
        digest.update(name.encode())
        for row_hash in sorted(rows):
            digest.update(row_hash)
    return digest.hexdigest(), counts


def migrate(source, target):
    source, target = Path(source), Path(target)
    database = "forkbasket.sqlite3"
    if not (source / database).is_file():
        raise RuntimeError("Source database is missing")
    if any(target.iterdir()):
        raise RuntimeError("Destination is not empty; refusing to overwrite it")
    # Recover/check a local copy, so even leftover WAL files never require a
    # writable source mount or a WAL index on the NFS destination.
    with tempfile.TemporaryDirectory() as temporary:
        local = Path(temporary) / "source"
        shutil.copytree(source, local, ignore=shutil.ignore_patterns("lost+found"))
        with sqlite3.connect(local / database) as original:
            if original.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise RuntimeError("Source integrity check failed")
            expected, counts = contents(original)
            destination_path = target / database
            # Create privately before SQLite opens it, rather than leaving a
            # window where the database uses the process's default umask.
            destination_path.touch(mode=0o600, exist_ok=False)
            with sqlite3.connect(destination_path) as destination:
                original.backup(destination)
                mode = destination.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
                if mode != "delete":
                    raise RuntimeError("Could not enable DELETE journaling")
                destination.execute("PRAGMA synchronous=EXTRA")
                if destination.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise RuntimeError("Destination integrity check failed")
                actual, copied_counts = contents(destination)
                if actual != expected or copied_counts != counts:
                    raise RuntimeError("Source and destination content differ")
        for entry in local.iterdir():
            if entry.name in {database, database + "-wal", database + "-shm"}:
                continue
            if entry.is_dir():
                shutil.copytree(entry, target / entry.name)
            else:
                shutil.copy2(entry, target / entry.name)
        print(json.dumps({
            "sourceIntegrity": "ok", "destinationIntegrity": "ok",
            "journalMode": mode, "contentHash": expected, "tableCounts": counts,
            "status": "verified",
        }, indent=2))


if __name__ == "__main__":
    migrate("/source", "/target")
