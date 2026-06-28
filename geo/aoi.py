"""
Theater AOI → clip geometry and tile lists for raster downloads.
Reads from config/theaters.yaml.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

import yaml
from shapely.geometry import box


def load_theaters() -> dict:
    cfg = Path(__file__).parent.parent / "config" / "theaters.yaml"
    with open(cfg) as f:
        return yaml.safe_load(f)["theaters"]


def get_bbox(theater_id: str) -> tuple[float, float, float, float]:
    """Return (west, south, east, north) for the theater AOI."""
    theaters = load_theaters()
    if theater_id not in theaters:
        raise ValueError(f"Unknown theater: {theater_id!r}")
    b = theaters[theater_id]["bbox"]
    return float(b[0]), float(b[1]), float(b[2]), float(b[3])


def get_clip_geom(theater_id: str):
    """Return Shapely box for the theater AOI."""
    west, south, east, north = get_bbox(theater_id)
    return box(west, south, east, north)


def get_worldcover_tiles(theater_id: str) -> list[str]:
    """Return ESA WorldCover tile IDs covering the AOI."""
    cfg = Path(__file__).parent.parent / "config" / "layer_sources.yaml"
    with open(cfg) as f:
        sources = yaml.safe_load(f)["layers"]
    tiles = sources.get("landcover", {}).get("tiles", {})
    return tiles.get(theater_id, [])


def get_aoi_buffer(theater_id: str) -> float:
    """Return AOI buffer in km (used for tile download margin)."""
    theaters = load_theaters()
    return float(theaters.get(theater_id, {}).get("aoi_buffer_km", 20))
