-- 0010_assessment.sql — Phase-4a extensions to world.assessment (created skeletal in 0004).
--
-- The base table (0004) has: assessment_id, theater_id, cell_id (NOT NULL, FK→grid_cell),
-- assessment_type, score, rationale, as_of, created_at. Phase 4a adds the columns the insights
-- engine needs: a SOFT event reference (link a significance score to its event; no FK, the read
-- model is rebuildable), an anomaly subkind, and the score components for explainability.
--
-- It remains a REBUILDABLE read model: assess/run.py truncates the theater's slice and recomputes
-- from world.event. assessment_type ∈ significance|anomaly (4a); mobility|flood|exposure|gaps later.

ALTER TABLE world.assessment ADD COLUMN IF NOT EXISTS event_id   UUID;
ALTER TABLE world.assessment ADD COLUMN IF NOT EXISTS subkind    TEXT;
ALTER TABLE world.assessment ADD COLUMN IF NOT EXISTS components JSONB NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_assessment_theater_type_score
    ON world.assessment (theater_id, assessment_type, score DESC);
