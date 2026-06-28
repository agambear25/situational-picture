"""
The ONLY SQL boundary for fusion. Pure stages never touch the DB; this module loads
observations from log.observation and writes Events back to the read model. Lazy psycopg2
import so the pure fusion path and the eval harness never require a database.
"""
from __future__ import annotations

from datetime import timezone

from ingest.contract import Observation


def load_observations(conn, theater_id: str) -> list[Observation]:
    """Load placed observations for a theater in deterministic order."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT obs_id, theater_id, source_id, source_family_id, modality, obs_type,
                   lower(occurred_at), upper(occurred_at), cell_id, geom_precision_m,
                   place_id, raw_text, embedding, content_hash, lang, self_conf
            FROM log.observation
            WHERE theater_id = %s
            ORDER BY lower(occurred_at), obs_id
            """,
            (theater_id,),
        )
        rows = cur.fetchall()

    out = []
    for r in rows:
        emb = tuple(r[12]) if r[12] is not None else None
        out.append(Observation(
            obs_id=str(r[0]), theater_id=r[1], source_id=r[2], source_family_id=r[3],
            modality=r[4], obs_type=r[5],
            occurred_start=_utc(r[6]), occurred_end=_utc(r[7]),
            cell_id=r[8], geom_precision_m=r[9] or 1000.0, place_id=r[10],
            text=r[11] or "", embedding=emb, content_hash=r[13], lang=r[14], self_conf=r[15],
        ))
    return out


def landcover_by_obs(conn, obs: list[Observation]) -> dict:
    """Map obs_id → dominant_landcover for the land-cover plausibility gate."""
    cells = list({o.cell_id for o in obs})
    if not cells:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT cell_id, dominant_landcover FROM geo.cell_context WHERE cell_id = ANY(%s)",
            (cells,),
        )
        lc = {row[0]: row[1] for row in cur.fetchall()}
    return {o.obs_id: lc.get(o.cell_id) for o in obs}


def write_events(conn, result, theater_id: str, truncate: bool = True) -> int:
    """Materialize Events + event_observation. If truncate, clear the theater's read model first
    (used by the replay/admin endpoint). Append-only log is never touched."""
    with conn.cursor() as cur:
        if truncate:
            cur.execute("DELETE FROM world.event_observation WHERE event_id IN "
                        "(SELECT event_id FROM world.event WHERE theater_id = %s)", (theater_id,))
            cur.execute("DELETE FROM world.event WHERE theater_id = %s", (theater_id,))

        for e in result.events:
            cur.execute(
                """
                INSERT INTO world.event
                    (event_id, theater_id, event_type, cell_id, resolved_precision_m,
                     occurred_at, status, confidence, confidence_band, n_sources,
                     n_independent_families, flags, created_from_obs, decision_time)
                VALUES (%s, %s, %s, %s, %s, tstzrange(%s, %s, '[)'), %s, %s, %s, %s, %s, %s, %s, NULL)
                ON CONFLICT (event_id) DO NOTHING
                """,
                (e.event_id, theater_id, e.event_type, e.cell_id, e.resolved_precision_m,
                 e.occurred_start, e.occurred_end, e.status, e.confidence, e.confidence_band,
                 e.n_sources, e.n_independent_families, list(e.flags), list(e.created_from_obs)),
            )
            for obs_id in e.created_from_obs:
                cur.execute(
                    "INSERT INTO world.event_observation (event_id, obs_id, member_score) "
                    "VALUES (%s, %s, NULL) ON CONFLICT DO NOTHING",
                    (e.event_id, obs_id),
                )
    conn.commit()
    return len(result.events)


def _utc(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
