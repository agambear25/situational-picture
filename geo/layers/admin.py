"""
CORE: Admin boundary loader (hromada level, geoBoundaries UKR ADM3).
Populates geo.geo_feature(layer='admin') which grid/ uses for admin_link.
"""
from __future__ import annotations

import json
import logging
import urllib.request
import zipfile
from pathlib import Path
from typing import Iterable

from geo.layers.base import LayerLoader, CACHE_DIR

logger = logging.getLogger(__name__)

GEOBOUNDARIES_UA_ADM3 = (
    "https://github.com/wmgeolab/geoBoundaries/raw/main/releaseData/gbOpen/UKR/ADM3/"
    "geoBoundaries-UKR-ADM3.geojson"
)


class AdminLoader(LayerLoader):
    layer_name = "admin"

    def _get_url(self, bbox: tuple) -> str:
        return GEOBOUNDARIES_UA_ADM3

    def _normalize(self, raw_path: Path, bbox: tuple) -> Iterable[dict]:
        west, south, east, north = bbox
        with open(raw_path) as f:
            fc = json.load(f)

        for feat in fc.get("features", []):
            geom = feat.get("geometry")
            if geom is None:
                continue
            props = feat.get("properties", {})
            # geoBoundaries field names
            yield {
                "geom_wkt": _geom_to_wkt(geom),
                "properties": {
                    "admin_l1": props.get("shapeName_1") or props.get("shapeISO"),
                    "admin_l2": props.get("shapeName_2"),
                    "admin_l3": props.get("shapeName") or props.get("shapeName_3"),
                    "admin_path": "/".join(filter(None, [
                        props.get("shapeISO"),
                        props.get("shapeName_1"),
                        props.get("shapeName_2"),
                        props.get("shapeName"),
                    ])),
                    "admin_level": "3",
                    "shape_id": props.get("shapeID"),
                },
            }


def _geom_to_wkt(geom: dict) -> str:
    """Minimal GeoJSON geometry → WKT converter (handles Polygon + MultiPolygon)."""
    gtype = geom["type"]
    coords = geom["coordinates"]

    if gtype == "Polygon":
        return "POLYGON(" + _rings_to_wkt(coords) + ")"
    elif gtype == "MultiPolygon":
        parts = ",".join("(" + _rings_to_wkt(ring) + ")" for ring in coords)
        return f"MULTIPOLYGON({parts})"
    else:
        raise ValueError(f"Unsupported geometry type: {gtype}")


def _rings_to_wkt(rings: list) -> str:
    return ",".join(
        "(" + ",".join(f"{c[0]} {c[1]}" for c in ring) + ")"
        for ring in rings
    )
