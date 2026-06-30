"""
Human-readable place names for events — the "where is this?" the operator actually wants.

The read model stores only a 1km cell_id. This module maps a cell's (coarse, public) centroid to
the nearest known settlement so the UI can say "Avdiivka" instead of "37UCQ8049". It is a labelling
convenience, NOT a precise locator: the input is already the 1km centroid, the output is a town name
plus a rough distance. Authoritative admin geometry (region/district) comes later from geoBoundaries.

Dependency-light (json + math) so the coarsening boundary that calls it stays offline-testable.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Optional

_CONFIG = Path(__file__).resolve().parents[1] / "config"

# Distance bands for the label wording (km from the nearest settlement centroid).
_IN_TOWN_KM = 4.0      # within this → just the town name
_NEAR_KM = 18.0        # within this → "near <town>"; beyond → rural, name as a reference only


@lru_cache(maxsize=8)
def _places(theater_id: str) -> list[dict]:
    """The gazetteer for a theater (config/places_<theater>.json), or [] if there isn't one."""
    try:
        return json.loads((_CONFIG / f"places_{theater_id}.json").read_text(encoding="utf-8")).get("places", [])
    except (OSError, ValueError):
        return []


def _haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def nearest_place(lon: float, lat: float, theater_id: str = "ua_donbas") -> Optional[dict]:
    """Return {name, label, distance_km} for the nearest known settlement, or None if no gazetteer.

    `label` is the operator-facing string: the town name when on top of it, "near <town>" when
    close, or "rural area near <town>" when far (so a fire in farmland reads honestly, not as if
    it were in the town).
    """
    places = _places(theater_id)
    if not places:
        return None
    best, best_d = None, float("inf")
    for p in places:
        d = _haversine_km(lon, lat, p["lon"], p["lat"])
        if d < best_d:
            best, best_d = p, d
    if best is None:
        return None
    if best_d <= _IN_TOWN_KM:
        label = best["name"]
    elif best_d <= _NEAR_KM:
        label = f"near {best['name']}"
    else:
        label = f"rural area near {best['name']}"
    return {"name": best["name"], "label": label, "distance_km": round(best_d, 1)}
