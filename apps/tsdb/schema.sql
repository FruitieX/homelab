-- Schema of the tsdb database (TimescaleDB), see docs/monitoring.md.
-- Run once at bootstrap (postInitApplicationSQLRefs, as the superuser) and
-- safe to re-run: psql -v ON_ERROR_STOP=1 -d tsdb -f schema.sql
--
-- One hypertable per InfluxDB measurement, in Telegraf's outputs.postgresql
-- layout: a `time` column, one text column per tag and one typed column per
-- field. Telegraf (role tsdb) adds columns for new fields itself.
-- InfluxDB's nordpool bucket (measurement price) is table nordpool_price.
--
-- Tables whose writers re-send the same points (FMI, Fingrid, Datahub, Nord
-- Pool) have an upsert trigger: a re-sent point (same tags and time) replaces
-- the old one, as it did in InfluxDB. It is a trigger rather than a unique
-- index because TimescaleDB checks unique indexes on compressed chunks before
-- the trigger runs, and Telegraf can't send ON CONFLICT. The live
-- MQTT/NIBE/ESPHome tables are append-only.

BEGIN;
SET ROLE tsdb;

-- Re-sent point = same tags and time: the new row replaces the old one.
-- Rows already sent in the same statement are visible here, so duplicates
-- within one COPY are handled too.
CREATE OR REPLACE FUNCTION replace_point() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  cond text := 'time = $1.time';
  col text;
BEGIN
  FOREACH col IN ARRAY TG_ARGV LOOP
    cond := cond || format(' AND %1$I IS NOT DISTINCT FROM $1.%1$I', col);
  END LOOP;
  EXECUTE format('DELETE FROM %I.%I WHERE %s', TG_TABLE_SCHEMA, TG_TABLE_NAME, cond) USING NEW;
  RETURN NEW;
END $$;

CREATE OR REPLACE PROCEDURE tsdb_table(
  name text, columns text, chunk interval, segmentby text, index_cols text,
  upsert_key text DEFAULT NULL)
LANGUAGE plpgsql AS $$
BEGIN
  IF to_regclass(name) IS NOT NULL THEN
    RETURN;
  END IF;
  EXECUTE format('CREATE TABLE %I (time timestamptz NOT NULL, %s)', name, columns);
  PERFORM create_hypertable(name::regclass, by_range('time', chunk));
  EXECUTE format('CREATE INDEX %I ON %I (%s, time DESC)', name || '_series', name, coalesce(upsert_key, index_cols));
  IF upsert_key IS NOT NULL THEN
    EXECUTE format('CREATE TRIGGER replace_point BEFORE INSERT ON %I FOR EACH ROW EXECUTE FUNCTION replace_point(%s)',
                   name, (SELECT string_agg(quote_literal(trim(c)), ', ') FROM unnest(string_to_array(upsert_key, ',')) c));
  END IF;
  -- Columnstore: chunks older than 7 days are compressed, one segment per series.
  EXECUTE format('ALTER TABLE %I SET (timescaledb.enable_columnstore, timescaledb.segmentby = %L, timescaledb.orderby = %L)',
                 name, segmentby, 'time DESC');
  CALL add_columnstore_policy(name::regclass, after => interval '7 days');
END $$;

-- MQTT devices via telegraf-home. Tags as Telegraf sets them; fields are
-- added by Telegraf (and the backfill) as devices report them. A field whose
-- InfluxDB type differed in some weeks keeps those values in <field>_<type>
-- (for example sensor_value_string).
CALL tsdb_table('mqtt_consumer',
  'kind text, integration_id text, device_id text, topic text, name text, entity text, '
  'mac text, brand text, model text, model_id text, type text',
  '1 day', 'integration_id, device_id', 'device_id');
CREATE INDEX IF NOT EXISTS mqtt_consumer_integration ON mqtt_consumer (integration_id, time DESC);

CALL tsdb_table('esphome', 'component text, device_id text, entity text, topic text, value double precision',
  '7 days', 'device_id, entity', 'device_id, entity');
CALL tsdb_table('esphome_binary', 'component text, device_id text, entity text, topic text, value text',
  '7 days', 'device_id, entity', 'device_id, entity');

-- telegraf-cloud
CALL tsdb_table('nibe', 'id text, name text, unit text, value double precision',
  '7 days', 'id', 'id');
CALL tsdb_table('verisure', 'topic text, value text', '30 days', 'topic', 'topic');
CALL tsdb_table('fmi_observation', 'param text NOT NULL, value double precision',
  '7 days', 'param', NULL, 'param');
CALL tsdb_table('fmi_forecast', 'param text NOT NULL, value double precision',
  '30 days', 'param', NULL, 'param');
CALL tsdb_table('fmi_daily', 'param text NOT NULL, value double precision',
  '365 days', 'param', NULL, 'param');
CALL tsdb_table('fingrid', 'series text NOT NULL, value double precision',
  '7 days', 'series', NULL, 'series');
CALL tsdb_table('fingrid_hourly', 'series text NOT NULL, value double precision',
  '90 days', 'series', NULL, 'series');

-- scripts/datahub-import.py (hourly, then 15-minute energy)
CALL tsdb_table('electricity', 'source text NOT NULL, resolution text NOT NULL, kwh double precision',
  '90 days', 'source, resolution', NULL, 'source, resolution');

-- Nord Pool spot price in c/kWh incl. VAT (nordpool-mqtt, spot-price-backfill.py)
CALL tsdb_table('nordpool_price', 'id text NOT NULL, price double precision',
  '90 days', 'id', NULL, 'id');

RESET ROLE;

-- Grafana reads everything, including tables Telegraf creates later.
GRANT USAGE ON SCHEMA public TO grafana_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE tsdb IN SCHEMA public GRANT SELECT ON TABLES TO grafana_ro;
COMMIT;
