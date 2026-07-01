"""
The DB boundary for the assessment engine. Loads events from the read model and writes
world.assessment. Lazy psycopg2 so the pure scorers (significance/anomaly) import freely.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timezone


def load_events(conn, theater_id: str) -> list[dict]:
    """Events for a theater as plain dicts (the fields the scorers need)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.event_id, e.event_type, e.cell_id, e.confidence, e.confidence_band,
                   lower(e.occurred_at), e.n_sources, e.n_independent_families, e.flags,
                   ST_X(gc.centroid), ST_Y(gc.centroid),
                   cc.builtup_pct, cc.landcover_label, cc.nearest_road_class, cc.road_surface
            FROM world.event e
            LEFT JOIN geo.grid_cell gc ON gc.cell_id = e.cell_id
            LEFT JOIN geo.cell_context cc ON cc.cell_id = e.cell_id
            WHERE e.theater_id = %s
            """,
            (theater_id,),
        )
        rows = cur.fetchall()
    out = []
    for r in rows:
        ts = r[5]
        out.append({
            "event_id": str(r[0]), "event_type": r[1], "cell_id": r[2],
            "confidence": r[3], "confidence_band": r[4],
            "occurred_start": ts if (ts is None or ts.tzinfo) else ts.replace(tzinfo=timezone.utc),
            "n_sources": r[6], "n_independent_families": r[7], "flags": list(r[8] or []),
            "lon": r[9], "lat": r[10],
            "builtup_pct": r[11], "landcover_label": r[12],
            "nearest_road_class": r[13], "road_surface": r[14],
        })
    return out


def cell_type_counts(events: list[dict]) -> dict[str, dict[str, int]]:
    """{cell_id: {event_type: count}} — the per-cell history novelty scoring needs."""
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for e in events:
        counts[e["cell_id"]][e["event_type"]] += 1
    return counts


def write_assessments(conn, theater_id: str, rows: list[dict], now, truncate: bool = True) -> int:
    """Materialise assessments. Truncate the theater's slice first (rebuildable read model).

    Maps to the 0004+0010 columns: assessment_type (= our 'kind'), cell_id (NOT NULL, every
    assessment is cell-anchored), as_of (the computation time), + event_id/subkind/components.
    """
    import json
    with conn.cursor() as cur:
        if truncate:
            cur.execute("DELETE FROM world.assessment WHERE theater_id = %s", (theater_id,))
        for a in rows:
            cur.execute(
                """
                INSERT INTO world.assessment
                    (theater_id, assessment_type, subkind, event_id, cell_id, score,
                     components, rationale, as_of)
                VALUES (%s, %s, %s, %s::uuid, %s, %s, %s::jsonb, %s, %s)
                """,
                (theater_id, a["kind"], a.get("subkind"), a.get("event_id"), a["cell_id"],
                 float(a["score"]), json.dumps(a.get("components", {})), a.get("rationale"), now),
            )
    conn.commit()
    return len(rows)
