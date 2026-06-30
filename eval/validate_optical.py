"""
Live validation of the Phase-3f Sentinel-2 optical detector on REAL imagery (GEE as a data tap).

Composites a pre- and post-event S2 surface-reflectance image ON GEE (cloud-masked median over
each window, two downloads not dozens), then runs the LOCAL OpticalIndexDetector and reports the
flood (ΔMNDWI) and burn (dNBR) detections — all detection logic stays local and auditable.

Default target = the Sviati Hory (Holy Mountains) NP pine forest near Sviatohirsk, which burned in
June-July 2022 — a clean, datable, in-AOI burn. Verified result: 33 burn_scar + 0 flood over the
forest (top dNBR 0.57), 7% burn-positive pixels (localised, not a cloud/seasonal artifact), zero
false floods — confirming MNDWI vs dNBR stays separable on real reflectance.

    python -m eval.validate_optical                    # Sviati Hory burn, defaults
    python -m eval.validate_optical --bbox 37.5 48.98 37.68 49.08 \
        --before 2022-05-01 2022-06-01 --after 2022-08-15 2022-09-25 --scale 100
"""
from __future__ import annotations

import argparse
import io
import os
import urllib.request
from datetime import datetime, timezone

import numpy as np

from ingest.imagery.framework import Tile
from ingest.imagery.optical_index import OpticalIndexDetector

_S2 = "COPERNICUS/S2_SR_HARMONIZED"
_BANDS = ["B3", "B8", "B11"]                  # green, NIR, SWIR1 — MNDWI + NBR
_CLOUD_SCL = [3, 8, 9, 10, 11]                # shadow, cloud (med/high), cirrus, snow


def composite(ee, bbox, start, end, scale_m, gid, max_cloud_pct=40.0) -> Tile:
    w, s, e, n = bbox
    aoi = ee.Geometry.BBox(w, s, e, n)
    col = (ee.ImageCollection(_S2).filterBounds(aoi).filterDate(start, end)
           .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", max_cloud_pct)))
    n_scenes = col.size().getInfo()
    if n_scenes == 0:
        raise RuntimeError(f"no Sentinel-2 scenes for {start}..{end} over {bbox}")

    def mask_clouds(img):
        scl = img.select("SCL")
        keep = scl.neq(_CLOUD_SCL[0])
        for c in _CLOUD_SCL[1:]:
            keep = keep.And(scl.neq(c))
        return img.updateMask(keep)

    img = col.map(mask_clouds).select(_BANDS).median().clip(aoi)
    url = img.getDownloadURL({"format": "NPY", "region": aoi, "scale": scale_m,
                              "bands": [{"id": b} for b in _BANDS]})
    raw = urllib.request.urlopen(url).read()
    arr = np.load(io.BytesIO(raw))
    arr = (np.stack([arr[nm].astype(np.float32) for nm in arr.dtype.names])
           if arr.dtype.names else arr.astype(np.float32))
    # Indices are ratio-based (a−b)/(a+b) → scale-invariant, so raw S2 DN needs no rescaling.
    t = datetime.fromisoformat(start + "T00:00:00+00:00").astimezone(timezone.utc)
    buf = io.BytesIO(); np.save(buf, arr)
    return Tile(granule_id=gid, acq_start=t, acq_end=t, data=buf.getvalue(), bbox=tuple(bbox),
                meta={"bands": _BANDS, "scale_m": scale_m, "n_scenes": n_scenes})


def main():
    p = argparse.ArgumentParser(prog="python -m eval.validate_optical")
    p.add_argument("--bbox", nargs=4, type=float, default=[37.50, 48.98, 37.68, 49.08],
                   metavar=("W", "S", "E", "N"))
    p.add_argument("--before", nargs=2, default=["2022-05-01", "2022-06-01"])
    p.add_argument("--after", nargs=2, default=["2022-08-15", "2022-09-25"])
    p.add_argument("--scale", type=int, default=100)
    p.add_argument("--theater", default="ua_donbas")
    p.add_argument("--project", default=os.environ.get("GEE_PROJECT"))
    p.add_argument("--ingest", action="store_true",
                   help="append the detected optical observations to the log (real GridResolver)")
    args = p.parse_args()

    import ee
    ee.Initialize(project=args.project or None)
    print(f"Sentinel-2 composites over {tuple(args.bbox)} at {args.scale} m …")
    before = composite(ee, args.bbox, args.before[0], args.before[1], args.scale, "S2_BEFORE")
    after = composite(ee, args.bbox, args.after[0], args.after[1], args.scale, "S2_AFTER")
    print(f"  scenes: before={before.meta['n_scenes']}, after={after.meta['n_scenes']}")

    detector = OpticalIndexDetector(theater_id=args.theater)
    obs = detector.infer([before, after])
    burns = sorted((o for o in obs if o.obs_type == "burn_scar"), key=lambda o: -o.meta["delta"])
    floods = sorted((o for o in obs if o.obs_type == "flood"), key=lambda o: -o.meta["delta"])
    print("=" * 60)
    print(f"  burn_scar detections : {len(burns)}")
    print(f"  flood detections     : {len(floods)}")
    for o in burns[:5]:
        print(f"    burn dNBR={o.meta['delta']:.2f}  {o.meta['n_pixels']}px @ {o.geo.lat:.3f},{o.geo.lon:.3f}")
    for o in floods[:5]:
        print(f"    flood ΔMNDWI={o.meta['delta']:.2f}  {o.meta['n_pixels']}px @ {o.geo.lat:.3f},{o.geo.lon:.3f}")
    print("=" * 60)

    if args.ingest:
        # Reuse the proper imagery-ingest plumbing: cached_detect (coarsen via the REAL grid
        # resolver) → append-only log. Same path as the SAR board ingest.
        from ingest.imagery.run import detect_and_persist
        from ingest.imagery.caches import PgDetectionCache
        from ingest.pipeline import build_context
        ctx = build_context(args.theater)
        counters = detect_and_persist(detector, [before, after], ctx.conn, ctx.resolver,
                                      ctx.embedder, PgDetectionCache(ctx.conn), bus=ctx.bus)
        print(f"INGEST: {counters['ingested']} optical obs written to the log "
              f"({counters['exact_dup']} dups, {counters['rejected']} rejected "
              f"{counters['by_reason'] or ''})")
        print("Next: python -m fusion.run --theater %s && python -m assess.run --theater %s"
              % (args.theater, args.theater))


if __name__ == "__main__":
    main()
