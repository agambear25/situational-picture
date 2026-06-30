"""
Offline gate for the Phase-3f Sentinel-2 optical spectral-index detector. No GEE / model / DB.
Proves: pure/deterministic infer, MNDWI flood + dNBR burn detection at the cluster centroid, that
the two indices stay separable (a burn does not false-flag as flood and vice-versa), and the
headline — an optical flood corroborates a text flood report via noisy-OR (second independent
family ⇒ band lifts above Rumored) and replays bit-identical.
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import yaml

from grid.mgrs_1km import to_cell_id
from grid.types import Cell, CellResolution, GeoPrecision
from ingest.contract import GeoRef, RawObservation, normalize
from ingest.imagery.framework import Tile
from ingest.imagery.caches import (
    InMemoryDetectionCache, cached_detect_multi, detections_to_observations,
)
from ingest.imagery.optical_index import OpticalIndexDetector
from fusion.fuse import fuse
from fusion.replay import assert_bit_identical

_TAX = yaml.safe_load((Path(__file__).parents[3] / "config" / "taxonomy.yaml").read_text())

BBOX = (37.0, 48.0, 37.5, 48.5)             # 10×10 grid → 0.05°/pixel
T_BEFORE = datetime(2024, 6, 1, 9, 0, tzinfo=timezone.utc)
T_AFTER = datetime(2024, 6, 11, 9, 0, tzinfo=timezone.utc)   # ~10-day S2 revisit
PATCH_LON, PATCH_LAT = 37.275, 48.225       # centroid of the 4..6 patch

# Healthy-vegetation baseline (green, NIR, SWIR): NBR≈+0.54, MNDWI≈−0.20.
VEG = (0.08, 0.40, 0.12)
# A 3×3 burn:  NIR drops, SWIR rises → dNBR≈0.87 (burn); ΔMNDWI≈−0.34 (NOT flood).
BURN = (0.09, 0.15, 0.30)
# A 3×3 flood: NIR + SWIR both low → ΔMNDWI≈1.07 (flood); dNBR≈−0.17 (NOT burn).
WATER = (0.07, 0.03, 0.005)


class _LocalResolver:
    def resolve_to_cell(self, lon, lat, precision_m, place_name, theater_id):
        if lon is None or lat is None:
            return None
        cid = to_cell_id(lon, lat)
        return CellResolution(cell=Cell(cell_id=cid, theater_id=theater_id, label=cid),
                              precision=GeoPrecision.PRECISE, non_precise=False)


class _Empty:
    def get(self, k): return None
    def put(self, k, v, **kw): pass


class _AlwaysSame:
    def adjudicate(self, ctx):
        from llm.schema import Verdict
        return Verdict(same=True, confidence=0.9, rationale="test")


class _FakeEmbedder:
    @property
    def dim(self): return 16

    def embed(self, text):
        import hashlib
        v = [0.0] * 16
        for i in range(6):
            v[i] = 1.0
        h = hashlib.sha256((text or "").encode()).digest()
        for i in range(10):
            v[6 + i] = (h[i] % 7) / 6.0
        return tuple(v)


def _s2(triple, gid, t, patch=None, h=10, w=10):
    """A 3-band (B3,B8,B11) S2 tile of constant reflectance, optionally with a 3×3 patch at 4..6."""
    g, n, s = triple
    a = np.stack([np.full((h, w), g, np.float32), np.full((h, w), n, np.float32),
                  np.full((h, w), s, np.float32)])
    if patch is not None:
        pg, pn, ps = patch
        a[0, 4:7, 4:7], a[1, 4:7, 4:7], a[2, 4:7, 4:7] = pg, pn, ps
    buf = io.BytesIO(); np.save(buf, a)
    return Tile(granule_id=gid, acq_start=t, acq_end=t + timedelta(hours=1),
                data=buf.getvalue(), bbox=BBOX, meta={"bands": ["B3", "B8", "B11"], "scale_m": 100})


# --------------------------------------------------------------------------- pure infer

def test_no_change_no_detection():
    det = OpticalIndexDetector()
    assert det.infer([_s2(VEG, "B", T_BEFORE), _s2(VEG, "A", T_AFTER)]) == []


def test_single_tile_cannot_form_a_pair():
    assert OpticalIndexDetector().infer([_s2(VEG, "B", T_BEFORE)]) == []


def test_burn_detected_and_not_flagged_as_flood():
    obs = OpticalIndexDetector().infer([_s2(VEG, "B", T_BEFORE), _s2(VEG, "A", T_AFTER, patch=BURN)])
    burns = [o for o in obs if o.obs_type == "burn_scar"]
    assert len(burns) == 1 and not [o for o in obs if o.obs_type == "flood"]
    o = burns[0]
    assert o.source_family_id == "copernicus_optical" and o.modality == "imagery"
    assert o.meta["index"] == "dNBR" and o.obs_type in set(_TAX["event_types"])
    assert abs(o.geo.lon - PATCH_LON) < 1e-6 and abs(o.geo.lat - PATCH_LAT) < 1e-6


def test_flood_detected_and_not_flagged_as_burn():
    obs = OpticalIndexDetector().infer([_s2(VEG, "B", T_BEFORE), _s2(VEG, "A", T_AFTER, patch=WATER)])
    floods = [o for o in obs if o.obs_type == "flood"]
    assert len(floods) == 1 and not [o for o in obs if o.obs_type == "burn_scar"]
    assert floods[0].meta["index"] == "MNDWI" and floods[0].source_family_id == "copernicus_optical"


def test_infer_is_deterministic():
    det = OpticalIndexDetector()
    tiles = [_s2(VEG, "B", T_BEFORE), _s2(VEG, "A", T_AFTER, patch=BURN)]
    a, b = det.infer(tiles), det.infer(tiles)
    assert [(o.obs_type, o.geo.lon, o.self_conf) for o in a] == \
           [(o.obs_type, o.geo.lon, o.self_conf) for o in b]


# --------------------------------------------------------------------------- headline: 2nd family

def test_optical_flood_corroborates_text_and_replays():
    resolver, embedder = _LocalResolver(), _FakeEmbedder()
    det = OpticalIndexDetector()
    cds, _ = cached_detect_multi(det, [_s2(VEG, "B", T_BEFORE), _s2(VEG, "A", T_AFTER, patch=WATER)],
                                 InMemoryDetectionCache(), resolver, "ua_donbas")
    img = detections_to_observations(cds, det.source_id, det.family_id, "ua_donbas", _TAX,
                                     embedder=embedder)
    flood_obs = next(o for o in img if o.obs_type == "flood")

    text = RawObservation(
        theater_id="ua_donbas", source_id="ucdp_ged_bulk", source_family_id="ucdp",
        modality="text", obs_type="flood",
        occurred_start=T_AFTER, occurred_end=T_AFTER + timedelta(hours=1),
        geo=GeoRef(lon=PATCH_LON, lat=PATCH_LAT, precision_m=1000.0),
        text="residents report the river has flooded the low ground")
    obs = [normalize(text, resolver, _TAX, embedder=embedder)[0], flood_obs]

    r1 = fuse(obs, _Empty(), _AlwaysSame(), theater_id="ua_donbas")
    r2 = fuse(obs, _Empty(), _AlwaysSame(), theater_id="ua_donbas")
    assert len(r1.events) == 1
    e = r1.events[0]
    assert e.n_independent_families == 2          # ucdp (text) + copernicus_optical (imagery)
    assert e.confidence_band == "High"            # cross-modal noisy-OR lift above Rumored
    assert r1.no_silent_drop({o.obs_id for o in obs})
    assert assert_bit_identical(r1, r2)
