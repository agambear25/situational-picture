-- 0013_area_read.sql — cache for the synthesis Read (one per area of interest).
--
-- The Read (synth/run.py) is generated offline by a runner (like fusion.run / assess.run) using the
-- local LLM, then cached here; the read-only API serves it. Regenerated only when the input context
-- changes (input_hash). ON DELETE CASCADE with the AOI — a read has no meaning without its area.

CREATE TABLE IF NOT EXISTS world.area_read (
    aoi_id       BIGINT PRIMARY KEY REFERENCES world.area_of_interest(aoi_id) ON DELETE CASCADE,
    summary      TEXT NOT NULL,
    indicators   TEXT NOT NULL,                 -- escalating | steady | quieting
    provenance   JSONB NOT NULL DEFAULT '[]',
    input_hash   TEXT NOT NULL,
    generated_by TEXT NOT NULL,                 -- 'llm' | 'template'
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
