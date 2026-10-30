#!/bin/sh
# 1-minute compaction of old high-frequency data in InfluxDB bucket `home`.
#
# Rewritten (measurement mqtt_consumer only):
#   Tuya plugs (integration_id=tuya, kind=devices), Tuya/LIFX/Hue/ESPHome bulbs
#   (kind=lights, integration_id=tuya|lifx|hue1|esphome), Zigbee (zigbee2mqtt) and the
#   owner's BLE sensors (ble1..ble4, device_id in OWNER_BLE).
#   Numeric measurement fields (MEAN_FIELDS) -> 1-minute mean; every other field
#   (states, colours, counters, versions, strings) -> last value of the minute.
#   Dropped: BLE tempf, redundant Zigbee metadata (DROP_FIELDS), and every BLE
#   device that is not in OWNER_BLE (neighbours' sensors, phones, beacons).
# Never touched: water meter, NIBE, FMI, prices, electricity, Fingrid, Verisure
# and everything else.
#
# Works one shard group (Monday 00:00Z .. next Monday 00:00Z) at a time:
#   1. guard     the mean fields hold floats (a float mean written back over an
#                integer field would be a type conflict after the delete)
#   2. stage     aggregate the week into bucket home_staging, 6 h per query
#   3. verify    per series+field and day: staged points == non-empty raw
#                minutes, staged last value == raw last value (last fields);
#                plug energy (integral of raw_19) within 1 %
#   4. delete    the week's raw points of every rewritten series (incl. the
#                dropped fields and foreign BLE devices)
#   5. copy back home_staging -> home, 6 h per query
#   6. verify    per series+field counts in home == home_staging, and nothing
#                left of the deleted series/fields
#   7. mark      write compaction_done,week=<start> to home_staging (kept) and
#                drop the staged points of the week
# Any failure stops the run. If it stops after step 4, the week's data is only
# in home_staging (and the backup): rerun with  --resume-copyback <week-start>.
#
# Usage (influx CLI with INFLUX_HOST, INFLUX_ORG and INFLUX_TOKEN set):
#   compact.sh plan  FROM TO          list weeks and whether they are done
#   compact.sh stage FROM TO          steps 1-3 only (dry run, home untouched)
#   compact.sh run   FROM TO          steps 1-7
#   compact.sh auto  [MAX_WEEKS]      run every unmarked week that ended more
#                                     than MIN_AGE_DAYS ago (the CronJob)
#   compact.sh --resume-copyback WEEK steps 5-7 for one week
# FROM/TO are Monday 00:00Z timestamps (RFC3339); TO is exclusive.
set -eu
set -o pipefail

SRC=${SRC:-home}
STAGE=${STAGE:-home_staging}
TEMPLATE=${TEMPLATE:-/scripts/compact-window.flux}
CHUNK_H=${CHUNK_H:-6}
MIN_AGE_DAYS=${MIN_AGE_DAYS:-90}
PAUSE=${PAUSE:-5}             # seconds between queries, lets InfluxDB breathe
FIRST_WEEK=${FIRST_WEEK:-2024-07-29T00:00:00Z}  # first shard group with MQTT data
WORK=${WORK:-/tmp/compact}
# Weeks to leave alone (space-separated Monday dates). If a week's data is
# partly invisible to queries, compacting it would leave the hidden raw data
# behind: fix the shard first (see docs/monitoring.md), then remove it here.
SKIP_WEEKS=${SKIP_WEEKS:-""}
mkdir -p "$WORK"

# The owner's BLE sensors (SwitchBot Outdoor Meters and one Xiaomi LYWSD03MMC).
OWNER_BLE="CE2A82463674 D63534385106 D83535301C43 D7353530665A C76A00462055 CE2A8486490B
C76A05065C4E D83534387029 D7353530520F D83431306571 D4343037362D D9353438450D C76A03460A73
CE2A82463295 CE2A83062A34 D43535302778 D9343130222A C76A05062842 C76A0306280B C76A0246647E
A4C138FD13EF"
# 1-minute mean; everything else in scope gets the last value of the minute.
MEAN_FIELDS="raw_18 raw_19 raw_20 brightness tempc hum batt volt rssi pres pressure temperature
humidity illuminance illuminance_lux battery voltage linkquality target_distance power current
co2 voc pm25 soil_moisture device_temperature"
# Dropped from every rewritten series (tempf is BLE; the rest only exist in Zigbee
# except color_h/color_s, which bulbs keep: they are dropped for Zigbee only).
DROP_FIELDS="tempf device_applicationVersion device_hardwareVersion device_manufacturerID
device_networkAddress device_stackVersion device_zclVersion update_installed_version
update_latest_version update_progress update_remaining fading_time minimum_range
large_motion_detection_distance large_motion_detection_sensitivity
medium_motion_detection_distance medium_motion_detection_sensitivity small_detection_distance
small_detection_sensitivity occupancy_timeout color_temp_startup"
ZIGBEE_DROP="color_h color_s"
# Delete predicates: together they cover every series SCOPE_ALL matches.
PREDICATES='_measurement="mqtt_consumer" AND integration_id="tuya" AND kind="devices"
_measurement="mqtt_consumer" AND integration_id="tuya" AND kind="lights"
_measurement="mqtt_consumer" AND integration_id="lifx" AND kind="lights"
_measurement="mqtt_consumer" AND integration_id="hue1" AND kind="lights"
_measurement="mqtt_consumer" AND integration_id="esphome" AND kind="lights"
_measurement="mqtt_consumer" AND integration_id="ble1"
_measurement="mqtt_consumer" AND integration_id="ble2"
_measurement="mqtt_consumer" AND integration_id="ble3"
_measurement="mqtt_consumer" AND integration_id="ble4"
_measurement="mqtt_consumer" AND integration_id="zigbee2mqtt"'

log() { echo "$(date -u +%FT%TZ) $*"; }
die() { log "ERROR: $*"; exit 1; }

join_cmp() { # sep op column values... -> r.col op "a" sep r.col op "b" ...
  sep=$1; op=$2; col=$3; shift 3; out=""
  for v in "$@"; do out="${out:+$out $sep }r.$col $op \"$v\""; done; echo "$out"
}
BLE_IDS='r.integration_id == "ble1" or r.integration_id == "ble2" or r.integration_id == "ble3" or r.integration_id == "ble4"'
# Every series the delete predicates hit.
SCOPE_ALL="r._measurement == \"mqtt_consumer\" and ((r.integration_id == \"tuya\" and r.kind == \"devices\") or (r.kind == \"lights\" and (r.integration_id == \"tuya\" or r.integration_id == \"lifx\" or r.integration_id == \"hue1\" or r.integration_id == \"esphome\")) or r.integration_id == \"zigbee2mqtt\" or $BLE_IDS)"
# The series that are kept (rewritten).
SCOPE="r._measurement == \"mqtt_consumer\" and ((r.integration_id == \"tuya\" and r.kind == \"devices\") or (r.kind == \"lights\" and (r.integration_id == \"tuya\" or r.integration_id == \"lifx\" or r.integration_id == \"hue1\" or r.integration_id == \"esphome\")) or r.integration_id == \"zigbee2mqtt\" or (($BLE_IDS) and ($(join_cmp or == device_id $OWNER_BLE))))"
MEAN="$(join_cmp or == _field $MEAN_FIELDS)"
NOT_DROPPED="$(join_cmp and != _field $DROP_FIELDS) and (r.integration_id != \"zigbee2mqtt\" or ($(join_cmp and != _field $ZIGBEE_DROP)))"
KEEP="$NOT_DROPPED"
LAST="$(join_cmp and != _field $MEAN_FIELDS) and $NOT_DROPPED"

q() { influx query --raw -f "$1"; }                       # annotated CSV
data_rows() { awk '!/^#/ && NF && !/,result,table,/'; }   # never fails on empty

iso() { date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ; }
# Inclusive end for influx delete (its range includes stop): last ns before $1.
iso_end() { date -u -d "@$(( $1 - 1 ))" +%Y-%m-%dT%H:%M:%S.999999999Z; }
epoch() { date -u -d "$(echo "$1" | sed 's/T/ /; s/Z$//')" +%s; }

health() {
  influx ping >/dev/null 2>&1 || { sleep 30; influx ping >/dev/null 2>&1 || die "InfluxDB not answering"; }
}

render() { # start stop src dst
  awk -v s="$1" -v e="$2" -v src="$3" -v dst="$4" -v scope="$SCOPE" -v mean="$MEAN" -v last="$LAST" '
    { gsub(/__START__/, s); gsub(/__STOP__/, e); gsub(/__SRC__/, src); gsub(/__DST__/, dst)
      i = index($0, "__SCOPE__"); if (i) $0 = substr($0, 1, i - 1) scope substr($0, i + 9)
      i = index($0, "__MEAN__");  if (i) $0 = substr($0, 1, i - 1) mean  substr($0, i + 8)
      i = index($0, "__LAST__");  if (i) $0 = substr($0, 1, i - 1) last  substr($0, i + 8)
      print }' "$TEMPLATE"
}

marked() { # week-start -> exit 0 if done
  cat > "$WORK/m.flux" <<EOF
from(bucket: "$STAGE") |> range(start: 2020-01-01T00:00:00Z)
  |> filter(fn: (r) => r._measurement == "compaction_done" and r.week == "$1") |> count()
EOF
  [ -n "$(q "$WORK/m.flux" | data_rows)" ]
}

guard_types() { # ws we
  cat > "$WORK/g.flux" <<EOF
import "types"
from(bucket: "$SRC") |> range(start: $1, stop: $2)
  |> filter(fn: (r) => $SCOPE)
  |> filter(fn: (r) => $MEAN)
  |> first()
  |> filter(fn: (r) => not types.isType(v: r._value, type: "float"))
  |> group() |> keep(columns: ["_field", "device_id"])
EOF
  bad=$(q "$WORK/g.flux" | data_rows)
  [ -z "$bad" ] || die "non-float values in mean fields for $1: $bad"
}

stage_week() { # ws we
  ws=$(epoch "$1"); we=$(epoch "$2"); step=$((CHUNK_H * 3600))
  influx delete --bucket "$STAGE" --start "$1" --stop "$(iso_end $we)" \
    --predicate '_measurement="mqtt_consumer"' >/dev/null
  t=$ws
  while [ "$t" -lt "$we" ]; do
    health
    render "$(iso $t)" "$(iso $((t + step)))" "$SRC" "$STAGE" > "$WORK/c.flux"
    out=$(q "$WORK/c.flux" | data_rows | awk -F, '{s += $NF} END {print s + 0}')
    log "  staged $(iso $t) +${CHUNK_H}h: $out points"
    t=$((t + step)); sleep "$PAUSE"
  done
}

KEY='r._field + "|" + (if exists r.integration_id then r.integration_id else "") + "|" + (if exists r.kind then r.kind else "") + "|" + (if exists r.device_id then r.device_id else "") + "|" + (if exists r.topic then r.topic else "") + "|" + (if exists r.name then r.name else "") + "|" + (if exists r.brand then r.brand else "") + "|" + (if exists r.model then r.model else "") + "|" + (if exists r.model_id then r.model_id else "") + "|" + (if exists r.mac then r.mac else "") + "|" + (if exists r.type then r.type else "")'

# Sorted "key,count" (and "key,last" for last fields) lines for one day.
# mode raw: non-empty minutes in raw data (what staging must hold); plain: points.
series_stats() { # bucket start stop mode
  if [ "$4" = raw ]; then
    agg='|> aggregateWindow(every: 1m, fn: count, createEmpty: false) |> count()'
  else
    agg='|> count()'
  fi
  cat > "$WORK/s.flux" <<EOF
from(bucket: "$1") |> range(start: $2, stop: $3)
  |> filter(fn: (r) => $SCOPE)
  |> filter(fn: (r) => $KEEP)
  $agg
  |> map(fn: (r) => ({k: "n|" + $KEY, v: string(v: r._value)}))
  |> group() |> keep(columns: ["k", "v"])
EOF
  cat > "$WORK/l.flux" <<EOF
from(bucket: "$1") |> range(start: $2, stop: $3)
  |> filter(fn: (r) => $SCOPE)
  |> filter(fn: (r) => $LAST)
  |> last()
  |> map(fn: (r) => ({k: "l|" + $KEY, v: string(v: r._value)}))
  |> group() |> keep(columns: ["k", "v"])
EOF
  { q "$WORK/s.flux" | data_rows; q "$WORK/l.flux" | data_rows; } | awk -F, '{print $(NF-1) "," $NF}' | sort
}

leftover() { # start stop -> number of points of deleted series/fields still in SRC
  cat > "$WORK/x.flux" <<EOF
from(bucket: "$SRC") |> range(start: $1, stop: $2)
  |> filter(fn: (r) => $SCOPE_ALL)
  |> filter(fn: (r) => not ($SCOPE) or not ($KEEP))
  |> count() |> group() |> sum()
EOF
  q "$WORK/x.flux" | data_rows | awk -F, '{s += $NF} END {print s + 0}'
}

verify_counts() { # bucketA modeA bucketB modeB ws we   (day by day)
  ws=$(epoch "$5"); we=$(epoch "$6"); t=$ws
  while [ "$t" -lt "$we" ]; do
    health
    a=$(series_stats "$1" "$(iso $t)" "$(iso $((t + 86400)))" "$2")
    b=$(series_stats "$3" "$(iso $t)" "$(iso $((t + 86400)))" "$4")
    if [ "$a" != "$b" ]; then
      echo "$a" > "$WORK/a.csv"; echo "$b" > "$WORK/b.csv"
      diff "$WORK/a.csv" "$WORK/b.csv" | head -20
      die "mismatch $1($2) vs $3($4) on $(iso $t)"
    fi
    log "  verified $(iso $t): $(echo "$a" | grep -c '^n|') series/fields, counts and last values match"
    t=$((t + 86400)); sleep "$PAUSE"
  done
}

verify_energy() { # ws we: integral of plug power, raw vs staged, per plug, < 1 %
  for b in "$SRC" "$STAGE"; do
    cat > "$WORK/e.flux" <<EOF
from(bucket: "$b") |> range(start: $1, stop: $2)
  |> filter(fn: (r) => r._measurement == "mqtt_consumer" and r.integration_id == "tuya" and r.kind == "devices" and r._field == "raw_19")
  |> group(columns: ["device_id"])
  |> aggregateWindow(every: 1d, fn: (tables=<-, column) => tables |> integral(unit: 1h), createEmpty: false)
  |> sum() |> group() |> keep(columns: ["device_id", "_value"])
EOF
    q "$WORK/e.flux" | data_rows | awk -F, '{print $(NF-1) "," $NF}' > "$WORK/e_$b.csv"
  done
  # busybox has no join(1)
  awk -F, 'NR == FNR { s[$1] = $2; next } { r = s[$1] + 0; d = r - $2; if (d < 0) d = -d
      printf "  energy %s raw %.0f Wh staged %.0f Wh\n", $1, r / 10, $2 / 10
      if (r > 1000 && d / r > 0.01) bad = 1 } END { exit bad }' \
    "$WORK/e_$SRC.csv" "$WORK/e_$STAGE.csv" || die "plug energy differs by more than 1 %"
}

delete_raw() { # ws we
  stop=$(iso_end "$(epoch "$2")")
  echo "$PREDICATES" | while IFS= read -r p; do
    health
    log "  delete $1..$stop where $p"
    influx delete --bucket "$SRC" --start "$1" --stop "$stop" --predicate "$p"
    sleep "$PAUSE"
  done
}

copy_back() { # ws we
  ws=$(epoch "$1"); we=$(epoch "$2"); step=$((CHUNK_H * 3600)); t=$ws
  while [ "$t" -lt "$we" ]; do
    health
    cat > "$WORK/cb.flux" <<EOF
from(bucket: "$STAGE") |> range(start: $(iso $t), stop: $(iso $((t + step))))
  |> filter(fn: (r) => r._measurement == "mqtt_consumer")
  |> to(bucket: "$SRC") |> count() |> group() |> sum()
EOF
    out=$(q "$WORK/cb.flux" | data_rows | awk -F, '{s += $NF} END {print s + 0}')
    log "  copied back $(iso $t) +${CHUNK_H}h: $out points"
    t=$((t + step)); sleep "$PAUSE"
  done
}

finish_week() { # ws we
  verify_counts "$SRC" plain "$STAGE" plain "$1" "$2"
  left=$(leftover "$1" "$2")
  [ "$left" = 0 ] || die "$left points of deleted series/fields remain in $SRC for $1"
  echo "compaction_done,week=$1 done=1i $(epoch "$1")000000000" | influx write --bucket "$STAGE" --precision ns
  influx delete --bucket "$STAGE" --start "$1" --stop "$(iso_end "$(epoch "$2")")" \
    --predicate '_measurement="mqtt_consumer"'
  log "week $1 done"
}

do_week() { # mode ws we
  case " $(echo $SKIP_WEEKS) " in *" ${2%%T*} "*) log "week $2 is in SKIP_WEEKS, skipping"; return 0 ;; esac
  if marked "$2"; then log "week $2 already compacted, skipping"; return 0; fi
  log "week $2: guard"; guard_types "$2" "$3"
  log "week $2: stage"; stage_week "$2" "$3"
  log "week $2: verify"; verify_counts "$SRC" raw "$STAGE" plain "$2" "$3"; verify_energy "$2" "$3"
  if [ "$1" = stage ]; then log "week $2 staged and verified (dry run)"; return 0; fi
  log "week $2: delete"; delete_raw "$2" "$3"
  log "week $2: copy back"; copy_back "$2" "$3"
  finish_week "$2" "$3"
}

weeks() { # from to -> "start stop" lines
  t=$(epoch "$1"); end=$(epoch "$2")
  [ $(( (t - 345600) % 604800 )) -eq 0 ] || die "$1 is not a Monday 00:00Z shard boundary"
  while [ "$t" -lt "$end" ]; do echo "$(iso $t) $(iso $((t + 604800)))"; t=$((t + 604800)); done
}

case "${1:-}" in
  plan)  weeks "$2" "$3" | while read -r a b; do
           if marked "$a"; then echo "$a done"; else echo "$a pending"; fi; done ;;
  stage|run)
         weeks "$2" "$3" > "$WORK/weeks"
         while read -r a b; do do_week "$1" "$a" "$b"; done < "$WORK/weeks" ;;
  auto)  max=${2:-8}; n=0
         cutoff=$(( $(date -u +%s) - MIN_AGE_DAYS * 86400 ))
         weeks "$FIRST_WEEK" "$(iso $cutoff)" > "$WORK/weeks"
         while read -r a b; do
           [ "$(epoch "$b")" -le "$cutoff" ] || continue
           case " $(echo $SKIP_WEEKS) " in *" ${a%%T*} "*) continue ;; esac
           if marked "$a"; then continue; fi
           n=$((n + 1)); [ "$n" -le "$max" ] || break
           do_week run "$a" "$b"
         done < "$WORK/weeks"
         log "auto: $n week(s) handled" ;;
  --resume-copyback)
         b=$(iso $(( $(epoch "$2") + 604800 )))
         copy_back "$2" "$b"; finish_week "$2" "$b" ;;
  *) sed -n '2,38p' "$0"; exit 2 ;;
esac
