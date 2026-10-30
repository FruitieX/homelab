// Compact one time window of bucket __SRC__ into bucket __DST__ at 1-minute
// resolution (one value per series, field and minute; _time = start of the
// minute; every series keeps all its tags). compact.sh fills in:
//   __START__/__STOP__  RFC3339 window inside one shard group (Monday-Monday)
//   __SCOPE__           the series that are compacted (measurement mqtt_consumer):
//                       Tuya plugs, Tuya/LIFX/Hue bulbs, Zigbee, and the owner's
//                       BLE sensors (other BLE devices are not copied, so the
//                       delete in compact.sh removes them)
//   __MEAN__            numeric measurement fields -> 1-minute mean
//   __LAST__            every other kept field (states, colours, counters,
//                       versions, strings) -> last value of the minute; the
//                       dropped fields (BLE tempf, redundant Zigbee metadata)
//                       are excluded here
// Only plain ==/!= comparisons on tags and _field are used, so InfluxDB pushes
// the whole range/filter/aggregateWindow down to storage (low memory).
// Each block yields only the number of points it wrote.

data = () => from(bucket: "__SRC__")
    |> range(start: __START__, stop: __STOP__)
    |> filter(fn: (r) => __SCOPE__)

data()
    |> filter(fn: (r) => __MEAN__)
    |> aggregateWindow(every: 1m, fn: mean, timeSrc: "_start", createEmpty: false)
    |> to(bucket: "__DST__")
    |> count()
    |> group()
    |> sum()
    |> yield(name: "mean")

data()
    |> filter(fn: (r) => __LAST__)
    |> aggregateWindow(every: 1m, fn: last, timeSrc: "_start", createEmpty: false)
    |> to(bucket: "__DST__")
    |> count()
    |> group()
    |> sum()
    |> yield(name: "last")
