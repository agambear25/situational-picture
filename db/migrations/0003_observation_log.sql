-- 0003_observation_log.sql — the immutable event-sourcing log

CREATE TABLE IF NOT EXISTS log.observation (
    obs_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    theater_id          TEXT NOT NULL,
    source_id           TEXT NOT NULL,
    source_family_id    TEXT NOT NULL,          -- independence key; echo/syndication collapses to one family
    modality            TEXT NOT NULL CHECK (modality IN ('text', 'thermal', 'imagery', 'ais', 'adsb', 'seismic')),
    obs_type            TEXT NOT NULL,          -- controlled taxonomy; validated via config/taxonomy.yaml
    occurred_at         TSTZRANGE NOT NULL,     -- when the THING happened; may be wide if uncertain
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    cell_id             TEXT REFERENCES geo.grid_cell(cell_id),
    geom_precision_m    DOUBLE PRECISION,       -- radius of positional uncertainty
    place_id            BIGINT,                 -- resolved gazetteer id, NULL if no place match
    raw_text            TEXT,
    embedding           vector(384),            -- pinned all-MiniLM-L6-v2; dim enforced by ingest/embedding.py
    content_hash        TEXT NOT NULL UNIQUE,   -- sha256 of (normalized text + snapped cell + time-bucket); blocks exact dups
    lang                TEXT,
    self_conf           REAL,                   -- source-asserted confidence, if provided
    meta                JSONB NOT NULL DEFAULT '{}'
);

-- APPEND-ONLY: no row may be updated or deleted after write
CREATE OR REPLACE FUNCTION log.enforce_append_only()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'observation log is append-only: % not permitted', TG_OP;
END;
$$;

CREATE TRIGGER observation_no_update
    BEFORE UPDATE ON log.observation
    FOR EACH ROW EXECUTE FUNCTION log.enforce_append_only();

CREATE TRIGGER observation_no_delete
    BEFORE DELETE ON log.observation
    FOR EACH ROW EXECUTE FUNCTION log.enforce_append_only();

-- Indexes
CREATE INDEX IF NOT EXISTS obs_cell_id ON log.observation(cell_id);
CREATE INDEX IF NOT EXISTS obs_occurred_at ON log.observation USING GIST(occurred_at);
CREATE INDEX IF NOT EXISTS obs_content_hash ON log.observation(content_hash);
CREATE INDEX IF NOT EXISTS obs_theater ON log.observation(theater_id, ingested_at DESC);
CREATE INDEX IF NOT EXISTS obs_source_family ON log.observation(source_family_id);
CREATE INDEX IF NOT EXISTS obs_embedding_hnsw ON log.observation
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Rejection ledger: every observation that could not be placed or was invalid
-- Zero silent drops: raw item either lands in log.observation or here.
CREATE TABLE IF NOT EXISTS log.obs_rejection (
    rejection_id    BIGSERIAL PRIMARY KEY,
    theater_id      TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    raw_payload     JSONB NOT NULL,
    reason          TEXT NOT NULL,             -- 'no_cell_resolve', 'invalid_type', 'exact_dup', 'invalid_geom', etc.
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS obs_rejection_theater ON log.obs_rejection(theater_id, ingested_at DESC);
