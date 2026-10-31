-- Run once when the tsdb cluster is created (postInitApplicationSQLRefs, as
-- the superuser in database tsdb), before schema.sql. Safe to re-run.
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Calendar days are Helsinki days: date_trunc/time_bucket without an explicit
-- zone and Grafana's raw timestamps both use this.
ALTER DATABASE tsdb SET timezone TO 'Europe/Helsinki';

-- grafana_ro is managed by the Cluster (password, pg_monitor); it is created
-- here first so its settings can be applied at bootstrap.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'grafana_ro') THEN
    CREATE ROLE grafana_ro LOGIN;
  END IF;
END $$;
ALTER ROLE grafana_ro SET default_transaction_read_only = on;
ALTER ROLE grafana_ro SET statement_timeout = '30s';

REVOKE CONNECT ON DATABASE tsdb FROM PUBLIC;
GRANT CONNECT ON DATABASE tsdb TO grafana_ro;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
