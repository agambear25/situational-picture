"""
Sentinel-2 optical spectral-index change detector (Phase 3f) — a SECOND, independent imagery
family (copernicus_optical) so optical change can corroborate SAR / thermal / text via noisy-OR.

Two classic unsupervised indices over before/after S2 surface-reflectance composites:
  • flood     — ΔMNDWI, MNDWI = (Green − SWIR)/(Green + SWIR) [B3,B11]. New open water ⇒ rises.
                (MNDWI, not NDWI: a burn also drops NIR so NDWI would false-flag it as water, but a
                 burn RAISES SWIR so MNDWI correctly falls — the two indices stay separable.)
  • burn_scar — dNBR,  NBR  = (NIR − SWIR)/(NIR + SWIR) [B8,B11]. Vegetation burns ⇒ NBR falls,
                so dNBR = NBR_before − NBR_after rises over a burn.

Same shape as the 3e SAR detector and PURE for the same reason: infer(tiles) touches no DB / no
network / no clock, so it replays bit-identical through the detection cache. Needs ≥2 acquisitions
over a footprint; with fewer it honestly returns nothing. Single-family ⇒ Rumored until a second
family agrees. These are change PROXIES (new water isn't proof of flooding; a burn scar isn't proof
of a strike) — conservative thresholds + magnitude-banded confidence, refined later by the deep
model. The general clustering / geo helpers are shared with sar_logratio (one implementation).
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Optional

import numpy as np

from ingest.contract import GeoRef, RawObservation
from ingest.imagery.framework import Tile, registry
from ingest.imagery.sar_logratio import (
    _group_by_bbox, _label, _magnitude_conf, _mean_band, _pixel_to_lonlat,
)


@dataclass
class OpticalIndexDetector:
    name: str = "optical_index"
    source_id: str = "sentinel2_optical_index"
    family_id: str = "copernicus_optical"
    model_digest: str = "optical-index-v1"     # classical → version string, pinned in the cache key
    green_band: str = "B3"
    nir_band: str = "B8"
    swir_band: str = "B11"
    mndwi_delta_threshold: float = 0.20        # ΔMNDWI to call a pixel newly-water (flood)
    nbr_delta_threshold: float = 0.25          # dNBR to call a pixel burned
    saturate_delta: float = 0.55               # index-change at/above which confidence saturates
    min_cluster_px: int = 6
    theater_id: str = "ua_donbas"

    def infer(self, tiles: list[Tile]) -> list[RawObservation]:
        out: list[RawObservation] = []
        for bbox, group in _group_by_bbox(tiles):
            if bbox is None or len(group) < 2:
                continue
            group = sorted(group, key=lambda t: t.acq_start)
            mid = max(1, len(group) // 2)

            def comp(band, lo, hi):                       # speckle/cloud-suppressed composite
                return _mean_band([_band(t, band) for t in group[lo:hi]])

            g_b, g_a = comp(self.green_band, 0, mid), comp(self.green_band, mid, len(group))
            n_b, n_a = comp(self.nir_band, 0, mid), comp(self.nir_band, mid, len(group))
            s_b, s_a = comp(self.swir_band, 0, mid), comp(self.swir_band, mid, len(group))
            occ_start, occ_end = group[-1].acq_start, group[-1].acq_end
            change_from = group[mid - 1].acq_start

            if _ok(g_b, g_a, s_b, s_a):
                d_mndwi = _ndi(g_a, s_a) - _ndi(g_b, s_b)  # increase = new open water
                out += self._emit(d_mndwi, bbox, "flood", "MNDWI", self.mndwi_delta_threshold,
                                  occ_start, occ_end, change_from, group[-1])
            if _ok(n_b, n_a, s_b, s_a):
                d_nbr = _ndi(n_b, s_b) - _ndi(n_a, s_a)   # NBR drop (before − after) = burn
                out += self._emit(d_nbr, bbox, "burn_scar", "dNBR", self.nbr_delta_threshold,
                                  occ_start, occ_end, change_from, group[-1])
        return out

    def _emit(self, delta, bbox, obs_type, index, threshold, occ_start, occ_end, change_from,
              latest: Tile) -> list[RawObservation]:
        mask = delta >= threshold                          # directional: new water / fresh burn only
        if not mask.any():
            return []
        labels, n = _label(mask)
        H, W = delta.shape
        precision_m = float(latest.meta.get("scale_m", 100.0))
        obs: list[RawObservation] = []
        for lab in range(1, n + 1):
            ys, xs = np.where(labels == lab)
            if ys.size < self.min_cluster_px:
                continue
            mag = float(np.mean(delta[ys, xs]))
            lon, lat = _pixel_to_lonlat(float(ys.mean()), float(xs.mean()), H, W, bbox)
            obs.append(RawObservation(
                theater_id=self.theater_id, source_id=self.source_id,
                source_family_id=self.family_id, modality="imagery", obs_type=obs_type,
                occurred_start=occ_start, occurred_end=occ_end,
                geo=GeoRef(lon=lon, lat=lat, precision_m=precision_m),
                text=(f"Sentinel-2 {index} change of {mag:.2f} over ~{int(ys.size)} px "
                      f"({'new surface water' if obs_type == 'flood' else 'vegetation burn signature'})"),
                self_conf=_magnitude_conf(mag, threshold, threshold + self.saturate_delta),
                meta={"detector": self.name, "index": index, "delta": round(mag, 3),
                      "n_pixels": int(ys.size), "model_digest": self.model_digest,
                      "change_from": change_from.isoformat(), "change_to": occ_end.isoformat()},
            ))
        return obs


# --------------------------------------------------------------------------- helpers (pure)

def _band(tile: Tile, band: str) -> Optional[np.ndarray]:
    """Decode a tile's reflectance bytes (float32 .npy, shape (bands,H,W)) to one 2-D band array."""
    arr = np.load(io.BytesIO(tile.data))
    if arr.ndim == 2:
        return arr.astype(float)
    bands = tile.meta.get("bands")
    idx = bands.index(band) if bands and band in bands else 0
    return arr[idx].astype(float)


def _ndi(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Normalised difference index (a−b)/(a+b), 0 where the denominator vanishes."""
    num, den = a - b, a + b
    return np.divide(num, den, out=np.zeros_like(num, dtype=float), where=den != 0)


def _ok(*arrs) -> bool:
    return all(a is not None for a in arrs) and len({a.shape for a in arrs}) == 1


try:
    registry.register(OpticalIndexDetector())
except ValueError:
    pass  # already registered (module re-import in the same process)
