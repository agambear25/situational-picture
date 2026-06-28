"""
CORE + MANDATORY: GeoNames UA place names + multilingual aliases.
Populates geo.place_alias for all languages (UA/RU/en/translit/hist).
Without this, BLOCK under-groups on name variants → recall drops → events missed.
"""
from __future__ import annotations

import csv
import io
import logging
import os
import zipfile
from pathlib import Path
from typing import Iterable

from geo.layers.base import CACHE_DIR
from geo.db import bulk_upsert_features

logger = logging.getLogger(__name__)

GEONAMES_UA_URL = "http://download.geonames.org/export/dump/UA.zip"
GEONAMES_ALTNAMES_URL = "http://download.geonames.org/export/dump/alternateNamesV2.zip"

# GeoNames feature class filter — keep populated places and hydrography
KEEP_CLASSES = {"P", "H", "L", "S", "T"}


def load_gazetteer(theater_id: str, conn, bbox: tuple[float, float, float, float]) -> int:
    """Download GeoNames UA + alternateNamesV2, clip to bbox, write place_alias rows.

    Returns number of aliases inserted.
    """
    west, south, east, north = bbox

    # 1. Download and parse UA.txt
    ua_path = _fetch(GEONAMES_UA_URL, "geonames_ua.zip")
    places = _parse_geonames_ua(ua_path, west, south, east, north)
    logger.info("Parsed %d GeoNames places in bbox", len(places))

    # 2. Download and parse alternateNamesV2
    alt_path = _fetch(GEONAMES_ALTNAMES_URL, "geonames_altnames.zip")
    alt_names = _parse_alternate_names(alt_path, {p["geoname_id"] for p in places})
    logger.info("Parsed %d alternate name records", len(alt_names))

    # 3. Write to place_alias
    total = 0
    with conn.cursor() as cur:
        for place in places:
            gid = place["geoname_id"]
            # snap to cell (if in AOI)
            cell_id = _snap_to_cell(conn, place["lon"], place["lat"])

            # preferred name (English or native)
            _upsert_alias(cur, theater_id, gid, place["name"], "en",
                         is_preferred=True, cell_id=cell_id,
                         lon=place["lon"], lat=place["lat"])
            total += 1

            # Alternate names
            for alt in alt_names.get(gid, []):
                lang = alt["isolanguage"] or "xx"
                _upsert_alias(cur, theater_id, gid, alt["alternate_name"], lang,
                             is_preferred=False, cell_id=cell_id,
                             lon=place["lon"], lat=place["lat"])
                total += 1

        conn.commit()

    logger.info("Inserted %d place_alias rows for theater %s", total, theater_id)
    return total


def _upsert_alias(cur, theater_id, place_id, name, lang, is_preferred, cell_id, lon, lat):
    if not name or not name.strip():
        return
    cur.execute(
        """
        INSERT INTO geo.place_alias
            (place_id, theater_id, name, lang, is_preferred, cell_id, geom)
        VALUES
            (%s, %s, %s, %s, %s, %s,
             ST_SetSRID(ST_MakePoint(%s, %s), 4326))
        ON CONFLICT DO NOTHING
        """,
        (place_id, theater_id, name.strip(), lang, is_preferred, cell_id, lon, lat),
    )


def _snap_to_cell(conn, lon: float, lat: float) -> str | None:
    from grid.mgrs_1km import to_cell_id
    try:
        cell_id = to_cell_id(lon, lat)
        with conn.cursor() as cur:
            cur.execute("SELECT cell_id FROM geo.grid_cell WHERE cell_id = %s", (cell_id,))
            row = cur.fetchone()
            return row[0] if row else None
    except Exception:
        return None


def _fetch(url: str, local_name: str) -> Path:
    import urllib.request
    path = CACHE_DIR / local_name
    if path.exists():
        logger.info("Cache hit: %s", path)
        return path
    logger.info("Downloading %s", url)
    urllib.request.urlretrieve(url, path)
    return path


def _parse_geonames_ua(zip_path: Path, west, south, east, north) -> list[dict]:
    places = []
    with zipfile.ZipFile(zip_path) as z:
        name = next(n for n in z.namelist() if n.endswith(".txt") and not n.startswith("readme"))
        with z.open(name) as f:
            reader = csv.reader(io.TextIOWrapper(f, encoding="utf-8"), delimiter="\t")
            for row in reader:
                if len(row) < 19:
                    continue
                feature_class = row[6]
                if feature_class not in KEEP_CLASSES:
                    continue
                try:
                    lat, lon = float(row[4]), float(row[5])
                except ValueError:
                    continue
                if not (west <= lon <= east and south <= lat <= north):
                    continue
                places.append({
                    "geoname_id": int(row[0]),
                    "name": row[1],
                    "lat": lat,
                    "lon": lon,
                    "feature_class": feature_class,
                    "feature_code": row[7],
                    "country": row[8],
                    "population": int(row[14]) if row[14] else 0,
                })
    return places


def _parse_alternate_names(zip_path: Path, geoname_ids: set) -> dict[int, list[dict]]:
    result: dict[int, list[dict]] = {}
    wanted_langs = {"uk", "ru", "en", "translit", ""}  # empty = unclassified
    with zipfile.ZipFile(zip_path) as z:
        name = next(n for n in z.namelist() if n.endswith(".txt") and not n.startswith("readme"))
        with z.open(name) as f:
            reader = csv.reader(io.TextIOWrapper(f, encoding="utf-8"), delimiter="\t")
            for row in reader:
                if len(row) < 4:
                    continue
                try:
                    gid = int(row[1])
                except ValueError:
                    continue
                if gid not in geoname_ids:
                    continue
                lang = row[2]
                if lang not in wanted_langs and not lang.startswith("uk") and not lang.startswith("ru"):
                    continue
                result.setdefault(gid, []).append({
                    "geoname_id": gid,
                    "isolanguage": lang,
                    "alternate_name": row[3],
                    "is_preferred_name": row[4] == "1" if len(row) > 4 else False,
                })
    return result
