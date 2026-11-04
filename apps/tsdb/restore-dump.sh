#!/bin/bash
# Restore tsdb from a tsdb-dump "latest" directory (written by tsdb-dump.sh).
#
#   restore-dump.sh [--roles] [-j JOBS] [--no-verify] LATEST_DIR [DBNAME]
#
# Connect with the usual PG* variables, as a superuser, to a server running the
# same PostgreSQL major version and TimescaleDB version (timescaledb-version)
# with timescaledb in shared_preload_libraries. DBNAME (default tsdb) must not
# exist yet; it is created owned by $OWNER (default tsdb).
#   --roles      first run roles-with-passwords.sql (for a server without the
#                roles; "already exists" errors are harmless)
#   -j JOBS      parallel pg_restore jobs (default 1)
#   --no-verify  skip checking the data files against manifest.tsv
#
# It rebuilds the pg_dump directory in a temporary directory (toc.dat plus
# <dumpId>.dat.zst symlinks to the data files) and follows TimescaleDB's
# restore procedure: CREATE EXTENSION, timescaledb_pre_restore(), pg_restore,
# timescaledb_post_restore(), ANALYZE.
set -euo pipefail

roles=0 jobs=1 verify=1
while [[ $# -gt 0 ]]; do
  case $1 in
    --roles) roles=1; shift ;;
    -j) jobs=$2; shift 2 ;;
    --no-verify) verify=0; shift ;;
    -*) echo "unknown option $1" >&2; exit 2 ;;
    *) break ;;
  esac
done
[[ $# -ge 1 ]] || { sed -n '2,/^set /p' "$0" | grep '^#' >&2; exit 2; }
src=$(realpath "$1")
db=${2:-tsdb}
owner=${OWNER:-tsdb}

if [[ -e $src/COMMITTING || ! -e $src/manifest.tsv || ! -e $src/toc.dat ]]; then
  echo "$src is not a complete dump (missing manifest.tsv/toc.dat or an interrupted commit)" >&2
  exit 1
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cp "$src/toc.dat" "$tmp/toc.dat"
while IFS=$'\t' read -r id file _; do
  ln -s "$src/data/$file" "$tmp/$id.dat.zst"
done < "$src/manifest.tsv"
if (( verify )); then
  echo "verifying $(wc -l < "$src/manifest.tsv") data files"
  awk -F'\t' '{ print $3 "  " $2 }' "$src/manifest.tsv" | (cd "$src/data" && sha256sum --quiet -c -)
fi
pg_restore -l "$tmp" > /dev/null

psql() { command psql -X -v ON_ERROR_STOP=1 "$@"; }
if (( roles )); then
  command psql -X -q -d postgres -f "$src/roles-with-passwords.sql" || true
fi
if [[ $(psql -d postgres -Atc "select 1 from pg_database where datname = '$db'") ]]; then
  echo "database $db already exists" >&2
  exit 1
fi
version=$(cat "$src/timescaledb-version")
psql -d postgres -c "CREATE DATABASE \"$db\" OWNER \"$owner\""
psql -d "$db" -c "CREATE EXTENSION IF NOT EXISTS timescaledb VERSION '$version'"
psql -d "$db" -c "SELECT timescaledb_pre_restore()"
status=0
pg_restore -d "$db" -j "$jobs" "$tmp" || status=$?
psql -d "$db" -c "SELECT timescaledb_post_restore()"
psql -d "$db" -c "ANALYZE"
if (( status )); then
  echo "pg_restore exited with $status; check the errors above" >&2
fi
exit "$status"
