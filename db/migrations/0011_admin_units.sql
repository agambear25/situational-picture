-- 0011_admin_units.sql — the admin hierarchy substrate (region → district → community).
--
-- geo.cell_context has admin_l1/l2/l3 NAME columns since Phase 0 but they were never populated
-- (no boundary geometry was loaded). This adds the geometry table the rollup/drill-down UI needs,
-- plus the id columns so an event can be grouped to its unit at any level by a plain join.
--
-- Source: geoBoundaries gbOpen UKR ADM1 (oblast) / ADM2 (raion) / ADM3 (hromada), CC-BY, clipped
-- to the theater AOI by geo/admin_load.py. Reference substrate (static), not the event log.

CREATE TABLE IF NOT EXISTS geo.admin_unit (
    admin_id    TEXT PRIMARY KEY,                 -- geoBoundaries shapeID
    theater_id  TEXT NOT NULL,
    level       INT  NOT NULL CHECK (level IN (1, 2, 3)),   -- 1 oblast, 2 raion, 3 hromada
    name        TEXT NOT NULL,
    parent_id   TEXT REFERENCES geo.admin_unit(admin_id),   -- level L-1 unit containing this one
    geom        geometry(MultiPolygon, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_admin_unit_geom   ON geo.admin_unit USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_admin_unit_lookup ON geo.admin_unit (theater_id, level, parent_id);

-- The id columns let a cell (hence any event in it) be rolled up to a unit at any level by id,
-- no point-in-polygon at query time. Names stay for display (event detail Region/District/Locality).
ALTER TABLE geo.cell_context ADD COLUMN IF NOT EXISTS admin_l1_id TEXT;
ALTER TABLE geo.cell_context ADD COLUMN IF NOT EXISTS admin_l2_id TEXT;
ALTER TABLE geo.cell_context ADD COLUMN IF NOT EXISTS admin_l3_id TEXT;
