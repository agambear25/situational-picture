"""
CORE: OSM highway/rail → nearest_road_class and has_bridge per 1km cell.
"""
from __future__ import annotations

import logging
from pathlib import Path

import osmium
from shapely.geometry import LineString, MultiLineString

logger = logging.getLogger(__name__)

ROAD_CLASS_RANK = {
    "motorway": 1, "trunk": 2, "primary": 3, "secondary": 4,
    "tertiary": 5, "residential": 6, "track": 7, "path": 8,
}


class TransportHandler(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.roads: list[dict] = []   # {geom: LineString, road_class: str, bridge: bool}

    def way(self, w):
        highway = w.tags.get("highway")
        if not highway:
            return
        is_bridge = w.tags.get("bridge") == "yes"
        try:
            wkb = osmium.geom.WKBFactory()
            geom_wkb = wkb.create_linestring(w)
            from shapely import wkb as swkb
            geom = swkb.loads(geom_wkb, hex=True)
            self.roads.append({"geom": geom, "road_class": highway, "bridge": is_bridge})
        except Exception:
            pass


def load_transport(theater_id: str, conn, pbf_path: str, bbox: tuple) -> int:
    from geo.db import cell_ids_for_theater
    from grid.mgrs_1km import cell_id_to_polygon

    if not Path(pbf_path).exists():
        logger.warning("PBF not found at %s — skipping transport layer", pbf_path)
        return 0

    handler = TransportHandler()
    handler.apply_file(pbf_path, locations=True)
    logger.info("Loaded %d road geometries", len(handler.roads))

    cell_ids = cell_ids_for_theater(conn, theater_id)
    updated = 0

    with conn.cursor() as cur:
        for cell_id in cell_ids:
            poly = cell_id_to_polygon(cell_id)
            roads_in_cell = [r for r in handler.roads if r["geom"].intersects(poly)]

            nearest_class = None
            has_bridge = any(r["bridge"] for r in roads_in_cell)

            if roads_in_cell:
                # Pick the highest-ranked road class present
                ranked = sorted(
                    roads_in_cell,
                    key=lambda r: ROAD_CLASS_RANK.get(r["road_class"], 99),
                )
                nearest_class = ranked[0]["road_class"]

            cur.execute(
                """
                INSERT INTO geo.cell_context
                    (cell_id, theater_id, nearest_road_class, has_bridge, updated_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (cell_id) DO UPDATE SET
                    nearest_road_class = EXCLUDED.nearest_road_class,
                    has_bridge = EXCLUDED.has_bridge,
                    updated_at = now()
                """,
                (cell_id, theater_id, nearest_class, has_bridge),
            )
            updated += 1
        conn.commit()

    logger.info("Updated transport for %d cells", updated)
    return updated
