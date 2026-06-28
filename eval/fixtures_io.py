"""
The single, deterministic writer from label_annotation rows → eval fixtures.

Human labels and gray-band verdicts live durably in world.label_annotation (append-only,
0007). The fixture FILES the gate reads are GENERATED from those rows here — so a fixture
regen is reproducible and diff-clean, never lossy hand-editing:

  kind='incident_label' rows → eval/fixtures/realworld_ua_v1.yaml  (advisory real-world set)
  kind='gray_verdict'   rows → eval/fixtures/verdicts_v1.json      (frozen gray-band cache)

The verdict digest is rebuilt from the versions captured AT LABEL TIME (model/prompt/schema/
embedding), so the regenerated cache keys identically to what fusion will look up. Pure: yaml
+ json + the PairKey digest; no DB, no network.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from llm.cache import PairKey

FIX = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------- gray-band verdicts

def _verdict_digest(content_hash_a: str, content_hash_b: str, type_a: str, type_b: str,
                    versions: dict) -> str:
    """Order-independent verdict cache digest from explicit pinned versions."""
    hlo, hhi = sorted([content_hash_a, content_hash_b])
    tlo, thi = sorted([type_a, type_b])
    key = PairKey(
        hash_lo=hlo, hash_hi=hhi, type_lo=tlo, type_hi=thi,
        prompt_version=versions["prompt_version"],
        model_version=versions["model_version"],
        schema_version=versions["schema_version"],
        embedding_version=versions["embedding_version"],
    )
    return key.digest()


def gray_verdicts_to_frozen(label_rows: list[dict], fallback_cfg=None) -> dict:
    """Build the frozen verdict dict {digest: verdict} from gray_verdict label rows.

    Later rows for the same pair WIN (append-only history; newest analyst call is authoritative).
    `fallback_cfg` (an LLMConfig) supplies versions for any legacy row that didn't capture them.
    """
    frozen: dict[str, dict] = {}
    for row in label_rows:
        if row.get("kind") != "gray_verdict":
            continue
        p = row["payload"]
        versions = {
            "prompt_version": row.get("prompt_version") or _cfg_attr(fallback_cfg, "prompt_version"),
            "model_version": row.get("model_version") or _cfg_attr(fallback_cfg, "composite_model_version"),
            "schema_version": row.get("schema_version") or _cfg_attr(fallback_cfg, "schema_version"),
            "embedding_version": row.get("embedding_version") or _cfg_attr(fallback_cfg, "embedding_version"),
        }
        if any(v is None for v in versions.values()):
            raise ValueError(
                f"gray_verdict {row.get('label_id')} is missing pinned versions and no "
                "fallback LLMConfig was supplied — refusing to write an unkeyable verdict."
            )
        digest = _verdict_digest(
            p["content_hash_a"], p["content_hash_b"], p["obs_type_a"], p["obs_type_b"], versions,
        )
        frozen[digest] = {
            "same": bool(p["same"]),
            "confidence": float(p.get("confidence", 0.9)),
            "rationale": p.get("rationale", "human verdict (label_studio)"),
            "evidence_spans": p.get("evidence_spans", []),
            "tier": "frozen",
        }
    return frozen


def verdicts_json(label_rows: list[dict], fallback_cfg=None) -> str:
    return json.dumps(gray_verdicts_to_frozen(label_rows, fallback_cfg), indent=2, sort_keys=True)


def write_verdicts(label_rows: list[dict], path: Optional[Path] = None, fallback_cfg=None) -> Path:
    path = Path(path) if path else (FIX / "verdicts_v1.json")
    path.write_text(verdicts_json(label_rows, fallback_cfg))
    return path


# --------------------------------------------------------------- realworld incidents

def incident_labels_to_spec(label_rows: list[dict], theater_id: str = "ua_donbas") -> dict:
    """Build the realworld incident spec (same shape as incidents_v1.yaml) from label rows.

    Last write per incident_id wins. Output keys are sorted for a stable, diff-clean file.
    """
    by_incident: dict[str, dict] = {}
    for row in label_rows:
        if row.get("kind") != "incident_label":
            continue
        p = row["payload"]
        inc_id = p["incident_id"]
        by_incident[inc_id] = {
            "id": inc_id,
            "expect": p.get("expect", {}),
            "must_not_merge_with": p.get("must_not_merge_with", []),
            "observations": p.get("observations", []),
        }
    incidents = [by_incident[k] for k in sorted(by_incident)]
    return {"theater_id": theater_id, "incidents": incidents}


def realworld_yaml(label_rows: list[dict], theater_id: str = "ua_donbas") -> str:
    import yaml
    return yaml.safe_dump(
        incident_labels_to_spec(label_rows, theater_id),
        sort_keys=False, allow_unicode=True,
    )


def write_realworld(label_rows: list[dict], path: Optional[Path] = None,
                    theater_id: str = "ua_donbas") -> Path:
    path = Path(path) if path else (FIX / "realworld_ua_v1.yaml")
    path.write_text(realworld_yaml(label_rows, theater_id))
    return path


def _cfg_attr(cfg, name: str):
    return getattr(cfg, name, None) if cfg is not None else None
