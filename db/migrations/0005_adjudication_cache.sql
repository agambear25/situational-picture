-- 0005_adjudication_cache.sql — LLM verdict cache + ML run provenance

CREATE TABLE IF NOT EXISTS ml.adjudication_cache (
    pair_key            TEXT NOT NULL,          -- sha256(sorted(obs_a_norm, obs_b_norm))
    prompt_version      TEXT NOT NULL,
    model_version       TEXT NOT NULL,
    schema_version      TEXT NOT NULL,
    embedding_version   TEXT NOT NULL,          -- pinned digest; part of cache key
    verdict             JSONB NOT NULL,         -- {same: bool, confidence: float, rationale: str}
    evidence_spans      JSONB,                  -- [{obs_id, span, score}] — Cross-check #3
    tier                TEXT NOT NULL CHECK (tier IN ('gate_3b', 'adjudicator_7b', 'escalation_14b', 'claude')),
    latency_ms          INTEGER,
    tokens_in           INTEGER,
    tokens_out          INTEGER,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (pair_key, prompt_version, model_version, schema_version, embedding_version)
);
CREATE INDEX IF NOT EXISTS adjcache_pair ON ml.adjudication_cache(pair_key);
