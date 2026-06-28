"""
CORE: ESA WorldCover 10m → dominant_landcover per 1km grid cell.
Feeds the land-cover plausibility gate in fusion/score.py.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import rasterio
from rasterio.mask import mask
from shapely.geometry import mapping

logger = logging.getLogger(__name__)


def load_landcover(theater_id: str, conn, tiles: list[str]) -> int:
    """Compute dominant ESA WorldCover class per cell and write to cell_context.

    tiles: list of tile file paths (already downloaded via layer_sources.yaml URLs).
    Updates geo.cell_context.dominant_landcover and .landcover_label.
    Returns number of cells updated.
    """
    from geo.db import cell_ids_for_theater
    from grid.mgrs_1km import cell_id_to_polygon

    cell_ids = cell_ids_for_theater(conn, theater_id)
    logger.info("Computing landcover for %d cells", len(cell_ids))

    # ESA WorldCover class labels
    LC_LABELS = {
        10: "trees", 20: "shrubland", 30: "grassland", 40: "cropland",
        50: "built-up", 60: "bare", 70: "snow-ice", 80: "water",
        90: "wetland", 95: "mangroves", 100: "moss-lichen",
    }

    updated = 0
    # Open all tiles and query each cell
    datasets = [rasterio.open(t) for t in tiles if Path(t).exists()]
    if not datasets:
        logger.warning("No WorldCover tiles found — skipping landcover")
        return 0

    with conn.cursor() as cur:
        for cell_id in cell_ids:
            poly = cell_id_to_polygon(cell_id)
            dominant = _dominant_class(datasets, poly)
            if dominant is None:
                continue
            label = LC_LABELS.get(dominant, str(dominant))
            cur.execute(
                """
                INSERT INTO geo.cell_context (cell_id, theater_id, dominant_landcover, landcover_label, updated_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (cell_id) DO UPDATE SET
                    dominant_landcover = EXCLUDED.dominant_landcover,
                    landcover_label = EXCLUDED.landcover_label,
                    updated_at = now()
                """,
                (cell_id, theater_id, int(dominant), label),
            )
            updated += 1
        conn.commit()

    for ds in datasets:
        ds.close()

    logger.info("Updated landcover for %d cells", updated)
    return updated


def _dominant_class(datasets: list, poly) -> int | None:
    """Return the most frequent raster class within the polygon."""
    for ds in datasets:
        try:
            out_image, _ = mask(ds, [mapping(poly)], crop=True, nodata=0)
            data = out_image[0]
            valid = data[data > 0]
            if len(valid) == 0:
                continue
            values, counts = np.unique(valid, return_counts=True)
            return int(values[np.argmax(counts)])
        except Exception:
            continue
    return None
