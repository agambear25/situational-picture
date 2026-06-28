"""
Cell detail router — the 1km-cell drill-down surface.

A cell is the atomic geography of the COP: every event, observation, and context layer is
pinned to a 1km MGRS cell, never a precise point. This endpoint serves the cell's context
plus the events that resolved into it, each one passed back through the coarsening boundary
so geometry stays cell-only (analytical-not-targeting; see api.coarsen).

Read-only: the handler takes get_conn and never writes. The two annotation write paths live
on the review/label routers, not here.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api import queries
from api.coarsen import coarsen_cell, coarsen_event
from api.deps import get_conn

router = APIRouter()


@router.get("/cells/{cell_id}")
def get_cell(cell_id: str, conn=Depends(get_conn)) -> dict:
    """Return one cell's coarsened context plus its coarsened events.

    queries.get_cell already attaches the cell's "context" and nested "events"; we coarsen the
    cell shell and then each event individually so every emitted geometry is derived from a
    cell_id alone — no precise coordinate, no person entity, ever leaves the API.
    """
    c = queries.get_cell(conn, cell_id)
    if c is None:
        raise HTTPException(status_code=404, detail="cell not found")
    out = coarsen_cell(c)
    # Coarsen the nested events too: the cell shell and its events are separate payloads, each
    # of which must independently clear the coarsening boundary.
    out["events"] = [coarsen_event(e) for e in (c.get("events") or [])]
    return out
