"""
Deterministically expand eval/fixtures/incidents_v1.yaml into:
  - synthetic_v1.yaml   (the fusion input: observations with real MGRS cell_ids)
  - ground_truth_v1.yaml (incident partition + per-incident expectations + must-not-merge)
  - verdicts_v1.json    (frozen gray-band verdicts, answered from ground truth — no model)

Run once (and on any deliberate corpus/threshold change):
    python -m eval.build_fixtures
The frozen verdicts encode the human/ground-truth answer for every pair that the scorer
places in the gray band, so CI replays the gray band with NO Ollama and NO GPU.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path

import yaml

from grid.mgrs_1km import to_cell_id
from ingest.contract import Observation
from llm.cache import PairKey
from llm.config import load_llm_config
from fusion.block import block
from fusion.config import load_fusion_config
from fusion.score import score_pair

FIX = Path(__file__).parent / "fixtures"


def _content_hash(text: str, cell_id: str, start: datetime) -> str:
    bucket = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")
    raw = f"{' '.join(text.lower().split())}|{cell_id}|{bucket}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _obs_from_spec(inc_id: str, o: dict, theater: str) -> Observation:
    start = datetime.fromisoformat(o["time"].replace("Z", "+00:00"))
    end = start + timedelta(minutes=int(o.get("dur_min", 60)))
    cell_id = to_cell_id(o["lon"], o["lat"])
    text = o["text"]
    chash = _content_hash(text, cell_id, start)
    src = o["source_id"]
    family = _FAMILY.get(src, src)
    return Observation(
        obs_id=f"{inc_id}.{o['ref']}",
        theater_id=theater,
        source_id=src,
        source_family_id=family,
        modality=o.get("modality", "text"),
        obs_type=o["type"],
        occurred_start=start,
        occurred_end=end,
        cell_id=cell_id,
        geom_precision_m=float(o.get("precision_m", 1000)),
        content_hash=chash,
        place_id=o.get("place_id"),
        text=text,
    )


def main():
    spec = yaml.safe_load((FIX / "incidents_v1.yaml").read_text())
    theater = spec["theater_id"]
    global _FAMILY
    _FAMILY = _load_families()

    observations: list[Observation] = []
    partition: dict[str, str] = {}        # obs_id -> incident_id
    expectations: dict[str, dict] = {}
    must_not_merge_inc: dict[str, list] = {}

    for inc in spec["incidents"]:
        inc_id = inc["id"]
        expectations[inc_id] = inc.get("expect", {})
        if inc.get("must_not_merge_with"):
            must_not_merge_inc[inc_id] = inc["must_not_merge_with"]
        for o in inc["observations"]:
            obs = _obs_from_spec(inc_id, o, theater)
            observations.append(obs)
            partition[obs.obs_id] = inc_id

    # --- write synthetic_v1.yaml (fusion input) ---
    syn = [{
        "obs_id": o.obs_id, "theater_id": o.theater_id, "source_id": o.source_id,
        "source_family_id": o.source_family_id, "modality": o.modality, "obs_type": o.obs_type,
        "occurred_start": o.occurred_start.isoformat(), "occurred_end": o.occurred_end.isoformat(),
        "cell_id": o.cell_id, "geom_precision_m": o.geom_precision_m, "place_id": o.place_id,
        "content_hash": o.content_hash, "text": o.text,
    } for o in observations]
    (FIX / "synthetic_v1.yaml").write_text(
        yaml.safe_dump({"observations": syn}, sort_keys=False, allow_unicode=True))

    # --- must-not-merge obs-pairs (cross-incident for flagged incidents + ALL cross-incident) ---
    must_not_merge_pairs = set()
    for a, b in combinations(observations, 2):
        if partition[a.obs_id] != partition[b.obs_id]:
            must_not_merge_pairs.add(tuple(sorted([a.obs_id, b.obs_id])))

    echo_groups = [
        sorted(o.obs_id for o in observations if partition[o.obs_id] == inc_id)
        for inc_id, exp in expectations.items()
        if "echo-only" in (exp.get("flags_any") or [])
    ]

    gt = {
        "theater_id": theater,
        "n_incidents": len(expectations),
        "partition": partition,
        "expectations": expectations,
        "must_not_merge_with": must_not_merge_inc,
        "echo_groups": echo_groups,
    }
    (FIX / "ground_truth_v1.yaml").write_text(
        yaml.safe_dump(gt, sort_keys=False, allow_unicode=True))

    # --- discover gray-band pairs and freeze ground-truth verdicts ---
    cfg = load_fusion_config()
    llm_cfg = load_llm_config()
    obs_by_id = {o.obs_id: o for o in observations}

    diagnostics = {"same_incident_different": [], "must_not_merge_same": [], "gray": []}
    frozen: dict[str, dict] = {}

    for g in block(observations, cfg):
        for a_id, b_id in combinations(g.obs_ids, 2):
            a, b = obs_by_id[a_id], obs_by_id[b_id]
            sp = score_pair(a, b, cfg)
            same_incident = partition[a_id] == partition[b_id]

            if sp.band == "same" and not same_incident:
                diagnostics["must_not_merge_same"].append((a_id, b_id, sp.p))
            if sp.band == "different" and same_incident:
                diagnostics["same_incident_different"].append((a_id, b_id, sp.p, dict(sp.factors)))

            if sp.band == "gray":
                diagnostics["gray"].append((a_id, b_id, sp.p, same_incident))
                key = PairKey.build(a.content_hash, b.content_hash, a.obs_type, b.obs_type, llm_cfg)
                frozen[key.digest()] = {
                    "same": bool(same_incident),
                    "confidence": 0.9,
                    "rationale": "frozen from ground truth (build_fixtures)",
                    "evidence_spans": [],
                    "tier": "frozen",
                }

    (FIX / "verdicts_v1.json").write_text(json.dumps(frozen, indent=2, sort_keys=True))

    # --- report ---
    print(f"observations: {len(observations)}  incidents: {len(expectations)}")
    print(f"gray-band pairs frozen: {len(frozen)}")
    print(f"  gray detail: {diagnostics['gray']}")
    if diagnostics["same_incident_different"]:
        print("WARNING: same-incident pairs scored 'different' (recall risk — fix text/geo):")
        for d in diagnostics["same_incident_different"]:
            print("   ", d)
    if diagnostics["must_not_merge_same"]:
        print("WARNING: cross-incident pairs auto-merged 'same' (over-merge risk — separate them):")
        for d in diagnostics["must_not_merge_same"]:
            print("   ", d)
    if not diagnostics["same_incident_different"] and not diagnostics["must_not_merge_same"]:
        print("OK: no same-incident drops, no cross-incident auto-merges at current thresholds.")


def _load_families() -> dict:
    src = yaml.safe_load((Path(__file__).parent.parent / "config" / "sources.yaml").read_text())
    return {sid: s.get("family_id", sid) for sid, s in src["sources"].items()}


if __name__ == "__main__":
    main()
