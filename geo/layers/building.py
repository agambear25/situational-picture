"""
CORE: OSM building footprints → builtup_pct and building_count per 1km cell.
Reads from the Geofabrik Ukraine .pbf (already downloaded).
"""
from __future__ import annotations

import logging
from pathlib import Path

import osmium
from shapely.geometry import Polygon, mapping
from shapely.ops import unary_union

logger = logging.getLogger(__name__)


class BuildingHandler(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.buildings: list[Polygon] = []

    def area(self, a):
        if a.tags.get("building"):
            try:
                wkb = osmium.geom.WKBFactory()
                geom_wkb = wkb.create_multipolygon(a)
                from shapely import wkb as swkb
                geom = swkb.loads(geom_wkb, hex=True)
                if geom.is_valid:
                    self.buildings.append(geom)
            except Exception:
                pass


def load_buildings(theater_id: str, conn, pbf_path: str, bbox: tuple) -> int:
    """Extract OSM buildings, compute builtup_pct per cell, upsert into cell_context."""
    from geo.db import cell_ids_for_theater
    from grid.mgrs_1km import cell_id_to_polygon

    if not Path(pbf_path).exists():
        logger.warning("PBF not found at %s — skipping building layer", pbf_path)
        return 0

    logger.info("Parsing OSM buildings from %s", pbf_path)
    handler = BuildingHandler()
    handler.apply_file(pbf_path, locations=True)
    logger.info("Loaded %d building geometries", len(handler.buildings))

    if not handler.buildings:
        return 0

    cell_ids = cell_ids_for_theater(conn, theater_id)
    updated = 0

    with conn.cursor() as cur:
        for cell_id in cell_ids:
            poly = cell_id_to_polygon(cell_id)
            cell_area = poly.area

            # Buildings intersecting this cell
            intersecting = [b for b in handler.buildings if b.intersects(poly)]
            if not intersecting:
                builtup_pct = 0.0
                count = 0
            else:
                clipped = unary_union([b.intersection(poly) for b in intersecting])
                builtup_pct = float(clipped.area / cell_area) if cell_area > 0 else 0.0
                count = len(intersecting)

            cur.execute(
                """
                INSERT INTO geo.cell_context
                    (cell_id, theater_id, builtup_pct, building_count, updated_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (cell_id) DO UPDATE SET
                    builtup_pct = EXCLUDED.builtup_pct,
                    building_count = EXCLUDED.building_count,
                    updated_at = now()
                """,
                (cell_id, theater_id, builtup_pct, count),
            )
            updated += 1
        conn.commit()

    logger.info("Updated building data for %d cells", updated)
    return updated
