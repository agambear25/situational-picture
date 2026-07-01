"""
Areas router — the top-down region → district → community drill-down, plus the named-geographic-
entity layer (Tier-1 feature library + Tier-2 areas of interest).

  GET  /rollup?level=&parent=&until=     admin-unit event rollup (the drill-down)
  GET  /features?kind=&bbox=             Tier-1 reference features as map layers
  GET  /aois        · GET /aois/{id}     Tier-2 areas of interest (list / focus)
  POST /aois        · DELETE /aois/{id}  create (promote feature / drawn geom / lassoed cells) / remove

Reads are read-only; AOI writes go through the annotation-write session. AOI geometry arrives as
GeoJSON and is converted to WKT here; create resolves it to a 1km cell-set (the event join).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api import queries
from api.deps import get_conn, get_settings, get_write_conn

router = APIRouter()


class AoiIn(BaseModel):
    kind: str
    label: str
    source: str = "drawn"                 # 'drawn' | 'derived'
    source_feature_id: int | None = None  # promote a geo_feature
    geometry: dict | None = None          # GeoJSON for a drawn line/polygon
    cell_ids: list[str] | None = None     # lassoed cells
    admin_id: str | None = None           # watch a whole region (its cells)
    note: str | None = None
    created_by: str | None = None
    theater_id: str | None = None


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


@router.get("/features")
def features(kind: str | None = Query(default=None),
             bbox: str | None = Query(default=None, description="w,s,e,n"),
             theater_id: str | None = Query(default=None),
             conn=Depends(get_conn)) -> dict:
    s = get_settings()
    theater_id = theater_id or s["default_theater"]
    box = [float(x) for x in bbox.split(",")] if bbox else None
    return {"features": queries.list_features(conn, theater_id, kind, box)}


@router.get("/aois")
def aois(kind: str | None = Query(default=None),
         theater_id: str | None = Query(default=None),
         conn=Depends(get_conn)) -> dict:
    s = get_settings()
    theater_id = theater_id or s["default_theater"]
    return {"aois": queries.list_aois(conn, theater_id, kind)}


@router.get("/aois/{aoi_id}")
def aoi_detail(aoi_id: int, conn=Depends(get_conn)) -> dict:
    aoi = queries.get_aoi(conn, aoi_id)
    if not aoi:
        raise HTTPException(status_code=404, detail="area of interest not found")
    return aoi


@router.post("/aois")
def create_aoi(body: AoiIn, conn=Depends(get_write_conn)) -> dict:
    s = get_settings()
    theater_id = body.theater_id or s["default_theater"]
    wkt = None
    if body.geometry:
        from shapely.geometry import shape  # lazy ([full] dep)
        wkt = shape(body.geometry).wkt
    res = queries.create_aoi(
        conn, theater_id, body.kind, body.label, body.source,
        source_feature_id=body.source_feature_id, geom_wkt=wkt,
        cell_ids=body.cell_ids, admin_id=body.admin_id, note=body.note, created_by=body.created_by)
    if res["n_cells"] == 0:
        raise HTTPException(status_code=422,
                            detail="geometry/cells did not resolve to any in-grid cell")
    return res


@router.delete("/aois/{aoi_id}")
def delete_aoi(aoi_id: int, conn=Depends(get_write_conn)) -> dict:
    if not queries.delete_aoi(conn, aoi_id):
        raise HTTPException(status_code=404, detail="area of interest not found")
    return {"deleted": True, "aoi_id": aoi_id}


def _live_read(conn, aoi_id):
    """Cached Read if present, else a deterministic on-the-fly Read (read-only, never blocks)."""
    cached = queries.get_cached_read(conn, aoi_id)
    if cached:
        return cached, None
    ctx = queries.gather_area_context(conn, aoi_id)
    if not ctx:
        return None, None
    from datetime import datetime, timezone
    from assess.attention import classify_attention
    from synth.context import build_context
    from synth.read import deterministic_read
    now = datetime.now(timezone.utc)
    att = classify_attention(ctx["events"], ctx["anomalies"], now)
    return deterministic_read(build_context(ctx["label"], ctx["events"], ctx["anomalies"],
                                            ctx["families"], now), att), att


@router.get("/aois/{aoi_id}/read")
def aoi_read(aoi_id: int, conn=Depends(get_conn)) -> dict:
    read, _ = _live_read(conn, aoi_id)
    if read is None:
        raise HTTPException(status_code=404, detail="area of interest not found")
    return read


@router.get("/area/{ref}")
def area(ref: str, conn=Depends(get_conn)) -> dict:
    """Unified place view for an area-ref ('admin:<id>' or 'aoi:<id>'): Read + attention + terrain
    + recent activity + significant incidents + attention-ranked child areas."""
    p = queries.area_payload(conn, ref)
    if p is None:
        raise HTTPException(status_code=404, detail="area not found")
    return p


@router.get("/area/{ref}/read")
def area_read(ref: str, conn=Depends(get_conn)) -> dict:
    p = queries.area_payload(conn, ref)
    if p is None:
        raise HTTPException(status_code=404, detail="area not found")
    return {**p["read"], "attention": p["attention"]}


@router.get("/watch")
def watch(theater_id: str | None = Query(default=None), conn=Depends(get_conn)) -> dict:
    """The AOR home feed: every area of interest with its attention status + Read snippet,
    ranked so the areas that need the analyst lead."""
    from datetime import datetime, timezone
    from assess.attention import attention_sort_key, classify_attention
    s = get_settings()
    theater_id = theater_id or s["default_theater"]
    now = datetime.now(timezone.utc)
    out = []
    for a in queries.list_aois(conn, theater_id):
        ctx = queries.gather_area_context(conn, a["aoi_id"])
        att = (classify_attention(ctx["events"], ctx["anomalies"], now)
               if ctx else {"status": "steady", "recent": 0})
        read, _ = _live_read(conn, a["aoi_id"])
        out.append({**a, "attention": att,
                    "read": (read or {}).get("summary"),
                    "indicators": (read or {}).get("indicators", att["status"]),
                    "provenance": (read or {}).get("provenance", [])})
    out.sort(key=lambda x: attention_sort_key(x["attention"], x.get("n_events", 0)))
    return {"theater_id": theater_id,
            "needs_attention": sum(1 for x in out if x["attention"]["status"] == "escalating"),
            "areas": out}
