"""
Live validation of the Phase-5b SAR vessel detector on real Sentinel-1 (GEE as a data tap).

Pulls one Sentinel-1 σ0 image over a busy strait, masks land to a low constant (ESA WorldCover —
keep permanent water, class 80), and runs the LOCAL detector. Ships should appear as a scatter of
bright-target detections along the shipping lanes. Default target = the Strait of Hormuz.

    python -m eval.validate_vessels                          # Hormuz, defaults
    python -m eval.validate_vessels --bbox 56.2 26.4 56.9 26.9 --start 2024-05-01 --end 2024-05-13
"""
from __future__ import annotations

import argparse
import io
import os
import urllib.request
from datetime import datetime, timezone

import numpy as np

from ingest.imagery.framework import Tile
from ingest.imagery.sar_vessel import SarVesselDetector


def scene(ee, bbox, start, end, scale_m, gid="S1") -> Tile:
    w, s, e, n = bbox
    aoi = ee.Geometry.BBox(w, s, e, n)
    col = (ee.ImageCollection("COPERNICUS/S1_GRD")
           .filterBounds(aoi).filterDate(start, end)
           .filter(ee.Filter.eq("instrumentMode", "IW"))
           .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
           .select(["VV"]))
    n_sc = col.size().getInfo()
    if n_sc == 0:
        raise RuntimeError(f"no Sentinel-1 scenes for {start}..{end} over {bbox}")
    img = col.mosaic()                                   # one pass over the strait
    water = ee.ImageCollection("ESA/WorldCover/v200").first().select("Map").eq(80)
    img = img.updateMask(water).unmask(-30).clip(aoi)    # land → -30 (dark, ignored); sea → σ0
    url = img.getDownloadURL({"format": "NPY", "region": aoi, "scale": scale_m, "bands": [{"id": "VV"}]})
    raw = urllib.request.urlopen(url).read()
    arr = np.load(io.BytesIO(raw))
    arr = (np.stack([arr[m].astype(np.float32) for m in arr.dtype.names])
           if arr.dtype.names else arr.astype(np.float32))
    t = datetime.fromisoformat(start + "T00:00:00+00:00").astimezone(timezone.utc)
    buf = io.BytesIO(); np.save(buf, arr)
    return Tile(granule_id=gid, acq_start=t, acq_end=t, data=buf.getvalue(), bbox=tuple(bbox),
                meta={"bands": ["VV"], "scale_m": scale_m, "n_scenes": n_sc})


def main():
    p = argparse.ArgumentParser(prog="python -m eval.validate_vessels")
    p.add_argument("--bbox", nargs=4, type=float, default=[56.2, 26.4, 56.9, 26.9],
                   metavar=("W", "S", "E", "N"))
    p.add_argument("--start", default="2024-05-01")
    p.add_argument("--end", default="2024-05-13")
    p.add_argument("--scale", type=int, default=40)
    p.add_argument("--k", type=float, default=4.0)
    p.add_argument("--project", default=os.environ.get("GEE_PROJECT"))
    args = p.parse_args()

    import ee
    ee.Initialize(project=args.project or None)
    print(f"Sentinel-1 over {tuple(args.bbox)} @ {args.scale} m, {args.start}..{args.end} …")
    tile = scene(ee, args.bbox, args.start, args.end, args.scale)
    det = SarVesselDetector(k_sigma=args.k, theater_id="hormuz")
    ships = det.infer([tile])
    print("=" * 56)
    print(f"  Sentinel-1 scenes mosaicked : {tile.meta['n_scenes']}")
    print(f"  vessel detections           : {len(ships)}")
    for o in sorted(ships, key=lambda o: -o.meta["peak_db_over_thr"])[:8]:
        print(f"    {o.meta['peak_db_over_thr']:5.1f} dB  {o.meta['n_pixels']:>3} px  @ {o.geo.lat:.3f},{o.geo.lon:.3f}")
    print("=" * 56)


if __name__ == "__main__":
    main()
