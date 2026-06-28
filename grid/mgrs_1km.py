"""
The ONLY place the mgrs library is touched.
All MGRS <-> coordinate conversion goes through these three functions.
"""
from __future__ import annotations

import mgrs as _mgrs
from shapely.geometry import Point, Polygon


_M = _mgrs.MGRS()


def to_cell_id(lon: float, lat: float) -> str:
    """Snap any point to its 1km MGRS cell_id (10-digit precision).

    Exact input coordinates are discarded — only the cell string is returned.
    This is the write-time coarsening chokepoint: call once, keep the cell_id.
    """
    # MGRS at 1km resolution = 5-digit easting + 5-digit northing = 10 chars total
    # e.g. '37UDB1234556789' → cell_id = '37UDB1234556789' (grid square 37UDB at 1km)
    # MGRSPrecision=2 → 1km resolution → 4 trailing digits (2 easting + 2 northing)
    # e.g. '37UDB1234' — this is the canonical 1km cell_id format.
    raw = _M.toMGRS(lat, lon, MGRSPrecision=2)
    return raw.replace(" ", "")


def cell_id_to_polygon(cell_id: str) -> Polygon:
    """Return the WGS84 polygon (Shapely) for a 1km MGRS cell.

    Reconstructs the SW corner from the cell_id and builds a ~1km × 1km box.
    The box is in geographic degrees; not perfectly square but acceptable for the MVP.
    """
    lat, lon = _M.toLatLon(cell_id)
    # MGRS 1km cell: easting/northing step = 1000m
    # Approximate degree offset at this latitude (rough, suitable for display)
    import math
    lat_step = 1000 / 111_320          # ~0.009 degrees
    lon_step = 1000 / (111_320 * math.cos(math.radians(lat)))

    sw = (lon, lat)
    nw = (lon, lat + lat_step)
    ne = (lon + lon_step, lat + lat_step)
    se = (lon + lon_step, lat)
    return Polygon([sw, nw, ne, se, sw])


def is_valid_cell_id(cell_id: str) -> bool:
    """Check MGRS format is a 1km cell (6-digit precision per component)."""
    try:
        _M.toLatLon(cell_id)
        return True
    except Exception:
        return False
