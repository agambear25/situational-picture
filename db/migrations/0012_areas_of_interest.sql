-- 0012_areas_of_interest.sql — Tier 2 of the named-geographic-entity model (UI sub-project 1).
--
-- geo.geo_feature (Tier 1, the reference feature library) already exists with layer/geom/
-- properties/source — used as-is (layer = kind, properties = {subkind, name, ...}). This adds the
-- analyst-defined, focused entities + their cell-set.
--
-- An AOI is the analyst's "named entity / area of interest": a river bank as an obstacle, a
-- defensive line, a grid of interest. Created by promoting a geo_feature, drawing a line/polygon,
-- or lassoing cells — all resolved ONCE to the 1km cells they cover (world.aoi_cell), which is how
-- an AOI joins to events and is the analytical-not-targeting boundary (cells, never precise geom
-- vs observations). AOIs are analyst annotations: persistent user data, NOT rebuilt by fusion.run.

CREATE TABLE IF NOT EXISTS world.area_of_interest (
    aoi_id            BIGSERIAL PRIMARY KEY,
    theater_id        TEXT NOT NULL,
    kind              TEXT NOT NULL,            -- obstacle|defensive_line|grid_of_interest|named_feature|corridor|…
    label             TEXT NOT NULL,
    source            TEXT NOT NULL CHECK (source IN ('derived', 'drawn')),
    source_feature_id BIGINT REFERENCES geo.geo_feature(feature_id) ON DELETE SET NULL,
    geom              geometry(Geometry, 4326),  -- line/polygon/point; NULL for a pure cell lasso
    note              TEXT,
    created_by        TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_aoi_theater_kind ON world.area_of_interest (theater_id, kind);
CREATE INDEX IF NOT EXISTS idx_aoi_geom ON world.area_of_interest USING GIST (geom);

-- The load-bearing join: AOI → the 1km cells it covers (soft cell ref; grid is stable substrate).
CREATE TABLE IF NOT EXISTS world.aoi_cell (
    aoi_id  BIGINT NOT NULL REFERENCES world.area_of_interest(aoi_id) ON DELETE CASCADE,
    cell_id TEXT   NOT NULL,
    PRIMARY KEY (aoi_id, cell_id)
);
CREATE INDEX IF NOT EXISTS idx_aoi_cell_cell ON world.aoi_cell (cell_id);
