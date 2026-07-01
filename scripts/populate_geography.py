"""One-time populate of the land-cover + road substrate for both theaters (Task A3).
Reads WorldCover tiles + the Geofabrik PBF from data/ground_truth/, writes geo.cell_context.
Run: set -a && source .env && set +a && .venv/bin/python scripts/populate_geography.py
"""
import os
import sys
import time

import psycopg2
import yaml

from geo.layers.landcover import load_landcover
from geo.layers.transport import load_transport

WC = "data/ground_truth/worldcover/ESA_WorldCover_10m_2021_v200_%s_Map.tif"
PBF = "data/ground_truth/osm/ukraine-latest.osm.pbf"
BBOX = {"ua_donbas": (36.0, 46.8, 39.5, 49.5), "black_sea": (32.0, 44.0, 37.0, 46.3)}


def main():
    src = yaml.safe_load(open("config/layer_sources.yaml"))["layers"]
    theaters = sys.argv[1:] or ["ua_donbas", "black_sea"]
    conn = psycopg2.connect(os.environ["DB_DSN"])
    for t in theaters:
        tiles = [WC % name for name in src["landcover"]["tiles"][t]]
        t0 = time.time()
        n_lc = load_landcover(t, conn, tiles)
        print(f"[{t}] landcover: {n_lc} cells in {time.time()-t0:.0f}s", flush=True)
        t1 = time.time()
        n_rd = load_transport(t, conn, PBF, BBOX[t])
        print(f"[{t}] transport: {n_rd} cells in {time.time()-t1:.0f}s", flush=True)
    conn.close()
    print("populate done", flush=True)


if __name__ == "__main__":
    main()
