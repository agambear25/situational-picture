"""
Context-substrate layer endpoint — cell-keyed map backdrops (built-up, landcover, river/bridge).

These are the static analytical underlay the COP draws beneath events: each feature is a 1km
cell with substrate properties, NOT a precise feature footprint. Because layers describe the
grid itself (never an observation), no per-feature coarsening pass is needed — queries.list_layer
already returns cell-keyed rows. The handler only resolves the theater default and caps the limit.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_conn, get_settings
from api import queries

router = APIRouter()


@router.get("/layers/{layer}")
def get_layer(
    layer: str,
    # Resolve the theater default inside the handler so the OpenAPI default stays honest.
    theater_id: str | None = Query(default=None),
    limit: int = Query(default=1000),
    conn=Depends(get_conn),
) -> dict:
    settings = get_settings()
    theater_id = theater_id or settings["default_theater"]
    # Cap to the page-size ceiling so a caller can't ask the read model for an unbounded scan.
    limit = min(limit, settings["max_page_size"])
    try:
        # features are cell-keyed substrate properties (no precise geometry) — the grid, not events.
        features = queries.list_layer(conn, theater_id, layer, limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"layer": layer, "features": features}
