"""
fixtures_io must produce verdict digests IDENTICAL to what fusion looks up (PairKey), and the
offline fusion snapshot must run with no DB / no Ollama. These are the correctness hinges of
the Phase-2 labelling loop.
"""
from __future__ import annotations

from llm.cache import PairKey
from llm.config import load_llm_config
from eval.fixtures_io import gray_verdicts_to_frozen, incident_labels_to_spec
from eval.snapshot import fusion_snapshot


def _versions(cfg) -> dict:
    return {
        "prompt_version": cfg.prompt_version,
        "model_version": cfg.composite_model_version,
        "schema_version": cfg.schema_version,
        "embedding_version": cfg.embedding_version,
    }


def test_gray_verdict_digest_matches_fusion_pairkey():
    cfg = load_llm_config()
    v = _versions(cfg)
    # hashes/types intentionally out of order — the key must be order-independent
    row = {
        "kind": "gray_verdict", **{f"{k}": v[k] for k in v},
        "payload": {"content_hash_a": "hb", "content_hash_b": "ha",
                    "obs_type_a": "strike", "obs_type_b": "fire",
                    "same": True, "confidence": 0.9},
    }
    frozen = gray_verdicts_to_frozen([row])
    expected = PairKey.build("ha", "hb", "fire", "strike", cfg).digest()
    assert expected in frozen
    assert frozen[expected]["same"] is True
    assert frozen[expected]["tier"] == "frozen"


def test_gray_verdict_uses_fallback_cfg_when_row_lacks_versions():
    cfg = load_llm_config()
    row = {"kind": "gray_verdict",
           "payload": {"content_hash_a": "a", "content_hash_b": "b",
                       "obs_type_a": "strike", "obs_type_b": "strike",
                       "same": False, "confidence": 0.8}}
    frozen = gray_verdicts_to_frozen([row], fallback_cfg=cfg)
    assert len(frozen) == 1


def test_later_verdict_for_same_pair_wins():
    cfg = load_llm_config()
    v = _versions(cfg)
    def mk(same):
        return {"kind": "gray_verdict", **v,
                "payload": {"content_hash_a": "a", "content_hash_b": "b",
                            "obs_type_a": "strike", "obs_type_b": "strike",
                            "same": same, "confidence": 0.9}}
    frozen = gray_verdicts_to_frozen([mk(True), mk(False)])  # append-only; newest wins
    assert len(frozen) == 1
    assert next(iter(frozen.values()))["same"] is False


def test_incident_labels_to_spec_is_sorted_and_shaped():
    rows = [
        {"kind": "incident_label", "payload": {"incident_id": "INC-02",
         "expect": {"band": "Rumored"}, "observations": []}},
        {"kind": "incident_label", "payload": {"incident_id": "INC-01",
         "expect": {"band": "High"}, "must_not_merge_with": ["INC-02"], "observations": []}},
    ]
    spec = incident_labels_to_spec(rows, "ua_donbas")
    assert spec["theater_id"] == "ua_donbas"
    assert [i["id"] for i in spec["incidents"]] == ["INC-01", "INC-02"]


def test_fusion_snapshot_runs_fully_offline():
    snap = fusion_snapshot()
    assert snap["pairs"], "snapshot must contain scored pairs"
    assert all("p" in p and "band" in p and "same_incident" in p for p in snap["pairs"])
    assert all(p["band"] == "gray" for p in snap["gray_pairs"])
    assert snap["thresholds"]["tau_high"] >= snap["thresholds"]["tau_low"]
    assert snap["n_events"] >= 1
