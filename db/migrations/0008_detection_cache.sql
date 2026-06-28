-- 0008_detection_cache.sql — Phase-3 imagery detection cache (the determinism contract)
-- Mirrors ml.adjudication_cache: version-pinned, replay-reused. A neural (or classical)
-- detector's output is cached here keyed by (tile_hash, model_digest, detector), so replay
-- reuses detections and NEVER re-runs the model or re-hits Google Earth Engine.
--
-- HARD: stores ONLY coarsened detections — cell_id + a BANDED confidence — never a precise
-- coordinate and never a raw logit. This is invariants #2/#3/#5 expressed in the schema.

CREATE TABLE IF NOT EXISTS ml.detection_cache (
    detection_id    BIGSERIAL PRIMARY KEY,
    cache_key       TEXT NOT NULL,          -- sha256(tile_hash | model_digest | detector)
    tile_hash       TEXT NOT NULL,
    model_digest    TEXT NOT NULL,          -- pinned weight digest (classical detectors use a version string)
    detector        TEXT NOT NULL,
    obs_type        TEXT NOT NULL,          -- discrete/quantized; must exist in config/taxonomy.yaml
    self_conf_band  TEXT NOT NULL,          -- banded ('high'/'medium'/…), never a raw model score
    self_conf       REAL NOT NULL,          -- representative value of the band (small float drift can't move it)
    cell_id         TEXT NOT NULL,          -- coarsened placement; NO precise lon/lat is ever persisted
    occurred_start  TEXT NOT NULL,          -- ISO-8601 acquisition window (cache, not analytic store)
    occurred_end    TEXT NOT NULL,
    text            TEXT,
    meta            JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS detection_cache_key  ON ml.detection_cache(cache_key);
CREATE INDEX IF NOT EXISTS detection_cache_tile ON ml.detection_cache(tile_hash, model_digest);
