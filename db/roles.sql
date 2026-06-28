-- roles.sql — read-only API role; engine write-path unreachable from API
-- Run as superuser after migrations.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cop_api') THEN
        CREATE ROLE cop_api LOGIN PASSWORD 'changeme';
    END IF;
END
$$;

-- Read-only on all read-model tables
GRANT USAGE ON SCHEMA world, geo, ml TO cop_api;
GRANT SELECT ON ALL TABLES IN SCHEMA world TO cop_api;
GRANT SELECT ON ALL TABLES IN SCHEMA geo TO cop_api;
GRANT SELECT ON ALL TABLES IN SCHEMA ml TO cop_api;

-- API may append to review_annotation only (no UPDATE/DELETE — enforced by triggers)
GRANT INSERT ON world.review_annotation TO cop_api;

-- Explicitly deny write access to the append-only log
REVOKE ALL ON log.observation FROM cop_api;
REVOKE ALL ON log.obs_rejection FROM cop_api;

-- Future tables inherit
ALTER DEFAULT PRIVILEGES IN SCHEMA world GRANT SELECT ON TABLES TO cop_api;
ALTER DEFAULT PRIVILEGES IN SCHEMA geo GRANT SELECT ON TABLES TO cop_api;
