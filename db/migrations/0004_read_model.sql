-- 0004_read_model.sql — rebuildable read models: event, entity, assessment, source

-- Entity enum has NO 'person' value — analytical-not-targeting enforced in schema
CREATE TYPE world.entity_kind AS ENUM (
    'formation', 'site', 'vessel', 'vehicle', 'unit', 'installation'
);

CREATE TABLE IF NOT EXISTS world.event (
    event_id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    theater_id              TEXT NOT NULL,
    event_type              TEXT NOT NULL,
    cell_id                 TEXT NOT NULL REFERENCES geo.grid_cell(cell_id),
    resolved_precision_m    DOUBLE PRECISION,
    occurred_at             TSTZRANGE NOT NULL,
    status                  TEXT NOT NULL CHECK (status IN ('candidate', 'confirmed', 'stale', 'retracted')),
    confidence              REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    confidence_band         TEXT NOT NULL CHECK (confidence_band IN ('High', 'Medium', 'Low', 'Rumored')),
    n_sources               INTEGER NOT NULL DEFAULT 1,
    n_independent_families  INTEGER NOT NULL DEFAULT 1,
    flags                   TEXT[] NOT NULL DEFAULT '{}',  -- 'verification-needed', 'echo-only', etc.
    created_from_obs        UUID[] NOT NULL DEFAULT '{}',
    decision_time           TSTZRANGE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS event_cell ON world.event(cell_id);
CREATE INDEX IF NOT EXISTS event_theater ON world.event(theater_id, status);
CREATE INDEX IF NOT EXISTS event_occurred ON world.event USING GIST(occurred_at);

CREATE TABLE IF NOT EXISTS world.event_observation (
    event_id        UUID NOT NULL REFERENCES world.event(event_id),
    obs_id          UUID NOT NULL REFERENCES log.observation(obs_id),
    member_score    REAL,
    PRIMARY KEY (event_id, obs_id)
);

CREATE TABLE IF NOT EXISTS world.entity (
    entity_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    theater_id      TEXT NOT NULL,
    kind            world.entity_kind NOT NULL,
    label           TEXT NOT NULL,
    cell_id         TEXT REFERENCES geo.grid_cell(cell_id),
    confidence      REAL,
    first_seen      TIMESTAMPTZ,
    last_seen       TIMESTAMPTZ,
    meta            JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS entity_theater ON world.entity(theater_id, kind);
CREATE INDEX IF NOT EXISTS entity_cell ON world.entity(cell_id);

CREATE TABLE IF NOT EXISTS world.assessment (
    assessment_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    theater_id      TEXT NOT NULL,
    cell_id         TEXT NOT NULL REFERENCES geo.grid_cell(cell_id),
    assessment_type TEXT NOT NULL,             -- 'significance', 'anomaly', 'mobility', 'flood', 'exposure', 'gaps'
    score           REAL,
    rationale       TEXT,
    as_of           TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS assessment_cell ON world.assessment(cell_id, assessment_type);

CREATE TABLE IF NOT EXISTS world.source (
    source_id       TEXT PRIMARY KEY,
    family_id       TEXT NOT NULL,
    label           TEXT NOT NULL,
    modality        TEXT NOT NULL,
    reliability_w   REAL NOT NULL DEFAULT 1.0,
    license         TEXT,
    url             TEXT,
    notes           TEXT
);
