"""
Load the Tier-1 reference feature library (geo.geo_feature) from an OSM extract.

Reads a Geofabrik .osm.pbf (via pyrosm), keeps water / roads / forests / built-up / rail within the
theater AOI, classifies OSM tags → (kind, subkind), and inserts each feature as a geo_feature row
(layer = kind, properties = {subkind, name}, geom, source = 'osm'). These render as toggleable map
layers and are the raw material the analyst promotes into areas of interest.

The tag→kind classification is a PURE function (unit-tested offline); the pbf read + PostGIS insert
is the I/O shell. Mirrors geo/admin_load.py. Ridgelines (DEM) are a separate, later pipeline.

    bash scripts/fetch_features.sh            # downloads the Ukraine extract
    python -m geo.feature_load --theater ua_donbas --pbf data/ground_truth/osm/ukraine-latest.osm.pbf
"""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# OSM keys we read; the pyrosm custom filter keeps only features carrying one of these values.
_KEEP = {
    "waterway": ["river", "canal", "stream"],
    "natural": ["water", "wood"],
    "landuse": ["forest", "residential", "industrial"],
    "highway": ["motorway", "trunk", "primary"],
    "railway": ["rail"],
}


def classify_feature(tags: dict) -> tuple[str, str] | None:
    """OSM tags → (kind, subkind), or None to skip. Pure + deterministic — the unit-tested core."""
    waterway = tags.get("waterway")
    natural = tags.get("natural")
    landuse = tags.get("landuse")
    highway = tags.get("highway")
    railway = tags.get("railway")
    if waterway in ("river", "canal", "stream"):
        return ("water", waterway)
    if natural == "water":
        return ("water", "waterbody")
    if natural == "wood" or landuse == "forest":
        return ("forest", landuse or natural)
    if highway in ("motorway", "trunk", "primary"):
        return ("road", highway)
    if railway == "rail":
        return ("rail", "rail")
    if landuse in ("residential", "industrial"):
        return ("builtup", landuse)
    return None


def _rows_from_gdf(gdf, theater_id: str):
    """Yield (kind, subkind, name, wkt, rep_lon, rep_lat) for each classifiable feature."""
    cols = set(gdf.columns)
    tag_keys = [k for k in _KEEP if k in cols]
    for _, r in gdf.iterrows():
        geom = r.get("geometry")
        if geom is None or geom.is_empty:
            continue
        cls = classify_feature({k: r.get(k) for k in tag_keys})
        if cls is None:
            continue
        rep = geom.representative_point()
        yield cls[0], cls[1], (r.get("name") if "name" in cols else None), geom.wkt, rep.x, rep.y


def load_features(theater_id: str, bbox, pbf_path: Path, conn) -> dict:
    from pyrosm import OSM
    from psycopg2.extras import execute_values
    from grid.mgrs_1km import to_cell_id

    osm = OSM(str(pbf_path), bounding_box=list(bbox))
    gdf = osm.get_data_by_custom_criteria(
        custom_filter=_KEEP, filter_type="keep",
        keep_nodes=False, keep_ways=True, keep_relations=True)
    if gdf is None or len(gdf) == 0:
        return {"features": 0, "by_kind": {}}

    rows, by_kind = [], {}
    import json
    for kind, subkind, name, wkt, lon, lat in _rows_from_gdf(gdf, theater_id):
        rows.append((theater_id, kind, to_cell_id(lon, lat), wkt,
                     json.dumps({"subkind": subkind, "name": name})))
        by_kind[kind] = by_kind.get(kind, 0) + 1

    with conn.cursor() as cur:
        cur.execute("DELETE FROM geo.geo_feature WHERE theater_id = %s AND source = 'osm'", (theater_id,))
        execute_values(
            cur,
            """INSERT INTO geo.geo_feature (theater_id, layer, cell_id, geom, properties, source, as_of)
               VALUES %s""",
            rows,
            template="(%s, %s, %s, ST_GeomFromText(%s, 4326), %s::jsonb, 'osm', now()::date)",
            page_size=500,
        )
    conn.commit()
    logger.info("loaded %d features %s", len(rows), by_kind)
    return {"features": len(rows), "by_kind": by_kind}


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    import psycopg2
    import yaml
    p = argparse.ArgumentParser(prog="python -m geo.feature_load")
    p.add_argument("--theater", default="ua_donbas")
    p.add_argument("--pbf", default="data/ground_truth/osm/ukraine-latest.osm.pbf")
    args = p.parse_args()
    bbox = yaml.safe_load(open("config/theaters.yaml", encoding="utf-8"))["theaters"][args.theater]["bbox"]
    conn = psycopg2.connect(os.environ.get("DB_DSN", "postgresql://localhost:5432/osint_cop"))
    try:
        s = load_features(args.theater, bbox, Path(args.pbf), conn)
    finally:
        conn.close()
    print("=" * 56)
    print(f"FEATURE LIBRARY — {args.theater}")
    print(f"  features loaded : {s['features']}")
    for k, n in sorted(s["by_kind"].items(), key=lambda kv: -kv[1]):
        print(f"    {k:<10} {n}")
    print("=" * 56)


if __name__ == "__main__":
    main()
