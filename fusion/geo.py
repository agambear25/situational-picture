"""
Pure geo/time helpers for scoring. No DB, no precise input coords — distance is
computed between 1km cell centroids (derived from the cell_id), honoring coarsening.
"""
from __future__ import annotations

import math
from functools import lru_cache


@lru_cache(maxsize=100_000)
def cell_centroid(cell_id: str) -> tuple[float, float]:
    """(lon, lat) of a 1km cell centroid, derived from the cell_id (no stored coord)."""
    from grid.mgrs_1km import cell_id_to_polygon
    c = cell_id_to_polygon(cell_id).centroid
    return (c.x, c.y)


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle distance in meters."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def cell_distance_m(cell_a: str, cell_b: str) -> float:
    if cell_a == cell_b:
        return 0.0
    lon1, lat1 = cell_centroid(cell_a)
    lon2, lat2 = cell_centroid(cell_b)
    return haversine_m(lon1, lat1, lon2, lat2)


def temporal_overlap_ratio(a_start, a_end, b_start, b_end) -> float:
    """Jaccard-style overlap of two time intervals in [0,1]; 0 if disjoint."""
    lo = max(a_start, b_start)
    hi = min(a_end, b_end)
    inter = max(0.0, (hi - lo).total_seconds())
    if inter <= 0:
        return 0.0
    union = (max(a_end, b_end) - min(a_start, b_start)).total_seconds()
    return inter / union if union > 0 else 1.0


def temporal_gap_s(a_start, a_end, b_start, b_end) -> float:
    """Seconds between intervals; 0 if they overlap."""
    if a_end >= b_start and b_end >= a_start:
        return 0.0
    if a_end < b_start:
        return (b_start - a_end).total_seconds()
    return (a_start - b_end).total_seconds()
