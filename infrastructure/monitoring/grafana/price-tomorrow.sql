-- SQL for the Grafana rule "Electricity prices tomorrow" (home-price-tomorrow in grafana-alerting.sops.yaml),
-- ready for when nordpool-mqtt writes to tsdb: the rule still runs its Flux query until then, because
-- nordpool_price only gets new prices from backfill runs. Swap it in as query A (datasource tsdb, format
-- time_series); B and C stay. Backtested against the Flux query on six days with expensive or negative
-- hours (identical messages) and outside 14:00-24:00 (no message).
-- Tomorrow's day-ahead prices (c/kWh incl. VAT) arrive around 14:45 local.
-- From 14:00 to midnight, fire when tomorrow has hours at or above 30 c/kWh or
-- at or below 0 c/kWh. The message travels in the "text" label, with runs of
-- consecutive hours, e.g. "7–10, 17–19 (up to 34.2 c)".
WITH day AS (SELECT date_add(date_trunc('day', now(), 'Europe/Helsinki'), interval '1 day', 'Europe/Helsinki') AS d),
hours AS (
  SELECT time_bucket('1 hour', time) AS t, avg(price) AS p
  FROM nordpool_price, day
  WHERE time >= day.d AND time < date_add(day.d, interval '1 day', 'Europe/Helsinki')
  GROUP BY 1),
h AS (SELECT t, extract(hour FROM t AT TIME ZONE 'Europe/Helsinki')::int AS h, p FROM hours),
runs AS (
  SELECT kind, min(h) AS a, max(h) + 1 AS b FROM (
    SELECT 'high' AS kind, h, h - row_number() OVER (ORDER BY t) AS run FROM h WHERE p >= 30
    UNION ALL
    SELECT 'low', h, h - row_number() OVER (ORDER BY t) FROM h WHERE p <= 0) r
  GROUP BY kind, run),
words AS (
  SELECT (SELECT string_agg(a || '–' || b, ', ' ORDER BY a) FROM runs WHERE kind = 'high')
         || ' (up to ' || (SELECT trim_scale(round(max(p)::numeric, 1)) FROM h WHERE p >= 30) || ' c)' AS expensive,
         (SELECT string_agg(a || '–' || b, ', ' ORDER BY a) FROM runs WHERE kind = 'low')
         || ' (down to ' || (SELECT trim_scale(round(min(p)::numeric, 1)) FROM h WHERE p <= 0) || ' c)' AS cheap),
msg AS (
  SELECT concat_ws('; ', 'expensive ' || expensive, 'cheap ' || cheap) AS text,
         extract(hour FROM now() AT TIME ZONE 'Europe/Helsinki') >= 14 AND (SELECT count(*) FROM hours) >= 20 AS ready
  FROM words)
SELECT now() AS time,
       CASE WHEN ready AND text <> '' THEN 1.0 ELSE 0.0 END AS "Value",
       CASE WHEN ready AND text <> '' THEN 'Tomorrow''s electricity is ' || text || ' per kWh.' ELSE '' END AS text
FROM msg
