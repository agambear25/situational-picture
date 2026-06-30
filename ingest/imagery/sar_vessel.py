"""
Sentinel-1 SAR vessel detector (Phase 5b, maritime) — ships as bright targets on dark water.

On a calm sea, radar backscatter is low (the water is "dark"); a metal ship is a strong specular
reflector and lights up. So vessels are bright statistical OUTLIERS over water: with land masked to
a low constant during acquisition, the detector takes one σ0 image, computes the sea clutter
mean/σ over the valid water pixels, thresholds at mean + k·σ, and clusters the bright pixels into
one detection per vessel. This is the classic adaptive-threshold (CFAR-style) ship detector.

PURE: infer(tiles) touches no DB / network / clock — deterministic, replays bit-identical through
the detection cache. Single-image (no before/after needed). Emits 'naval_transit' observations
(a vessel present in a cell at a time), single-family copernicus_sar ⇒ Rumored until corroborated
(e.g. by AIS or a naval-incident report). Shares the geo/cluster helpers with sar_logratio.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ingest.contract import GeoRef, RawObservation
from ingest.imagery.framework import Tile, registry
from ingest.imagery.sar_logratio import _band_db, _label, _magnitude_conf, _pixel_to_lonlat


@dataclass
class SarVesselDetector:
    name: str = "sar_vessel"
    source_id: str = "sentinel1_sar_vessel"
    family_id: str = "copernicus_sar"
    model_digest: str = "sar-vessel-cfar-v1"
    obs_type: str = "naval_transit"
    band: str = "VV"
    k_sigma: float = 4.0          # threshold = sea_median + k·σ (σ from MAD, robust to the ships themselves)
    margin_db: float = 8.0        # …but at least this far above the sea (ships are ≥ ~8 dB over clutter)
    land_floor_db: float = -28.0  # pixels ≤ this are masked land / no-data → excluded from sea stats
    saturate_db: float = 10.0     # brightness above threshold at which confidence saturates
    min_cluster_px: int = 2       # ignore single-pixel speckle
    max_cluster_px: int = 300     # a huge bright blob is land/artifact, not a vessel
    theater_id: str = "black_sea"

    def infer(self, tiles: list[Tile]) -> list[RawObservation]:
        out: list[RawObservation] = []
        for tile in tiles:
            arr = _band_db(tile, self.band)
            if arr is None or tile.bbox is None:
                continue
            water = arr > self.land_floor_db
            if int(water.sum()) < 50:                 # essentially no sea in view
                continue
            sea = arr[water]
            # Robust background: median + MAD-derived σ are unaffected by the bright ships, so a
            # ship-contaminated scene doesn't push the threshold above its own targets.
            med = float(np.median(sea))
            robust_sd = 1.4826 * float(np.median(np.abs(sea - med)))
            thr = med + max(self.k_sigma * robust_sd, self.margin_db)
            mask = (arr >= thr) & water
            if not mask.any():
                continue
            labels, n = _label(mask)
            H, W = arr.shape
            precision_m = float(tile.meta.get("scale_m", 50.0))
            for lab in range(1, n + 1):
                ys, xs = np.where(labels == lab)
                if not (self.min_cluster_px <= ys.size <= self.max_cluster_px):
                    continue
                peak_over_thr = float(arr[ys, xs].max() - thr)
                lon, lat = _pixel_to_lonlat(float(ys.mean()), float(xs.mean()), H, W, tile.bbox)
                out.append(RawObservation(
                    theater_id=self.theater_id, source_id=self.source_id,
                    source_family_id=self.family_id, modality="imagery", obs_type=self.obs_type,
                    occurred_start=tile.acq_start, occurred_end=tile.acq_end,
                    geo=GeoRef(lon=lon, lat=lat, precision_m=precision_m),
                    text=(f"SAR bright target {peak_over_thr:.1f} dB above sea clutter "
                          f"(~{int(ys.size)} px) — likely vessel (Sentinel-1 {self.band})"),
                    self_conf=_magnitude_conf(peak_over_thr, 0.0, self.saturate_db),
                    meta={"detector": self.name, "peak_db_over_thr": round(peak_over_thr, 2),
                          "n_pixels": int(ys.size), "sea_median_db": round(med, 1),
                          "model_digest": self.model_digest},
                ))
        return out


try:
    registry.register(SarVesselDetector())
except ValueError:
    pass  # already registered (module re-import in the same process)
