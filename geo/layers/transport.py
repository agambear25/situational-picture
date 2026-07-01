"""
CORE: OSM highway/rail → nearest_road_class, road_surface and has_bridge per 1km cell.

`classify_surface` (pure) coarsens the OSM surface/highway tags to paved|unpaved|unknown — the
"dirt road" proxy. `load_transport` bbox-filters the roads to the theater then uses an STRtree so
the per-cell lookup is O(log roads), not a full scan over every Ukraine road for every cell.
"""
from __future__ import annotations

import logging
from pathlib import Path

import osmium
from shapely.geometry import LineString, MultiLineString  # noqa: F401 (kept for callers/back-compat)

logger = logging.getLogger(__name__)

ROAD_CLASS_RANK = {
    "motorway": 1, "trunk": 2, "primary": 3, "secondary": 4,
    "tertiary": 5, "residential": 6, "track": 7, "path": 8,
}

_UNPAVED = {"unpaved", "dirt", "ground", "gravel", "compacted", "fine_gravel", "earth", "mud", "sand"}
_PAVED = {"paved", "asphalt", "concrete", "paving_stones", "sett", "cobblestone"}
_PAVED_BY_DEFAULT = {"motorway", "trunk", "primary", "secondary", "tertiary", "residential"}


def classify_surface(highway: str, surface: str | None) -> str:
    """Coarse paving: 'paved' | 'unpaved' | 'unknown'. track/path default unpaved (the dirt proxy)."""
    if surface:
        if surface in _UNPAVED:
            return "unpaved"
        if surface in _PAVED:
            return "paved"
    if highway in ("track", "path"):
        return "unpaved"
    if highway in _PAVED_BY_DEFAULT:
        return "paved"
    return "unknown"


class TransportHandler(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.roads: list[dict] = []   # {geom, road_class, bridge, surface}

    def way(self, w):
        highway = w.tags.get("highway")
        if not highway:
            return
        is_bridge = w.tags.get("bridge") == "yes"
        surface = classify_surface(highway, w.tags.get("surface"))
        try:
            wkb = osmium.geom.WKBFactory()
            geom_wkb = wkb.create_linestring(w)
            from shapely import wkb as swkb
            geom = swkb.loads(geom_wkb, hex=True)
            self.roads.append({"geom": geom, "road_class": highway, "bridge": is_bridge,
                               "surface": surface})
        except Exception:
            pass


def load_transport(theater_id: str, conn, pbf_path: str, bbox: tuple) -> int:
    from geo.db import cell_ids_for_theater
    from grid.mgrs_1km import cell_id_to_polygon

    if not Path(pbf_path).exists():
        logger.warning("PBF not found at %s — skipping transport layer", pbf_path)
        return 0

    from shapely.geometry import box
    from shapely.strtree import STRtree

    handler = TransportHandler()
    handler.apply_file(pbf_path, locations=True)
    logger.info("Loaded %d road geometries (all)", len(handler.roads))

    # Keep only roads inside the theater bbox, then index them so each cell is an O(log n) lookup
    # instead of a scan over every Ukraine road (millions × 71k cells is intractable).
    bb = box(bbox[0], bbox[1], bbox[2], bbox[3])
    roads = [r for r in handler.roads if r["geom"].intersects(bb)]
    logger.info("  %d roads inside theater bbox", len(roads))
    geoms = [r["geom"] for r in roads]
    tree = STRtree(geoms)

    cell_ids = cell_ids_for_theater(conn, theater_id)
    updated = 0

    with conn.cursor() as cur:
        for cell_id in cell_ids:
            poly = cell_id_to_polygon(cell_id)
            cand = tree.query(poly)                       # shapely 2.x → candidate integer indices
            roads_in_cell = [roads[i] for i in cand if geoms[i].intersects(poly)]

            nearest_class = None
            nearest_surface = None
            has_bridge = any(r["bridge"] for r in roads_in_cell)

            if roads_in_cell:
                ranked = sorted(roads_in_cell, key=lambda r: ROAD_CLASS_RANK.get(r["road_class"], 99))
                nearest_class = ranked[0]["road_class"]
                nearest_surface = ranked[0]["surface"]

            cur.execute(
                """
                INSERT INTO geo.cell_context
                    (cell_id, theater_id, nearest_road_class, road_surface, has_bridge, updated_at)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (cell_id) DO UPDATE SET
                    nearest_road_class = EXCLUDED.nearest_road_class,
                    road_surface = EXCLUDED.road_surface,
                    has_bridge = EXCLUDED.has_bridge,
                    updated_at = now()
                """,
                (cell_id, theater_id, nearest_class, nearest_surface, has_bridge),
            )
            updated += 1
        conn.commit()

    logger.info("Updated transport for %d cells", updated)
    return updated
