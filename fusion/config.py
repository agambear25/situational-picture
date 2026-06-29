"""
FusionConfig — all tuning knobs parsed from config/*.yaml into one typed surface.
No magic numbers in fusion code; every value here is a version-controlled YAML diff.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

_CFG = Path(__file__).parent.parent / "config"


def _load(name: str) -> dict:
    with open(_CFG / name) as f:
        return yaml.safe_load(f)


@dataclass
class FusionConfig:
    # thresholds
    tau_high: float
    tau_low: float
    # band cutoffs
    band_high_min: float
    band_high_min_families: int
    band_medium_min: float
    band_low_min: float
    single_source_always_rumored: bool
    # weights
    w_geo: float
    w_time: float
    w_text: float
    w_type: float
    sigma_k: float
    tau_time_s: float
    alpha_text: float
    lambda_family: float
    toponym_bonus: float
    number_bonus: float
    # reliability cap keeps noisy-OR non-degenerate even at reliability 1.0
    reliability_cap: float
    # parsed tables
    _type_compat: dict = None
    _block_params: dict = None
    _landcover: dict = None
    _reliability: dict = None
    _source_family: dict = None
    _source_reliability: dict = None

    # ---- type compatibility ----
    def type_compat(self, a: str, b: str) -> float:
        tc = self._type_compat
        if a == b:
            return float(tc.get("_default_same_type", 1.0))
        # symmetric lookup
        row = tc.get(a, {})
        if b in row:
            return float(row[b])
        row = tc.get(b, {})
        if a in row:
            return float(row[a])
        return float(tc.get("_default_cross_type", 0.1))

    # ---- blocking params ----
    def block_radius_m(self, obs_type: str) -> float:
        bp = self._block_params.get(obs_type) or self._block_params["_default"]
        return float(bp["radius_m"])

    def block_window_s(self, obs_type: str) -> float:
        bp = self._block_params.get(obs_type) or self._block_params["_default"]
        return float(bp["window_hours"]) * 3600.0

    # ---- temporal-score time constant (type-aware) ----
    # Transient events (strike/fire/movement) decay over the global tau_time_s (hours): a stale
    # report is weak evidence. PERSISTENT-STATE types (damage/flood/burn — those with a wide block
    # window ≥48h) describe a lasting condition, so a satellite pass and a ground assessment weeks
    # apart still corroborate; their decay constant is the (wide) block window itself.
    _PERSISTENT_MIN_S = 48 * 3600.0

    def time_tau_s(self, a_type: str, b_type: str) -> float:
        widest = max(self.block_window_s(a_type), self.block_window_s(b_type))
        return widest if widest >= self._PERSISTENT_MIN_S else self.tau_time_s

    # ---- landcover plausibility ----
    def landcover_penalty(self, obs_type: str, landcover_code) -> float:
        rule = self._landcover.get(obs_type)
        if not rule or landcover_code is None:
            return 1.0
        req = rule.get("required_codes")
        if req is not None:
            return 1.0 if landcover_code in req else float(rule.get("penalty_if_not", 1.0))
        impl = rule.get("implausible_codes")
        if impl is not None and landcover_code in impl:
            return float(rule.get("penalty_if_implausible", 1.0))
        return 1.0

    # ---- sources ----
    def family(self, source_id: str) -> str:
        return self._source_family.get(source_id, source_id)

    def reliability(self, source_id: str) -> float:
        r = self._source_reliability.get(source_id)
        if r is None:
            r = self._reliability.get(source_id, self._reliability.get("_default", 0.7))
        return min(float(r), self.reliability_cap)


@lru_cache(maxsize=1)
def load_fusion_config() -> FusionConfig:
    thr = _load("thresholds.yaml")
    w = _load("weights.yaml")
    tax = _load("taxonomy.yaml")
    src = _load("sources.yaml")

    bands = thr["confidence_bands"]
    sf = w["score_factors"]

    cfg = FusionConfig(
        tau_high=float(thr["fusion"]["tau_high"]),
        tau_low=float(thr["fusion"]["tau_low"]),
        band_high_min=float(bands["High"]["min_confidence"]),
        band_high_min_families=int(bands["High"]["min_independent_families"]),
        band_medium_min=float(bands["Medium"]["min_confidence"]),
        band_low_min=float(bands["Low"]["min_confidence"]),
        single_source_always_rumored=bool(bands["Rumored"].get("single_source_always_rumored", True)),
        w_geo=float(sf.get("w_geo", 1.0)),
        w_time=float(sf.get("w_time", 1.0)),
        w_text=float(sf.get("w_text", 0.6)),
        w_type=float(sf.get("w_type", 1.0)),
        sigma_k=float(sf["sigma_k"]),
        tau_time_s=float(sf["tau_time"]) * 3600.0,
        alpha_text=float(sf["alpha_text"]),
        lambda_family=float(sf["lambda_family"]),
        toponym_bonus=float(sf["toponym_bonus"]),
        number_bonus=float(sf["number_bonus"]),
        reliability_cap=0.95,
    )
    cfg._type_compat = tax["type_compat"]
    cfg._block_params = tax["block_params"]
    cfg._landcover = tax.get("landcover_plausibility", {})
    cfg._reliability = w.get("source_reliability", {})
    cfg._source_family = {sid: s.get("family_id", sid) for sid, s in src["sources"].items()}
    cfg._source_reliability = {sid: s["reliability_w"] for sid, s in src["sources"].items()
                               if "reliability_w" in s}
    return cfg
