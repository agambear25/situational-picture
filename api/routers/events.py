"""
Events router — the cell-only event feed and the per-event evidence trail.

Two read-only endpoints over the event-sourced read model:

  GET /events              paged list of fused events for a theater, with filters
  GET /events/{event_id}   one event plus its observation evidence trail

Every geometry these endpoints emit is reduced to its 1km MGRS cell by api.coarsen
(centroid + cell polygon derived from cell_id alone). No precise coordinate or person
entity ever leaves the API — that is the analytical-not-targeting boundary, enforced
inside coarsen_* and nowhere here. These handlers only read (get_conn); there are no
write paths in this router.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from api import queries
from api.coarsen import coarsen_event, coarsen_observation
from api.deps import get_conn, get_settings

router = APIRouter()


@router.get("/events")
def list_events(
    theater_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    band: str | None = Query(default=None),
    flag: str | None = Query(default=None),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    limit: int = Query(default=200),
    offset: int = Query(default=0),
    conn=Depends(get_conn),
) -> dict:
    """Paged, filtered list of fused events for a theater (cell-only geometry).

    since/until (ISO timestamps) filter by event start time — the chronology scrubber passes
    `until` for the cumulative 'as of date T' view.
    """
    settings = get_settings()
    # theater_id is optional on the wire; resolve the configured default when omitted.
    theater_id = theater_id or settings["default_theater"]
    # Cap the page size so a caller cannot demand an unbounded scan.
    limit = min(limit, settings["max_page_size"])
    rows = queries.list_events(conn, theater_id, status, band, flag, limit, offset, since, until)
    return {
        "events": [coarsen_event(e) for e in rows],
        "count": queries.event_count(conn, theater_id),
    }


@router.get("/events/{event_id}")
def get_event(event_id: str, conn=Depends(get_conn)) -> dict:
    """One event plus its observation evidence trail (cell-only geometry)."""
    ev = queries.get_event(conn, event_id)
    if ev is None:
        raise HTTPException(status_code=404, detail="event not found")
    out = coarsen_event(ev)
    # The evidence trail is already stored cell-only (the observation log keeps cell_id +
    # uncertainty radius, never a precise coord). Running coarsen_observation here is
    # defense-in-depth: it re-asserts the no-coord / no-person invariants on each obs and
    # attaches a derived centroid before the row leaves the API.
    out["observations"] = [coarsen_observation(o) for o in (ev.get("observations") or [])]
    return out
