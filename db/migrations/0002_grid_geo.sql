-- 0002_grid_geo.sql — grid cells, geo features, cell context, control overlay, place aliases

CREATE TABLE IF NOT EXISTS geo.grid_cell (
    cell_id         TEXT PRIMARY KEY,          -- MGRS 1km e.g. '37UDB1234'
    theater_id      TEXT NOT NULL,             -- 'ua_donbas', 'delhi', etc.
    geom            geometry(Polygon, 4326) NOT NULL,
    centroid        geometry(Point, 4326) NOT NULL,
    admin_l1        TEXT,                      -- oblast
    admin_l2        TEXT,                      -- raion
    admin_l3        TEXT,                      -- hromada (local_seq parent)
    admin_path      TEXT,                      -- full dotted path
    local_seq       INTEGER,                   -- stable seq within admin_l3
    label           TEXT,                      -- human label e.g. 'Avdiivka-16'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS grid_cell_geom_gist ON geo.grid_cell USING GIST(geom);
CREATE INDEX IF NOT EXISTS grid_cell_theater ON geo.grid_cell(theater_id);

CREATE TABLE IF NOT EXISTS geo.geo_feature (
    feature_id      BIGSERIAL PRIMARY KEY,
    theater_id      TEXT NOT NULL,
    layer           TEXT NOT NULL,             -- 'admin', 'landcover', 'dem', 'building', 'transport', 'hydro', 'gazetteer'
    cell_id         TEXT REFERENCES geo.grid_cell(cell_id),
    geom            geometry(Geometry, 4326),
    properties      JSONB NOT NULL DEFAULT '{}',
    as_of           DATE,
    source          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS geo_feature_geom_gist ON geo.geo_feature USING GIST(geom);
CREATE INDEX IF NOT EXISTS geo_feature_layer ON geo.geo_feature(layer, theater_id);
CREATE INDEX IF NOT EXISTS geo_feature_cell ON geo.geo_feature(cell_id);

CREATE TABLE IF NOT EXISTS geo.cell_context (
    cell_id             TEXT PRIMARY KEY REFERENCES geo.grid_cell(cell_id),
    theater_id          TEXT NOT NULL,
    -- terrain
    mean_slope_deg      REAL,
    -- landcover (ESA WorldCover codes)
    dominant_landcover  INTEGER,
    landcover_label     TEXT,
    -- hydro
    has_river           BOOLEAN NOT NULL DEFAULT FALSE,
    has_bridge          BOOLEAN NOT NULL DEFAULT FALSE,
    -- built environment
    builtup_pct         REAL,
    building_count      INTEGER,
    -- transport
    nearest_road_class  TEXT,
    -- admin (denormalized for fast lookup)
    admin_l1            TEXT,
    admin_l2            TEXT,
    admin_l3            TEXT,
    label               TEXT,
    -- Phase 3 stubs (NULL in MVP)
    soil_type           TEXT,
    has_utility         BOOLEAN,
    has_installation    BOOLEAN,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS geo.control_status (
    status_id       BIGSERIAL PRIMARY KEY,
    cell_id         TEXT NOT NULL REFERENCES geo.grid_cell(cell_id),
    theater_id      TEXT NOT NULL,
    controller      TEXT,                      -- 'ua', 'ru', 'contested', 'unknown'
    confidence      REAL,
    as_of           TIMESTAMPTZ NOT NULL,
    source          TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    -- APPEND-ONLY: no UPDATE/DELETE, latest row = current status
);
CREATE INDEX IF NOT EXISTS control_status_cell_asof ON geo.control_status(cell_id, as_of DESC);

CREATE TABLE IF NOT EXISTS geo.place_alias (
    alias_id        BIGSERIAL PRIMARY KEY,
    place_id        BIGINT NOT NULL,           -- geonames feature_id or internal id
    theater_id      TEXT NOT NULL,
    name            TEXT NOT NULL,
    lang            TEXT NOT NULL,             -- 'uk', 'ru', 'en', 'translit', 'hist'
    is_preferred    BOOLEAN NOT NULL DEFAULT FALSE,
    cell_id         TEXT REFERENCES geo.grid_cell(cell_id),
    geom            geometry(Point, 4326),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS place_alias_name_trgm ON geo.place_alias USING GIN(name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS place_alias_cell ON geo.place_alias(cell_id);
CREATE INDEX IF NOT EXISTS place_alias_place ON geo.place_alias(place_id, lang);
