"""Fusion value objects — all frozen, all serialization-stable for replay hashing."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class CandidateGroup:
    group_id: int
    obs_ids: tuple[str, ...]


@dataclass(frozen=True)
class ScoredPair:
    obs_a: str
    obs_b: str
    p: float
    band: str                      # 'same' | 'gray' | 'different'
    factors: tuple                 # ((name, value), ...) sorted — audit/threshold-tuner
    def factors_dict(self) -> dict:
        return dict(self.factors)


@dataclass(frozen=True)
class EdgeDecision:
    obs_a: str
    obs_b: str
    same: bool
    source: str                    # 'cache' | 'verdict' | 'degraded_keep_separate'
    confidence: float
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Event:
    event_id: str
    theater_id: str
    event_type: str
    cell_id: str
    occurred_start: datetime
    occurred_end: datetime
    status: str                    # 'candidate' | 'confirmed' | 'stale' | 'retracted'
    confidence: float
    confidence_band: str           # 'High' | 'Medium' | 'Low' | 'Rumored'
    n_sources: int
    n_independent_families: int
    resolved_precision_m: float
    flags: tuple[str, ...]
    created_from_obs: tuple[str, ...]

    def canonical(self) -> dict:
        return {
            "event_type": self.event_type,
            "cell_id": self.cell_id,
            "status": self.status,
            "band": self.confidence_band,
            "confidence": round(self.confidence, 6),
            "n_families": self.n_independent_families,
            "flags": sorted(self.flags),
            "obs": sorted(self.created_from_obs),
        }


@dataclass(frozen=True)
class ObsRejection:
    obs_id: str
    reason: str


@dataclass
class FusionResult:
    events: list                   # list[Event]
    rejections: list               # list[ObsRejection]
    scored_pairs: list             # list[ScoredPair] (audit / threshold tuner)
    gray_pairs: list               # list[(obs_a, obs_b)]
    counters: dict = field(default_factory=dict)

    def coverage(self, input_obs_ids: set[str]) -> dict:
        in_events: list[str] = []
        for e in self.events:
            in_events.extend(e.created_from_obs)
        in_events_set = set(in_events)
        rejected = {r.obs_id for r in self.rejections}
        accounted = in_events_set | rejected
        return {
            "input": len(input_obs_ids),
            "in_events": len(in_events_set),
            "rejected": len(rejected),
            "duplicated_across_events": len(in_events) - len(in_events_set),
            "unaccounted": sorted(input_obs_ids - accounted),
            "extra": sorted(accounted - input_obs_ids),
        }

    def no_silent_drop(self, input_obs_ids: set[str]) -> bool:
        cov = self.coverage(input_obs_ids)
        return (not cov["unaccounted"]) and (not cov["extra"]) and cov["duplicated_across_events"] == 0

    def digest(self) -> str:
        """Canonical, order-independent hash of the event set — the replay equality check."""
        cans = sorted(
            (json.dumps(e.canonical(), sort_keys=True) for e in self.events)
        )
        blob = "\n".join(cans)
        return hashlib.sha256(blob.encode()).hexdigest()
