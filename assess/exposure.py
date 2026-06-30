"""
Exposure scoring (Phase 4c) — "who / what is at risk".

An event's exposure ≈ severity × how close it is to a populated place. Proximity to a gazetteer
settlement is the $0 proxy until cell_context.builtup_pct / population is populated; the major
Donbas cities (the highest-value exposure targets) are exactly what the gazetteer holds. Pure +
deterministic: settlements and the event centroid are injected, so it is unit-tested directly.
"""
from __future__ import annotations

import math


def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def nearest_settlement(lon: float, lat: float, settlements: list[dict]):
    best, best_d = None, float("inf")
    for s in settlements:
        d = haversine_km(lon, lat, s["lon"], s["lat"])
        if d < best_d:
            best, best_d = s, d
    return (best["name"] if best else None), best_d


def exposure(event: dict, settlements: list[dict], cfg) -> dict | None:
    """Score one event's exposure. `event` needs lon/lat (cell centroid) and event_type."""
    lon, lat = event.get("lon"), event.get("lat")
    if lon is None or lat is None or not settlements:
        return None
    name, km = nearest_settlement(lon, lat, settlements)
    if name is None or km > cfg.exposure_radius_km:
        return None
    proximity = 1.0 - km / cfg.exposure_radius_km        # 1 at the settlement, 0 at the radius
    sev = cfg.severity(event["event_type"])
    score = round(sev * proximity, 4)
    if score < cfg.exposure_min_score:
        return None
    etype = str(event["event_type"]).replace("_", " ")
    where = "in" if km < 2.0 else f"{round(km, 1)} km from"
    return {
        "score": score, "settlement": name, "distance_km": round(km, 1),
        "rationale": f"{etype} {where} {name} — populated area exposed.",
    }
