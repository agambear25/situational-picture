"""
Read-through verdict cache. The key is order-independent over the pair and carries
every pinned version, so a replay re-uses verdicts exactly and never re-pays the model.

Two implementations:
  - PgVerdictCache: production, backed by ml.adjudication_cache.
  - FrozenVerdictCache: hermetic CI, backed by eval/fixtures/verdicts_v1.json (read-only,
    no model, no DB). A miss raises so a CI run can never silently call Ollama.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from llm.schema import Verdict


@dataclass(frozen=True)
class PairKey:
    """Order-independent identity of an adjudication request + all pinned versions."""
    hash_lo: str
    hash_hi: str
    type_lo: str
    type_hi: str
    prompt_version: str
    model_version: str
    schema_version: str
    embedding_version: str

    @classmethod
    def build(cls, hash_a, hash_b, type_a, type_b, cfg) -> "PairKey":
        hlo, hhi = sorted([hash_a, hash_b])
        tlo, thi = sorted([type_a, type_b])
        return cls(
            hash_lo=hlo, hash_hi=hhi, type_lo=tlo, type_hi=thi,
            prompt_version=cfg.prompt_version,
            model_version=cfg.composite_model_version,
            schema_version=cfg.schema_version,
            embedding_version=cfg.embedding_version,
        )

    def digest(self) -> str:
        raw = "|".join([
            self.hash_lo, self.hash_hi, self.type_lo, self.type_hi,
            self.prompt_version, self.model_version, self.schema_version, self.embedding_version,
        ])
        return hashlib.sha256(raw.encode()).hexdigest()


class FrozenVerdictCache:
    """Read-only cache from a frozen JSON snapshot. The CI stand-in for Ollama."""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        with open(self._path) as f:
            self._data: dict[str, dict] = json.load(f)

    def get(self, key: PairKey) -> Optional[Verdict]:
        rec = self._data.get(key.digest())
        if rec is None:
            return None
        return Verdict(**rec)

    def put(self, key: PairKey, verdict: Verdict) -> None:
        raise RuntimeError(
            "FrozenVerdictCache is read-only. Regenerate eval/fixtures/verdicts_v1.json "
            "deliberately (via the labelling tool / regen script) on a version bump."
        )

    def keys(self) -> set[str]:
        return set(self._data.keys())


class PgVerdictCache:
    """Production read-through cache over ml.adjudication_cache."""

    def __init__(self, conn):
        self._conn = conn

    def get(self, key: PairKey) -> Optional[Verdict]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT verdict FROM ml.adjudication_cache
                WHERE pair_key = %s AND prompt_version = %s AND model_version = %s
                  AND schema_version = %s AND embedding_version = %s
                """,
                (key.digest(), key.prompt_version, key.model_version,
                 key.schema_version, key.embedding_version),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return Verdict(**row[0])

    def put(self, key: PairKey, verdict: Verdict, tier: str = "", latency_ms: int = 0,
            tokens_in: int = 0, tokens_out: int = 0) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ml.adjudication_cache
                    (pair_key, prompt_version, model_version, schema_version, embedding_version,
                     verdict, evidence_spans, tier, latency_ms, tokens_in, tokens_out)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    key.digest(), key.prompt_version, key.model_version,
                    key.schema_version, key.embedding_version,
                    json.dumps(verdict.model_dump()),
                    json.dumps([e.model_dump() for e in verdict.evidence_spans]),
                    tier or verdict.tier, latency_ms, tokens_in, tokens_out,
                ),
            )
        self._conn.commit()
