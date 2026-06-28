"""
CORE: Copernicus DEM GLO-30 → mean_slope_deg per 1km grid cell.
NOTE: Run this pass when Ollama is NOT resident with a large model — both need significant RAM.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import rasterio
from rasterio.mask import mask
from shapely.geometry import mapping

logger = logging.getLogger(__name__)

# Copernicus GLO-30 tiles at 1° × 1° coverage
# Tile naming: Copernicus_DSM_COG_10_N47_00_E037_00_DEM.tif etc.


def load_dem(theater_id: str, conn, tile_paths: list[str]) -> int:
    """Compute mean slope per cell from DEM tiles and upsert into cell_context."""
    from geo.db import cell_ids_for_theater
    from grid.mgrs_1km import cell_id_to_polygon

    cell_ids = cell_ids_for_theater(conn, theater_id)
    datasets = [rasterio.open(p) for p in tile_paths if Path(p).exists()]
    if not datasets:
        logger.warning("No DEM tiles found — skipping slope computation")
        return 0

    logger.info("Computing slope for %d cells from %d DEM tiles", len(cell_ids), len(datasets))
    updated = 0

    with conn.cursor() as cur:
        for cell_id in cell_ids:
            poly = cell_id_to_polygon(cell_id)
            slope = _mean_slope(datasets, poly)
            if slope is None:
                continue
            cur.execute(
                """
                INSERT INTO geo.cell_context (cell_id, theater_id, mean_slope_deg, updated_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (cell_id) DO UPDATE SET
                    mean_slope_deg = EXCLUDED.mean_slope_deg,
                    updated_at = now()
                """,
                (cell_id, theater_id, float(slope)),
            )
            updated += 1
        conn.commit()

    for ds in datasets:
        ds.close()
    logger.info("Updated slope for %d cells", updated)
    return updated


def _mean_slope(datasets: list, poly) -> float | None:
    """Approximate mean slope in degrees from elevation differences."""
    for ds in datasets:
        try:
            out_image, out_transform = mask(ds, [mapping(poly)], crop=True, nodata=-9999)
            elev = out_image[0].astype(float)
            elev[elev == -9999] = np.nan
            if np.all(np.isnan(elev)):
                continue
            # Gradient magnitude in pixel units × m/pixel → degrees
            res_m = abs(out_transform.a) * 111_320  # approx degrees → meters at this lat
            dy, dx = np.gradient(np.nan_to_num(elev))
            slope_rad = np.arctan(np.sqrt((dx / res_m) ** 2 + (dy / res_m) ** 2))
            return float(np.nanmean(np.degrees(slope_rad)))
        except Exception:
            continue
    return None
