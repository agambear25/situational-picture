"""
Verification queue + analyst review — one of the two append-only write paths.

GET /verify-queue surfaces the events the fusion engine flagged for a human look
(verification-needed / single-source / echo-only), each coarsened to cell-only geometry
like every other read. POST /review records the analyst's verdict.

The review write is append-only by construction: it goes through the cop_api role, which holds
INSERT (and nothing else) on the annotation table, and the DB triggers reject UPDATE/DELETE.
We never mutate the event itself here — the read model is rebuilt from the log, so an analyst
verdict is just one more immutable annotation, replayable and auditable.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api import queries
from api.coarsen import coarsen_event
from api.deps import get_conn, get_settings, get_write_conn

router = APIRouter()


class ReviewIn(BaseModel):
    """Analyst verdict on a flagged event. `action` is validated by queries.insert_review."""

    event_id: str
    action: str
    reason: str = ""
    analyst: str


@router.get("/verify-queue")
def verify_queue(theater_id: str | None = None, limit: int = 50, conn=Depends(get_conn)):
    """Events awaiting human verification, cell-coarsened for the COP map.

    theater_id defaults to the configured theater; limit is capped at max_page_size so a
    client can't force an unbounded scan.
    """
    s = get_settings()
    theater_id = theater_id or s["default_theater"]
    limit = min(limit, s["max_page_size"])
    rows = queries.verify_queue(conn, theater_id, limit)
    return {"events": [coarsen_event(e) for e in rows]}


@router.post("/review")
def post_review(body: ReviewIn, conn=Depends(get_write_conn)):
    """Append an analyst review annotation (confirm/split/reject/flag) — the only write here.

    insert_review raises ValueError on an unrecognized action; surface that as a 400 rather
    than a 500 so the client learns the verdict was rejected, not that the server broke.
    """
    try:
        rid = queries.insert_review(conn, body.event_id, body.action, body.reason, body.analyst)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"review_id": rid}
