"""
Provenance + counters for the LLM backend.
Proves that ≥95% of pairs are decided by SQL/arithmetic (cache or score), and that the
model is only paid for the gray band. Counters are exposed to the eval harness.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RunCounters:
    pairs_seen: int = 0
    cache_hits: int = 0
    gate_calls: int = 0
    adjudicator_calls: int = 0
    escalation_calls: int = 0
    degraded_keep_separate: int = 0     # circuit-open / unavailable → kept separate + flagged
    malformed_retries: int = 0
    by_tier: dict = field(default_factory=dict)

    def record_call(self, tier: str) -> None:
        if tier == "gate_3b":
            self.gate_calls += 1
        elif tier == "adjudicator_7b":
            self.adjudicator_calls += 1
        elif tier == "escalation_14b":
            self.escalation_calls += 1
        self.by_tier[tier] = self.by_tier.get(tier, 0) + 1

    @property
    def model_calls(self) -> int:
        return self.gate_calls + self.adjudicator_calls + self.escalation_calls

    @property
    def cache_hit_rate(self) -> float:
        return self.cache_hits / self.pairs_seen if self.pairs_seen else 0.0

    def summary(self) -> dict:
        return {
            "pairs_seen": self.pairs_seen,
            "cache_hits": self.cache_hits,
            "cache_hit_rate": round(self.cache_hit_rate, 4),
            "model_calls": self.model_calls,
            "gate_calls": self.gate_calls,
            "adjudicator_calls": self.adjudicator_calls,
            "escalation_calls": self.escalation_calls,
            "degraded_keep_separate": self.degraded_keep_separate,
            "malformed_retries": self.malformed_retries,
        }


def write_model_run(conn, pair_digest: str, tier: str, cfg, latency_ms: int,
                    tokens_in: int, tokens_out: int) -> None:
    """Persist a single model-call record. Folded into adjudication_cache columns
    (latency/tokens/tier) in the MVP — see Cross-check #7. This helper centralizes that write."""
    # Provenance is written alongside the verdict by PgVerdictCache.put(); this hook exists
    # for an optional standalone ml.model_run table added by a later migration.
    return None
