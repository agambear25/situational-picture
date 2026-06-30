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

-- The append-only log is READABLE for the evidence trail (GET /events/{id}) and the
-- no-drop ledger (GET /rejections) — these are core analytical-honesty surfaces.
-- Read-only: SELECT only; the API is never granted INSERT/UPDATE/DELETE here, and the
-- append-only triggers in 0003 block writes even if a grant were ever added by mistake.
GRANT USAGE ON SCHEMA log TO cop_api;
GRANT SELECT ON log.observation TO cop_api;
GRANT SELECT ON log.obs_rejection TO cop_api;

-- API may APPEND ONLY to the two human-in-the-loop annotation tables (no UPDATE/DELETE —
-- enforced by triggers in 0006/0007). Everything else in the engine write-path is unreachable.
GRANT INSERT ON world.review_annotation TO cop_api;
GRANT INSERT ON world.label_annotation TO cop_api;

-- Areas of interest: analyst-created named entities/areas. INSERT + DELETE (the analyst can remove
-- an AOI), plus the id sequence. Still no access to the evidence log or the read-model events.
GRANT SELECT, INSERT, DELETE ON world.area_of_interest TO cop_api;
GRANT SELECT, INSERT, DELETE ON world.aoi_cell TO cop_api;
GRANT USAGE, SELECT ON SEQUENCE world.area_of_interest_aoi_id_seq TO cop_api;

-- Explicitly deny WRITE access to the append-only log (defense in depth; SELECT above stands).
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON log.observation FROM cop_api;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON log.obs_rejection FROM cop_api;

-- Future tables inherit read-only
ALTER DEFAULT PRIVILEGES IN SCHEMA world GRANT SELECT ON TABLES TO cop_api;
ALTER DEFAULT PRIVILEGES IN SCHEMA geo GRANT SELECT ON TABLES TO cop_api;
