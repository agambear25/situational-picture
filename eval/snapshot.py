"""
Offline fusion snapshot — the score distribution the labelling + threshold tools run on.

Both Phase-2 tuning UIs operate on the SYNTHETIC eval corpus, fully offline (frozen verdicts
stand in for Ollama, in-memory fixtures stand in for the DB) so they are useful BEFORE any
live feed is wired. `fusion_snapshot()` runs fuse() once and returns every scored pair (p,
band, per-factor breakdown, both observations' context, and the ground-truth same/different
label) plus the gray band — enough for:
  - threshold_tuner.py to recompute gray-fraction + pairwise P/R at any τ in-browser, and
  - label_studio.py to show each gray pair side-by-side for human adjudication.

No magic: this is the SAME fuse() the gate runs, so what you tune here is what ships.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from ingest.contract import Observation
from llm.backend import FrozenBackend
from llm.cache import FrozenVerdictCache
from fusion.config import load_fusion_config
from fusion.fuse import fuse

FIX = Path(__file__).parent / "fixtures"


def _load_obs() -> list[Observation]:
    data = yaml.safe_load((FIX / "synthetic_v1.yaml").read_text())
    return [Observation.from_fixture(d) for d in data["observations"]]


def _load_ground_truth() -> dict:
    gt_path = FIX / "ground_truth_v1.yaml"
    if not gt_path.exists():
        return {}
    return yaml.safe_load(gt_path.read_text())


def fusion_snapshot(run_id: str = "synthetic_v1") -> dict:
    """Run fuse() over the synthetic corpus and return a JSON-ready score snapshot."""
    obs = _load_obs()
    obs_by_id = {o.obs_id: o for o in obs}
    gt = _load_ground_truth()
    partition = gt.get("partition", {})
    theater = gt.get("theater_id", "ua_donbas")

    cfg = load_fusion_config()
    cache = FrozenVerdictCache(FIX / "verdicts_v1.json")
    result = fuse(obs, cache, FrozenBackend(), theater_id=theater)

    def _ctx(o: Observation) -> dict:
        return {
            "obs_id": o.obs_id, "obs_type": o.obs_type, "cell_id": o.cell_id,
            "content_hash": o.content_hash,   # the verdict-cache key the adjudicator UI posts back
            "source_id": o.source_id, "source_family_id": o.source_family_id,
            "occurred_start": o.occurred_start.isoformat(), "text": o.text,
            "incident_id": partition.get(o.obs_id),
        }

    pairs = []
    for sp in result.scored_pairs:
        a, b = obs_by_id[sp.obs_a], obs_by_id[sp.obs_b]
        same_incident = None
        if sp.obs_a in partition and sp.obs_b in partition:
            same_incident = partition[sp.obs_a] == partition[sp.obs_b]
        pairs.append({
            "obs_a": sp.obs_a, "obs_b": sp.obs_b,
            "p": round(sp.p, 6), "band": sp.band,
            "factors": {k: round(v, 6) for k, v in sp.factors_dict().items()},
            "same_incident": same_incident,
            "a": _ctx(a), "b": _ctx(b),
        })
    # deterministic ordering
    pairs.sort(key=lambda d: (d["obs_a"], d["obs_b"]))
    gray = [p for p in pairs if p["band"] == "gray"]

    return {
        "run_id": run_id,
        "theater_id": theater,
        "thresholds": {"tau_high": cfg.tau_high, "tau_low": cfg.tau_low},
        "n_obs": len(obs),
        "n_events": len(result.events),
        "counters": result.counters,
        "pairs": pairs,
        "gray_pairs": gray,
        "events": [
            {
                "event_id": e.event_id, "event_type": e.event_type, "cell_id": e.cell_id,
                "confidence": round(e.confidence, 6), "confidence_band": e.confidence_band,
                "n_independent_families": e.n_independent_families, "flags": list(e.flags),
                "created_from_obs": list(e.created_from_obs),
            }
            for e in result.events
        ],
    }
