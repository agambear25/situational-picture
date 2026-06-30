"""Offline gate for the Phase-5b SAR vessel detector (pure: no GEE, no DB)."""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import numpy as np

from ingest.imagery.framework import Tile
from ingest.imagery.sar_vessel import SarVesselDetector

BBOX = (56.0, 26.0, 56.5, 26.5)        # Strait of Hormuz-ish, 10×10 grid → 0.05°/pixel
T = datetime(2024, 3, 1, 2, 0, tzinfo=timezone.utc)


def _tile(arr2d):
    a = np.asarray(arr2d, np.float32)[None, :, :]
    buf = io.BytesIO(); np.save(buf, a)
    return Tile(granule_id="S1", acq_start=T, acq_end=T + timedelta(hours=1),
                data=buf.getvalue(), bbox=BBOX, meta={"bands": ["VV"], "scale_m": 50})


def _sea(val=-22.0, h=10, w=10):
    return np.full((h, w), val, np.float32)


def test_calm_sea_has_no_vessels():
    assert SarVesselDetector().infer([_tile(_sea())]) == []


def test_bright_target_is_a_vessel():
    a = _sea(); a[4, 4] = 5.0; a[4, 5] = 4.0      # a 2-pixel bright ship
    obs = SarVesselDetector().infer([_tile(a)])
    assert len(obs) == 1
    o = obs[0]
    assert o.obs_type == "naval_transit" and o.source_family_id == "copernicus_sar"
    assert o.modality == "imagery" and o.self_conf >= 0.3
    assert o.meta["detector"] == "sar_vessel" and o.meta["n_pixels"] == 2


def test_two_ships_two_detections():
    a = _sea(); a[2, 2] = 6.0; a[2, 3] = 5.0; a[7, 8] = 7.0; a[8, 8] = 6.0
    assert len(SarVesselDetector().infer([_tile(a)])) == 2


def test_masked_land_excluded():
    a = _sea(-22.0)
    a[:, :4] = -35.0                              # masked land (< land_floor) on the left
    a[5, 7] = 6.0; a[5, 8] = 5.0                  # a 2-pixel ship in the water on the right
    obs = SarVesselDetector().infer([_tile(a)])
    assert len(obs) == 1 and obs[0].geo.lon > 56.25   # detected in the water half, land ignored


def test_single_pixel_speckle_ignored():
    a = _sea(); a[5, 5] = 8.0                     # one isolated bright pixel (< min_cluster_px=2)
    assert SarVesselDetector().infer([_tile(a)]) == []


def test_deterministic():
    a = _sea(); a[4, 4] = 6.0; a[4, 5] = 6.0
    d = SarVesselDetector()
    assert [(o.geo.lon, o.geo.lat, o.self_conf) for o in d.infer([_tile(a)])] == \
           [(o.geo.lon, o.geo.lat, o.self_conf) for o in d.infer([_tile(a)])]
