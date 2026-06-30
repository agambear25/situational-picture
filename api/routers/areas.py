"""
Areas router — the top-down region → district → community drill-down.

GET /rollup?level=1|2|3&parent=<admin_id>&until=<iso>  → event activity aggregated to admin units
at that level (oblast / raion / hromada), with a breadcrumb. Respects the time scrubber via `until`.
Read-only over the admin substrate (geo.admin_unit + cell_context) and the event read model.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api import queries
from api.deps import get_conn, get_settings

router = APIRouter()


@router.get("/rollup")
def rollup(level: int = Query(1, ge=1, le=3),
           parent: str | None = Query(default=None),
           until: str | None = Query(default=None),
           theater_id: str | None = Query(default=None),
           conn=Depends(get_conn)) -> dict:
    s = get_settings()
    theater_id = theater_id or s["default_theater"]
    units = queries.rollup(conn, theater_id, level, parent, until)
    breadcrumb = queries.admin_breadcrumb(conn, parent) if parent else []
    return {
        "level": level,
        "parent": parent,
        "breadcrumb": breadcrumb,
        "total_events": sum(u["n_events"] for u in units),
        "units": units,
    }
