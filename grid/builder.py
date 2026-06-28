"""
Grid builder: enumerate all 1km MGRS cells over the AOI, attach admin labels,
assign local_seq, and upsert into geo.grid_cell.

Called by: `python -m grid.cli build --theater ua_donbas`
"""
from __future__ import annotations

import logging
from typing import Iterator

import mgrs as _mgrs
import pyproj
from shapely.geometry import box, Point
from shapely.ops import transform

from grid.admin_link import AdminResolver, NullAdminResolver
from grid.local_seq import assign_local_seqs, build_label
from grid.mgrs_1km import to_cell_id, cell_id_to_polygon

logger = logging.getLogger(__name__)

_M = _mgrs.MGRS()


def _enumerate_cells_over_aoi(
    west: float, south: float, east: float, north: float, step_deg: float = 0.01
) -> Iterator[tuple[float, float]]:
    """Yield (lon, lat) sample points covering the AOI at ~1km spacing.

    1km ≈ 0.009° latitude. We use 0.01° to ensure no cell is missed.
    Deduplicated by cell_id in build_grid.
    """
    lat = south
    while lat <= north:
        lon = west
        while lon <= east:
            yield lon, lat
            lon += step_deg
        lat += step_deg


def build_grid(
    theater_id: str,
    bbox: tuple[float, float, float, float],  # (west, south, east, north)
    conn,
    admin_resolver: AdminResolver | None = None,
) -> int:
    """Enumerate MGRS 1km cells over AOI, attach labels, upsert into geo.grid_cell.

    Returns the number of cells upserted.
    """
    if admin_resolver is None:
        admin_resolver = NullAdminResolver()

    west, south, east, north = bbox
    logger.info("Building grid for %s over bbox %s", theater_id, bbox)

    # Collect all unique cells
    seen: dict[str, dict] = {}
    for lon, lat in _enumerate_cells_over_aoi(west, south, east, north):
        try:
            cell_id = to_cell_id(lon, lat)
        except Exception:
            continue
        if cell_id in seen:
            continue

        admin = admin_resolver.resolve(lon, lat)
        seen[cell_id] = {
            "cell_id": cell_id,
            "theater_id": theater_id,
            "admin_l1": admin.admin_l1 if admin else None,
            "admin_l2": admin.admin_l2 if admin else None,
            "admin_l3": admin.admin_l3 if admin else None,
            "admin_path": admin.admin_path if admin else None,
            "lon": lon,
            "lat": lat,
        }

    logger.info("Collected %d unique cells", len(seen))

    # Assign local sequences
    seqs = assign_local_seqs(list(seen.values()), admin_key="admin_l3")

    # Upsert
    upserted = 0
    with conn.cursor() as cur:
        for cell_id, cell in seen.items():
            seq = seqs.get(cell_id)
            label = build_label(cell.get("admin_l3"), seq) if seq else cell_id
            poly = cell_id_to_polygon(cell_id)
            centroid = poly.centroid

            cur.execute(
                """
                INSERT INTO geo.grid_cell
                    (cell_id, theater_id, geom, centroid, admin_l1, admin_l2, admin_l3,
                     admin_path, local_seq, label)
                VALUES
                    (%s, %s,
                     ST_SetSRID(ST_GeomFromText(%s), 4326),
                     ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                     %s, %s, %s, %s, %s, %s)
                ON CONFLICT (cell_id) DO UPDATE SET
                    admin_l1 = EXCLUDED.admin_l1,
                    admin_l2 = EXCLUDED.admin_l2,
                    admin_l3 = EXCLUDED.admin_l3,
                    admin_path = EXCLUDED.admin_path,
                    local_seq = EXCLUDED.local_seq,
                    label = EXCLUDED.label
                """,
                (
                    cell_id, theater_id,
                    poly.wkt,
                    centroid.x, centroid.y,
                    cell.get("admin_l1"), cell.get("admin_l2"), cell.get("admin_l3"),
                    cell.get("admin_path"), seq, label,
                ),
            )
            upserted += 1

    conn.commit()
    logger.info("Upserted %d cells for theater %s", upserted, theater_id)
    return upserted
