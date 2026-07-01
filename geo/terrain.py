"""Aggregate the land-cover/road substrate of a set of 1km cells into an area terrain profile. Pure
`summarize_cells` (unit-tested on injected rows); `terrain_profile` is the thin DB wrapper."""
from __future__ import annotations


def summarize_cells(rows: list[dict]) -> dict:
    n = len(rows)
    if n == 0:
        return {"landcover": {}, "builtup_pct": 0.0, "road_unpaved_share": 0.0, "n_cells": 0}
    counts: dict[str, int] = {}
    bu_sum = 0.0
    surfaced = unpaved = 0
    for r in rows:
        lbl = r.get("landcover_label")
        if lbl:
            counts[lbl] = counts.get(lbl, 0) + 1
        bu = r.get("builtup_pct")
        if bu is not None:
            bu_sum += bu
        s = r.get("road_surface")
        if s in ("paved", "unpaved"):
            surfaced += 1
            unpaved += (s == "unpaved")
    known = sum(counts.values()) or 1
    return {
        "landcover": {k: v / known for k, v in sorted(counts.items(), key=lambda x: -x[1])},
        "builtup_pct": bu_sum / n,
        "road_unpaved_share": (unpaved / surfaced) if surfaced else 0.0,
        "n_cells": n,
    }


def terrain_profile(conn, theater_id: str, cell_ids: list[str]) -> dict:
    if not cell_ids:
        return summarize_cells([])
    with conn.cursor() as cur:
        cur.execute(
            """SELECT landcover_label, builtup_pct, road_surface FROM geo.cell_context
               WHERE theater_id = %s AND cell_id = ANY(%s)""",
            (theater_id, cell_ids))
        rows = [{"landcover_label": r[0], "builtup_pct": r[1], "road_surface": r[2]}
                for r in cur.fetchall()]
    return summarize_cells(rows)
