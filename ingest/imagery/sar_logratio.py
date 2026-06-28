"""
Classical Sentinel-1 SAR log-ratio change detector (Phase 3e) — the headline imagery detector
and the deterministic baseline the deep change model (3h) must beat.

The classic unsupervised SAR change detector:
  - GEE COPERNICUS/S1_GRD σ0 bands are already in dB (a log scale), so the log-ratio
    log(σ0_after / σ0_before) is simply the dB difference  after_dB − before_dB.
  - Multi-temporal speckle suppression: average the earlier acquisitions into a 'before'
    composite and the later ones into an 'after' composite before differencing.
  - Threshold |Δ dB|, label connected components, emit one detection per change cluster at its
    centroid, with confidence monotone in the change magnitude (banded once at the cache).

PURE: infer(tiles) touches no DB, no network, no clock — deterministic given the tiles, so it
replays bit-identical through the detection cache. It needs ≥2 acquisitions over the same
footprint; with fewer it honestly returns nothing rather than inventing a change.

This is a damage *proxy*: a backscatter change is real change, not proof of a destroyed
building. So it emits a configurable, conservative obs_type with magnitude-banded confidence,
single-family (copernicus_sar) ⇒ Rumored until a second family corroborates. The deep model
(3h) refines the change into a specific damage type later.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Optional

import numpy as np

from ingest.contract import GeoRef, RawObservation
from ingest.imagery.framework import Tile, registry


@dataclass
class SarLogRatioDetector:
    name: str = "sar_logratio"
    source_id: str = "sentinel1_sar_logratio"
    family_id: str = "copernicus_sar"
    model_digest: str = "sar-logratio-v1"     # classical → a version string, pinned in the cache key
    obs_type: str = "building_damaged"        # damage proxy; the deep model (3h) refines the type
    band: str = "VV"
    threshold_db: float = 4.0                 # |Δσ0| in dB to call a pixel 'changed'
    saturate_db: float = 12.0                 # Δ at/above which confidence saturates
    min_cluster_px: int = 4                   # ignore speckle-sized clusters
    theater_id: str = "ua_donbas"

    def infer(self, tiles: list[Tile]) -> list[RawObservation]:
        out: list[RawObservation] = []
        for bbox, group in _group_by_bbox(tiles):
            if bbox is None or len(group) < 2:
                continue
            group = sorted(group, key=lambda t: t.acq_start)
            mid = max(1, len(group) // 2)
            before = _mean_band([_band_db(t, self.band) for t in group[:mid]])
            after = _mean_band([_band_db(t, self.band) for t in group[mid:]])
            if before is None or after is None or before.shape != after.shape:
                continue
            delta = after - before            # dB difference = log-ratio
            # The detection is MADE at the 'after' acquisition, so the observation is timestamped
            # there (so it blocks with events co-temporal to detection). The change *interval*
            # (last-before → after) is kept in meta for provenance, not as the occurred window.
            latest = group[-1]
            change_from = group[mid - 1].acq_start
            out.extend(self._emit(delta, bbox, latest, change_from))
        return out

    def _emit(self, delta, bbox, latest: Tile, change_from) -> list[RawObservation]:
        mask = np.abs(delta) >= self.threshold_db
        if not mask.any():
            return []
        labels, n = _label(mask)
        H, W = delta.shape
        precision_m = float(latest.meta.get("scale_m", 500.0))
        obs: list[RawObservation] = []
        for lab in range(1, n + 1):
            ys, xs = np.where(labels == lab)
            if ys.size < self.min_cluster_px:
                continue
            patch = delta[ys, xs]
            mag = float(np.mean(np.abs(patch)))
            direction = "increase" if float(np.mean(patch)) > 0 else "decrease"
            lon, lat = _pixel_to_lonlat(float(ys.mean()), float(xs.mean()), H, W, bbox)
            obs.append(RawObservation(
                theater_id=self.theater_id, source_id=self.source_id,
                source_family_id=self.family_id, modality="imagery", obs_type=self.obs_type,
                occurred_start=latest.acq_start, occurred_end=latest.acq_end,
                geo=GeoRef(lon=lon, lat=lat, precision_m=precision_m),
                text=(f"SAR backscatter {direction} of {mag:.1f} dB over ~{int(ys.size)} px "
                      f"(Sentinel-1 {self.band} log-ratio change)"),
                self_conf=_magnitude_conf(mag, self.threshold_db, self.saturate_db),
                meta={"detector": self.name, "delta_db": round(mag, 2), "direction": direction,
                      "n_pixels": int(ys.size), "model_digest": self.model_digest,
                      "change_from": change_from.isoformat(), "change_to": latest.acq_end.isoformat()},
            ))
        return obs


# --------------------------------------------------------------------------- helpers (pure)

def _band_db(tile: Tile, band: str) -> Optional[np.ndarray]:
    """Decode a tile's σ0 bytes (float32 .npy) to a single-band 2-D dB array."""
    arr = np.load(io.BytesIO(tile.data))
    if arr.ndim == 2:
        return arr.astype(float)
    bands = tile.meta.get("bands")
    idx = bands.index(band) if bands and band in bands else 0
    return arr[idx].astype(float)


def _mean_band(arrs: list[Optional[np.ndarray]]) -> Optional[np.ndarray]:
    arrs = [a for a in arrs if a is not None]
    if not arrs:
        return None
    shape = arrs[0].shape
    arrs = [a for a in arrs if a.shape == shape]   # only composite co-registered (same-shape) tiles
    return np.mean(np.stack(arrs), axis=0)


def _group_by_bbox(tiles: list[Tile]):
    """Group tiles by footprint so change is only computed over co-registered stacks."""
    groups: dict = {}
    for t in tiles:
        key = tuple(round(x, 6) for x in t.bbox) if t.bbox else None
        groups.setdefault(key, []).append(t)
    return list(groups.items())


def _pixel_to_lonlat(r: float, c: float, H: int, W: int, bbox) -> tuple[float, float]:
    """Map an array pixel (row r from north, col c from west) to lon/lat via the tile bbox.

    The precise coord is consumed by the coarsening resolver and discarded — only the 1km
    cell_id survives (the determinism + analytical-not-targeting contract).
    """
    w, s, e, n = bbox
    lon = w + (c + 0.5) * (e - w) / W
    lat = n - (r + 0.5) * (n - s) / H
    return lon, lat


def _magnitude_conf(mag: float, thr: float, sat: float) -> float:
    """Raw confidence monotone in change magnitude, in [0.3, 0.9]. Banded at the cache boundary."""
    if sat <= thr:
        return 0.6
    frac = max(0.0, min(1.0, (mag - thr) / (sat - thr)))
    return round(0.3 + 0.6 * frac, 4)


def _label(mask: np.ndarray):
    """Connected-component labeling. Uses scipy when present, else a pure-numpy flood fill so the
    detector has no hard scipy dependency in the offline gate."""
    try:
        from scipy import ndimage  # lazy; a [full] transitive dep
        return ndimage.label(mask)
    except ImportError:
        return _label_numpy(mask)


def _label_numpy(mask: np.ndarray):
    H, W = mask.shape
    labels = np.zeros((H, W), dtype=int)
    cur = 0
    for i in range(H):
        for j in range(W):
            if mask[i, j] and labels[i, j] == 0:
                cur += 1
                stack = [(i, j)]
                labels[i, j] = cur
                while stack:
                    y, x = stack.pop()
                    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < H and 0 <= nx < W and mask[ny, nx] and labels[ny, nx] == 0:
                            labels[ny, nx] = cur
                            stack.append((ny, nx))
    return labels, cur


# "config + one module": register a default-configured instance so the runner can look it up.
try:
    registry.register(SarLogRatioDetector())
except ValueError:
    pass  # already registered (module re-import in the same process)
