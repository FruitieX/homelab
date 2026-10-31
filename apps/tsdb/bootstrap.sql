-- Run once when the tsdb cluster is created (postInitApplicationSQLRefs, as
-- the superuser in database tsdb), before schema.sql. Safe to re-run.
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Calendar days are Helsinki days: date_trunc/time_bucket without an explicit
-- zone and Grafana's raw timestamps both use this.
ALTER DATABASE tsdb SET timezone TO 'Europe/Helsinki';

-- The read-only roles grafana_ro and homectl_ro are managed by the Cluster
-- (password, pg_monitor); they are created here first so their settings can
-- be applied at bootstrap. A role added later needs these statements run by
-- hand (as postgres, in database tsdb), plus its grants from schema.sql.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'grafana_ro') THEN
    CREATE ROLE grafana_ro LOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'homectl_ro') THEN
    CREATE ROLE homectl_ro LOGIN;
  END IF;
END $$;
ALTER ROLE grafana_ro SET default_transaction_read_only = on;
ALTER ROLE grafana_ro SET statement_timeout = '30s';
ALTER ROLE homectl_ro SET default_transaction_read_only = on;
ALTER ROLE homectl_ro SET statement_timeout = '30s';

REVOKE CONNECT ON DATABASE tsdb FROM PUBLIC;
GRANT CONNECT ON DATABASE tsdb TO grafana_ro, homectl_ro;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
