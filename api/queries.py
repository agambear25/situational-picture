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
) -> list[dict]:
    """Events for a theater, newest-first, with optional status/band/flag filters."""
    where = ["theater_id = %s"]
    params: list = [theater_id]
    if status:
        where.append("status = %s"); params.append(status)
    if band:
        where.append("confidence_band = %s"); params.append(band)
    if flag:
        where.append("%s = ANY(flags)"); params.append(flag)
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
        return [_event_row(r) for r in cur.fetchall()]


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


def _iso(v):
    if v is None:
        return None
    try:
        return v.isoformat()
    except AttributeError:
        return str(v)
