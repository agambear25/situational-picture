"""
Projection runner — the engine-side materialization of the read model (CQRS projection).

This is the missing link between the append-only log and the read model:

    log.observation  --fuse()-->  world.event / world.event_observation

The API is deliberately read-only and never writes the read model (see api/queries.replay_check,
which re-derives events in memory only). Materialization is a write-role engine operation, and
this CLI is where it lives. It runs the SAME pure fuse() the eval gate runs, then calls the
tested fusion.db.write_events to persist the result.

Determinism: fuse() is bit-identical given the same log + same verdict cache. Re-running this
runner is idempotent — it truncates the theater's read model and rebuilds it from the log.

Adjudicator tiers:
  --adjudicator keep-separate  (default)  no Ollama needed; gray-band pairs stay SEPARATE and
                                          flagged (the documented circuit-breaker fallback).
  --adjudicator ollama                    the real 3B→7B→14B cascade; requires Ollama running
                                          with the qwen2.5 models pulled.

Usage:
    python -m fusion.run --theater ua_donbas
    python -m fusion.run --theater ua_donbas --adjudicator ollama
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

logger = logging.getLogger(__name__)


class _KeepSeparateBackend:
    """No-model adjudicator: every gray-band pair is kept separate and flagged, never merged.

    Mirrors api.queries.replay_check's fallback and the circuit-breaker's keep_separate_and_flag
    rule. Lets the read model materialize without Ollama; gray pairs simply don't auto-merge.
    """

    def adjudicate(self, ctx):
        from llm.circuit_breaker import LLMUnavailable
        raise LLMUnavailable("keep-separate backend: no live model; gray pairs kept separate")


def _build_backend(kind: str):
    if kind == "keep-separate":
        return _KeepSeparateBackend()
    if kind == "ollama":
        from llm.backend import OllamaBackend
        from llm.config import load_llm_config
        return OllamaBackend(load_llm_config())
    raise ValueError(f"unknown adjudicator {kind!r}")


def run(theater_id: str, adjudicator: str = "keep-separate") -> dict:
    """Rebuild the read model for one theater from the log. Returns a summary dict."""
    import psycopg2  # lazy
    from fusion.db import load_observations, landcover_by_obs, write_events
    from fusion.fuse import fuse

    dsn = os.environ.get("DB_DSN", "postgresql://localhost:5432/osint_cop")
    conn = psycopg2.connect(dsn)
    try:
        obs = load_observations(conn, theater_id)
        if not obs:
            logger.warning("no observations for theater %r — nothing to project", theater_id)
            return {"theater_id": theater_id, "n_obs": 0, "n_events": 0, "bands": {}}

        # Verdict cache reuses any previously-adjudicated gray-band verdicts (replay-safe).
        from llm.cache import PgVerdictCache
        cache = PgVerdictCache(conn)
        backend = _build_backend(adjudicator)

        lc = landcover_by_obs(conn, obs)   # {} if cell_context unpopulated → gate just won't penalize
        result = fuse(obs, cache, backend, theater_id=theater_id, landcover_by_obs=lc)

        n = write_events(conn, result, theater_id, truncate=True)

        bands: dict[str, int] = {}
        for e in result.events:
            bands[e.confidence_band] = bands.get(e.confidence_band, 0) + 1

        # Integrity check: every input observation is accounted for in the read model.
        cov = result.coverage({o.obs_id for o in obs})
        dropped = len(cov["unaccounted"])

        summary = {
            "theater_id": theater_id,
            "n_obs": len(obs),
            "n_events": n,
            "bands": bands,
            "dropped_obs": dropped,
            "adjudicator": adjudicator,
        }
        logger.info("projection complete: %s", summary)
        if dropped:
            logger.error("INTEGRITY: %d observations unaccounted for in the read model", dropped)
        return summary
    finally:
        conn.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m fusion.run")
    p.add_argument("--theater", default="ua_donbas")
    p.add_argument("--adjudicator", choices=["keep-separate", "ollama"], default="keep-separate",
                   help="keep-separate (no Ollama) or ollama (3B→7B→14B cascade)")
    args = p.parse_args()

    summary = run(args.theater, adjudicator=args.adjudicator)

    print("=" * 60)
    print(f"READ-MODEL PROJECTION — theater {summary['theater_id']}")
    print("=" * 60)
    print(f"  observations in log : {summary['n_obs']}")
    print(f"  events materialized : {summary['n_events']}")
    print(f"  confidence bands    : {summary.get('bands', {})}")
    print(f"  dropped (must be 0) : {summary.get('dropped_obs', 0)}")
    print(f"  adjudicator         : {summary.get('adjudicator')}")
    print("=" * 60)
    # Non-zero exit if any observation was silently dropped — fail loud.
    sys.exit(1 if summary.get("dropped_obs", 0) else 0)


if __name__ == "__main__":
    main()
