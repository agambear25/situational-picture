"""AssessmentConfig — Phase-4a knobs parsed from config/assessment.yaml (no magic numbers)."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

_CFG = Path(__file__).parent.parent / "config" / "assessment.yaml"


@dataclass
class AssessmentConfig:
    recency_tau_days: float
    recency_floor: float
    corroboration_single_factor: float
    _severity: dict
    recent_window_days: float
    baseline_window_days: float
    spike_min_recent: int
    spike_ratio: float
    min_significance: float

    def severity(self, event_type: str) -> float:
        return float(self._severity.get(event_type, self._severity.get("_default", 0.5)))


@lru_cache(maxsize=1)
def load_assessment_config() -> AssessmentConfig:
    a = yaml.safe_load(_CFG.read_text(encoding="utf-8"))["assessment"]
    sig, anom = a["significance"], a["anomaly"]
    return AssessmentConfig(
        recency_tau_days=float(sig["recency_tau_days"]),
        recency_floor=float(sig["recency_floor"]),
        corroboration_single_factor=float(sig["corroboration_single_factor"]),
        _severity=sig["severity"],
        recent_window_days=float(anom["recent_window_days"]),
        baseline_window_days=float(anom["baseline_window_days"]),
        spike_min_recent=int(anom["spike_min_recent"]),
        spike_ratio=float(anom["spike_ratio"]),
        min_significance=float(anom["min_significance"]),
    )
