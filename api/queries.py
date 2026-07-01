"""
Deterministic read-model SQL — the API's only DB boundary.

Every function takes a psycopg2 connection and returns plain dicts (JSON-ready). No geometry
is shaped here; api/coarsen.py owns the cell-only reduction. psycopg2 is never imported (the
caller injects the connection), so this module imports cleanly without a DB driver.

Read surfaces map 1:1 to PRD §11 endpoints. Two append-only writers (insert_review,
insert_label) are the ONLY mutations — both to human-in-the-loop annotation tables.
"""
from __future__ import annotations

import json
from typing import Optional


# --------------------------------------------------------------------------- events

def list_events(
    conn, theater_id: str, status: Optional[str] = None, band: Optional[str] = None,
    flag: Optional[str] = None, limit: int = 100, offset: int = 0,
    since: Optional[str] = None, until: Optional[str] = None,
    admin_id: Optional[str] = None, admin_level: Optional[int] = None,
    aoi_id: Optional[int] = None,
) -> list[dict]:
    """Events for a theater, newest-first, with optional status/band/flag/time filters.

    `since`/`until` (ISO timestamps) filter on the event's start time — `until` gives the
    cumulative 'everything known as of date T' view the chronology scrubber uses.
    """
    where = ["theater_id = %s"]
    params: list = [theater_id]
    if status:
        where.append("status = %s"); params.append(status)
    if band:
        where.append("confidence_band = %s"); params.append(band)
    if flag:
        where.append("%s = ANY(flags)"); params.append(flag)
    if since:
        where.append("lower(occurred_at) >= %s"); params.append(since)
    if until:
        where.append("lower(occurred_at) <= %s"); params.append(until)
    if admin_id and admin_level in (1, 2, 3):
        col = _ADMIN_COL[admin_level]
        where.append(f"cell_id IN (SELECT cell_id FROM geo.cell_context WHERE {col} = %s)")
        params.append(admin_id)
    if aoi_id is not None:
        where.append("cell_id IN (SELECT cell_id FROM world.aoi_cell WHERE aoi_id = %s)")
        params.append(aoi_id)
    params.extend([limit, offset])
    sql = f"""
        SELECT event_id, theater_id, event_type, cell_id, resolved_precision_m,
               lower(occurred_at), upper(occurred_at), status, confidence, confidence_band,
               n_sources, n_independent_families, flags, created_from_obs, updated_at
        FROM world.event
        WHERE {' AND '.join(where)}
        ORDER BY upper(occurred_at) DESC NULLS LAST, event_id
        LIMIT %s OFFSET %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = [_event_row(r) for r in cur.fetchall()]
    _attach_geo_context(conn, rows)
    return rows


def _attach_geo_context(conn, rows: list[dict]) -> None:
    """Batch-fill each event row's cell land-cover/road fields (for the geo_context phrase). One query."""
    cell_ids = list({r["cell_id"] for r in rows if r.get("cell_id")})
    if not cell_ids:
        return
    with conn.cursor() as cur:
        cur.execute(
            """SELECT cell_id, landcover_label, nearest_road_class, road_surface
               FROM geo.cell_context WHERE cell_id = ANY(%s)""", (cell_ids,))
        ctx = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}
    for r in rows:
        lc, rc, rs = ctx.get(r.get("cell_id"), (None, None, None))
        r["landcover_label"], r["nearest_road_class"], r["road_surface"] = lc, rc, rs


def event_count(conn, theater_id: str) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM world.event WHERE theater_id = %s", (theater_id,))
        return int(cur.fetchone()[0])


def get_event(conn, event_id: str) -> Optional[dict]:
    """One event + its full evidence trail (the corroboration chain) from the obs log."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT event_id, theater_id, event_type, cell_id, resolved_precision_m,
                   lower(occurred_at), upper(occurred_at), status, confidence, confidence_band,
                   n_sources, n_independent_families, flags, created_from_obs, updated_at
            FROM world.event WHERE event_id = %s
            """,
            (event_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    ev = _event_row(row)
    ev["observations"] = _evidence_trail(conn, event_id)
    ev["context"] = get_cell_context(conn, ev["cell_id"])
    ev["reviews"] = _event_reviews(conn, event_id)
    return ev


def _evidence_trail(conn, event_id: str) -> list[dict]:
    """Per-observation evidence: family, modality, time, excerpt, source — cell-only, no coord."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT o.obs_id, o.source_id, o.source_family_id, o.modality, o.obs_type,
                   lower(o.occurred_at), o.cell_id, o.raw_text, eo.member_score, s.label, s.url
            FROM world.event_observation eo
            JOIN log.observation o ON o.obs_id = eo.obs_id
            LEFT JOIN world.source s ON s.source_id = o.source_id
            WHERE eo.event_id = %s
            ORDER BY lower(o.occurred_at), o.obs_id
            """,
            (event_id,),
        )
        rows = cur.fetchall()
    out = []
    for r in rows:
        text = r[7] or ""
        out.append({
            "obs_id": str(r[0]), "source_id": r[1], "source_family_id": r[2],
            "modality": r[3], "obs_type": r[4],
            "occurred_at": _iso(r[5]), "cell_id": r[6],
            "excerpt": text[:280], "member_score": r[8],
            "source_label": r[9], "source_url": r[10],
        })
    return out


def _event_reviews(conn, event_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT action, reason, analyst, created_at FROM world.review_annotation "
            "WHERE event_id = %s ORDER BY created_at DESC",
            (event_id,),
        )
        return [{"action": r[0], "reason": r[1], "analyst": r[2], "created_at": _iso(r[3])}
                for r in cur.fetchall()]


# --------------------------------------------------------------------------- cells

def get_cell_context(conn, cell_id: str) -> Optional[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT cell_id, theater_id, mean_slope_deg, dominant_landcover, landcover_label,
                   has_river, has_bridge, builtup_pct, building_count, nearest_road_class,
                   admin_l1, admin_l2, admin_l3, label
            FROM geo.cell_context WHERE cell_id = %s
            """,
            (cell_id,),
        )
        r = cur.fetchone()
    if r is None:
        return None
    return {
        "cell_id": r[0], "theater_id": r[1], "mean_slope_deg": r[2],
        "dominant_landcover": r[3], "landcover_label": r[4], "has_river": r[5],
        "has_bridge": r[6], "builtup_pct": r[7], "building_count": r[8],
        "nearest_road_class": r[9], "admin_l1": r[10], "admin_l2": r[11],
        "admin_l3": r[12], "label": r[13],
    }


def get_cell(conn, cell_id: str) -> Optional[dict]:
    """A cell: its label/admin spine, static context, and recent events in it."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT cell_id, theater_id, label, admin_l1, admin_l2, admin_l3, local_seq "
            "FROM geo.grid_cell WHERE cell_id = %s",
            (cell_id,),
        )
        g = cur.fetchone()
    if g is None:
        return None
    out = {
        "cell_id": g[0], "theater_id": g[1], "label": g[2] or g[0],
        "admin_l1": g[3], "admin_l2": g[4], "admin_l3": g[5], "local_seq": g[6],
        "context": get_cell_context(conn, cell_id),
        "events": list_events_in_cell(conn, cell_id),
    }
    return out


def list_events_in_cell(conn, cell_id: str, limit: int = 50) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT event_id, theater_id, event_type, cell_id, resolved_precision_m,
                   lower(occurred_at), upper(occurred_at), status, confidence, confidence_band,
                   n_sources, n_independent_families, flags, created_from_obs, updated_at
            FROM world.event WHERE cell_id = %s
            ORDER BY upper(occurred_at) DESC NULLS LAST LIMIT %s
            """,
            (cell_id, limit),
        )
        return [_event_row(r) for r in cur.fetchall()]


# --------------------------------------------------------------------------- layers

def list_layer(conn, theater_id: str, layer: str, limit: int = 1000) -> list[dict]:
    """geo_feature rows for a substrate layer, as cell-keyed properties (no precise geom)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT feature_id, cell_id, properties, as_of, source
            FROM geo.geo_feature
            WHERE theater_id = %s AND layer = %s
            ORDER BY feature_id LIMIT %s
            """,
            (theater_id, layer, limit),
        )
        return [{"feature_id": r[0], "cell_id": r[1], "properties": r[2],
                 "as_of": _iso(r[3]), "source": r[4]} for r in cur.fetchall()]


# --------------------------------------------------------------------- verify queue

def verify_queue(conn, theater_id: str, limit: int = 50) -> list[dict]:
    """Events most in need of a human: verification-needed flag first, then low band/confidence.

    Significance proxy = needs-review flags, then weaker bands, then more recent. Confirmed
    multi-family events sink to the bottom.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT event_id, theater_id, event_type, cell_id, resolved_precision_m,
                   lower(occurred_at), upper(occurred_at), status, confidence, confidence_band,
                   n_sources, n_independent_families, flags, created_from_obs, updated_at
            FROM world.event
            WHERE theater_id = %s
            ORDER BY
              ('verification-needed' = ANY(flags)) DESC,
              array_position(ARRAY['Rumored','Low','Medium','High'], confidence_band) ASC,
              confidence ASC,
              upper(occurred_at) DESC NULLS LAST
            LIMIT %s
            """,
            (theater_id, limit),
        )
        return [_event_row(r) for r in cur.fetchall()]


# --------------------------------------------------------------------- no-drop ledger

def list_rejections(conn, theater_id: str, limit: int = 200) -> list[dict]:
    """The rejection ledger — proof that non-placed observations are accounted for, not dropped."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT rejection_id, theater_id, source_id, raw_payload, reason, ingested_at
            FROM log.obs_rejection
            WHERE theater_id = %s
            ORDER BY ingested_at DESC LIMIT %s
            """,
            (theater_id, limit),
        )
        return [{"rejection_id": r[0], "theater_id": r[1], "source_id": r[2],
                 "raw_payload": r[3], "reason": r[4], "ingested_at": _iso(r[5])}
                for r in cur.fetchall()]


def rejection_summary(conn, theater_id: str) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT reason, count(*) FROM log.obs_rejection WHERE theater_id = %s GROUP BY reason",
            (theater_id,),
        )
        by_reason = {r[0]: int(r[1]) for r in cur.fetchall()}
    return {"total": sum(by_reason.values()), "by_reason": by_reason}


# --------------------------------------------------------------- append-only writers

def insert_review(conn, event_id: str, action: str, reason: str, analyst: str) -> str:
    """Append a review annotation (confirm/split/reject/flag). Append-only; returns review_id."""
    if action not in ("confirm", "split", "reject", "flag"):
        raise ValueError(f"invalid review action {action!r}")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO world.review_annotation (event_id, action, reason, analyst) "
            "VALUES (%s, %s, %s, %s) RETURNING review_id",
            (event_id, action, reason, analyst),
        )
        rid = cur.fetchone()[0]
    conn.commit()
    return str(rid)


def insert_label(conn, kind: str, payload: dict, analyst: str,
                 versions: Optional[dict] = None, run_id: Optional[str] = None) -> str:
    """Append a human label / gray-band verdict. Append-only; returns label_id.

    `versions` carries the pinned model/prompt/schema/embedding versions captured at label time
    so a regenerated verdict cache keys identically (see eval/fixtures_io.py).
    """
    if kind not in ("incident_label", "gray_verdict"):
        raise ValueError(f"invalid label kind {kind!r}")
    v = versions or {}
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO world.label_annotation
                (kind, payload, model_version, prompt_version, schema_version,
                 embedding_version, run_id, analyst)
            VALUES (%s, %s::jsonb, %s, %s, %s, %s, %s, %s) RETURNING label_id
            """,
            (kind, json.dumps(payload, sort_keys=True), v.get("model_version"),
             v.get("prompt_version"), v.get("schema_version"), v.get("embedding_version"),
             run_id, analyst),
        )
        lid = cur.fetchone()[0]
    conn.commit()
    return str(lid)


def replay_check(conn, theater_id: str) -> dict:
    """Read-only audit: rebuild events from the current log and confirm determinism + no-drop.

    Runs the SAME fuse() twice over log.observation using the production verdict cache
    (ml.adjudication_cache) and a keep-separate backend, so it never calls a model. Reports
    whether the two rebuilds are bit-identical, the dropped-observation count (must be 0), and
    replayed vs materialized event counts. It does NOT write — rematerialization is an engine
    (write-role) operation, out of the read-only API's scope.
    """
    from llm.circuit_breaker import LLMUnavailable  # lazy; all pure
    from llm.cache import PgVerdictCache
    from fusion.db import load_observations
    from fusion.fuse import fuse
    from fusion.replay import assert_bit_identical

    class _KeepSeparate:
        def adjudicate(self, ctx):
            raise LLMUnavailable("replay audit: no live model; gray pairs kept separate")

    obs = load_observations(conn, theater_id)
    input_ids = {o.obs_id for o in obs}
    cache = PgVerdictCache(conn)
    r1 = fuse(obs, cache, _KeepSeparate(), theater_id=theater_id)
    r2 = fuse(obs, cache, _KeepSeparate(), theater_id=theater_id)
    cov = r1.coverage(input_ids)
    return {
        "theater_id": theater_id,
        "bit_identical": assert_bit_identical(r1, r2),
        "dropped_obs": len(cov["unaccounted"]),
        "n_obs": len(obs),
        "n_events_replayed": len(r1.events),
        "n_events_materialized": event_count(conn, theater_id),
        "digest": r1.digest(),
    }


def list_labels(conn, kind: Optional[str] = None) -> list[dict]:
    """All label annotations (optionally one kind), oldest-first — the fixture source-of-record."""
    where, params = "", []
    if kind:
        where = "WHERE kind = %s"; params.append(kind)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT label_id, kind, payload, model_version, prompt_version, schema_version,
                   embedding_version, run_id, analyst, created_at
            FROM world.label_annotation {where}
            ORDER BY created_at ASC, label_id ASC
            """,
            params,
        )
        rows = cur.fetchall()
    return [{
        "label_id": str(r[0]), "kind": r[1], "payload": r[2], "model_version": r[3],
        "prompt_version": r[4], "schema_version": r[5], "embedding_version": r[6],
        "run_id": r[7], "analyst": r[8], "created_at": _iso(r[9]),
    } for r in rows]


# --------------------------------------------------------------------------- helpers

def _event_row(r) -> dict:
    return {
        "event_id": str(r[0]), "theater_id": r[1], "event_type": r[2], "cell_id": r[3],
        "resolved_precision_m": r[4], "occurred_start": _iso(r[5]), "occurred_end": _iso(r[6]),
        "status": r[7], "confidence": r[8], "confidence_band": r[9], "n_sources": r[10],
        "n_independent_families": r[11], "flags": list(r[12] or []),
        "created_from_obs": _uuid_array(r[13]), "updated_at": _iso(r[14]),
    }


def _uuid_array(v) -> list[str]:
    """Normalize a uuid[] column to a list of strings.

    psycopg2 parses text[] into a Python list, but returns uuid[] as its raw array text form
    '{uuid1,uuid2}' (no uuid array caster registered). Iterating that string yields characters,
    so we parse it explicitly; an already-parsed list/tuple is coerced to strings.
    """
    if v is None:
        return []
    if isinstance(v, str):
        inner = v.strip().lstrip("{").rstrip("}").strip()
        return [x.strip().strip('"') for x in inner.split(",")] if inner else []
    return [str(x) for x in v]


# --------------------------------------------------------------------------- admin rollup (drill-down)

_ADMIN_COL = {1: "admin_l1_id", 2: "admin_l2_id", 3: "admin_l3_id"}


def rollup(conn, theater_id: str, level: int, parent_id: str | None = None,
           until: str | None = None) -> list[dict]:
    """Event activity aggregated to admin units at `level` (1 oblast / 2 raion / 3 hromada),
    optionally restricted to children of `parent_id` and to events on/before `until`. Units with
    no events still appear (n=0) so the map shows the whole area. Geometry is simplified for the
    choropleth; the time filter lives in the JOIN so empty-in-window units survive."""
    col = _ADMIN_COL[level]
    where = ["u.theater_id = %s", "u.level = %s"]
    params: list = [theater_id, level]
    if parent_id:
        where.append("u.parent_id = %s")
        params.append(parent_id)
    time_clause = ""
    if until:
        time_clause = "AND lower(e.occurred_at) <= %s"
        params.append(until)
    sql = f"""
        SELECT u.admin_id, u.name, u.level, u.parent_id,
               ST_X(ST_PointOnSurface(u.geom)) AS lon, ST_Y(ST_PointOnSurface(u.geom)) AS lat,
               ST_AsGeoJSON(ST_SimplifyPreserveTopology(u.geom, 0.005)) AS geojson,
               count(e.event_id) AS n,
               count(e.event_id) FILTER (WHERE e.confidence_band='High')    AS n_high,
               count(e.event_id) FILTER (WHERE e.confidence_band='Medium')  AS n_medium,
               count(e.event_id) FILTER (WHERE e.confidence_band='Low')     AS n_low,
               count(e.event_id) FILTER (WHERE e.confidence_band='Rumored') AS n_rumored,
               mode() WITHIN GROUP (ORDER BY e.event_type) AS top_type
        FROM geo.admin_unit u
        LEFT JOIN geo.cell_context cc ON cc.{col} = u.admin_id AND cc.theater_id = u.theater_id
        LEFT JOIN world.event e ON e.cell_id = cc.cell_id AND e.theater_id = u.theater_id {time_clause}
        WHERE {' AND '.join(where)}
        GROUP BY u.admin_id, u.name, u.level, u.parent_id, u.geom
        ORDER BY n DESC, u.name
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    import json as _json
    return [{
        "admin_id": r[0], "name": r[1], "level": r[2], "parent_id": r[3],
        "centroid": {"type": "Point", "coordinates": [r[4], r[5]]},
        "geometry": _json.loads(r[6]) if r[6] else None,
        "n_events": r[7],
        "bands": {"High": r[8], "Medium": r[9], "Low": r[10], "Rumored": r[11]},
        "top_type": r[12],
    } for r in rows]


def admin_breadcrumb(conn, admin_id: str) -> list[dict]:
    """The chain from the theater root down to `admin_id` (for the UI breadcrumb)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH RECURSIVE chain AS (
              SELECT admin_id, name, level, parent_id FROM geo.admin_unit WHERE admin_id = %s
              UNION ALL
              SELECT u.admin_id, u.name, u.level, u.parent_id
              FROM geo.admin_unit u JOIN chain c ON u.admin_id = c.parent_id
            )
            SELECT admin_id, name, level FROM chain ORDER BY level
            """,
            (admin_id,),
        )
        return [{"admin_id": r[0], "name": r[1], "level": r[2]} for r in cur.fetchall()]


# --------------------------------------------------- areas of interest (named geographic entities)

def list_features(conn, theater_id: str, kind: str | None = None,
                  bbox: list | None = None, limit: int = 3000) -> list[dict]:
    """Tier-1 reference feature library (geo.geo_feature) as map layers; simplified geometry."""
    import json as _json
    where = ["theater_id = %s"]
    params: list = [theater_id]
    if kind:
        where.append("layer = %s"); params.append(kind)
    if bbox and len(bbox) == 4:
        where.append("geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)"); params += list(bbox)
    params.append(limit)
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT feature_id, layer, properties->>'name',
                       ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom, 0.0008))
                FROM geo.geo_feature WHERE {' AND '.join(where)} LIMIT %s""",
            params,
        )
        return [{"feature_id": r[0], "kind": r[1], "name": r[2],
                 "geometry": _json.loads(r[3]) if r[3] else None} for r in cur.fetchall()]


def list_aois(conn, theater_id: str, kind: str | None = None) -> list[dict]:
    """Tier-2 areas of interest with their cell count + how many events fall in them."""
    where = ["a.theater_id = %s"]
    params: list = [theater_id]
    if kind:
        where.append("a.kind = %s"); params.append(kind)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT a.aoi_id, a.kind, a.label, a.source, a.note, lower(a.created_at::text),
                   count(DISTINCT ac.cell_id), count(DISTINCT e.event_id),
                   ST_X(ST_PointOnSurface(a.geom)), ST_Y(ST_PointOnSurface(a.geom))
            FROM world.area_of_interest a
            LEFT JOIN world.aoi_cell ac ON ac.aoi_id = a.aoi_id
            LEFT JOIN world.event e ON e.cell_id = ac.cell_id AND e.theater_id = a.theater_id
            WHERE {' AND '.join(where)}
            GROUP BY a.aoi_id ORDER BY a.created_at DESC
            """,
            params,
        )
        out = []
        for r in cur.fetchall():
            out.append({"aoi_id": r[0], "kind": r[1], "label": r[2], "source": r[3], "note": r[4],
                        "created_at": r[5], "n_cells": r[6], "n_events": r[7],
                        "centroid": ({"type": "Point", "coordinates": [r[8], r[9]]}
                                     if r[8] is not None else None)})
        return out


def get_aoi(conn, aoi_id: int) -> dict | None:
    """One AOI: geometry, its cell-set, and an event-band summary (the rich brief is sub-project 2)."""
    import json as _json
    with conn.cursor() as cur:
        cur.execute(
            """SELECT aoi_id, theater_id, kind, label, source, note,
                      ST_AsGeoJSON(geom), ST_X(ST_PointOnSurface(geom)), ST_Y(ST_PointOnSurface(geom))
               FROM world.area_of_interest WHERE aoi_id = %s""",
            (aoi_id,),
        )
        r = cur.fetchone()
        if not r:
            return None
        cur.execute("SELECT cell_id FROM world.aoi_cell WHERE aoi_id = %s", (aoi_id,))
        cells = [x[0] for x in cur.fetchall()]
        cur.execute(
            """SELECT confidence_band, count(*) FROM world.event
               WHERE theater_id = %s AND cell_id IN (SELECT cell_id FROM world.aoi_cell WHERE aoi_id = %s)
               GROUP BY 1""",
            (r[1], aoi_id),
        )
        bands = {x[0]: x[1] for x in cur.fetchall()}
    return {"aoi_id": r[0], "theater_id": r[1], "kind": r[2], "label": r[3], "source": r[4],
            "note": r[5], "geometry": _json.loads(r[6]) if r[6] else None,
            "centroid": ({"type": "Point", "coordinates": [r[7], r[8]]} if r[7] is not None else None),
            "cells": cells, "n_cells": len(cells), "bands": bands}


def create_aoi(conn, theater_id: str, kind: str, label: str, source: str, *,
               source_feature_id: int | None = None, geom_wkt: str | None = None,
               cell_ids: list | None = None, admin_id: str | None = None,
               note: str | None = None, created_by: str | None = None) -> dict:
    """Create an AOI from a feature / drawn geometry / lassoed cells, resolving its cell-set ONCE.
    Returns {aoi_id, n_cells}; n_cells==0 means the geometry didn't intersect the grid (caller 422s)."""
    with conn.cursor() as cur:
        wkt = geom_wkt
        if source_feature_id and not wkt:
            cur.execute("SELECT ST_AsText(geom) FROM geo.geo_feature WHERE feature_id = %s",
                        (source_feature_id,))
            row = cur.fetchone()
            wkt = row[0] if row else None
        cur.execute(
            """INSERT INTO world.area_of_interest
                 (theater_id, kind, label, source, source_feature_id, geom, note, created_by)
               VALUES (%s, %s, %s, %s, %s, ST_GeomFromText(%s, 4326), %s, %s) RETURNING aoi_id""",
            (theater_id, kind, label, source, source_feature_id, wkt, note, created_by),
        )
        aoi_id = cur.fetchone()[0]
        if cell_ids:
            cells = [c for c in cell_ids if c]
        elif admin_id:                                    # watch a whole region → its cells
            cur.execute(
                """SELECT cell_id FROM geo.cell_context
                   WHERE theater_id = %s
                     AND %s IN (admin_l1_id, admin_l2_id, admin_l3_id)""",
                (theater_id, admin_id),
            )
            cells = [x[0] for x in cur.fetchall()]
        elif wkt:
            cur.execute(
                """SELECT cell_id FROM geo.grid_cell
                   WHERE theater_id = %s AND ST_Intersects(geom, ST_GeomFromText(%s, 4326))""",
                (theater_id, wkt),
            )
            cells = [x[0] for x in cur.fetchall()]
        else:
            cells = []
        for cid in cells:
            cur.execute("INSERT INTO world.aoi_cell (aoi_id, cell_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        (aoi_id, cid))
    conn.commit()
    return {"aoi_id": aoi_id, "n_cells": len(cells)}


def delete_aoi(conn, aoi_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM world.area_of_interest WHERE aoi_id = %s", (aoi_id,))
        deleted = cur.rowcount > 0
    conn.commit()
    return deleted


# ----------------------------------------------------- synthesis: AOR watch + per-area Read (6a)

def gather_area_context(conn, aoi_id: int) -> dict | None:
    """The grounded inputs for one area's attention + Read: its events (with a place label), the
    anomaly assessments scoped to its cells, and the distinct sensor families that saw it."""
    from datetime import timezone
    from api.places import nearest_place
    with conn.cursor() as cur:
        cur.execute("SELECT label, theater_id FROM world.area_of_interest WHERE aoi_id = %s", (aoi_id,))
        row = cur.fetchone()
        if not row:
            return None
        label, theater = row
        cur.execute(
            """SELECT e.event_type, e.confidence_band, lower(e.occurred_at), e.n_independent_families,
                      ST_X(gc.centroid), ST_Y(gc.centroid)
               FROM world.event e
               JOIN world.aoi_cell ac ON ac.cell_id = e.cell_id
               JOIN geo.grid_cell gc ON gc.cell_id = e.cell_id
               WHERE ac.aoi_id = %s AND e.theater_id = %s""",
            (aoi_id, theater),
        )
        events = []
        for r in cur.fetchall():
            occ = r[2]
            events.append({
                "event_type": r[0], "confidence_band": r[1],
                "occurred_start": (occ.replace(tzinfo=timezone.utc) if (occ and not occ.tzinfo) else occ),
                "n_independent_families": r[3],
                "place_label": (nearest_place(r[4], r[5], theater) or {}).get("label"),
            })
        cur.execute(
            """SELECT subkind FROM world.assessment
               WHERE theater_id = %s AND assessment_type = 'anomaly'
                 AND cell_id IN (SELECT cell_id FROM world.aoi_cell WHERE aoi_id = %s)""",
            (theater, aoi_id),
        )
        anomalies = [{"subkind": r[0]} for r in cur.fetchall()]
        cur.execute(
            """SELECT DISTINCT source_family_id FROM log.observation
               WHERE theater_id = %s
                 AND cell_id IN (SELECT cell_id FROM world.aoi_cell WHERE aoi_id = %s)""",
            (theater, aoi_id),
        )
        families = [r[0] for r in cur.fetchall()]
    return {"label": label, "theater_id": theater, "events": events,
            "anomalies": anomalies, "families": families}


def get_cached_read(conn, aoi_id: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT summary, indicators, provenance, generated_by, input_hash
               FROM world.area_read WHERE aoi_id = %s""", (aoi_id,))
        r = cur.fetchone()
    if not r:
        return None
    return {"summary": r[0], "indicators": r[1], "provenance": r[2] or [],
            "generated_by": r[3], "input_hash": r[4]}


def upsert_read(conn, aoi_id: int, read: dict, input_hash: str) -> None:
    import json
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO world.area_read
                 (aoi_id, summary, indicators, provenance, input_hash, generated_by, generated_at)
               VALUES (%s, %s, %s, %s::jsonb, %s, %s, now())
               ON CONFLICT (aoi_id) DO UPDATE SET
                 summary=EXCLUDED.summary, indicators=EXCLUDED.indicators,
                 provenance=EXCLUDED.provenance, input_hash=EXCLUDED.input_hash,
                 generated_by=EXCLUDED.generated_by, generated_at=now()""",
            (aoi_id, read["summary"], read["indicators"], json.dumps(read.get("provenance", [])),
             input_hash, read.get("generated_by", "template")),
        )
    conn.commit()


# --------------------------------------------------------------------------- assessments (Phase 4a)

def top_significant(conn, theater_id: str, limit: int = 20) -> list[dict]:
    """The ranked 'what to look at' feed — significance assessments joined to their events."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.event_id, a.score, a.rationale, a.components,
                   e.event_type, e.confidence_band, e.cell_id, e.n_independent_families,
                   lower(e.occurred_at)
            FROM world.assessment a
            JOIN world.event e ON e.event_id = a.event_id
            WHERE a.theater_id = %s AND a.assessment_type = 'significance'
            ORDER BY a.score DESC, e.event_id
            LIMIT %s
            """,
            (theater_id, limit),
        )
        return [{
            "event_id": str(r[0]), "score": r[1], "rationale": r[2], "components": r[3] or {},
            "event_type": r[4], "confidence_band": r[5], "cell_id": r[6],
            "n_independent_families": r[7], "occurred_start": _iso(r[8]),
        } for r in cur.fetchall()]


def anomaly_assessments(conn, theater_id: str, limit: int = 20) -> list[dict]:
    """Per-cell anomaly assessments (activity spikes / escalations), highest first."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT subkind, cell_id, score, rationale, components
            FROM world.assessment
            WHERE theater_id = %s AND assessment_type = 'anomaly'
            ORDER BY score DESC, cell_id
            LIMIT %s
            """,
            (theater_id, limit),
        )
        return [{"subkind": r[0], "cell_id": r[1], "score": r[2], "rationale": r[3],
                 "components": r[4] or {}} for r in cur.fetchall()]


def event_assessments(conn, theater_id: str, assessment_type: str, limit: int = 25) -> list[dict]:
    """Event-linked assessments of a given kind (exposure / gaps), highest score first."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.event_id, a.score, a.rationale, a.components,
                   e.event_type, e.confidence_band, e.cell_id, e.n_independent_families
            FROM world.assessment a
            JOIN world.event e ON e.event_id = a.event_id
            WHERE a.theater_id = %s AND a.assessment_type = %s
            ORDER BY a.score DESC, e.event_id
            LIMIT %s
            """,
            (theater_id, assessment_type, limit),
        )
        return [{
            "event_id": str(r[0]), "score": r[1], "rationale": r[2], "components": r[3] or {},
            "event_type": r[4], "confidence_band": r[5], "cell_id": r[6],
            "n_independent_families": r[7],
        } for r in cur.fetchall()]


def _iso(v):
    if v is None:
        return None
    try:
        return v.isoformat()
    except AttributeError:
        return str(v)
