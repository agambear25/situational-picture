"""Compose a short, cell-level geography phrase for an event from substrate columns. Pure —
descriptive context ("where"), never evidence. Cell-level only; no precise coords involved."""
from __future__ import annotations

_LABEL = {"trees": "woodland", "cropland": "cropland", "grassland": "grassland",
          "built-up": "a built-up area", "water": "water", "wetland": "wetland",
          "shrubland": "shrubland", "bare": "bare ground"}


def geo_phrase(landcover_label, road_class, road_surface):
    parts = []
    if landcover_label:
        parts.append("on " + _LABEL.get(landcover_label, landcover_label))
    if road_class:
        if road_surface == "unpaved":
            parts.append("along an unpaved track" if road_class in ("track", "path")
                         else f"along an unpaved {road_class} road")
        elif road_class in ("motorway", "trunk", "primary", "secondary"):
            parts.append(f"near a {road_class} road")
    return " · ".join(parts) if parts else None
