-- 0009_soft_event_refs.sql
--
-- world.event is a REBUILDABLE read model: fusion.run truncates and rematerialises it from the
-- append-only observation log. Event ids are deterministic (content-hash derived), so an event's
-- id is stable ONLY while its set of member observations is unchanged. Add a new feed (e.g. the
-- UNOSAT historical layer) and some events recompose into different ids — at which point any
-- append-only audit row holding a HARD foreign key to world.event blocks the rebuild
-- (ForeignKeyViolation on DELETE).
--
-- Fix: audit tables hold event_id as a SOFT reference (no enforced FK). The annotation records
-- "the analyst reviewed/labelled the event with this id at this time"; if a later rebuild changes
-- that id, the annotation remains as historical record (its event_id simply may not resolve).
-- This is the correct CQRS posture: the write-side audit log does not depend on read-side identity.
--
-- event_observation -> event is intentionally LEFT in place: write_events deletes it before the
-- event rows in the same transaction, so it never blocks the rebuild and it keeps the membership
-- join clean within a single materialised generation.

ALTER TABLE world.review_annotation DROP CONSTRAINT IF EXISTS review_annotation_event_id_fkey;
ALTER TABLE world.label_annotation  DROP CONSTRAINT IF EXISTS label_annotation_event_id_fkey;
