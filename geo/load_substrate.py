"""
Operator CLI — the runnable Phase-0 gate.

Usage:
    python -m geo.load_substrate --theater ua_donbas --layers core
    python -m geo.load_substrate --theater ua_donbas --layers admin gazetteer
    python -m geo.load_substrate --theater ua_donbas --verify

Runs all CORE layers in sequence, then calls build_cell_context.
Phase-0 gate: every cell resolves to MGRS + label + seq AND carries cell_context.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import psycopg2
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

LAYER_SOURCES_CFG = Path(__file__).parent.parent / "config" / "layer_sources.yaml"
CACHE_DIR = Path(os.environ.get("GEO_CACHE_DIR", Path.home() / ".osint_cop" / "geo_cache"))

CORE_LAYERS = ["admin", "gazetteer", "landcover", "dem", "building", "transport", "hydro"]


def load_layer_sources() -> dict:
    with open(LAYER_SOURCES_CFG) as f:
        return yaml.safe_load(f)["layers"]


def run_layers(theater_id: str, layers: list[str], conn, bbox: tuple):
    sources = load_layer_sources()
    results = {}

    for layer in layers:
        logger.info("=== Loading layer: %s ===", layer)
        try:
            if layer == "admin":
                from geo.layers.admin import AdminLoader
                loader = AdminLoader(theater_id, conn)
                n = loader.load(bbox)

            elif layer == "gazetteer":
                from geo.layers.gazetteer import load_gazetteer
                n = load_gazetteer(theater_id, conn, bbox)

            elif layer == "landcover":
                from geo.layers.landcover import load_landcover
                tile_cfg = sources.get("landcover", {}).get("tiles", {})
                tile_names = tile_cfg.get(theater_id, [])
                # Tiles should already be downloaded to CACHE_DIR
                tile_paths = [str(CACHE_DIR / f"ESA_WorldCover_10m_2021_v200_{t}_Map.tif")
                              for t in tile_names]
                n = load_landcover(theater_id, conn, tile_paths)

            elif layer == "dem":
                # DEM tiles: enumerate cache for GLO-30 files
                dem_tiles = list(CACHE_DIR.glob("Copernicus_DSM_*.tif"))
                n = 0
                if dem_tiles:
                    from geo.layers.dem import load_dem
                    n = load_dem(theater_id, conn, [str(t) for t in dem_tiles])
                else:
                    logger.warning("No DEM tiles in cache — slope will be NULL. Download GLO-30 tiles first.")

            elif layer == "building":
                from geo.layers.building import load_buildings
                pbf = str(CACHE_DIR / "ukraine-latest.osm.pbf")
                n = load_buildings(theater_id, conn, pbf, bbox)

            elif layer == "transport":
                from geo.layers.transport import load_transport
                pbf = str(CACHE_DIR / "ukraine-latest.osm.pbf")
                n = load_transport(theater_id, conn, pbf, bbox)

            elif layer == "hydro":
                from geo.layers.hydro import load_hydro
                pbf = str(CACHE_DIR / "ukraine-latest.osm.pbf")
                n = load_hydro(theater_id, conn, pbf, bbox)

            else:
                logger.warning("Unknown layer '%s' — skipping", layer)
                n = 0

            results[layer] = {"status": "ok", "n": n}
            logger.info("Layer %s: %d features", layer, n)

        except Exception as e:
            logger.error("Layer %s FAILED: %s", layer, e, exc_info=True)
            results[layer] = {"status": "error", "error": str(e)}

    return results


def cmd_load(args):
    from geo.aoi import get_bbox
    from geo.context import build_cell_context, cell_context_coverage

    dsn = os.environ.get("DB_DSN", "postgresql://localhost:5432/osint_cop")
    conn = psycopg2.connect(dsn)

    bbox = get_bbox(args.theater)
    layers = CORE_LAYERS if "core" in args.layers else args.layers

    # First: build the grid if not already built
    if "admin" in layers or "core" in (args.layers or []):
        from grid.builder import build_grid
        from grid.admin_link import NullAdminResolver
        logger.info("Ensuring grid exists for %s", args.theater)
        n_cells = build_grid(args.theater, bbox, conn, admin_resolver=NullAdminResolver())
        logger.info("Grid: %d cells", n_cells)

    results = run_layers(args.theater, layers, conn, bbox)

    # Finalize cell_context (denormalize admin labels, ensure every cell has a row)
    logger.info("=== Materializing cell_context ===")
    n_ctx = build_cell_context(conn, args.theater)

    # Re-run admin linker now that admin features exist
    if "admin" in layers:
        logger.info("Re-linking admin to grid cells via PostGIS")
        _relink_admin(conn, args.theater)
        build_cell_context(conn, args.theater)

    coverage = cell_context_coverage(conn, args.theater)
    logger.info("cell_context coverage: %s", coverage)

    conn.close()

    # Summary
    errors = [l for l, r in results.items() if r["status"] == "error"]
    if errors:
        logger.error("LAYERS WITH ERRORS: %s", errors)
        sys.exit(1)

    print(f"\nPhase-0 substrate loaded for theater '{args.theater}'")
    print(f"  Cells with context: {coverage['total_cells']}")
    for field, stat in coverage.items():
        if isinstance(stat, dict):
            print(f"  {field}: {stat['n']} cells ({stat['pct']}%)")


def _relink_admin(conn, theater_id: str):
    """After admin polygons are in geo_feature, update grid_cell admin columns via PostGIS."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE geo.grid_cell gc
            SET
                admin_l1 = (gf.properties->>'admin_l1'),
                admin_l2 = (gf.properties->>'admin_l2'),
                admin_l3 = (gf.properties->>'admin_l3'),
                admin_path = (gf.properties->>'admin_path')
            FROM geo.geo_feature gf
            WHERE gf.layer = 'admin'
              AND gf.theater_id = %s
              AND gc.theater_id = %s
              AND ST_Contains(gf.geom, gc.centroid)
            """,
            (theater_id, theater_id)
        )
    conn.commit()


def cmd_verify(args):
    from geo.context import cell_context_coverage

    dsn = os.environ.get("DB_DSN", "postgresql://localhost:5432/osint_cop")
    conn = psycopg2.connect(dsn)

    failures = []
    coverage = cell_context_coverage(conn, args.theater)

    if coverage["total_cells"] == 0:
        failures.append(f"No cells for theater '{args.theater}' — run --layers core first")

    conn.close()

    if failures:
        for f in failures:
            print(f"FAIL: {f}", file=sys.stderr)
        sys.exit(1)

    print(f"PASS: Phase-0 gate satisfied for theater '{args.theater}'")
    print(f"  Total cells: {coverage['total_cells']}")


def main():
    parser = argparse.ArgumentParser(prog="python -m geo.load_substrate")
    sub = parser.add_subparsers(dest="cmd")

    p_load = parser.add_argument_group("load options")
    parser.add_argument("--theater", default="ua_donbas")
    parser.add_argument("--layers", nargs="+", default=["core"],
                        help="Layer names or 'core' for all CORE layers")
    parser.add_argument("--verify", action="store_true",
                        help="Just verify gate conditions, don't load")

    args = parser.parse_args()

    if args.verify:
        cmd_verify(args)
    else:
        cmd_load(args)


if __name__ == "__main__":
    main()
