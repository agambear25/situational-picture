"""
Human-in-the-loop labeling endpoints — the write side of the Phase-2 tuning UIs.

These four endpoints let an analyst close the loop on the fusion model offline:
inspect the gray-band of a synthetic eval run, record an append-only verdict on a
specific observation pair (or a free-form incident label), and regenerate the eval
fixtures from the accumulated labels. They are the ONLY writers in the API besides
/review, and they write exclusively through get_write_conn into the two append-only
annotation tables (see api.deps) — never the read model, never the log.

Why versions are pinned at label time (POST /gray-verdict): a gray verdict is only
reusable if we can recompute the SAME cache key that produced the pair. So we snapshot
the live prompt/model/schema/embedding versions into the label payload's sibling
`versions` column the moment the analyst decides. fixtures_io later regenerates the
verdict cache against those pinned versions, so a regenerated verdict keys identically
to the one the model would emit — that is what makes the offline corpus replay-stable.

Everything here runs against the SYNTHETIC eval corpus offline: GET /gray-band reads a
fusion_snapshot (no DB at all), and Claude stays OFF. Live feeds remain gated.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api import queries
from api.deps import get_conn, get_write_conn
from eval.fixtures_io import write_realworld, write_verdicts
from eval.snapshot import fusion_snapshot
from llm.config import load_llm_config

router = APIRouter()


class LabelIn(BaseModel):
    """Generic append-only label. `versions` lets the caller pin its own provenance."""

    kind: str
    payload: dict
    analyst: str
    versions: dict | None = None
    run_id: str | None = None


class GrayVerdictIn(BaseModel):
    """An analyst's same/different ruling on one gray-band observation pair.

    The pair is identified by content hashes (not obs_ids) so the verdict survives a
    re-ingest that re-numbers observations but preserves their content.
    """

    content_hash_a: str
    content_hash_b: str
    obs_type_a: str
    obs_type_b: str
    same: bool
    confidence: float
    rationale: str = ""
    analyst: str
    run_id: str | None = None


@router.get("/gray-band/{run_id}")
def gray_band(run_id: str) -> dict:
    """Offline synthetic snapshot for the gray-band tuning UI — no DB, no live feed."""
    return fusion_snapshot(run_id)


@router.post("/label")
def post_label(body: LabelIn, conn=Depends(get_write_conn)) -> dict:
    """Append-only insert of a free-form label (incident_label / gray_verdict)."""
    try:
        lid = queries.insert_label(
            conn, body.kind, body.payload, body.analyst, body.versions, body.run_id
        )
    except ValueError as e:
        # insert_label rejects unknown kinds — surface as a client error, not a 500.
        raise HTTPException(status_code=400, detail=str(e))
    return {"label_id": lid}


@router.post("/gray-verdict")
def post_gray_verdict(body: GrayVerdictIn, conn=Depends(get_write_conn)) -> dict:
    """Record a same/different verdict on a gray pair, pinning the live model versions.

    Pinning the prompt/model/schema/embedding versions here is what lets fixtures_io
    later regenerate a verdict that keys identically to the model's own cache entry.
    """
    cfg = load_llm_config()
    versions = {
        "prompt_version": cfg.prompt_version,
        "model_version": cfg.composite_model_version,
        "schema_version": cfg.schema_version,
        "embedding_version": cfg.embedding_version,
    }
    payload = {
        "content_hash_a": body.content_hash_a,
        "content_hash_b": body.content_hash_b,
        "obs_type_a": body.obs_type_a,
        "obs_type_b": body.obs_type_b,
        "same": body.same,
        "confidence": body.confidence,
        "rationale": body.rationale,
    }
    lid = queries.insert_label(conn, "gray_verdict", payload, body.analyst, versions, body.run_id)
    return {"label_id": lid}


@router.post("/fixtures/regenerate")
def regenerate_fixtures(conn=Depends(get_conn)) -> dict:
    """Rebuild the eval fixtures from all accumulated labels (read-only on the DB).

    Reading labels is a read, and writing fixtures is a local file operation, so this
    uses the read-only conn — no write path is touched.
    """
    rows = queries.list_labels(conn)
    cfg = load_llm_config()
    # fallback_cfg supplies pinned versions for any verdict labeled before versions existed.
    vp = write_verdicts(rows, fallback_cfg=cfg)
    rp = write_realworld([r for r in rows if r["kind"] == "incident_label"])
    return {
        "n_gray_verdicts": sum(1 for r in rows if r["kind"] == "gray_verdict"),
        "n_incident_labels": sum(1 for r in rows if r["kind"] == "incident_label"),
        "verdicts_path": str(vp),
        "realworld_path": str(rp),
    }
