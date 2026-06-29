"""
Validate the classical SAR log-ratio detector (3e) on REAL Sentinel-1, scored against UNOSAT.

This is the live proof the detector was built for. It focuses on ONE city at fine resolution
(building-scale damage is invisible at the 500m AOI default), composites a pre- and post-event
Sentinel-1 image ON GEE (median over each window — robust to speckle, two downloads not dozens),
runs the pure detector, coarsens its detections to 1km cells, and scores precision/recall against
the UNOSAT damage cells inside the same box.

GEE is still only a data tap: it delivers the two σ0 composites; all detection/decision logic is
the same local, deterministic code the offline gate exercises.

Usage (defaults to Mariupol, pre-invasion vs the May-2022 UNOSAT assessment):
    python -m eval.validate_sar
    python -m eval.validate_sar --city Sievierodonetsk --before 2022-01-01 2022-02-23 \
        --after 2022-07-10 2022-08-10 --scale 20 --threshold-db 3
"""
from __future__ import annotations

import argparse
import hashlib
import io
import pickle
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

_TILE_CACHE = Path(".tile_cache")

from grid.mgrs_1km import to_cell_id
from grid.types import Cell, CellResolution, GeoPrecision
from ingest.imagery.framework import Tile
from ingest.imagery.sar_logratio import SarLogRatioDetector
from ingest.imagery.caches import InMemoryDetectionCache, cached_detect_multi
from eval.unosat import (
    load_unosat_features, truth_cells, score, recall_by_grade, recall_by_city, format_report,
)

# Validation boxes around heavily-assessed cities (w, s, e, n). Tight enough to pull at ~20m.
CITIES = {
    "Mariupol":        (37.45, 47.03, 37.72, 47.18),
    "Sievierodonetsk": (38.42, 48.90, 38.55, 49.00),
    "Rubizhne":        (38.33, 48.97, 38.44, 49.05),
    "Volnovakha":      (37.44, 47.55, 37.55, 47.65),
    "Avdiivka":        (37.69, 48.10, 37.81, 48.18),
}


class _LocalResolver:
    """Offline MGRS-snap resolver — turns a detector's precise pixel coord into its 1km cell."""
    def resolve_to_cell(self, lon, lat, precision_m, place_name, theater_id):
        if lon is None or lat is None:
            return None
        cid = to_cell_id(lon, lat)
        return CellResolution(cell=Cell(cell_id=cid, theater_id=theater_id, label=cid),
                              precision=GeoPrecision.PRECISE, non_precise=False)


def _composite_tile(ee, bbox, start, end, scale_m, granule_id, mask_water=True) -> Tile:
    """Median Sentinel-1 σ0 (VV+VH, IW, ascending) over [start,end], clipped to bbox, as a Tile.

    Water masking (mask_water): over the sea/port, SAR backscatter swings wildly (waves, ships,
    tides) and produces the bulk of the false positives. We mask permanent-water pixels (ESA
    WorldCover class 80) in BOTH composites and unmask to a constant, so water reads as exactly
    zero change and the detector ignores it. Applied identically to before+after so it can't
    itself create a change.
    """
    w, s, e, n = bbox
    aoi = ee.Geometry.BBox(w, s, e, n)
    col = (ee.ImageCollection("COPERNICUS/S1_GRD")
           .filterBounds(aoi).filterDate(start, end)
           .filter(ee.Filter.eq("instrumentMode", "IW"))
           .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
           .filter(ee.Filter.eq("orbitProperties_pass", "ASCENDING"))
           .select(["VV", "VH"]))
    n_scenes = col.size().getInfo()
    if n_scenes == 0:
        raise RuntimeError(f"no Sentinel-1 scenes for {start}..{end} over {bbox}")
    img = col.median()
    if mask_water:
        land = ee.ImageCollection("ESA/WorldCover/v200").first().select("Map").neq(80)
        img = img.updateMask(land).unmask(-20)   # water → constant in both composites → Δ=0
    img = img.clip(aoi)
    url = img.getDownloadURL({"format": "NPY", "region": aoi, "scale": scale_m,
                              "bands": [{"id": "VV"}, {"id": "VH"}]})
    with urllib.request.urlopen(url) as resp:
        raw = resp.read()
    arr = np.load(io.BytesIO(raw))
    if arr.dtype.names:                       # GEE NPY → structured array; stack to (bands, H, W)
        arr = np.stack([arr[nm].astype(np.float32) for nm in arr.dtype.names])
    else:
        arr = arr.astype(np.float32)
    buf = io.BytesIO(); np.save(buf, arr)
    # Timestamp the composite at the window MIDPOINT — it represents the whole window, and it keeps
    # the detection within the (wide) damage block-window of a same-period UNOSAT/news assessment.
    t0 = datetime.fromisoformat(start + "T00:00:00+00:00")
    t1 = datetime.fromisoformat(end + "T00:00:00+00:00")
    t = (t0 + (t1 - t0) / 2).astimezone(timezone.utc)
    return Tile(granule_id=granule_id, acq_start=t, acq_end=t, data=buf.getvalue(), bbox=bbox,
                meta={"bands": ["VV", "VH"], "scale_m": scale_m, "n_scenes": n_scenes})


def get_tile(ee, city, bbox, start, end, scale_m, granule_id, mask_water=True) -> Tile:
    """Composite a tile, caching it on disk so parameter sweeps don't re-download from GEE."""
    _TILE_CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(f"{city}|{start}|{end}|{scale_m}|water={mask_water}".encode()).hexdigest()[:16]
    path = _TILE_CACHE / f"sar_{key}.pkl"
    if path.exists():
        with path.open("rb") as f:
            return pickle.load(f)
    tile = _composite_tile(ee, bbox, start, end, scale_m, granule_id, mask_water=mask_water)
    with path.open("wb") as f:
        pickle.dump(tile, f)
    return tile


def main():
    p = argparse.ArgumentParser(prog="python -m eval.validate_sar")
    p.add_argument("--city", default="Mariupol", choices=list(CITIES))
    p.add_argument("--before", nargs=2, metavar=("START", "END"), default=["2022-01-01", "2022-02-23"])
    p.add_argument("--after", nargs=2, metavar=("START", "END"), default=["2022-05-01", "2022-06-15"])
    p.add_argument("--scale", type=int, default=20, help="metres/pixel (S1 native ~10m)")
    p.add_argument("--threshold-db", type=float, default=3.0)
    p.add_argument("--min-cluster-px", type=int, default=8)
    p.add_argument("--buffer-m", type=float, default=0.0, help="UNOSAT match tolerance (0 = exact cell)")
    p.add_argument("--sweep", action="store_true", help="grid-sweep threshold × min-cluster, print a table")
    p.add_argument("--ingest", action="store_true",
                   help="write the detections to the live log as copernicus_sar imagery observations")
    p.add_argument("--project", default=None, help="GEE project (else GEE_PROJECT env)")
    args = p.parse_args()

    import os
    import ee
    ee.Initialize(project=args.project or os.environ.get("GEE_PROJECT") or None)

    bbox = CITIES[args.city]
    print(f"Sentinel-1 composites for {args.city} at {args.scale}m (cached after first pull) …")
    before = get_tile(ee, args.city, bbox, args.before[0], args.before[1], args.scale, "S1_before")
    after = get_tile(ee, args.city, bbox, args.after[0], args.after[1], args.scale, "S1_after")
    print(f"  before: {before.meta['n_scenes']} scenes  |  after: {after.meta['n_scenes']} scenes")

    truth = truth_cells(load_unosat_features(), bbox)   # UNOSAT damage cells inside the city box

    def run(thr, minpx):
        det = SarLogRatioDetector(threshold_db=thr, min_cluster_px=minpx)
        cds, _ = cached_detect_multi(det, [before, after], InMemoryDetectionCache(),
                                     _LocalResolver(), "ua_donbas")
        detected = {cd.cell_id for cd in cds}
        return detected, score(detected, truth, args.buffer_m)

    if args.sweep:
        print(f"\n  UNOSAT damage cells in box: {len(truth)}   (recall is vs these)")
        print(f"  {'thr_dB':>6} {'min_px':>6} {'cells':>6} {'prec':>7} {'recall':>7} {'F1':>7}")
        print("  " + "-" * 44)
        for thr in (3, 4, 5, 6, 7, 8):
            for minpx in (8, 30, 80):
                _d, r = run(thr, minpx)
                print(f"  {thr:>6.0f} {minpx:>6} {r['n_detected']:>6} "
                      f"{r['precision']:>6.1%} {r['recall']:>7.1%} {r['f1']:>7.1%}")
        print(f"\n  (target: {args.city}; before {args.before[0]}..{args.before[1]}; "
              f"after {args.after[0]}..{args.after[1]})")
        return

    if args.ingest:
        # Write the cleaned detections into the live log as copernicus_sar imagery observations,
        # using the real DB + embedder. fusion.run then projects them; co-located damage events
        # gain a 'satellite radar' family and lift. Uses the pre-coarsened contract (cell pinned).
        from ingest.pipeline import build_context
        from ingest.imagery.caches import detections_to_observations
        from ingest.contract import persist_observation
        ctx = build_context("ua_donbas")
        det = SarLogRatioDetector(threshold_db=args.threshold_db, min_cluster_px=args.min_cluster_px)
        # Use the REAL GridResolver (not the offline snap): it only resolves cells that exist in
        # the built grid, so detections outside the grid are cleanly skipped (never an FK error).
        cds, rej = cached_detect_multi(det, [before, after], InMemoryDetectionCache(), ctx.resolver, "ua_donbas")
        obs = detections_to_observations(cds, det.source_id, det.family_id, "ua_donbas",
                                         ctx.taxonomy, ctx.embedder)
        n_ing = sum(1 for o in obs if persist_observation(ctx.conn, o)[0] is not None)
        print(f"  ingested {n_ing}/{len(obs)} SAR detections into the log "
              f"({len(rej)} outside the grid, skipped; threshold {args.threshold_db} dB).")
        print(f"  next: python -m fusion.run --theater ua_donbas")
        return

    detected, overall = run(args.threshold_db, args.min_cluster_px)
    print(f"  detector: {len(detected)} distinct 1km cells "
          f"(threshold {args.threshold_db} dB, min cluster {args.min_cluster_px}px)")
    by_grade = recall_by_grade(detected, truth, args.buffer_m)
    by_city = recall_by_city(detected, truth, args.buffer_m)
    print()
    print(format_report(overall, by_grade, by_city, args.buffer_m))
    print(f"\n  (target: {args.city}; before {args.before[0]}..{args.before[1]}; "
          f"after {args.after[0]}..{args.after[1]})")


if __name__ == "__main__":
    main()
