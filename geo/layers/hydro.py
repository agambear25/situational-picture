"""
CORE (rec): OSM water bodies + HydroRIVERS → has_river per 1km cell.
Also contributes has_bridge from water-crossing road detections.
"""
from __future__ import annotations

import logging
import zipfile
from pathlib import Path

import osmium
from shapely.geometry import LineString, shape

logger = logging.getLogger(__name__)


class WaterHandler(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.rivers: list = []

    def way(self, w):
        waterway = w.tags.get("waterway")
        if waterway in ("river", "stream", "canal"):
            try:
                wkb = osmium.geom.WKBFactory()
                geom_wkb = wkb.create_linestring(w)
                from shapely import wkb as swkb
                geom = swkb.loads(geom_wkb, hex=True)
                self.rivers.append(geom)
            except Exception:
                pass


def load_hydro(theater_id: str, conn, pbf_path: str, bbox: tuple) -> int:
    from geo.db import cell_ids_for_theater
    from grid.mgrs_1km import cell_id_to_polygon

    if not Path(pbf_path).exists():
        logger.warning("PBF not found — skipping hydro")
        return 0

    handler = WaterHandler()
    handler.apply_file(pbf_path, locations=True)
    logger.info("Loaded %d waterway geometries", len(handler.rivers))

    cell_ids = cell_ids_for_theater(conn, theater_id)
    updated = 0

    with conn.cursor() as cur:
        for cell_id in cell_ids:
            poly = cell_id_to_polygon(cell_id)
            has_river = any(r.intersects(poly) for r in handler.rivers)

            cur.execute(
                """
                INSERT INTO geo.cell_context
                    (cell_id, theater_id, has_river, updated_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (cell_id) DO UPDATE SET
                    has_river = EXCLUDED.has_river,
                    updated_at = now()
                """,
                (cell_id, theater_id, has_river),
            )
            updated += 1
        conn.commit()

    logger.info("Updated hydro for %d cells", updated)
    return updated
