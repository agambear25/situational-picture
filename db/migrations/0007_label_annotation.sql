-- 0007_label_annotation.sql — human-in-the-loop labels & gray-band verdicts (Phase 2 UI)
-- APPEND-ONLY: the durable system-of-record that eval/fixtures_io.py serializes into
--   eval/fixtures/realworld_ua_v1.yaml  (kind='incident_label')
--   eval/fixtures/verdicts_v1.json      (kind='gray_verdict')
-- Fixtures are GENERATED from these rows so a regen is reproducible, never lossy hand-editing.
-- Mirrors world.review_annotation (0006): confirm/split/reject history is never deleted.

CREATE TABLE IF NOT EXISTS world.label_annotation (
    label_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind            TEXT NOT NULL CHECK (kind IN ('incident_label', 'gray_verdict')),
    -- payload shape depends on kind (validated by eval/fixtures_io.py, not the DB):
    --   incident_label: {incident_id, theater_id, obs_refs[], expect{band,n_families,flags_any[]},
    --                    must_not_merge_with[], observations[]}
    --   gray_verdict:   {content_hash_a, content_hash_b, obs_type_a, obs_type_b,
    --                    same:bool, confidence:float, rationale, evidence_spans[]}
    payload         JSONB NOT NULL,
    -- pinned versions captured AT LABEL TIME so a regenerated verdict cache keys identically.
    model_version   TEXT,                 -- composite_model_version when captured (gray_verdict)
    prompt_version  TEXT,
    schema_version  TEXT,
    embedding_version TEXT,
    run_id          TEXT,                 -- the fusion run whose gray band was adjudicated
    analyst         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION world.enforce_label_append_only()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'label_annotation is append-only: % not permitted', TG_OP;
END;
$$;

CREATE TRIGGER label_no_update
    BEFORE UPDATE ON world.label_annotation
    FOR EACH ROW EXECUTE FUNCTION world.enforce_label_append_only();

CREATE TRIGGER label_no_delete
    BEFORE DELETE ON world.label_annotation
    FOR EACH ROW EXECUTE FUNCTION world.enforce_label_append_only();

CREATE INDEX IF NOT EXISTS label_kind ON world.label_annotation(kind, created_at DESC);
CREATE INDEX IF NOT EXISTS label_run  ON world.label_annotation(run_id);
