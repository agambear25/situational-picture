"""
Load geoBoundaries admin units (oblast/raion/hromada) clipped to a theater AOI → geo.admin_unit,
then populate geo.cell_context.admin_l1/l2/l3 (+ ids) by point-in-polygon over the 1km grid.

This finally fills the admin substrate the schema reserved since Phase 0 (cell_context.admin_l*
were always NULL). It is the foundation of the region→district→community rollup/drill-down.

    bash scripts/fetch_admin.sh                       # downloads UKR ADM1/2/3 GeoJSON
    python -m geo.admin_load --theater ua_donbas --dir data/ground_truth/admin

geoBoundaries gbOpen fields: shapeID (stable id), shapeName, geometry. Parent links aren't in the
files, so we derive them by representative-point containment (hromada→raion→oblast).
"""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

LEVEL_FILE = {1: "UKR_ADM1.geojson", 2: "UKR_ADM2.geojson", 3: "UKR_ADM3.geojson"}


def _to_multipolygon_wkt(geom) -> str:
    from shapely.geometry import MultiPolygon
    if geom.geom_type == "Polygon":
        geom = MultiPolygon([geom])
    return geom.wkt


def load(theater_id: str, bbox, data_dir: Path, conn) -> dict:
    import geopandas as gpd
    from shapely import make_valid
    from shapely.geometry import box

    aoi = box(bbox[0], bbox[1], bbox[2], bbox[3])
    frames, loaded_ids = {}, {1: set(), 2: set(), 3: set()}
    for lvl, fname in LEVEL_FILE.items():
        gdf = gpd.read_file(data_dir / fname)
        gdf = gdf[gdf.geometry.notna()].copy()
        gdf["geometry"] = gdf.geometry.apply(make_valid)
        gdf = gdf[gdf.geometry.intersects(aoi)].copy()        # clip to the AOI
        gdf["rep"] = gdf.geometry.representative_point()
        frames[lvl] = gdf
        loaded_ids[lvl] = set(gdf["shapeID"])
        logger.info("admin L%d: %d units intersect AOI", lvl, len(gdf))

    def parents(child, parent_lvl):
        """Map child shapeID → containing parent shapeID (None if its parent isn't in the AOI set)."""
        import geopandas as gpd
        pts = gpd.GeoDataFrame(child[["shapeID"]], geometry=child["rep"], crs=child.crs)
        par = frames[parent_lvl][["shapeID", "geometry"]].rename(columns={"shapeID": "pid"})
        j = gpd.sjoin(pts, par, predicate="within", how="left")
        j = j[~j.index.duplicated(keep="first")]
        return {sid: (pid if pid in loaded_ids[parent_lvl] else None)
                for sid, pid in zip(j["shapeID"], j["pid"])}

    p2 = parents(frames[2], 1) if 2 in frames else {}
    p3 = parents(frames[3], 2) if 3 in frames else {}

    cur = conn.cursor()
    cur.execute("DELETE FROM geo.admin_unit WHERE theater_id = %s", (theater_id,))
    n = 0
    for lvl in (1, 2, 3):                                       # insert top-down so parent FK resolves
        for _, r in frames[lvl].iterrows():
            parent = {1: None, 2: p2.get(r["shapeID"]), 3: p3.get(r["shapeID"])}[lvl]
            cur.execute(
                """INSERT INTO geo.admin_unit (admin_id, theater_id, level, name, parent_id, geom)
                   VALUES (%s, %s, %s, %s, %s, ST_Multi(ST_GeomFromText(%s, 4326)))
                   ON CONFLICT (admin_id) DO NOTHING""",
                (r["shapeID"], theater_id, lvl, str(r.get("shapeName") or "(unnamed)"),
                 parent, _to_multipolygon_wkt(r.geometry)),
            )
            n += 1
    conn.commit()

    # Stamp every grid cell with the admin units that contain its centroid.
    cur.execute(
        """
        INSERT INTO geo.cell_context
            (cell_id, theater_id, admin_l1, admin_l1_id, admin_l2, admin_l2_id, admin_l3, admin_l3_id)
        SELECT c.cell_id, c.theater_id,
               a1.name, a1.admin_id, a2.name, a2.admin_id, a3.name, a3.admin_id
        FROM geo.grid_cell c
        LEFT JOIN geo.admin_unit a1 ON a1.theater_id=c.theater_id AND a1.level=1 AND ST_Contains(a1.geom, c.centroid)
        LEFT JOIN geo.admin_unit a2 ON a2.theater_id=c.theater_id AND a2.level=2 AND ST_Contains(a2.geom, c.centroid)
        LEFT JOIN geo.admin_unit a3 ON a3.theater_id=c.theater_id AND a3.level=3 AND ST_Contains(a3.geom, c.centroid)
        WHERE c.theater_id = %s
        ON CONFLICT (cell_id) DO UPDATE SET
            admin_l1=EXCLUDED.admin_l1, admin_l1_id=EXCLUDED.admin_l1_id,
            admin_l2=EXCLUDED.admin_l2, admin_l2_id=EXCLUDED.admin_l2_id,
            admin_l3=EXCLUDED.admin_l3, admin_l3_id=EXCLUDED.admin_l3_id
        """,
        (theater_id,),
    )
    conn.commit()
    cur.execute("SELECT count(*) FROM geo.cell_context WHERE theater_id=%s AND admin_l1_id IS NOT NULL",
                (theater_id,))
    cells_stamped = cur.fetchone()[0]
    return {"admin_units": n, "cells_stamped": cells_stamped}


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    import psycopg2
    import yaml
    p = argparse.ArgumentParser(prog="python -m geo.admin_load")
    p.add_argument("--theater", default="ua_donbas")
    p.add_argument("--dir", default="data/ground_truth/admin")
    args = p.parse_args()

    theaters = yaml.safe_load(open("config/theaters.yaml", encoding="utf-8"))["theaters"]
    bbox = theaters[args.theater]["bbox"]
    conn = psycopg2.connect(os.environ.get("DB_DSN", "postgresql://localhost:5432/osint_cop"))
    try:
        s = load(args.theater, bbox, Path(args.dir), conn)
    finally:
        conn.close()
    print("=" * 56)
    print(f"ADMIN SUBSTRATE — {args.theater}")
    print(f"  admin units loaded : {s['admin_units']}")
    print(f"  grid cells stamped : {s['cells_stamped']}")
    print("=" * 56)


if __name__ == "__main__":
    main()
