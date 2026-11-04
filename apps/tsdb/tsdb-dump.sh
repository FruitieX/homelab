#!/bin/bash
# Nightly logical backup of tsdb, run by the tsdb-dump CronJob (see tsdb-dump.yaml).
#
# /dumps/latest is laid out for incremental backups (Hyper Backup): a pg_dump
# directory-format dump whose data files are stored under stable names, and a
# file is only replaced when its content changes. Old compressed chunks
# therefore keep the same file (name, content and mtime) night after night.
#
#   latest/toc.dat                pg_dump's table of contents
#   latest/manifest.tsv           dumpId <TAB> data file <TAB> sha256, per TABLE DATA entry
#   latest/data/<schema>.<table>.dat.zst
#   latest/globals.sql, roles-with-passwords.sql, timescaledb-version
#   latest/restore-dump.sh        rebuilds the pg_dump directory and restores it
#
# An update is prepared next to the old files as *.new, then committed with
# renames. The COMMITTING marker makes an interrupted commit resumable, so
# latest/ is always one complete dump once the next run starts.
#
# With KEEP_DAYS > 0 a dated pg_dump -Fc is written too and kept for that many
# days (/dumps/YYYY-MM-DD/tsdb.dump).
set -euo pipefail
shopt -s nullglob

dumps=${DUMPS:-/dumps}
latest=$dumps/latest
work=$dumps/.work
keep_days=${KEEP_DAYS:-0}
log() { printf '%s %s\n' "$(date +%T)" "$*"; }

commit() {
  local f manifest=$latest/manifest.tsv
  [[ -e $latest/manifest.tsv.new ]] && manifest=$latest/manifest.tsv.new
  local -A keep=()
  while IFS=$'\t' read -r _ f _; do keep[$f]=1; done < "$manifest"
  for f in "$latest"/data/*.new; do mv -f "$f" "${f%.new}"; done
  # Drop data files of tables that no longer exist.
  for f in "$latest"/data/*; do
    [[ ${keep[${f##*/}]:-} ]] || { rm -f "$f"; echo "${f##*/}" >> "$work/removed"; }
  done
  for f in "$latest"/*.new; do
    [[ $f == "$latest/manifest.tsv.new" ]] || mv -f "$f" "${f%.new}"
  done
  [[ -e $latest/manifest.tsv.new ]] && mv -f "$latest/manifest.tsv.new" "$latest/manifest.tsv"
  rm -f "$latest/COMMITTING"
}

# Whether $2 exists with the content of $1. The \restrict lines of
# pg_dumpall's output carry a random key, so they don't count.
same() {
  [[ -e $2 ]] || return 1
  if [[ $2 == *.sql ]]; then
    cmp -s <(grep -v '^\\\(un\)\?restrict ' "$1") <(grep -v '^\\\(un\)\?restrict ' "$2")
  else
    cmp -s "$1" "$2"
  fi
}

# Stage $1 as $2.new unless $2 already has its content.
stage() {
  if same "$1" "$2"; then
    rm -f "$1"
    return 1
  fi
  mv -f "$1" "$2.new"
}

mkdir -p "$latest/data"
rm -rf "$work"
mkdir -p "$work"
if [[ -e $latest/COMMITTING ]]; then
  log "finishing the interrupted commit of the previous run"
  commit
fi
rm -f "$latest"/*.new "$latest"/data/*.new

log "dumping globals and roles"
pg_dumpall --globals-only --no-role-passwords > "$work/globals.sql"
pg_dumpall --roles-only > "$work/roles-with-passwords.sql"
chmod 600 "$work/roles-with-passwords.sql"
psql -X -d tsdb -Atc "select extversion from pg_extension where extname = 'timescaledb'" > "$work/timescaledb-version"
cp /scripts/restore-dump.sh "$work/restore-dump.sh"

# TimescaleDB's catalog has circular foreign keys; pg_dump warns about them,
# which is expected.
log "pg_dump -Fd"
pg_dump -Fd -Z zstd:1 -d tsdb -f "$work/dump"

# Map each TABLE DATA dumpId to a stable file name. pg_restore -l prints
# "<dumpId>; <tableoid> <oid> TABLE DATA <schema> <table> <owner>".
pg_restore -l "$work/dump" |
  awk '$4 == "TABLE" && $5 == "DATA" { if (NF != 8) { print "unexpected TOC line: " $0 > "/dev/stderr"; exit 1 }
                                        sub(/;$/, "", $1); print $1 "\t" $6 "." $7 ".dat.zst" }' > "$work/ids.tsv"
if [[ $(cut -f2 "$work/ids.tsv" | sort | uniq -d) ]]; then
  echo "duplicate data file names" >&2; exit 1
fi
if ! diff <(cut -f1 "$work/ids.tsv" | sort) \
          <(find "$work/dump" -mindepth 1 -printf '%f\n' | grep -vx toc.dat | sed 's/\.dat\.zst$//' | sort); then
  echo "TABLE DATA entries and data files differ" >&2; exit 1
fi

declare -A old_sum=()
if [[ -e $latest/manifest.tsv ]]; then
  while IFS=$'\t' read -r _ file sum; do old_sum[$file]=$sum; done < "$latest/manifest.tsv"
fi

log "staging changed data files"
unchanged=0 changed=0 added=0
: > "$work/manifest.tsv"
while IFS=$'\t' read -r id file; do
  src=$work/dump/$id.dat.zst
  sum=$(sha256sum < "$src" | cut -d' ' -f1)
  printf '%s\t%s\t%s\n' "$id" "$file" "$sum" >> "$work/manifest.tsv"
  if [[ ${old_sum[$file]:-} == "$sum" && -e $latest/data/$file ]]; then
    unchanged=$((unchanged + 1))
    rm -f "$src"
  else
    if [[ -e $latest/data/$file ]]; then changed=$((changed + 1)); else added=$((added + 1)); fi
    mv -f "$src" "$latest/data/$file.new"
  fi
done < "$work/ids.tsv"
stage "$work/dump/toc.dat" "$latest/toc.dat" || true
for f in globals.sql roles-with-passwords.sql timescaledb-version restore-dump.sh manifest.tsv; do
  stage "$work/$f" "$latest/$f" || true
done

log "committing"
: > "$work/removed"
touch "$latest/COMMITTING"
commit
log "latest/: $unchanged data files unchanged, $changed changed, $added new, $(wc -l < "$work/removed") removed"
du -sh "$latest"
rm -rf "$work"

if (( keep_days > 0 )); then
  dir=$dumps/$(date +%F)
  log "pg_dump -Fc to $dir"
  rm -rf "$dir.tmp"
  mkdir -p "$dir.tmp"
  cp -p "$latest/globals.sql" "$latest/roles-with-passwords.sql" "$latest/timescaledb-version" "$dir.tmp/"
  pg_dump -Fc -Z 1 -d tsdb -f "$dir.tmp/tsdb.dump"
  rm -rf "$dir" && mv "$dir.tmp" "$dir"
  ls -la "$dir"
fi
find "$dumps" -mindepth 1 -maxdepth 1 -type d -name '20[0-9][0-9]-[0-9][0-9]-[0-9][0-9]*' -mtime +"$keep_days" -exec rm -rf {} +
log "done"
