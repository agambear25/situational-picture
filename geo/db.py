"""
PostGIS utility wrappers for geo/ layer loaders.
All writes go through bulk_upsert_features; no other module writes geo_feature directly.
"""
from __future__ import annotations

import json
import logging
from typing import Iterable

logger = logging.getLogger(__name__)


def bulk_upsert_features(
    conn,
    theater_id: str,
    layer: str,
    features: Iterable[dict],
    as_of: str | None = None,
    source: str | None = None,
    batch_size: int = 500,
) -> int:
    """Idempotent upsert of geo_feature rows.

    Each feature dict must have:
      - 'geom_wkt': WKT string (EPSG:4326)
      - 'properties': dict
      - optionally 'cell_id': TEXT
    """
    rows = list(features)
    if not rows:
        return 0

    upserted = 0
    with conn.cursor() as cur:
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            for feat in batch:
                cur.execute(
                    """
                    INSERT INTO geo.geo_feature
                        (theater_id, layer, cell_id, geom, properties, as_of, source)
                    VALUES
                        (%s, %s, %s,
                         ST_SetSRID(ST_GeomFromText(%s), 4326),
                         %s::jsonb, %s::date, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        theater_id,
                        layer,
                        feat.get("cell_id"),
                        feat["geom_wkt"],
                        json.dumps(feat.get("properties", {})),
                        as_of,
                        source,
                    ),
                )
                upserted += 1
            conn.commit()
            logger.debug("Upserted batch %d–%d for layer '%s'", i, i + len(batch), layer)

    logger.info("Upserted %d features for layer '%s' in theater '%s'", upserted, layer, theater_id)
    return upserted


def cell_ids_for_theater(conn, theater_id: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT cell_id FROM geo.grid_cell WHERE theater_id = %s ORDER BY cell_id",
            (theater_id,)
        )
        return [row[0] for row in cur.fetchall()]
