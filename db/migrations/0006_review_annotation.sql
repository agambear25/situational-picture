-- 0006_review_annotation.sql — human review / verify-queue annotations (Cross-check #2)
-- APPEND-ONLY: analysts confirm, split, or reject events; history is never deleted.

CREATE TABLE IF NOT EXISTS world.review_annotation (
    review_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id    UUID NOT NULL REFERENCES world.event(event_id),
    action      TEXT NOT NULL CHECK (action IN ('confirm', 'split', 'reject', 'flag')),
    reason      TEXT,
    analyst     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION world.enforce_review_append_only()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'review_annotation is append-only: % not permitted', TG_OP;
END;
$$;

CREATE TRIGGER review_no_update
    BEFORE UPDATE ON world.review_annotation
    FOR EACH ROW EXECUTE FUNCTION world.enforce_review_append_only();

CREATE TRIGGER review_no_delete
    BEFORE DELETE ON world.review_annotation
    FOR EACH ROW EXECUTE FUNCTION world.enforce_review_append_only();

CREATE INDEX IF NOT EXISTS review_event ON world.review_annotation(event_id, created_at DESC);
